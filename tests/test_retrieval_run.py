"""Study 1 retrieval-ladder tests: the pure core, then the runner.

No servers, no DuckDB, no FAISS, no `claude` CLI - the two retrievers, the
reranker, the synthesizer and the judge pool are all fakes (same shapes as
tests/test_retrievers.py and tests/test_run.py), and the pure core is a set of
functions over already-fetched lists.
"""

import json
from pathlib import Path

import pytest

from src.config import FUSE_CANDIDATES, RERANK_DEPTH, RRF_K
from src.eval import usage
from src.eval.retrieval_run import (CONDITIONS, METRIC_KS, DeepFetch,
                                    _GenResult, assemble_condition,
                                    build_components, execute_question_retrieval,
                                    fetch_deep, fetches_for, index_meta,
                                    ranking_block, render_retrieval_report,
                                    run_retrieval)
from src.eval.run import RunProgress, needs_judge, read_records
from src.retrieval.base import SearchResult
from src.retrieval.hybrid import HybridRetriever, rrf_fuse

DEPTH = 100
K_GEN = 10


def mk(cid, pid, score=0.0, text=None):
    return SearchResult(chunk_id=cid, project_id=pid, acronym=f"A{pid}",
                        title=f"T{pid}", source="report", section="summary",
                        score=score, text=text or f"text for {cid}")


def deep_list(prefix, pids):
    """One chunk per project id, best-first, chunk ids unique per list."""
    return [mk(f"{prefix}{i}", pid) for i, pid in enumerate(pids)]


class CountingRetriever:
    """FakeRetriever that records how many times it was asked to fetch."""

    def __init__(self, results):
        self._results = results
        self.calls = 0

    def search(self, query, k=10, project_ids=None, source=None):
        self.calls += 1
        return self._results[:k]


class CountingReranker:
    """Reverses the candidate order, so a test can prove rerank reordered, and
    counts calls, so a test can prove it was called exactly once per question."""

    def __init__(self):
        self.calls = 0
        self.seen_depths = []

    def rerank_results(self, query, results, top_k):
        self.calls += 1
        self.seen_depths.append(len(results))
        return list(reversed(results))[:top_k]


# --- fetch once, reuse everywhere ---------------------------------------

def test_all_four_conditions_come_from_one_fetch_each():
    lex = CountingRetriever(deep_list("L", range(100, 130)))
    den = CountingRetriever(deep_list("D", range(120, 150)))
    reranker = CountingReranker()

    lex_deep = lex.search("q", k=DEPTH)
    den_deep = den.search("q", k=DEPTH)
    built = {c: assemble_condition(c, lex_deep, den_deep, k_gen=K_GEN,
                                   query="q", reranker=reranker)
             for c in CONDITIONS}

    assert set(built) == set(CONDITIONS)
    # One FTS query, one embed+FAISS search, one rerank call - for all four.
    assert lex.calls == 1 and den.calls == 1
    assert reranker.calls == 1
    assert reranker.seen_depths == [RERANK_DEPTH]


def test_assemble_condition_never_fetches():
    """The retrievers are not even reachable from assemble_condition - it is
    handed lists. Proven by the call counters staying at the caller's fetches."""
    lex = CountingRetriever(deep_list("L", range(100, 120)))
    den = CountingRetriever(deep_list("D", range(110, 130)))
    lex_deep, den_deep = lex.search("q", k=DEPTH), den.search("q", k=DEPTH)
    before = (lex.calls, den.calls)
    for c in CONDITIONS:
        assemble_condition(c, lex_deep, den_deep, k_gen=K_GEN, query="q",
                           reranker=CountingReranker())
    assert (lex.calls, den.calls) == before == (1, 1)


def test_assemble_condition_does_not_mutate_its_inputs():
    lex_deep = deep_list("L", range(100, 120))
    den_deep = deep_list("D", range(110, 130))
    lex_before = [r.chunk_id for r in lex_deep]
    den_before = [r.chunk_id for r in den_deep]
    for c in CONDITIONS:
        assemble_condition(c, lex_deep, den_deep, k_gen=3, query="q",
                           reranker=CountingReranker())
    assert [r.chunk_id for r in lex_deep] == lex_before
    assert [r.chunk_id for r in den_deep] == den_before


# --- assembly ------------------------------------------------------------

def test_lexical_and_dense_gen_lists_are_prefixes_of_the_deep_lists():
    lex_deep = deep_list("L", range(100, 130))
    den_deep = deep_list("D", range(200, 230))

    full, gen = assemble_condition("lexical", lex_deep, den_deep, k_gen=K_GEN)
    assert [r.chunk_id for r in full] == [r.chunk_id for r in lex_deep]
    assert gen == full[:K_GEN] and len(gen) == K_GEN

    full, gen = assemble_condition("dense", lex_deep, den_deep, k_gen=K_GEN)
    assert [r.chunk_id for r in full] == [r.chunk_id for r in den_deep]
    assert gen == full[:K_GEN] and len(gen) == K_GEN


def test_hybrid_full_list_is_rrf_fuse_of_the_two_deep_lists():
    lex_deep = deep_list("L", range(100, 130))
    den_deep = deep_list("D", range(120, 150))
    full, gen = assemble_condition("hybrid", lex_deep, den_deep, k_gen=K_GEN)
    expected = rrf_fuse([lex_deep, den_deep], RRF_K)
    assert [r.chunk_id for r in full] == [r.chunk_id for r in expected]
    assert [r.chunk_id for r in gen] == [r.chunk_id for r in expected[:K_GEN]]


def test_hybrid_rerank_ranking_block_reflects_the_full_reranked_order():
    """The reranker reverses, so gold parked at the END of the fused list lands
    at rank 1 after reranking. The block must see that, not the k_gen prefix."""
    lex_deep = deep_list("L", [1, 2, 3, 4, 5])
    den_deep = deep_list("D", [1, 2, 3, 4, 5])   # same chunk texts, other ids
    fused = rrf_fuse([lex_deep, den_deep], RRF_K)
    last_project = fused[-1].project_id

    full, gen = assemble_condition("hybrid_rerank", lex_deep, den_deep,
                                   k_gen=2, query="q",
                                   reranker=CountingReranker())
    assert [r.chunk_id for r in full] == [r.chunk_id
                                          for r in reversed(fused)]
    assert len(gen) == 2 and len(full) == len(fused)

    block = ranking_block(full, [last_project])
    assert block["at"]["10"]["mrr"] == 1.0     # first in the FULL ordering
    assert block["at"]["10"]["hit"] == 1.0


def test_hybrid_rerank_without_a_reranker_is_a_value_error():
    with pytest.raises(ValueError, match="reranker"):
        assemble_condition("hybrid_rerank", [], [], k_gen=K_GEN)


def test_unknown_condition_raises():
    with pytest.raises(ValueError, match="unknown condition"):
        assemble_condition("scoped", [], [], k_gen=K_GEN)


# --- equivalence with the shipped stack ----------------------------------

def test_hybrid_matches_the_shipped_hybrid_retriever():
    lex_results = deep_list("L", range(100, 130))
    den_results = deep_list("D", range(120, 150))
    shipped = HybridRetriever(lexical=CountingRetriever(lex_results),
                              dense=CountingRetriever(den_results),
                              rerank=False, fuse_candidates=DEPTH)
    want = shipped.search("q", k=K_GEN)

    _, gen = assemble_condition("hybrid", lex_results[:DEPTH],
                                den_results[:DEPTH], k_gen=K_GEN)
    assert [r.chunk_id for r in gen] == [r.chunk_id for r in want]


def test_hybrid_rerank_matches_the_shipped_hybrid_retriever():
    lex_results = deep_list("L", range(100, 130))
    den_results = deep_list("D", range(120, 150))
    shipped = HybridRetriever(lexical=CountingRetriever(lex_results),
                              dense=CountingRetriever(den_results),
                              reranker=CountingReranker(), rerank=True,
                              fuse_candidates=DEPTH, rerank_depth=RERANK_DEPTH)
    want = shipped.search("q", k=K_GEN)

    _, gen = assemble_condition("hybrid_rerank", lex_results[:DEPTH],
                                den_results[:DEPTH], k_gen=K_GEN, query="q",
                                reranker=CountingReranker())
    assert [r.chunk_id for r in gen] == [r.chunk_id for r in want]


# --- ranking metrics off the deep list -----------------------------------

def test_gold_at_rank_15_of_the_deep_list_is_a_recall_at_20_hit():
    deep = deep_list("L", range(500, 530))      # 30 distinct projects
    gold_project = deep[14].project_id          # rank 15, 1-based
    full, gen = assemble_condition("lexical", deep, [], k_gen=K_GEN)
    assert len(gen) == 10                       # only ten chunks go to the LLM

    block = ranking_block(full, [gold_project])
    assert block["gold_size"] == 1
    assert block["projects_retrieved"] == 30
    assert block["at"]["20"]["recall"] == 1.0
    assert block["at"]["10"]["recall"] == 0.0
    assert block["at"]["20"]["mrr"] == round(1 / 15, 4)


def test_ranking_block_shape_and_cutoffs():
    deep = deep_list("L", range(500, 510))
    block = ranking_block(deep, [500, 505])
    assert set(block) == {"gold_size", "projects_retrieved", "at"}
    assert set(block["at"]) == {str(k) for k in METRIC_KS}
    for cell in block["at"].values():
        assert set(cell) == {"hit", "recall", "mrr", "ndcg"}
        assert all(round(v, 4) == v for v in cell.values())


def test_ranking_block_is_none_without_gold_or_results():
    deep = deep_list("L", range(500, 510))
    assert ranking_block(deep, []) is None
    assert ranking_block(deep, None) is None
    assert ranking_block([], [500]) is None


# --- odds and ends -------------------------------------------------------

def test_gen_result_shim_carries_what_judge_case_for_reads():
    from src.eval.run import judge_case_for
    from src.eval.bank import BankQuestion

    q = BankQuestion(question_id="vec-01", text="a question",
                     expected_route="vector", level="L1", subtype="identify",
                     reference_answer="the reference")
    res = _GenResult(answer="an answer", chunks=[mk("c1", 7, text="ctx")])
    case = judge_case_for(q, res)
    assert case["answer"] == "an answer"
    assert case["contexts"] == ["ctx"]
    assert case["question_id"] == "vec-01"


def test_index_meta_on_a_missing_file_reports_instead_of_raising(monkeypatch,
                                                                 tmp_path):
    monkeypatch.setattr("src.eval.retrieval_run.INDEX_META_PATH",
                        tmp_path / "nope.json")
    meta = index_meta()
    assert "error" in meta and "content_hash" not in meta


def test_index_meta_reads_a_real_file(monkeypatch, tmp_path):
    path = tmp_path / "index_meta.json"
    path.write_text('{"embedding_model": "bge", "n_vectors": 12, '
                    '"built_at": "2026-07-01"}', encoding="utf-8")
    monkeypatch.setattr("src.eval.retrieval_run.INDEX_META_PATH", path)
    meta = index_meta()
    assert meta["embedding_model"] == "bge" and meta["n_vectors"] == 12
    assert len(meta["content_hash"]) == 12 and "error" not in meta


# ==========================================================================
# the runner: fetch once, four records, per-condition judging, resume
# ==========================================================================

# --- a two-question bank, valid against the v2 schema --------------------

Q1_TEXT = "Which project farms widgets at scale?"
Q2_TEXT = "Which project studies alpine glacier retreat?"

# Gold sits at LEXICAL deep rank 15 for q1 (project 514 is the 15th of the
# 500..529 list). Only ten chunks reach the generator, so this is the case that
# proves the ranking block is scored off the deep list.
Q1 = {
    "question_id": "vec-01", "text": Q1_TEXT,
    "expected_route": "vector", "level": "L1", "subtype": "identify",
    "term_style": "paraphrase", "gold_project_ids": [514],
    "pooling_evidence": {
        "conditions_run": ["lexical", "dense", "hybrid", "hybrid_rerank"],
        "k": 20, "pooled_candidate_count": 7, "accepted": [514],
        "rejected_count": 6, "index_fingerprint": "be84cbad9182"},
    "reference_answer": "A514 (514) farms widgets.",
}

Q2 = {
    "question_id": "vec-02", "text": Q2_TEXT,
    "expected_route": "vector", "level": "L1", "subtype": "identify",
    "term_style": "exact-term", "gold_project_ids": [700],
    "pooling_evidence": {
        "conditions_run": ["lexical", "dense", "hybrid", "hybrid_rerank"],
        "k": 20, "pooled_candidate_count": 5, "accepted": [700],
        "rejected_count": 4, "index_fingerprint": "be84cbad9182"},
    "reference_answer": "A700 (700) studies glaciers.",
}

Q3_TEXT = "Which projects weigh two widget farming methods against each other?"

# An L2 (two gold projects) paraphrase question, so the report's per-level and
# term_style tables have more than one level and both styles in them.
Q3 = {
    "question_id": "vec-03", "text": Q3_TEXT,
    "expected_route": "vector", "level": "L2", "subtype": "comparison",
    "term_style": "paraphrase", "gold_project_ids": [900, 901],
    "pooling_evidence": {
        "conditions_run": ["lexical", "dense", "hybrid", "hybrid_rerank"],
        "k": 20, "pooled_candidate_count": 9, "accepted": [900, 901],
        "rejected_count": 7, "index_fingerprint": "be84cbad9182"},
    "reference_answer": "A900 (900) and A901 (901) farm widgets differently.",
}

LEX_DEEP = {Q1_TEXT: deep_list("L", range(500, 530)),
            Q2_TEXT: deep_list("L", range(700, 730)),
            Q3_TEXT: deep_list("L", range(900, 930))}
DEN_DEEP = {Q1_TEXT: deep_list("D", range(600, 630)),
            Q2_TEXT: deep_list("D", range(800, 830)),
            Q3_TEXT: deep_list("D", range(950, 980))}


def write_bank(tmp_path, records):
    p = tmp_path / "bank.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return p


# --- fakes ---------------------------------------------------------------

class QueryRetriever:
    """Answers by query text and records exactly how it was called.

    **kwargs rather than named filters on purpose: a test can then assert that
    the runner passed NO project_ids/source, which is what keeps Study 1 a
    measurement of the unfiltered retrievers.
    """

    def __init__(self, by_query, raises=None):
        self.by_query = by_query
        self.raises = raises
        self.calls = []

    def search(self, query, k=10, **kwargs):
        self.calls.append((query, k, kwargs))
        if self.raises is not None:
            raise self.raises
        return list(self.by_query[query][:k])


class FakeSynthResult:
    def __init__(self, answer, chunks):
        self.answer = answer
        self.used_chunks = list(chunks)
        self.dropped_for_budget = 0
        self.citation_violations = []


class FakeSynth:
    """Spends a labelled `claude -p` call the way tests/test_run.py's FakeAsk
    does, so per-condition spend attribution is genuinely exercised.

    `die_at` raises KeyboardInterrupt on the Nth call - a killed run, which is
    what resume exists for. KeyboardInterrupt and not a plain exception because
    the runner deliberately turns exceptions into error records.
    """

    def __init__(self, cost=0.01, die_at=None, raises=None):
        self.cost = cost
        self.die_at = die_at
        self.raises = raises
        self.calls = []
        self.llm = type("L", (), {"model": "claude-haiku-4-5-20251001"})()

    def synthesize(self, question, chunks):
        self.calls.append((question, list(chunks)))
        if self.die_at is not None and len(self.calls) == self.die_at:
            raise KeyboardInterrupt("killed mid-run")
        if self.raises is not None:
            raise self.raises
        usage.record_envelope({"total_cost_usd": self.cost, "duration_ms": 100,
                               "usage": {"output_tokens": 5}}, "haiku")
        return FakeSynthResult(f"answer to {question}", chunks)


class FakeVerdict:
    def __init__(self, passed=True, detail=""):
        self.passed = passed
        self.path = "ragas"
        self.model = "claude-sonnet-x"
        self.factual_correctness = 0.9
        self.faithfulness = 0.95
        self.detail = detail


class BatchingPool:
    """Records the question ids of every batch it is handed, and labels each
    verdict with which batch it came from - that is how a test proves the four
    per-condition batches landed on the four conditions' records."""

    def __init__(self, cost=0.05, passed=True):
        self.cost = cost
        self.passed = passed
        self.batches = []

    def judge_all(self, cases, on_verdict=None):
        label = f"batch{len(self.batches)}"
        self.batches.append([c["question_id"] for c in cases])
        for case in cases:
            qid = case["question_id"]
            with usage.stage(qid, "judge"):
                usage.record_envelope({"total_cost_usd": self.cost,
                                       "usage": {"output_tokens": 7}},
                                      "sonnet")
            if on_verdict:
                on_verdict(case, FakeVerdict(self.passed, detail=label))


class RecordingProgress(RunProgress):
    """Every event the runner emits, in order, as (name, detail) tuples."""

    def __init__(self):
        self.events = []

    def condition_start(self, condition, n):
        self.events.append(("conditions", condition, n))

    def question_start(self, condition, i, n, q):
        self.events.append(("start", condition, q.question_id))

    def question_done(self, record, i, n):
        self.events.append(("done", record["condition"], record["question_id"]))

    def question_skipped(self, record, i, n, why):
        self.events.append(("skip", record["condition"],
                            record["question_id"], why))

    def judging_start(self, condition, n):
        self.events.append(("judging", condition, n))


def fake_components(cost=0.01, die_at=None, lex_raises=None, den_raises=None,
                    synth_raises=None):
    return {"lexical": QueryRetriever(LEX_DEEP, raises=lex_raises),
            "dense": QueryRetriever(DEN_DEEP, raises=den_raises),
            "reranker": CountingReranker(),
            "synth": FakeSynth(cost=cost, die_at=die_at, raises=synth_raises)}


@pytest.fixture(autouse=True)
def clean_recorder():
    usage.reset()
    yield
    usage.reset()


def by_key(records_path):
    return {(r["condition"], r["question_id"]): r
            for r in read_records(records_path)}


# --- build_components ----------------------------------------------------

def test_build_components_builds_only_what_the_conditions_need():
    synth = FakeSynth()
    built = build_components(["lexical"], lexical="LEX", dense="DEN",
                             reranker="RR", synth=synth)
    assert built == {"lexical": "LEX", "dense": None, "reranker": None,
                     "synth": synth}

    built = build_components(["hybrid"], lexical="LEX", dense="DEN",
                             reranker="RR", synth=synth)
    assert built["lexical"] == "LEX" and built["dense"] == "DEN"
    assert built["reranker"] is None          # only hybrid_rerank needs it

    built = build_components(CONDITIONS, lexical="LEX", dense="DEN",
                             reranker="RR", synth=synth)
    assert built["reranker"] == "RR"


def test_fetches_for_says_which_deep_lists_a_condition_needs():
    assert fetches_for("lexical") == (True, False)
    assert fetches_for("dense") == (False, True)
    assert fetches_for("hybrid") == (True, True)
    assert fetches_for("hybrid_rerank") == (True, True)


# --- fetch_deep ----------------------------------------------------------

def test_a_dead_dense_stack_is_data_not_an_exception():
    comp = fake_components(den_raises=RuntimeError("embedder is down"))
    fetched = fetch_deep(comp, Q1_TEXT, DEPTH, need_lex=True, need_den=True)
    assert fetched.lex and fetched.lex_error is None
    assert fetched.den is None
    assert fetched.den_error == "RuntimeError: embedder is down"
    assert "RuntimeError" in fetched.den_traceback
    # lexical can still run; the other three cannot.
    assert fetched.error_for("lexical") is None
    for condition in ("dense", "hybrid", "hybrid_rerank"):
        assert "embedder is down" in fetched.error_for(condition)


def test_fetch_deep_skips_what_was_not_asked_for():
    comp = fake_components()
    fetched = fetch_deep(comp, Q1_TEXT, DEPTH, need_lex=True, need_den=False)
    assert comp["dense"].calls == []
    assert fetched.error_for("lexical") is None
    assert "never run" in fetched.error_for("dense")


# --- 1. fetch once, through the real runner ------------------------------

def test_the_runner_fetches_once_per_question_and_generates_per_condition(
        tmp_path):
    comp = fake_components()
    run_retrieval(write_bank(tmp_path, [Q1, Q2]), runs_dir=tmp_path / "runs",
                  components=comp, pool=BatchingPool())

    assert len(comp["lexical"].calls) == 2        # one FTS query per question
    assert len(comp["dense"].calls) == 2          # one embed+FAISS per question
    assert comp["reranker"].calls == 2            # one rerank per question
    assert len(comp["synth"].calls) == 8          # 4 conditions x 2 questions

    for query, k, kwargs in comp["lexical"].calls + comp["dense"].calls:
        assert k == FUSE_CANDIDATES               # the default depth
        assert kwargs == {}                       # no project_ids/source filter
    assert comp["reranker"].seen_depths == [RERANK_DEPTH, RERANK_DEPTH]


# --- 2. the records ------------------------------------------------------

def test_four_records_per_question_scored_off_the_deep_list(tmp_path):
    comp = fake_components()
    meta = run_retrieval(write_bank(tmp_path, [Q1, Q2]),
                         runs_dir=tmp_path / "runs", components=comp,
                         pool=BatchingPool())
    records = by_key(meta["records_path"])
    assert len(records) == 8
    assert meta["study"] == "retrieval-ladder"
    assert meta["run_id"].startswith("retrieval_")

    for (condition, _qid), r in records.items():
        assert r["mode"] == condition             # keeps the console readable
        assert r["params"] == {"depth": FUSE_CANDIDATES, "k_gen": 10,
                               "rrf_k": RRF_K, "rerank_depth": RERANK_DEPTH,
                               "metric_ks": list(METRIC_KS)}
        assert r["chunks_passed_to_gen"] == 10
        assert set(r["timings_s"]) == {"fetch", "assemble", "retrieval",
                                       "synth"}

    lex = records[("lexical", "vec-01")]
    # gold 514 sits at deep rank 15 of a depth-100 fetch: a miss at 10, a hit
    # at 20, and only ten chunks ever reached the generator.
    assert lex["retrieved_project_ids"] == list(range(500, 530))
    assert lex["ranking"]["at"]["20"]["recall"] == 1.0
    assert lex["ranking"]["at"]["10"]["recall"] == 0.0
    assert lex["ranking"]["projects_retrieved"] == 30
    assert len(lex["chunk_ids"]) == 10


def test_wall_time_includes_the_fetch_this_condition_depended_on(tmp_path):
    """The latency table prints fetch, retrieval, synth and wall side by side,
    so a wall figure that left out the shared fetch would come in UNDER
    retrieval + synth and read as a bug in the table rather than in the clock."""
    comp = fake_components()
    meta = run_retrieval(write_bank(tmp_path, [Q1]), runs_dir=tmp_path / "runs",
                         components=comp, pool=BatchingPool())
    for r in by_key(meta["records_path"]).values():
        t = r["timings_s"]
        assert r["wall_s"] >= t["retrieval"] + t["synth"] - 0.01
        assert t["retrieval"] == round(t["fetch"] + t["assemble"], 3)


def test_meta_pins_what_the_run_measured_against(tmp_path):
    comp = fake_components()
    meta = run_retrieval(write_bank(tmp_path, [Q1]),
                         runs_dir=tmp_path / "runs", components=comp,
                         pool=BatchingPool())
    assert meta["models"]["generator"] == "claude-haiku-4-5-20251001"
    assert meta["models"]["judge"] and meta["models"]["embed"]
    assert meta["models"]["reranker"]                   # hybrid_rerank is in
    assert meta["versions"]["synth_prompt"].count(":") == 1
    assert meta["bank_hash"] and meta["ended"] and "index" in meta
    assert meta["k"] == meta["params"]["k_gen"] == 10   # ConsoleProgress reads k

    unranked = run_retrieval(write_bank(tmp_path, [Q1]),
                             conditions=["lexical"], run_id="lex-only",
                             runs_dir=tmp_path / "runs",
                             components=fake_components(),
                             pool=BatchingPool())
    assert unranked["models"]["reranker"] is None


# --- 3. judging, one batch per condition ---------------------------------

def test_one_judge_batch_per_condition_and_verdicts_land_on_the_right_record(
        tmp_path):
    comp = fake_components()
    pool = BatchingPool()
    meta = run_retrieval(write_bank(tmp_path, [Q1, Q2]),
                         runs_dir=tmp_path / "runs", components=comp,
                         pool=pool)

    assert len(pool.batches) == 4                       # never one big batch
    for batch in pool.batches:
        assert sorted(batch) == ["vec-01", "vec-02"]
        assert len(set(batch)) == len(batch)            # unique qids per batch

    records = by_key(meta["records_path"])
    # Batch n was condition n, so the label proves each verdict landed on the
    # condition's own record rather than collapsing onto one question id.
    for n, condition in enumerate(CONDITIONS):
        for qid in ("vec-01", "vec-02"):
            r = records[(condition, qid)]
            assert r["status"] == "judged"
            assert r["score"]["detail"] == f"batch{n}"
            assert r["score"]["passed"] is True
    assert meta["n_unjudged"] == 0


def test_judging_is_skipped_and_reported_when_asked_for(tmp_path):
    comp = fake_components()
    pool = BatchingPool()
    meta = run_retrieval(write_bank(tmp_path, [Q1]), judge=False,
                         runs_dir=tmp_path / "runs", components=comp, pool=pool)
    assert pool.batches == []
    assert meta["n_unjudged"] == 4
    assert meta["models"]["judge"] is None


# --- 4. resume -----------------------------------------------------------

def test_resume_re_runs_only_the_missing_conditions(tmp_path):
    """A run killed part way re-runs what is missing and nothing else, and a
    question that finished all four conditions is not even fetched for."""
    bank = write_bank(tmp_path, [Q1, Q2])
    runs = tmp_path / "runs"
    # Order is questions outer, conditions inner: calls 1-4 are q1's four
    # conditions, call 5 is q2/lexical, call 6 (q2/dense) dies.
    first = fake_components(die_at=6)
    with pytest.raises(KeyboardInterrupt):
        run_retrieval(bank, run_id="r1", runs_dir=runs, components=first,
                      pool=BatchingPool())

    on_disk = by_key(runs / "r1" / "records.jsonl")
    assert len(on_disk) == 5                    # q1 x4 and q2/lexical
    assert ("lexical", "vec-02") in on_disk

    second = fake_components()
    progress = RecordingProgress()
    meta = run_retrieval(bank, run_id="r1", runs_dir=runs, resume=True,
                         components=second, pool=BatchingPool(),
                         progress=progress)

    assert len(second["synth"].calls) == 3      # dense, hybrid, hybrid_rerank
    # q1 was complete, so it cost no fetch at all; q2 was fetched once.
    assert [c[0] for c in second["lexical"].calls] == [Q2_TEXT]
    assert [c[0] for c in second["dense"].calls] == [Q2_TEXT]
    assert [e[2] for e in progress.events if e[0] == "start"] == ["vec-02"] * 3
    assert [e[2] for e in progress.events if e[0] == "skip"] \
        == ["vec-01"] * 4 + ["vec-02"]
    assert len(by_key(meta["records_path"])) == 8
    assert meta["n_unjudged"] == 0
    assert meta["resumed"] is True


def test_the_progress_counter_is_over_records_not_questions(tmp_path):
    progress = RecordingProgress()
    run_retrieval(write_bank(tmp_path, [Q1, Q2]), runs_dir=tmp_path / "runs",
                  components=fake_components(), pool=BatchingPool(),
                  progress=progress)
    starts = [e for e in progress.events if e[0] == "conditions"]
    assert starts == [("conditions", "lexical, dense, hybrid, hybrid_rerank",
                       8)]


# --- 5. --no-judge, then resume to judge ---------------------------------

def test_no_judge_then_resume_judges_without_re_generating(tmp_path):
    bank = write_bank(tmp_path, [Q1])
    runs = tmp_path / "runs"
    first = fake_components()
    meta = run_retrieval(bank, judge=False, run_id="r1", runs_dir=runs,
                         components=first, pool=BatchingPool())
    records = by_key(meta["records_path"])
    assert len(first["synth"].calls) == 4
    for r in records.values():
        assert r["status"] == "executed" and r["score"] is None
        assert needs_judge(r) and r["judge_case"]["contexts"]

    second = fake_components()
    pool = BatchingPool()
    meta = run_retrieval(bank, run_id="r1", runs_dir=runs, resume=True,
                         components=second, pool=pool)
    assert second["synth"].calls == []               # generation never re-ran
    assert second["lexical"].calls == [] and second["dense"].calls == []
    assert len(pool.batches) == 4
    records = by_key(meta["records_path"])
    assert all(r["status"] == "judged" for r in records.values())
    # The FIRST run's generation spend survives onto the judged line.
    assert all(r["spend"]["gen"]["cost_usd"] == 0.01 for r in records.values())
    assert meta["n_unjudged"] == 0


# --- 6. failure isolation ------------------------------------------------

def test_a_dead_dense_stack_still_yields_lexical_records(tmp_path):
    comp = fake_components(den_raises=RuntimeError("embedder died"))
    meta = run_retrieval(write_bank(tmp_path, [Q1]), runs_dir=tmp_path / "runs",
                         components=comp, pool=BatchingPool())

    records = by_key(meta["records_path"])
    assert len(records) == 4
    assert meta["n_errors"] == 3
    for condition in ("dense", "hybrid", "hybrid_rerank"):
        r = records[(condition, "vec-01")]
        assert r["status"] == "error"
        assert r["error"] == "RuntimeError: embedder died"
        assert "RuntimeError" in r["traceback"]
        assert r["judge_case"] is None and r["score"] is None
        assert r["params"]["depth"] == FUSE_CANDIDATES   # still reproducible

    lexical = records[("lexical", "vec-01")]
    assert lexical["status"] == "judged"
    assert lexical["score"]["passed"] is True
    assert len(comp["synth"].calls) == 1              # only the one that could


def test_a_broken_generator_costs_one_condition_not_the_run(tmp_path):
    comp = fake_components(synth_raises=RuntimeError("generator died"))
    meta = run_retrieval(write_bank(tmp_path, [Q1]), runs_dir=tmp_path / "runs",
                         components=comp, pool=BatchingPool())
    records = by_key(meta["records_path"])
    assert meta["n_errors"] == 4                      # all four, one each
    assert all(r["error"] == "RuntimeError: generator died"
               for r in records.values())


# --- 7. spend ------------------------------------------------------------

def test_spend_is_attributed_per_condition_and_nothing_leaks(tmp_path):
    comp = fake_components(cost=0.02)
    meta = run_retrieval(write_bank(tmp_path, [Q1, Q2]),
                         runs_dir=tmp_path / "runs", components=comp,
                         pool=BatchingPool(cost=0.11))
    records = by_key(meta["records_path"])
    for r in records.values():
        assert r["spend"]["gen"]["calls"] == 1        # its own call, not four
        assert r["spend"]["gen"]["cost_usd"] == 0.02
        assert r["spend"]["judge"]["cost_usd"] == 0.11
        assert r["spend"]["total_cost_usd"] == pytest.approx(0.13)
    # Every call recorded during the run was taken by the record that made it.
    assert usage.snapshot() == []


def test_an_error_record_still_carries_a_spend_block(tmp_path):
    comp = fake_components(den_raises=RuntimeError("embedder died"))
    meta = run_retrieval(write_bank(tmp_path, [Q1]), runs_dir=tmp_path / "runs",
                         components=comp, pool=BatchingPool())
    dense = by_key(meta["records_path"])[("dense", "vec-01")]
    assert dense["spend"]["gen"]["calls"] == 0
    assert dense["spend"]["total_cost_usd"] == 0.0
    assert usage.snapshot() == []


# --- 8. run mechanics ----------------------------------------------------

def test_reusing_a_run_id_without_resume_is_refused(tmp_path):
    bank = write_bank(tmp_path, [Q1])
    runs = tmp_path / "runs"
    run_retrieval(bank, run_id="r1", runs_dir=runs,
                  components=fake_components(), pool=BatchingPool())
    with pytest.raises(ValueError, match="--resume"):
        run_retrieval(bank, run_id="r1", runs_dir=runs,
                      components=fake_components(), pool=BatchingPool())


def test_an_unknown_condition_fails_loudly(tmp_path):
    with pytest.raises(ValueError, match="unknown condition"):
        run_retrieval(write_bank(tmp_path, [Q1]), ["teleport"],
                      runs_dir=tmp_path / "runs", components=fake_components(),
                      pool=BatchingPool())


def test_execute_question_retrieval_never_raises_on_a_missing_fetch(tmp_path):
    from src.eval.bank import load_bank

    q = load_bank(write_bank(tmp_path, [Q1]))[0]
    comp = fake_components()
    record = execute_question_retrieval(q, "dense", DeepFetch(), comp,
                                        k_gen=K_GEN,
                                        params={"depth": DEPTH})
    assert record["status"] == "error" and "never run" in record["error"]
    assert comp["synth"].calls == []


# ==========================================================================
# 9. the report
# ==========================================================================

SECTIONS = ("Ranking ladder", "Exact term vs paraphrase", "By level",
            "Answer quality", "Latency", "Every cell")


def section(report: str, heading: str) -> str:
    """One `## heading` section of the report, up to the next one."""
    lines = report.splitlines()
    assert f"## {heading}" in lines, f"no '## {heading}' in the report"
    start = lines.index(f"## {heading}")
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end])


def row(text: str, first_cell: str) -> list[str]:
    """The cells of the table row whose first column is `first_cell`."""
    for line in text.splitlines():
        if line.startswith(f"| {first_cell} |"):
            return [c.strip() for c in line.strip("|").split("|")]
    raise AssertionError(f"no row starting {first_cell!r} in:\n{text}")


def report_of(meta) -> str:
    return Path(meta["report_path"]).read_text(encoding="utf-8")


def ranked_record(condition, qid, *, style, level="L1", at10, at20, **extra):
    """A minimal executed record with a ranking block, for the pure renderer."""
    record = {
        "condition": condition, "question_id": qid, "term_style": style,
        "level": level, "text": f"question {qid}", "wall_s": 0.5,
        "timings_s": {"fetch": 0.10, "assemble": 0.02, "retrieval": 0.12,
                      "synth": 0.40},
        "spend": {"total_cost_usd": 0.01},
        "ranking": {"gold_size": 1, "projects_retrieved": 20,
                    "at": {"10": at10, "20": at20}},
        "score": None,
    }
    record.update(extra)
    return record


def test_the_report_renders_every_section_on_a_real_run(tmp_path):
    """Three questions, both term styles, two levels - so no table is empty."""
    meta = run_retrieval(write_bank(tmp_path, [Q1, Q2, Q3]),
                         runs_dir=tmp_path / "runs",
                         components=fake_components(), pool=BatchingPool())
    report = report_of(meta)

    assert report.startswith(f"# Retrieval ladder {meta['run_id']}")
    for heading in SECTIONS:
        assert f"\n## {heading}\n" in report

    # the header carries what the run was measured against
    assert "hash `" in report and str(meta["bank_hash"]) in report
    assert f"depth {FUSE_CANDIDATES}" in report
    assert "FUSE_CANDIDATES" in report          # the note on why depth 100
    assert "claude-haiku-4-5-20251001" in report
    assert "synth_prompt" in report
    assert "A priced figure, not a billed one." in report

    # one row per condition in every per-condition table
    for heading in ("Ranking ladder", "Exact term vs paraphrase", "By level",
                    "Answer quality", "Latency"):
        text = section(report, heading)
        for condition in CONDITIONS:
            assert row(text, condition)[0] == condition

    # 12 records = 4 conditions x 3 questions, one line each
    cells = [line for line in section(report, "Every cell").splitlines()
             if line.startswith("| `vec-")]
    assert len(cells) == 12


def test_the_ladder_prints_the_mean_of_the_records_ranking_blocks(tmp_path):
    """hit@10 for lexical, by hand: gold 514 sits at deep rank 15 of q1 (miss),
    gold 700 at rank 1 of q2 (hit), gold 900/901 at ranks 1-2 of q3 (hit).
    Two of three, so 0.667 - and recall@20 catches the rank-15 one, so 1.000."""
    meta = run_retrieval(write_bank(tmp_path, [Q1, Q2, Q3]),
                         runs_dir=tmp_path / "runs",
                         components=fake_components(), pool=BatchingPool())
    ladder = section(report_of(meta), "Ranking ladder")
    condition, n, hit10, recall10, recall20, mrr10, _ndcg10 = row(ladder,
                                                                  "lexical")
    assert (condition, n) == ("lexical", "3")
    assert hit10 == f"{2 / 3:.3f}" == "0.667"
    assert recall10 == "0.667"
    assert recall20 == "1.000"                  # rank 15 is inside 20
    assert mrr10 == f"{(0.0 + 1.0 + 1.0) / 3:.3f}"

    # and the same numbers are what is on the records
    records = by_key(meta["records_path"])
    hits = [records[("lexical", q)]["ranking"]["at"]["10"]["hit"]
            for q in ("vec-01", "vec-02", "vec-03")]
    assert hits == [0.0, 1.0, 1.0]


def test_the_crossover_table_keeps_the_two_term_styles_apart():
    """The paraphrase question is found and the exact-term one is not. That has
    to show up in the paraphrase columns only, or the table is measuring the
    run average twice rather than the crossover."""
    miss = {"hit": 0.0, "recall": 0.0, "mrr": 0.0, "ndcg": 0.0}
    found_at_2 = {"hit": 1.0, "recall": 1.0, "mrr": 0.5, "ndcg": 1.0}
    records = [
        ranked_record("lexical", "vec-01", style="paraphrase",
                      at10=found_at_2, at20=found_at_2),
        ranked_record("lexical", "vec-02", style="exact-term",
                      at10=miss, at20=miss),
    ]
    report = render_retrieval_report(records, {"run_id": "x",
                                               "conditions": ["lexical"]})
    crossover = section(report, "Exact term vs paraphrase")
    assert "1 exact-term; 1 paraphrase question(s)" in crossover
    assert row(crossover, "lexical") == ["lexical", "0.000", "0.000",
                                         "1.000", "0.500"]
    # the run-wide mean would have been 0.500 recall@20 in both columns
    assert "0.500" not in row(crossover, "lexical")[1:3]


def test_the_per_level_table_splits_by_level():
    hit = {"hit": 1.0, "recall": 1.0, "mrr": 1.0, "ndcg": 1.0}
    miss = {"hit": 0.0, "recall": 0.0, "mrr": 0.0, "ndcg": 0.0}
    records = [
        ranked_record("dense", "vec-01", style="paraphrase", level="L1",
                      at10=hit, at20=hit),
        ranked_record("dense", "vec-03", style="paraphrase", level="L3",
                      at10=miss, at20=miss),
    ]
    by_level = section(render_retrieval_report(
        records, {"run_id": "x", "conditions": ["dense"]}), "By level")
    assert row(by_level, "dense") == ["dense", "1.000", "-", "0.000"]
    assert "L1 1; L2 0; L3 1 question(s)" in by_level


def test_an_empty_run_still_renders():
    report = render_retrieval_report([], {"run_id": "empty"})
    assert isinstance(report, str)
    assert report.startswith("# Retrieval ladder empty")
    for heading in SECTIONS[:-1]:               # no records, so no Every cell
        assert f"## {heading}" in report
    assert "## Every cell" not in report
    assert "0 record(s)" in report


def test_a_run_where_everything_errored_still_renders(tmp_path):
    comp = fake_components(synth_raises=RuntimeError("generator died"))
    meta = run_retrieval(write_bank(tmp_path, [Q1]), runs_dir=tmp_path / "runs",
                         components=comp, pool=BatchingPool())
    assert meta["n_errors"] == 4
    report = report_of(meta)

    assert "## Errors" in report
    assert "RuntimeError: generator died" in report
    for condition in CONDITIONS:
        # no ranking block anywhere, so every metric cell is "-" and never 0.000
        assert row(section(report, "Ranking ladder"), condition) \
            == [condition, "0", "-", "-", "-", "-", "-"]
        assert row(section(report, "By level"), condition) \
            == [condition, "-", "-", "-"]
    assert "ERROR" in section(report, "Every cell")


def test_a_failed_verdict_prints_the_answer_beside_the_reference(tmp_path):
    meta = run_retrieval(write_bank(tmp_path, [Q1]), runs_dir=tmp_path / "runs",
                         components=fake_components(),
                         pool=BatchingPool(passed=False))
    report = report_of(meta)
    assert "## Failures - answer beside reference" in report
    failures = section(report, "Failures - answer beside reference")
    assert Q1["reference_answer"] in failures
    assert f"answer to {Q1_TEXT}" in failures
    assert "thresholds" in failures
    assert "FAIL" in section(report, "Every cell")
    assert row(section(report, "Answer quality"), "lexical") \
        == ["lexical", "0/1", "-", "0/1"]       # Q1 is paraphrase


def test_an_unjudged_run_says_so_instead_of_printing_pass_rates(tmp_path):
    meta = run_retrieval(write_bank(tmp_path, [Q1, Q2]), judge=False,
                         runs_dir=tmp_path / "runs",
                         components=fake_components(), pool=BatchingPool())
    quality = section(report_of(meta), "Answer quality")
    assert "Nothing was judged in this run" in quality
    assert "--resume" in quality and "--no-judge" in quality
    assert "|" not in quality                   # the table is not printed


def test_the_report_survives_records_missing_optional_blocks():
    """A meta with nothing but a run id, and records with no ranking, no
    timings and no score. Nothing here should be a KeyError."""
    records = [{"condition": "lexical", "question_id": "vec-01"},
               {"condition": "dense", "question_id": "vec-01",
                "error": "boom", "traceback": "Traceback..."}]
    report = render_retrieval_report(records, {"run_id": "thin"})
    # conditions were not in meta, so they came off the records themselves
    assert row(section(report, "Ranking ladder"), "lexical")[1] == "0"
    assert row(section(report, "Latency"), "dense")[2] == "-"
    assert "not recorded" in report             # no index block
    assert "## Errors" in report
