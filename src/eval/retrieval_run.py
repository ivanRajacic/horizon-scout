"""Study 1: the four-condition retrieval ladder, one question at a time.

This answers RQ2 from `horizon-scout.md`. The bank's vector questions go through
four retrieval conditions - lexical (BM25), dense (FAISS), hybrid (RRF fusion of
the two), and hybrid + cross-encoder rerank - each condition generates an answer
from its own top chunks, and each condition is measured with ranking metrics.
The condition that wins here is frozen as the stack Study 2 runs on.

Nothing existing does this. `run-bank` (src/eval/run.py) varies the ROUTER mode,
not the retriever; `bench-retrievers` compares the four retrievers but fetches
independently per condition and never generates an answer.

FETCH ONCE, REUSE EVERYWHERE. This is the requirement the whole module is shaped
around. Per question there is exactly one FTS query, one embed call plus one
FAISS search, one rerank call, and four generator calls:

    lex_deep = lexical.search(q.text, k=depth)     # the only FTS query
    den_deep = dense.search(q.text, k=depth)       # the only embed + FAISS

    lexical        full = lex_deep                       gen = full[:k_gen]
    dense          full = den_deep                       gen = full[:k_gen]
    hybrid         full = rrf_fuse([lex_deep, den_deep]) gen = full[:k_gen]
    hybrid_rerank  full = rerank(fused[:RERANK_DEPTH])   gen = full[:k_gen]

All four conditions therefore see the exact same underlying candidates, so the
only thing that differs between them is the fusion and the rerank - not which
chunks happened to come back on that particular fetch. If each condition fetched
for itself, a difference in the ladder could be fetch variance rather than the
thing being measured, and there would be no way to tell the two apart.

At the default depth of 100 (= FUSE_CANDIDATES) the hybrid condition assembled
here is byte-identical to the shipped `HybridRetriever.search`, which is what
makes the measurement a measurement OF the shipped stack rather than of a
lookalike. The retrieval stack is frozen: src/retrieval/*.py is call-only.

Ranking metrics are computed off the FULL deep list, not off the truncated list
handed to the generator, at cutoffs 10 and 20. Only list order is used;
`SearchResult.score` is never compared across retrievers (the base.py contract).

JUDGING RUNS AS ONE BATCH PER CONDITION, and it cannot be one batch over all of
them. `judge_pending` (run.py:284) keys the batch it has in flight by question
id alone - `by_qid = {r["question_id"]: r for r in pending}` at run.py:302 - and
here all four conditions answer the SAME question ids. Putting four conditions
into one batch would collapse four records onto one key, so three of the four
verdicts would be handed to the wrong record and then thrown away. One batch per
condition is exactly how run.py already calls the function and needs zero
changes to it. Do not "optimize" this into a single batch: the four calls are
not four round trips wasted, they are the only shape that keeps a verdict
attached to the record it was passed.
"""

from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.config import (EMBED_MODEL, FUSE_CANDIDATES, INDEX_META_PATH,
                        JUDGE_DEFAULT, JUDGE_MODELS, JUDGE_PASS_FACTUAL,
                        JUDGE_PASS_FAITHFULNESS, RERANK_DEPTH,
                        RERANKER_MODEL, RRF_K)
from src.eval import usage
from src.eval.bank import BankQuestion, load_bank
from src.eval.metrics import METRICS, dedup_projects, score_ranking
# The report's tables, money, durations and pass cells are run.py's, imported
# and never copied: two studies whose reports drifted apart in formatting would
# be two studies you cannot read side by side.
from src.eval.run import (RUNS_DIR, STATUS_ERROR, STATUS_EXECUTED,
                          ConsoleProgress,  # noqa: F401 - re-export for the CLI
                          RunProgress, _dur, _money, _pass_cell, _sum_cost,
                          _table, checkpoint,
                          judge_case_for, judge_pending, needs_judge,
                          new_run_id, read_records, select_questions)
from src.llm import fingerprint
from src.retrieval.base import SearchResult
from src.retrieval.hybrid import rrf_fuse
from src.synthesis.synthesizer import SYNTH_PROMPT_VERSION, SYSTEM_PROMPT

# Same names as src/retrieval/registry.py:RETRIEVERS, deliberately - a condition
# here IS one of those retrievers, and a second vocabulary for the same four
# things would be a bug waiting to happen.
CONDITIONS = ("lexical", "dense", "hybrid", "hybrid_rerank")

# Cutoffs every condition is scored at. 20 is H2's recall cutoff; 10 is what the
# generator actually sees by default, so the pair says both "did it find the
# gold at all" and "did it find it early enough to be used".
METRIC_KS = (10, 20)


@dataclass
class _GenResult:
    """Adapter so run.py's `judge_case_for` can be imported instead of copied.

    That function (run.py:154) reads exactly two attributes off the result it is
    given, `.answer` and `.chunks`, and a SynthesisResult calls the second one
    `.used_chunks`. Two fields here is the whole cost of not duplicating it.
    """

    answer: str
    chunks: list


def index_meta() -> dict:
    """Which dense index this run measured against, for the run's meta block.

    A local six-line reader rather than an import of mcp_server._index_meta:
    importing that would drag the whole MCP server stack into the runner for one
    dict. It never raises - the index fingerprint is provenance, and provenance
    must never be the thing that fails a run - so an unreadable file comes back
    as an "error" key that the report can print.
    """
    try:
        raw = INDEX_META_PATH.read_text(encoding="utf-8")
        meta = json.loads(raw)
        return {"embedding_model": meta.get("embedding_model"),
                "n_vectors": meta.get("n_vectors"),
                "built_at": meta.get("built_at"),
                "content_hash": fingerprint(raw)}
    except Exception as e:                                   # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def assemble_condition(condition: str,
                       lex_deep: list[SearchResult],
                       den_deep: list[SearchResult],
                       *, k_gen: int, query: str = "",
                       reranker=None) -> tuple[list[SearchResult],
                                               list[SearchResult]]:
    """Build one condition's (full, gen) lists from the two deep lists.

    `full` is the complete ranking that condition produced and is what the
    metrics read. `gen` is the prefix handed to the generator. This function
    never fetches anything - it is handed both deep lists already - which is
    what makes the fetch-once flow in the module docstring possible.

    RRF_K and RERANK_DEPTH are taken from config and are not parameters: they
    are frozen-stack constants, not run knobs.

    On hybrid_rerank we ask the reranker for top_k=RERANK_DEPTH and slice to
    k_gen ourselves. That is byte-identical to the shipped
    `HybridRetriever.search` asking for top_k=k (rerank.py:85 sorts the whole
    candidate list and only then truncates, hybrid.py:75 passes k straight
    through), and it leaves us the full reranked ordering, which the metrics
    need and generation does not.
    """
    if condition == "lexical":
        full = list(lex_deep)
    elif condition == "dense":
        full = list(den_deep)
    elif condition == "hybrid":
        full = rrf_fuse([list(lex_deep), list(den_deep)], RRF_K)
    elif condition == "hybrid_rerank":
        if reranker is None:
            raise ValueError("hybrid_rerank needs a reranker")
        fused = rrf_fuse([list(lex_deep), list(den_deep)], RRF_K)
        full = list(reranker.rerank_results(query, fused[:RERANK_DEPTH],
                                            top_k=RERANK_DEPTH))
    else:
        raise ValueError(f"unknown condition {condition!r}; "
                         f"choose from {list(CONDITIONS)}")
    return full, full[:k_gen]


def ranking_block(full: list[SearchResult], gold, ks=METRIC_KS) -> dict | None:
    """Ranking metrics for one condition, off its FULL deep list.

    Deliberately not off the k_gen list: a gold project sitting at rank 15 of a
    depth-100 fetch is a real difference between conditions, and truncating to
    the ten chunks that went to the generator would score it as a miss and hide
    exactly the thing Study 1 is measuring.

    None when there is nothing to score - no gold labels, or no results at all.
    The cutoffs are string keys because a record is JSON.
    """
    if not gold or not full:
        return None
    ranked = dedup_projects(full)
    gold_set = set(gold)
    at: dict[str, dict[str, float]] = {}
    for k in ks:
        scores = score_ranking(ranked, gold_set, k)
        at[str(k)] = {name: round(scores[name], 4) for name in METRICS}
    return {"gold_size": len(gold_set),
            "projects_retrieved": len(ranked),
            "at": at}


# --------------------------------------------------------------------------
# the pieces a run needs
# --------------------------------------------------------------------------

def fetches_for(condition: str) -> tuple[bool, bool]:
    """(needs the lexical fetch, needs the dense fetch) for one condition.

    The one place that dependency lives, so "which conditions can still run
    when the embedder is down" and "which servers does this run need up" have a
    single answer rather than three.
    """
    return (condition in ("lexical", "hybrid", "hybrid_rerank"),
            condition in ("dense", "hybrid", "hybrid_rerank"))


def build_components(conditions, *, lexical=None, dense=None, reranker=None,
                     synth=None) -> dict:
    """Build only what the chosen conditions need, once, and share it.

    Same sharing as `cmd_bench_retrievers` (cli.py:404): hybrid and
    hybrid_rerank do not get retrievers of their own, they get the SAME lexical
    and dense objects the single-retriever conditions use, which is what makes
    one fetch serve all four conditions. A run of `--conditions lexical` must
    not need the embed server up, hence "only what is needed".

    Every component is injectable so a test can pass fakes and never build a
    real one, and the imports are LOCAL to this function so that importing this
    module (to read CONDITIONS, say, or to run the pure core's tests) never
    reaches for a llama-server, DuckDB or the FAISS index.
    """
    need_lex = any(fetches_for(c)[0] for c in conditions)
    need_den = any(fetches_for(c)[1] for c in conditions)
    need_rr = "hybrid_rerank" in conditions

    if need_lex and lexical is None:
        from src.retrieval.lexical import LexicalRetriever
        lexical = LexicalRetriever()
    if need_den and dense is None:
        from src.retrieval.vector_search import VectorSearcher
        dense = VectorSearcher()
    if need_rr and reranker is None:
        from src.retrieval.rerank import RerankClient
        reranker = RerankClient()
    if synth is None:
        from src.synthesis.synthesizer import Synthesizer
        synth = Synthesizer()

    return {"lexical": lexical if need_lex else None,
            "dense": dense if need_den else None,
            "reranker": reranker if need_rr else None,
            "synth": synth}


@dataclass
class DeepFetch:
    """The two deep lists for one question, and whatever went wrong getting
    them.

    A fetch failure is DATA here, not an exception. The whole point of fetching
    once per question is that four conditions share the result, so a dead dense
    stack must still leave the lexical condition able to produce a record - and
    it can only do that if the failure is carried alongside the good list
    instead of unwinding the question.
    """

    lex: list | None = None
    den: list | None = None
    lex_error: str | None = None
    den_error: str | None = None
    lex_traceback: str = ""
    den_traceback: str = ""
    timings: dict = field(default_factory=dict)

    def error_for(self, condition: str) -> str | None:
        """The error string blocking this condition, or None if it can run."""
        need_lex, need_den = fetches_for(condition)
        if need_lex and self.lex_error:
            return self.lex_error
        if need_den and self.den_error:
            return self.den_error
        # Neither failed but the list is missing: the caller asked for a
        # condition whose fetch it never requested. Say that rather than
        # silently scoring the condition on an empty list.
        if need_lex and self.lex is None:
            return "the lexical fetch this condition needs was never run"
        if need_den and self.den is None:
            return "the dense fetch this condition needs was never run"
        return None

    def traceback_for(self, condition: str) -> str:
        need_lex, need_den = fetches_for(condition)
        if need_lex and self.lex_error:
            return self.lex_traceback
        if need_den and self.den_error:
            return self.den_traceback
        return ""

    def seconds_for(self, condition: str) -> float:
        """Fetch seconds this condition depended on."""
        need_lex, need_den = fetches_for(condition)
        return ((self.timings.get("lex", 0.0) if need_lex else 0.0)
                + (self.timings.get("den", 0.0) if need_den else 0.0))


def fetch_deep(components: dict, query: str, depth: int, *,
               need_lex: bool, need_den: bool) -> DeepFetch:
    """One FTS query and one embed+FAISS search for a question. Never raises.

    Each fetch is in its OWN try: the two stacks fail independently (the
    embedder can be down while DuckDB is fine), and one of them dying must cost
    only the conditions that needed it.

    The calls are deliberately bare - positional query, k=depth, no
    project_ids/source filter. Study 1 measures the unfiltered retrievers; a
    filter here would make the four conditions a measurement of something else.
    """
    out = DeepFetch()
    if need_lex:
        started = time.perf_counter()
        try:
            out.lex = list(components["lexical"].search(query, k=depth))
        except Exception as e:                               # noqa: BLE001
            out.lex_error = f"{type(e).__name__}: {e}"
            out.lex_traceback = traceback.format_exc(limit=6)
        out.timings["lex"] = time.perf_counter() - started
    if need_den:
        started = time.perf_counter()
        try:
            out.den = list(components["dense"].search(query, k=depth))
        except Exception as e:                               # noqa: BLE001
            out.den_error = f"{type(e).__name__}: {e}"
            out.den_traceback = traceback.format_exc(limit=6)
        out.timings["den"] = time.perf_counter() - started
    return out


# --------------------------------------------------------------------------
# phase A - one record per (condition, question)
# --------------------------------------------------------------------------

def execute_question_retrieval(q: BankQuestion, condition: str,
                               fetched: DeepFetch, components: dict, *,
                               k_gen: int, params: dict) -> dict:
    """One (condition, question) record. Never raises.

    Same shape as run.py's `execute_question` where the two overlap - the bank
    metadata, the answer, the judge case, the spend - so the two studies'
    records can be read the same way. What is different is what Study 1 is
    for: `params`, the deep-list `ranking` block, and `retrieved_project_ids`
    taken from the DEEP list, which together mean every metric in the report can
    be recomputed from the record alone.
    """
    record = {
        "condition": condition, "question_id": q.question_id, "text": q.text,
        "expected_route": q.expected_route, "level": q.level,
        "subtype": q.subtype, "term_style": q.term_style,
        "specification": q.specification, "adversarial": q.is_adversarial,
        "gold_project_ids": q.gold_project_ids,
        # On the record and not only inside the judge case: reading a failure
        # means reading the answer BESIDE what it was supposed to say.
        "reference_answer": q.reference_answer or "",
        # ConsoleProgress prints `mode`; here the condition IS the mode, and a
        # blank column at 3am reads as a broken run.
        "mode": condition,
        # Written per record because _stamp restates the run id, the models and
        # the prompt versions but not these - and a record whose depth is
        # unknowable is not reproducible.
        "params": dict(params),
    }
    # The clock starts BEFORE the fetch this condition depended on, not at the
    # top of this function: the fetch already happened, shared with the other
    # conditions, and a wall figure that left it out would come in under
    # retrieval + synth in the latency table and read as a bug. Same reasoning
    # as the timings_s comment below - what a condition would cost on its own.
    started = time.perf_counter() - fetched.seconds_for(condition)

    blocked = fetched.error_for(condition)
    if blocked:
        record.update({
            "status": STATUS_ERROR,
            "error": blocked,
            "traceback": fetched.traceback_for(condition),
            "wall_s": round(time.perf_counter() - started, 3),
            "timings_s": {"fetch": round(fetched.seconds_for(condition), 3)},
            # take() even here: it costs nothing and it guarantees no stray call
            # can leak onto the next condition's record.
            "spend": {"gen": usage.total(usage.take(q.question_id)).as_dict()},
            "judge_case": None,
            "score": None,
        })
        return record

    try:
        t0 = time.perf_counter()
        full, gen = assemble_condition(condition, fetched.lex or [],
                                       fetched.den or [], k_gen=k_gen,
                                       query=q.text,
                                       reranker=components.get("reranker"))
        assemble_s = time.perf_counter() - t0

        t1 = time.perf_counter()
        with usage.stage(q.question_id, "gen"):
            res = components["synth"].synthesize(q.text, gen)
        synth_s = time.perf_counter() - t1
        # Immediately after the call, per condition. Phase A is sequential, so
        # this take is exactly this condition's generation and nothing else.
        spend = {"gen": usage.total(usage.take(q.question_id)).as_dict()}
    except Exception as e:                                   # noqa: BLE001
        # A broken reranker or a broken generator costs the conditions that
        # needed it and nothing more, the same way a broken fetch does.
        record.update({
            "status": STATUS_ERROR,
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc(limit=6),
            "wall_s": round(time.perf_counter() - started, 3),
            "timings_s": {"fetch": round(fetched.seconds_for(condition), 3)},
            "spend": {"gen": usage.total(usage.take(q.question_id)).as_dict()},
            "judge_case": None,
            "score": None,
        })
        return record

    fetch_s = fetched.seconds_for(condition)
    record.update({
        "status": STATUS_EXECUTED,
        "error": None,
        "answer": res.answer,
        "chunk_ids": [c.chunk_id for c in res.used_chunks],
        "chunks_passed_to_gen": len(res.used_chunks),
        "dropped_for_budget": res.dropped_for_budget,
        "citation_violations": res.citation_violations,
        # The DEEP dedup, not the generator's chunks: the ranking block below is
        # scored off this list, so keeping it means the report's numbers can be
        # recomputed from the record without re-running retrieval.
        "retrieved_project_ids": dedup_projects(full),
        "ranking": ranking_block(full, q.gold_project_ids),
        # The fetch seconds are SHARED by the conditions that used them and are
        # counted in full into each of those records. That is deliberate: the
        # honest per-condition latency is what that condition would cost if it
        # ran on its own, which is the number the latency ladder's prediction
        # (lexical < dense < hybrid < hybrid_rerank) is about. Summing this
        # column across conditions therefore over-counts the run's wall clock -
        # it is a per-condition figure, never a run total.
        "timings_s": {"fetch": round(fetch_s, 3),
                      "assemble": round(assemble_s, 3),
                      "retrieval": round(fetch_s + assemble_s, 3),
                      "synth": round(synth_s, 3)},
        "wall_s": round(time.perf_counter() - started, 3),
        "spend": spend,
        "score": None,                     # phase B fills it
        "judge_case": judge_case_for(q, _GenResult(res.answer,
                                                   res.used_chunks)),
    })
    return record


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

def run_retrieval(bank_path, conditions=CONDITIONS, *,
                  depth: int = FUSE_CANDIDATES, k_gen: int = 10,
                  judge: bool = True, ids=None, routes=("vector",),
                  limit: int | None = None, run_id: str | None = None,
                  runs_dir=RUNS_DIR, judge_model: str = JUDGE_DEFAULT,
                  resume: bool = False, components: dict | None = None,
                  pool=None, progress: RunProgress | None = None) -> dict:
    """Run the retrieval ladder: execute (phase A), then judge (phase B).

    Returns the run's meta dict. Every record is on disk the moment it lands,
    so a killed run keeps whatever it already paid for - the same discipline,
    and the same journal machinery, as run.py.
    """
    bad = [c for c in conditions if c not in CONDITIONS]
    if bad:
        raise ValueError(f"unknown condition(s) {bad}; "
                         f"choose from {list(CONDITIONS)}")
    # Questions are executed in the fixed CONDITIONS order regardless of the
    # order they were asked for in, so two runs of the same bank are comparable
    # line for line.
    ordered = [c for c in CONDITIONS if c in conditions]

    questions = select_questions(load_bank(bank_path), ids,
                                 list(routes) if routes else None, limit)
    run_id = run_id or f"retrieval_{new_run_id()}"
    out_dir = Path(runs_dir) / run_id
    records_path = out_dir / "records.jsonl"
    progress = progress or RunProgress()

    # Two runs appending to one journal would collapse into each other by
    # (condition, question_id) with no way to tell afterwards which line came
    # from which. Refuse rather than mix.
    if records_path.is_file() and not resume:
        raise ValueError(
            f"{records_path} already exists. Pass --resume to continue that "
            f"run (it skips what is recorded and judges what is owed), or "
            f"--run-id <something-else> to start a new one.")

    prior: dict[tuple[str, str], dict] = {}
    if resume:
        prior = {(r.get("condition"), r.get("question_id")): r
                 for r in read_records(records_path)}

    if components is None:
        components = build_components(ordered)

    params = {"depth": depth, "k_gen": k_gen, "rrf_k": RRF_K,
              "rerank_depth": RERANK_DEPTH, "metric_ks": list(METRIC_KS)}

    meta = {
        "run_id": run_id,
        "study": "retrieval-ladder",
        "started": datetime.now().isoformat(timespec="seconds"),
        "bank": str(bank_path),
        "bank_hash": fingerprint(Path(bank_path).read_text(encoding="utf-8")),
        # `ordered`, not what was asked for: the report and the console read
        # this, and they should say what actually ran, in the order it ran.
        "conditions": ordered,
        "params": params,
        # ConsoleProgress.run_start prints meta['k'] and a blank there reads as
        # a broken run. This is that line's copy; params["k_gen"] is the
        # authoritative one.
        "k": k_gen,
        "judged": judge,
        "resumed": bool(prior),
        "n_questions": len(questions),
        "models": {
            "generator": getattr(getattr(components.get("synth"), "llm", None),
                                 "model", "?"),
            "judge": (JUDGE_MODELS.get(judge_model, judge_model)
                      if judge else None),
            "embed": EMBED_MODEL,
            "reranker": (RERANKER_MODEL if "hybrid_rerank" in ordered
                         else None),
        },
        "versions": {"synth_prompt": f"{SYNTH_PROMPT_VERSION}:"
                                     f"{fingerprint(SYSTEM_PROMPT)}"},
        "index": index_meta(),
    }
    progress.run_start(meta, out_dir)

    # ONE condition_start for the whole of phase A: conditions are the INNER
    # loop here (that is what makes fetch-once possible), so there is no
    # per-condition pass to announce. The counter is over RECORDS, not
    # questions - four conditions on forty questions is a hundred and sixty
    # lines, and a progress bar that stopped at forty would be lying.
    progress.condition_start(", ".join(ordered), len(questions) * len(ordered))

    by_condition: dict[str, list[dict]] = {c: [] for c in ordered}
    n_records = len(questions) * len(ordered)
    i = 0
    for q in questions:
        done_already = {c: prior.get((c, q.question_id)) for c in ordered}
        missing = [c for c in ordered if done_already[c] is None]

        fetched = DeepFetch()
        if missing:
            # Only the deep lists the MISSING conditions need. A resumed run
            # that owes nothing on this question pays for no fetch at all -
            # fetches are cheap, but "cheap" is not "free" over 40 questions.
            need_lex = any(fetches_for(c)[0] for c in missing)
            need_den = any(fetches_for(c)[1] for c in missing)
            fetched = fetch_deep(components, q.text, depth,
                                 need_lex=need_lex, need_den=need_den)

        for condition in ordered:
            i += 1
            previous = done_already[condition]
            if previous is not None:
                # Already executed. If a verdict is still owed it joins this
                # run's judge batch - phase A for it is paid for and must not
                # be paid for twice.
                by_condition[condition].append(previous)
                progress.question_skipped(
                    previous, i, n_records,
                    "executed, judging owed" if needs_judge(previous)
                    else f"already recorded ({previous.get('status')})")
                continue
            progress.question_start(condition, i, n_records, q)
            record = execute_question_retrieval(q, condition, fetched,
                                                components, k_gen=k_gen,
                                                params=params)
            checkpoint(records_path, record, meta)
            by_condition[condition].append(record)
            progress.question_done(record, i, n_records)

    # PHASE B. One batch per condition - see the module docstring for why this
    # cannot be one batch over everything. One pool, built on first need and
    # reused, because a JudgePool is a pool of processes.
    if judge:
        for condition in ordered:
            batch = by_condition[condition]
            if not any(needs_judge(r) for r in batch):
                continue
            if pool is None:
                from src.judge.ragas_judge import JudgePool
                pool = JudgePool(model_key=judge_model)
            judge_pending(batch, pool, records_path=records_path, meta=meta,
                          condition=condition, progress=progress)

    meta["ended"] = datetime.now().isoformat(timespec="seconds")
    meta["out_dir"] = str(out_dir)
    meta["records_path"] = str(records_path)

    # Rebuild from disk so the report always reflects the file, resumed rows
    # included - the report is a view of the record, never of memory.
    on_disk = read_records(records_path)
    report = render_retrieval_report(on_disk, meta)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    meta["report_path"] = str(out_dir / "report.md")
    meta["n_records"] = len(on_disk)
    meta["n_errors"] = sum(1 for r in on_disk if r.get("error"))
    meta["n_unjudged"] = sum(1 for r in on_disk if needs_judge(r))
    progress.run_done(meta)
    return meta


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def _at(record: dict, k: int) -> dict:
    """One record's metrics at cutoff k, or {} if it has no ranking block."""
    return ((record.get("ranking") or {}).get("at") or {}).get(str(k)) or {}


def _metric_mean(records: list[dict], k: int, metric: str) -> str:
    """Mean of one metric at one cutoff over the records that have it.

    "-" and never 0.000 when nothing landed in the cell: a condition that never
    produced a ranking and a condition that ranked everything at zero are
    different findings, and a report that prints them the same way is lying
    about one of them.
    """
    values = [_at(r, k)[metric] for r in records if _at(r, k).get(metric)
              is not None]
    return f"{sum(values) / len(values):.3f}" if values else "-"


def _seconds_mean(records: list[dict], key: str) -> str:
    values = [(r.get("timings_s") or {})[key] for r in records
              if (r.get("timings_s") or {}).get(key) is not None]
    return f"{sum(values) / len(values):.2f}s" if values else "-"


def _wall_mean(records: list[dict]) -> str:
    values = [r["wall_s"] for r in records if r.get("wall_s") is not None]
    return f"{sum(values) / len(values):.2f}s" if values else "-"


def _n_questions(records: list[dict], **match) -> int:
    """How many DISTINCT questions match, not how many records.

    Four conditions answer every question, so counting records would report a
    twenty-question bank as eighty.
    """
    return len({r.get("question_id") for r in records
                if all(r.get(field) == value for field, value in match.items())})


def render_retrieval_report(records: list[dict], meta: dict) -> str:
    """The run, as markdown. Pure: records and meta in, one string out.

    Seven sections, in the order someone reads them: what ran, the ranking
    ladder (the headline), exact-term against paraphrase (RQ2's real result),
    the per-level breakdown, answer quality if the run judged, latency, and
    then everything that broke plus one line per cell.

    Every cell survives missing data. An empty run, a run where every record
    errored, a record with no `ranking` block, a `meta` with nothing but a run
    id - all render, with "-" wherever there is no number to print.
    """
    records = list(records or [])
    conditions = list(meta.get("conditions") or [])
    if not conditions:
        # No conditions in meta (an old or hand-built meta): fall back to the
        # ones the records themselves name, in the order they first appear.
        for r in records:
            if r.get("condition") and r["condition"] not in conditions:
                conditions.append(r["condition"])

    of: dict[str, list[dict]] = {c: [r for r in records
                                     if r.get("condition") == c]
                                 for c in conditions}
    ok = [r for r in records if not r.get("error")]
    broken = [r for r in records if r.get("error")]

    out: list[str] = [f"# Retrieval ladder {meta.get('run_id')}", ""]

    # --- 1. what ran --------------------------------------------------------
    models = meta.get("models") or {}
    params = meta.get("params") or {}
    index = meta.get("index") or {}
    depth = params.get("depth")
    out += [
        f"- **bank**: `{meta.get('bank')}` ({meta.get('n_questions', '?')} "
        f"question(s) selected, hash `{meta.get('bank_hash')}`)",
        f"- **conditions**: {', '.join(conditions) or '(none)'}",
        f"- **params**: depth {depth}, k_gen {params.get('k_gen')}, "
        f"rrf_k {params.get('rrf_k')}, rerank_depth "
        f"{params.get('rerank_depth')}, metric cutoffs "
        f"{', '.join(str(k) for k in params.get('metric_ks') or []) or '-'}"
        + ("  (depth 100 = FUSE_CANDIDATES, which is what makes the hybrid "
           "condition here identical to the shipped HybridRetriever)"
           if depth == FUSE_CANDIDATES else ""),
        f"- **generator**: `{models.get('generator')}`",
        f"- **judge**: `{models.get('judge')}`"
        + ("" if meta.get("judged") else "  (judging SKIPPED this run)"),
        f"- **embedder**: `{models.get('embed')}`",
        f"- **reranker**: `{models.get('reranker')}`",
        f"- **started** {meta.get('started')}  **ended** {meta.get('ended')}",
        "",
        "**Prompt versions** (label:content-hash - a silent edit without a "
        "version bump is still visible here):",
        "",
    ]
    for name, value in sorted((meta.get("versions") or {}).items()):
        out.append(f"- `{name}` = {value}")

    out += ["", "**Index this run measured against**: "]
    if not index:
        out[-1] += "not recorded"
    elif index.get("error"):
        out[-1] += f"unreadable - {index['error']}"
    else:
        out[-1] += (f"`{index.get('embedding_model')}`, "
                    f"{index.get('n_vectors')} vectors, built "
                    f"{index.get('built_at')}, hash "
                    f"`{index.get('content_hash')}`")

    total_cost = _sum_cost(records)
    out += ["",
            f"**Priced cost: {_money(total_cost)}** over "
            f"{len(records)} record(s). This is what these `claude -p` calls "
            "WOULD have cost on the API; on the Max subscription the marginal "
            "spend is ~EUR 0. A priced figure, not a billed one.",
            ""]

    # --- 2. the ladder ------------------------------------------------------
    out += ["## Ranking ladder", "",
            "Project-level metrics off the FULL depth-"
            f"{depth if depth is not None else 'N'} list each condition "
            "produced, not off the "
            f"{params.get('k_gen', 'k_gen')} chunks the generator saw. All "
            "four conditions were assembled from the same two deep fetches, so "
            "the difference between these rows is the fusion and the rerank and "
            "nothing else.", ""]
    rows = []
    for c in conditions:
        ranked = [r for r in of[c] if r.get("ranking")]
        rows.append([c, str(len(ranked)),
                     _metric_mean(ranked, 10, "hit"),
                     _metric_mean(ranked, 10, "recall"),
                     _metric_mean(ranked, 20, "recall"),
                     _metric_mean(ranked, 10, "mrr"),
                     _metric_mean(ranked, 10, "ndcg")])
    out += _table(["condition", "n", "hit@10", "recall@10", "recall@20",
                   "mrr@10", "ndcg@10"], rows) + [""]

    # --- 3. exact term vs paraphrase ---------------------------------------
    styles = ("exact-term", "paraphrase")
    out += ["## Exact term vs paraphrase", "",
            "; ".join(f"{_n_questions(records, term_style=s)} {s}"
                      for s in styles) + " question(s) in this run.",
            "",
            "Lexical should do well where the question reuses the corpus's own "
            "words and badly where it does not. Where the lexical row crosses "
            "the dense row between these two halves is the thing to look at.",
            ""]
    rows = []
    for c in conditions:
        cells = []
        for style in styles:
            group = [r for r in of[c]
                     if r.get("term_style") == style and r.get("ranking")]
            cells += [_metric_mean(group, 20, "recall"),
                      _metric_mean(group, 10, "mrr")]
        rows.append([c, *cells])
    out += _table(["condition", "exact-term recall@20", "exact-term mrr@10",
                   "paraphrase recall@20", "paraphrase mrr@10"], rows) + [""]

    # --- 4. by level --------------------------------------------------------
    levels = ("L1", "L2", "L3")
    out += ["## By level", "",
            "; ".join(f"{level} {_n_questions(records, level=level)}"
                      for level in levels) + " question(s). Every cell is "
            "recall@20. L3 (five or more gold projects) is the recall stress "
            "cell: a condition can carry L1 on one lucky chunk and still miss "
            "most of an L3 gold set.", ""]
    rows = []
    for c in conditions:
        cells = [_metric_mean([r for r in of[c] if r.get("level") == level
                               and r.get("ranking")], 20, "recall")
                 for level in levels]
        rows.append([c, *cells])
    out += _table(["condition", *levels], rows) + [""]

    # --- 5. answer quality --------------------------------------------------
    out += ["## Answer quality", ""]
    scored = [r for r in records if (r.get("score") or {}).get("passed")
              is not None]
    if scored:
        out += [f"Sonnet-judged RAGAS pass rates against the bank's reference "
                f"answers - a pass needs factual correctness >= "
                f"{JUDGE_PASS_FACTUAL} and faithfulness >= "
                f"{JUDGE_PASS_FAITHFULNESS}. This measures the ANSWER, which "
                "is downstream of retrieval: it moves for generation reasons "
                "the ranking ladder above cannot see, so read it beside that "
                "table rather than instead of it.", ""]
        rows = []
        for c in conditions:
            rows.append([c, _pass_cell(of[c]),
                         *[_pass_cell([r for r in of[c]
                                       if r.get("term_style") == s])
                           for s in styles]])
        out += _table(["condition", "all", *styles], rows) + [""]
    else:
        out += ["Nothing was judged in this run (`--no-judge`, or nothing got "
                "far enough to be judged). The judge cases are on the records, "
                "so `--resume` on this run id judges them later without "
                "re-running generation.", ""]

    # --- 6. latency ---------------------------------------------------------
    out += ["## Latency", "",
            "The prediction this table tests: retrieval time climbs lexical -> "
            "dense -> hybrid -> hybrid_rerank, because each step adds work to "
            "the one before it. Fetch seconds are SHARED by the conditions "
            "that used them and counted in full into each, so this column is "
            "what a condition would cost on its own - never sum it into a run "
            "total.", ""]
    rows = []
    for c in conditions:
        timed = [r for r in of[c] if r.get("timings_s")]
        rows.append([c, str(len(timed)),
                     _seconds_mean(timed, "fetch"),
                     _seconds_mean(timed, "assemble"),
                     _seconds_mean(timed, "retrieval"),
                     _seconds_mean(timed, "synth"),
                     _wall_mean(timed)])
    out += _table(["condition", "n", "fetch", "assemble", "retrieval", "synth",
                   "wall"], rows) + [""]

    # --- 7. what broke, and every cell --------------------------------------
    if broken:
        out += ["## Errors", ""]
        for r in broken:
            out += [f"### `{r.get('question_id')}` ({r.get('condition')}) - "
                    f"{r.get('error')}", "",
                    "```", str(r.get("traceback", "")).strip(), "```", ""]

    failures = [r for r in ok if (r.get("score") or {}).get("passed") is False]
    if failures:
        out += ["## Failures - answer beside reference", "",
                "Reading these is how you find out whether the condition "
                "retrieved the wrong thing or the generator fumbled the right "
                "thing.", ""]
        for r in failures:
            score = r.get("score") or {}
            out += [f"### `{r.get('question_id')}` ({r.get('condition')}) "
                    f"{r.get('level')}/{r.get('term_style')}", "",
                    f"factual={score.get('factual_correctness')} "
                    f"faithfulness={score.get('faithfulness')} "
                    f"(thresholds {JUDGE_PASS_FACTUAL}/"
                    f"{JUDGE_PASS_FAITHFULNESS}) "
                    f"path={score.get('judge_path')}"
                    + (f" - {score['detail']}" if score.get("detail") else ""),
                    "",
                    f"**Q** {r.get('text', '')}", "",
                    f"**Answer** {r.get('answer', '')}", "",
                    "**Reference** "
                    + (r.get("reference_answer")
                       or "(none on the bank entry)"), ""]

    if records:
        out += ["## Every cell", "",
                "One line per (condition, question), in execution order.", ""]
        rows = []
        for r in records:
            score = r.get("score") or {}
            mark = {True: "PASS", False: "FAIL"}.get(score.get("passed"), "-")
            note = ""
            if r.get("error"):
                mark, note = "ERROR", str(r["error"])[:60]
            elif score.get("passed") is not None:
                note = (f"factual={score.get('factual_correctness')} "
                        f"faith={score.get('faithfulness')}")
            else:
                note = str(score.get("reason") or "not judged")[:60]
            rows.append([
                f"`{r.get('question_id')}`", str(r.get("condition") or "-"),
                str(r.get("level") or "-"), str(r.get("term_style") or "-"),
                _metric_mean([r], 10, "hit"), _metric_mean([r], 20, "recall"),
                mark, _dur(r.get("wall_s", 0.0)),
                _money((r.get("spend") or {}).get("total_cost_usd", 0.0)),
                note])
        out += _table(["question", "condition", "level", "term_style",
                       "hit@10", "recall@20", "result", "time", "cost",
                       "note"], rows) + [""]

    return "\n".join(out)
