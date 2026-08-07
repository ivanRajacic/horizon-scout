"""Full ask pipeline: router -> (sql | vector | hybrid) -> answer.

Pure-SQL answers are the table plus one templated sentence - no LLM synthesis
(an 8B paraphrasing a correct table can only subtract accuracy). Vector and
hybrid answers go through the Synthesizer. Every ask is logged to
data/logs/ask.jsonl with per-stage timings for M5's failure analysis.

Retrieval is ONE stack, config.RUNTIME_RETRIEVER, built through the registry
and SHARED between the vector route and the scoped route's semantic step - so
both paths see identical retrieval and the lexical connection, FAISS index and
rerank client are constructed once per Ask. Until 2026-08-03 this was a bare
dense VectorSearcher; see the RUNTIME_RETRIEVER comment in config.py for why it
moved and what that means for older logs.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from src.config import EMBED_MODEL, RERANKER_MODEL, ROOT, RUNTIME_RETRIEVER
from src.llm import fingerprint, make_llm
from src.retrieval.base import Retriever
from src.retrieval.registry import build_retriever
from src.retrieval.scoped import ScopedRetriever
from src.retrieval.sql_path import SqlPath
from src.router import router as router_mod
from src.router.router import Router
from src.synthesis import synthesizer as synth_mod
from src.synthesis.synthesizer import Synthesizer

ASK_LOG_PATH = ROOT / "data" / "logs" / "ask.jsonl"
EXPLAIN_ROWS = 20  # rows shown to the LLM when --explain narrates a SQL result


@dataclass
class AskResult:
    question: str
    mode: str
    router_reason: str
    answer: str
    router_fallback: bool = False
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    chunks: list = field(default_factory=list)      # SearchResult list
    degraded: str | None = None
    weak_filter: bool = False
    # The scoped route's filter provenance, as the generator was given it. Kept
    # on the result because the judge's contexts must be what the generator saw.
    filter_note: str | None = None
    citation_violations: list[str] = field(default_factory=list)
    trace: dict = field(default_factory=dict)


# Prefixed to a scoped answer when the structured filter did not survive, so
# the reader (and the judge) sees that the search was unfiltered.
DEGRADED_NOTES = {
    "sql_failed": "[Note: the structured filter could not be applied - the SQL "
                  "step failed - so this answer is from an unfiltered search "
                  "over all projects.]",
    "value_not_found": "[Note: the structured filter was dropped - a filter "
                       "value does not exist in the database - so this answer "
                       "is from an unfiltered search over all projects.]",
}


def _templated_sql_answer(rows) -> str:
    if not rows:
        return "Query returned 0 rows."
    if len(rows) == 1 and len(rows[0]) == 1:
        return f"Result: {rows[0][0]}"
    return f"Query returned {len(rows)} row{'s' if len(rows) != 1 else ''}."


def _scoped_constraints(decision) -> tuple[list[str] | None, str | None]:
    """What the scoped route's narrowing step should be given.

    Three cases, and `None` constraints means something different in each:
      - decision with facts -> reuse the router's own list ("router"). One
        extraction per question, not two.
      - decision without facts (router fallback, or an archived mode-only
        prompt) -> the list is UNKNOWN, so narrowing re-reads the raw question.
      - no decision at all (forced mode, e.g. always-hybrid) -> nothing has been
        extracted yet, so ScopedRetriever calls extract() itself.
    """
    if decision is None:
        return None, None
    if decision.needs_project_text is None:
        return None, "fallback-raw"
    return decision.structured_constraints, "router"


class Ask:
    def __init__(self, llm=None,
                 retriever: Retriever | None = None,
                 log_path: Path = ASK_LOG_PATH,
                 retriever_name: str = RUNTIME_RETRIEVER):
        self.llm = llm or make_llm()
        self.router = Router(llm=self.llm)
        self.sql_path = SqlPath(llm=self.llm)
        # ONE retriever, shared by the vector route and the scoped route's
        # semantic step. An injected retriever keeps its caller's name only for
        # the trace - what it actually is, is the caller's business.
        self.retriever_name = retriever_name
        self.retriever = retriever or build_retriever(retriever_name)
        # The router doubles as the scoped path's constraint extractor: when a
        # forced mode (always-hybrid) skips route(), ScopedRetriever calls
        # extract() itself, so both study arms feed narrowing identical input.
        self.scoped = ScopedRetriever(self.retriever, extractor=self.router)
        self.synth = Synthesizer(llm=self.llm)
        self.log_path = log_path
        # Everything that could change an answer, pinned per trace (M5).
        self.versions = {
            "llm_model": self.llm.model,
            "embed_model": EMBED_MODEL,
            # The retrieval stack is an answer-changing input like any prompt.
            # Its absence in a log row means dense-only, pre-2026-08-03.
            "retriever": self.retriever_name,
            "router_prompt": f"{router_mod.ROUTER_PROMPT_VERSION}:"
                             f"{fingerprint(router_mod.SYSTEM_PROMPT)}",
            "synth_prompt": f"{synth_mod.SYNTH_PROMPT_VERSION}:"
                            f"{fingerprint(synth_mod.SYSTEM_PROMPT)}",
            "sql_prompt": self.sql_path.prompt_version,
            "narrow_prompt": self.scoped.narrow.prompt_version,
        }
        # Only when the stack actually reranks - recording a rerank model that
        # never scored anything would be a false entry in the trace.
        if "rerank" in self.retriever_name:
            self.versions["rerank_model"] = RERANKER_MODEL

    def ask(self, question: str, k: int = 10, mode: str | None = None,
            explain: bool = False) -> AskResult:
        timings: dict[str, float] = {}
        t0 = time.perf_counter()

        decision = None
        if mode is None:
            decision = self.router.route(question)
            timings["route"] = time.perf_counter() - t0
            mode = decision.mode
            reason, fallback = decision.reason, decision.router_fallback
        else:
            reason, fallback = "manual override", False

        if mode == "sql":
            res = self._ask_sql(question, explain)
        elif mode == "vector":
            res = self._ask_vector(question, k)
        else:
            # Deliberately `else`, not `elif mode == "scoped"`: the plan docs
            # call this route "hybrid" and the runtime calls it "scoped", so a
            # forced mode arrives under either name.
            res = self._ask_scoped(question, k, *_scoped_constraints(decision))

        res.mode = mode
        res.router_reason = reason
        res.router_fallback = fallback
        timings["total"] = time.perf_counter() - t0
        res.trace.setdefault("timings", {}).update(timings)
        self._log(res)
        return res

    def _ask_sql(self, question: str, explain: bool) -> AskResult:
        t = time.perf_counter()
        r = self.sql_path.ask(question)
        stage = {"sql": time.perf_counter() - t}
        if not r.ok:
            return AskResult(
                question=question, mode="sql", router_reason="", sql=r.sql,
                answer=f"The query could not be answered: {r.error}",
                degraded="sql_failed",
                trace={"timings": stage, "error": r.error,
                       "rows_passed_to_gen": 0, "chunks_passed_to_gen": 0})
        answer = _templated_sql_answer(r.rows)
        if explain:
            answer = self._explain_sql(question, r.columns, r.rows, answer)
        # RQ1 covariate: pure-SQL answers are templated, so nothing reaches a
        # generator unless --explain narrates the (truncated) result table.
        rows_to_gen = min(EXPLAIN_ROWS, len(r.rows)) if explain else 0
        return AskResult(
            question=question, mode="sql", router_reason="", answer=answer,
            sql=r.sql, columns=r.columns, rows=r.rows,
            trace={"timings": stage, "n_rows": len(r.rows),
                   "sql_retried": r.retried,
                   "rows_passed_to_gen": rows_to_gen,
                   "chunks_passed_to_gen": 0})

    def _ask_vector(self, question: str, k: int) -> AskResult:
        t = time.perf_counter()
        chunks = self.retriever.search(question, k=k)
        t_search = time.perf_counter() - t
        t = time.perf_counter()
        s = self.synth.synthesize(question, chunks)
        stage = {"search": t_search, "synth": time.perf_counter() - t}
        return AskResult(
            question=question, mode="vector", router_reason="",
            answer=s.answer, chunks=s.used_chunks,
            citation_violations=s.citation_violations,
            trace={"timings": stage, **s.trace,
                   "rows_passed_to_gen": 0,
                   "chunks_passed_to_gen": len(s.used_chunks)})

    def _ask_scoped(self, question: str, k: int,
                    constraints: list[str] | None = None,
                    constraints_source: str | None = None) -> AskResult:
        t = time.perf_counter()
        h = self.scoped.retrieve(question, k=k, constraints=constraints,
                                 constraints_source=constraints_source)
        t_retrieve = time.perf_counter() - t

        if h.status == "zero_match":
            return AskResult(
                question=question, mode="scoped", router_reason="",
                answer="No projects match the structured criteria in this "
                       "question, so there is nothing to summarise.",
                sql=h.sql, degraded=None, weak_filter=False,
                trace={"timings": {"retrieve": t_retrieve}, **h.trace,
                       "status": "zero_match",
                       "rows_passed_to_gen": 0, "chunks_passed_to_gen": 0})

        t = time.perf_counter()
        s = self.synth.synthesize(question, h.chunks,
                                  filter_note=h.filter_note)
        stage = {"retrieve": t_retrieve, "synth": time.perf_counter() - t}
        answer = s.answer
        note = DEGRADED_NOTES.get(h.degraded)
        if note:
            answer = f"{note}\n\n{answer}"
        return AskResult(
            question=question, mode="scoped", router_reason="", answer=answer,
            sql=h.sql, chunks=s.used_chunks, degraded=h.degraded,
            weak_filter=h.weak_filter, filter_note=h.filter_note,
            citation_violations=s.citation_violations,
            # rows_passed_to_gen stays 0: the generator is told what the filter
            # DID, not handed its rows. Redefining a covariate so a recorded
            # finding reads as closed would put a lie in the trace.
            trace={"timings": stage, **h.trace, **s.trace,
                   "rows_passed_to_gen": 0,
                   "filter_note_passed": bool(h.filter_note),
                   "chunks_passed_to_gen": len(s.used_chunks)})

    def _explain_sql(self, question, columns, rows, base) -> str:
        preview = [dict(zip(columns, r)) for r in rows[:EXPLAIN_ROWS]]
        msg = [{"role": "system", "content":
                "Explain this SQL query result in one or two plain sentences. "
                "State only what the numbers show; add no outside knowledge."},
               {"role": "user", "content":
                f"Question: {question}\nColumns: {columns}\nRows: {preview}"}]
        return base + "\n\n" + self.llm.chat(msg, max_tokens=200)

    def _log(self, res: AskResult):
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "question": res.question, "mode": res.mode,
            "router_reason": res.router_reason,
            "router_fallback": res.router_fallback, "sql": res.sql,
            "n_rows": len(res.rows),
            "chunk_ids": [c.chunk_id for c in res.chunks],
            "degraded": res.degraded, "weak_filter": res.weak_filter,
            "citation_violations": res.citation_violations,
            # What the narrowing step was GIVEN, not what it wrote - absent
            # from every row before 2026-08-05, which is why the filter
            # analysis had to reconstruct extraction output from prose.
            "structured_constraints": res.trace.get("constraints"),
            "constraints_source": res.trace.get("constraints_source"),
            # RQ1's covariate, first-class beside the trace (M5 §RQ1).
            "rows_passed_to_gen": res.trace.get("rows_passed_to_gen", 0),
            "chunks_passed_to_gen": res.trace.get("chunks_passed_to_gen", 0),
            "filter_note_passed": res.trace.get("filter_note_passed", False),
            "versions": self.versions,
            "trace": res.trace,
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
