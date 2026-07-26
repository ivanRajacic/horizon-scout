"""Bank-runner tests: scoring dispatched by declared route, misroute detection,
retrieval metrics, failure isolation, checkpointing and resume, spend
attribution, progress reporting, and the generated report. Ask and JudgePool
are faked - no servers, no `claude` CLI."""

import json

import pytest

from src.eval import usage
from src.eval.run import (CONDITIONS, ConsoleProgress, RunProgress,
                          execute_question, needs_judge, read_records,
                          render_report, run_bank, select_questions)
from src.eval.bank import load_bank
from src.retrieval.base import SearchResult

# --- a small bank, valid against the v2 schema ---

SQL_Q = {
    "question_id": "sql-01", "text": "How many projects were terminated?",
    "expected_route": "sql", "level": "L1", "subtype": "aggregate",
    "gold_sql": "SELECT COUNT(*) FROM project WHERE status = 'TERMINATED'",
    "answer_columns": ["count"],
    "level_evidence": {"join_count": 0, "non_trivial_where_count": 1,
                       "has_group_by": False, "has_order_by_limit": False,
                       "value_note_dependencies": [], "trap_documented": False},
    "schema_docs_hash": "c3435815b331",
    "reference_answer": "2127 projects have status TERMINATED.",
}

SQL_RANK_Q = {
    **SQL_Q, "question_id": "sql-02", "level": "L2", "subtype": "rank",
    "text": "The two largest grants, largest first?",
    "gold_sql": "SELECT acronym FROM project ORDER BY ecMaxContribution DESC "
                "LIMIT 2",
    "sql_comparison": "ordered", "answer_columns": ["acronym"],
    "level_evidence": {"join_count": 1, "non_trivial_where_count": 0,
                       "has_group_by": False, "has_order_by_limit": True,
                       "value_note_dependencies": [], "trap_documented": False},
}

VEC_Q = {
    "question_id": "vec-01", "text": "Find the project about widget farming.",
    "expected_route": "vector", "level": "L1", "subtype": "identify",
    "term_style": "paraphrase", "gold_project_ids": [101],
    "pooling_evidence": {
        "conditions_run": ["lexical", "dense", "hybrid", "hybrid_rerank"],
        "k": 20, "pooled_candidate_count": 7, "accepted": [101],
        "rejected_count": 6, "index_fingerprint": "be84cbad9182"},
    "reference_answer": "WIDGETFARM (101) farms widgets.",
}

HYB_Q = {
    "question_id": "hyb-01", "text": "Post-2021 glaciology projects in Italy?",
    "expected_route": "hybrid", "level": "L2", "subtype": "filter-synthesize",
    "term_style": "exact-term", "gold_project_ids": [201, 202],
    "pooling_evidence": {
        "conditions_run": ["lexical", "dense", "hybrid", "hybrid_rerank"],
        "k": 20, "pooled_candidate_count": 9, "accepted": [201, 202],
        "rejected_count": 7, "index_fingerprint": "be84cbad9182"},
    "filter_evidence": {"filter_sql": "SELECT project_id FROM project",
                        "survivor_count": 4,
                        "survivor_ids": [201, 202, 203, 204],
                        "schema_docs_hash": "c3435815b331"},
    "reference_answer": "Two glaciology projects.",
}


def write_bank(tmp_path, records):
    p = tmp_path / "bank.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return p


def chunk(pid, text="content", cid=None):
    return SearchResult(chunk_id=cid or f"{pid}-c", project_id=pid,
                        acronym=f"P{pid}", title="t", source="report",
                        section="summary", score=0.5, text=text)


class FakeAskResult:
    def __init__(self, mode, answer="an answer", sql=None, rows=(),
                 columns=(), chunks=(), degraded=None, trace=None):
        self.mode = mode
        self.answer = answer
        self.router_reason = "because"
        self.router_fallback = False
        self.sql = sql
        self.rows = list(rows)
        self.columns = list(columns)
        self.chunks = list(chunks)
        self.degraded = degraded
        self.weak_filter = False
        self.citation_violations = []
        self.trace = trace or {"timings": {"total": 1.5},
                               "rows_passed_to_gen": 0,
                               "chunks_passed_to_gen": len(chunks)}


class FakeAsk:
    """Answers by question_id, and spends a labelled `claude -p` call so spend
    attribution is exercised the way the real Ask exercises it."""

    def __init__(self, by_id, cost=0.01, raises=None):
        self.by_id = by_id
        self.cost = cost
        self.raises = raises or {}
        self.calls = []
        self.llm = type("L", (), {"model": "claude-haiku-4-5-20251001"})()
        self.versions = {"router_prompt": "r1:abc", "synth_prompt": "s1:def"}
        self.sql_path = FakeSqlPath()

    def ask(self, text, k=10, mode=None):
        self.calls.append((text, k, mode))
        for qid, q in self.by_id.items():
            if q["text"] == text:
                if qid in self.raises:
                    raise self.raises[qid]
                usage.record_envelope(
                    {"total_cost_usd": self.cost, "duration_ms": 100,
                     "usage": {"output_tokens": 5}}, "haiku")
                return q["result"]
        raise AssertionError(f"FakeAsk got an unexpected question: {text!r}")


class FakeSqlPath:
    """execute_trusted returns gold rows keyed by the SQL text."""
    GOLD = {SQL_Q["gold_sql"]: (["count"], [(2127,)]),
            SQL_RANK_Q["gold_sql"]: (["acronym"], [("BIG",), ("SMALL",)])}

    def __init__(self, fail=False):
        self.fail = fail

    def execute_trusted(self, sql):
        if self.fail:
            raise RuntimeError("gold blew up")
        return self.GOLD[sql]


class FakeVerdict:
    def __init__(self, passed, factual=0.9, faith=0.95, path="ragas"):
        self.passed = passed
        self.path = path
        self.model = "claude-sonnet-x"
        self.factual_correctness = factual
        self.faithfulness = faith
        self.detail = ""


class FakePool:
    """Judges every case and calls back per case, the way JudgePool does.

    `die_after` stops the batch part way through, standing in for a killed run:
    the callbacks that already fired must have left their records on disk.
    """

    def __init__(self, results=None, cost=0.05, die_after=None):
        self.results = results or {}
        self.cost = cost
        self.die_after = die_after
        self.seen = []

    def judge_all(self, cases, on_verdict=None):
        self.seen = list(cases)
        out = []
        for n, case in enumerate(cases, 1):
            if self.die_after is not None and n > self.die_after:
                raise KeyboardInterrupt("killed mid-batch")
            qid = case["question_id"]
            with usage.stage(qid, "judge"):
                usage.record_envelope(
                    {"total_cost_usd": self.cost,
                     "usage": {"output_tokens": 7}}, "sonnet")
            verdict = self.results.get(qid, FakeVerdict(True))
            if on_verdict:
                on_verdict(case, verdict)
            out.append(verdict)
        return out


class RecordingProgress(RunProgress):
    """Every event the runner emits, in order, as (name, detail) pairs."""

    def __init__(self):
        self.events = []

    def question_start(self, condition, i, n, q):
        self.events.append(("start", q.question_id))

    def question_done(self, record, i, n):
        self.events.append(("done", record["question_id"]))

    def question_skipped(self, record, i, n, why):
        self.events.append(("skip", record["question_id"], why))

    def judging_start(self, condition, n):
        self.events.append(("judging", n))

    def verdict(self, record, done, n):
        self.events.append(("verdict", record["question_id"], done, n))

    def run_done(self, meta):
        self.events.append(("run_done", meta["n_records"]))


@pytest.fixture(autouse=True)
def clean_recorder():
    usage.reset()
    yield
    usage.reset()


def bank_of(tmp_path, *records):
    return write_bank(tmp_path, list(records))


# --- selection ---

def test_select_by_id_preserves_the_given_order(tmp_path):
    qs = load_bank(bank_of(tmp_path, SQL_Q, VEC_Q, HYB_Q))
    picked = select_questions(qs, ["hyb-01", "sql-01"], None, None)
    assert [q.question_id for q in picked] == ["hyb-01", "sql-01"]


def test_select_by_unknown_id_fails_loudly(tmp_path):
    qs = load_bank(bank_of(tmp_path, SQL_Q))
    with pytest.raises(ValueError, match="no such question id"):
        select_questions(qs, ["nope-99"], None, None)


def test_select_by_route_and_limit(tmp_path):
    qs = load_bank(bank_of(tmp_path, SQL_Q, VEC_Q, HYB_Q))
    assert [q.question_id for q
            in select_questions(qs, None, ["vector", "hybrid"], None)] \
        == ["vec-01", "hyb-01"]
    assert len(select_questions(qs, None, None, 2)) == 2


# --- scoring dispatch: by DECLARED route, not by what the condition produced -

def _one(tmp_path, record, result, condition="router", k=10, ask=None):
    q = load_bank(bank_of(tmp_path, record))[0]
    ask = ask or FakeAsk({record["question_id"]: {"text": record["text"],
                                                  "result": result}})
    return execute_question(ask, q, condition, k, ask.sql_path)


def test_sql_question_scored_by_execution_not_judged(tmp_path):
    r = _one(tmp_path, SQL_Q,
             FakeAskResult("sql", sql="SELECT COUNT(*) ...", rows=[(2127,)],
                           columns=["n"]))
    assert r["score"]["method"] == "execution" and r["score"]["passed"]
    assert r["score"]["columns_ok"] is True
    assert r["judge_case"] is None           # never reaches the judge
    assert needs_judge(r) is False


def test_sql_wrong_rows_fail(tmp_path):
    r = _one(tmp_path, SQL_Q,
             FakeAskResult("sql", sql="SELECT 1", rows=[(9,)], columns=["n"]))
    assert r["score"]["passed"] is False
    assert r["score"]["reason"] == "rows_differ"


def test_ordered_comparison_is_honoured(tmp_path):
    """A rank question answered in the wrong order is wrong, and the set
    comparison would have called it right."""
    backwards = FakeAskResult("sql", sql="SELECT ...",
                              rows=[("SMALL",), ("BIG",)], columns=["acronym"])
    r = _one(tmp_path, SQL_RANK_Q, backwards)
    assert r["score"]["comparison"] == "ordered"
    assert r["score"]["passed"] is False

    forwards = FakeAskResult("sql", sql="SELECT ...",
                             rows=[("BIG",), ("SMALL",)], columns=["acronym"])
    assert _one(tmp_path, SQL_RANK_Q, forwards)["score"]["passed"] is True


def test_extra_column_is_flagged_beside_the_verdict_not_folded_into_it(tmp_path):
    r = _one(tmp_path, SQL_Q,
             FakeAskResult("sql", sql="SELECT COUNT(*), 1", rows=[(2127,)],
                           columns=["n", "spare"]))
    assert r["score"]["passed"] is True          # the answer is still right
    assert r["score"]["columns_ok"] is False     # but the shape is not pinned


def test_sql_question_under_a_forced_topical_condition_scores_no_sql(tmp_path):
    r = _one(tmp_path, SQL_Q, FakeAskResult("vector", chunks=[chunk(1)]),
             condition="force-vector")
    assert r["score"]["passed"] is False and r["score"]["reason"] == "no-sql"


def test_sql_path_failure_is_distinguished_from_a_wrong_answer(tmp_path):
    r = _one(tmp_path, SQL_Q,
             FakeAskResult("sql", sql="SELECT bad", degraded="sql_failed",
                           trace={"timings": {}, "error": "Binder Error"}))
    assert r["score"]["reason"] == "sql_failed"
    assert r["score"]["detail"] == "Binder Error"


def test_unexecutable_gold_sql_is_unscored_not_failed(tmp_path):
    q = load_bank(bank_of(tmp_path, SQL_Q))[0]
    ask = FakeAsk({"sql-01": {"text": SQL_Q["text"],
                              "result": FakeAskResult("sql", sql="x",
                                                      rows=[(1,)])}})
    r = execute_question(ask, q, "router", 10, FakeSqlPath(fail=True))
    assert r["score"]["passed"] is None
    assert "gold_sql failed to execute" in r["score"]["reason"]


def test_topical_question_builds_a_judge_case_with_real_contexts(tmp_path):
    r = _one(tmp_path, VEC_Q,
             FakeAskResult("vector", answer="WIDGETFARM does it",
                           chunks=[chunk(101, "widget farming text"),
                                   chunk(102, "other")]))
    case = r["judge_case"]
    assert case["contexts"] == ["widget farming text", "other"]
    assert case["reference_answer"] == VEC_Q["reference_answer"]
    assert case["adversarial"] is False
    assert r["score"] is None                # phase B fills it
    assert needs_judge(r) is True
    # the reference is on the record too, not only inside the judge case
    assert r["reference_answer"] == VEC_Q["reference_answer"]


# --- routing ---

def test_misroute_detected_against_route_to_mode(tmp_path):
    ok = _one(tmp_path, HYB_Q, FakeAskResult("scoped", chunks=[chunk(201)]))
    assert ok["expected_mode"] == "scoped" and ok["misroute"] is False

    bad = _one(tmp_path, HYB_Q, FakeAskResult("vector", chunks=[chunk(201)]))
    assert bad["misroute"] is True


# --- retrieval metrics ---

def test_retrieval_metrics_scored_against_gold(tmp_path):
    r = _one(tmp_path, HYB_Q,
             FakeAskResult("scoped", chunks=[chunk(999), chunk(201),
                                             chunk(201, cid="201-c2"),
                                             chunk(202)]))
    m = r["retrieval"]
    assert m["projects_retrieved"] == 3       # chunks deduped to projects
    assert m["gold_size"] == 2
    assert m["recall"] == 1.0 and m["hit"] == 1.0
    assert m["mrr"] == pytest.approx(0.5)     # first gold is second in rank
    assert r["retrieved_project_ids"] == [999, 201, 202]


def test_no_gold_means_no_retrieval_block(tmp_path):
    r = _one(tmp_path, SQL_Q, FakeAskResult("sql", sql="x", rows=[(2127,)]))
    assert r["retrieval"] is None


# --- failure isolation and spend ---

def test_a_raising_question_is_recorded_and_does_not_sink_the_run(tmp_path):
    bank = bank_of(tmp_path, SQL_Q, VEC_Q, HYB_Q)
    ask = FakeAsk(
        {"sql-01": {"text": SQL_Q["text"],
                    "result": FakeAskResult("sql", sql="x", rows=[(2127,)])},
         "vec-01": {"text": VEC_Q["text"], "result": None},
         "hyb-01": {"text": HYB_Q["text"],
                    "result": FakeAskResult("scoped", chunks=[chunk(201)])}},
        raises={"vec-01": RuntimeError("embedder died")})

    meta = run_bank(bank, ["router"], runs_dir=tmp_path / "runs",
                    ask=ask, pool=FakePool())
    records = {r["question_id"]: r for r in read_records(meta["records_path"])}
    assert len(records) == 3
    assert records["vec-01"]["error"] == "RuntimeError: embedder died"
    assert "traceback" in records["vec-01"]
    assert records["sql-01"]["error"] is None
    assert records["hyb-01"]["score"]["passed"] is True


def test_spend_is_attributed_per_question_and_per_stage(tmp_path):
    bank = bank_of(tmp_path, VEC_Q, HYB_Q)
    ask = FakeAsk({"vec-01": {"text": VEC_Q["text"],
                              "result": FakeAskResult("vector",
                                                      chunks=[chunk(101)])},
                   "hyb-01": {"text": HYB_Q["text"],
                              "result": FakeAskResult("scoped",
                                                      chunks=[chunk(201)])}},
                  cost=0.02)
    meta = run_bank(bank, ["router"], runs_dir=tmp_path / "runs",
                    ask=ask, pool=FakePool(cost=0.11))
    for r in read_records(meta["records_path"]):
        assert r["spend"]["gen"]["cost_usd"] == 0.02
        assert r["spend"]["judge"]["cost_usd"] == 0.11
        assert r["spend"]["total_cost_usd"] == pytest.approx(0.13)


def test_no_judge_skips_phase_b_but_keeps_the_case_for_later(tmp_path):
    """--no-judge is phase A only, and it leaves the run judgeable: the case
    the judge would have seen is on disk, so a later --resume costs judging
    and not generation."""
    bank = bank_of(tmp_path, VEC_Q)
    results = {"vec-01": {"text": VEC_Q["text"],
                          "result": FakeAskResult("vector",
                                                  chunks=[chunk(101, "ctx")])}}
    runs = tmp_path / "runs"
    pool = FakePool()
    meta = run_bank(bank, ["router"], judge=False, run_id="r1", runs_dir=runs,
                    ask=FakeAsk(results), pool=pool)
    (r,) = read_records(meta["records_path"])
    assert pool.seen == [] and r["score"] is None
    assert r["status"] == "executed" and needs_judge(r)
    assert r["judge_case"]["contexts"] == ["ctx"]
    assert meta["n_unjudged"] == 1

    second = FakeAsk(results)
    meta = run_bank(bank, ["router"], run_id="r1", runs_dir=runs, resume=True,
                    ask=second, pool=FakePool())
    assert second.calls == []                    # generation never re-ran
    (r,) = read_records(meta["records_path"])
    assert r["status"] == "judged" and r["score"]["passed"] is True
    assert r["spend"]["gen"]["cost_usd"] == 0.01     # the FIRST run's spend
    assert meta["n_unjudged"] == 0


def test_a_judge_exception_marks_one_record_not_the_batch(tmp_path):
    bank = bank_of(tmp_path, VEC_Q, HYB_Q)
    ask = FakeAsk({"vec-01": {"text": VEC_Q["text"],
                              "result": FakeAskResult("vector",
                                                      chunks=[chunk(101)])},
                   "hyb-01": {"text": HYB_Q["text"],
                              "result": FakeAskResult("scoped",
                                                      chunks=[chunk(201)])}})
    pool = FakePool({"vec-01": RuntimeError("ragas exploded")})
    meta = run_bank(bank, ["router"], runs_dir=tmp_path / "runs",
                    ask=ask, pool=pool)
    records = {r["question_id"]: r for r in read_records(meta["records_path"])}
    assert records["vec-01"]["score"]["passed"] is None
    assert "ragas exploded" in records["vec-01"]["score"]["reason"]
    assert records["hyb-01"]["score"]["passed"] is True


# --- run mechanics ---

def test_unknown_condition_fails_loudly(tmp_path):
    with pytest.raises(ValueError, match="unknown condition"):
        run_bank(bank_of(tmp_path, SQL_Q), ["teleport"],
                 runs_dir=tmp_path / "runs", ask=FakeAsk({}), pool=FakePool())


def test_conditions_map_to_ask_modes(tmp_path):
    assert CONDITIONS == {"router": None, "force-sql": "sql",
                          "force-vector": "vector",
                          "always-hybrid": "scoped"}
    bank = bank_of(tmp_path, VEC_Q)
    ask = FakeAsk({"vec-01": {"text": VEC_Q["text"],
                              "result": FakeAskResult("scoped",
                                                      chunks=[chunk(101)])}})
    run_bank(bank, ["always-hybrid"], runs_dir=tmp_path / "runs",
             ask=ask, pool=FakePool())
    assert ask.calls[0][2] == "scoped"


def test_a_kill_during_judging_keeps_everything_already_paid_for(tmp_path):
    """The failure this checkpointing exists for: a run dies part way through
    phase B. What executed stays executed, and the verdicts that landed stay
    landed - nothing is held in memory waiting for the batch to finish."""
    bank = bank_of(tmp_path, SQL_Q, VEC_Q, HYB_Q)
    results = {
        "sql-01": {"text": SQL_Q["text"],
                   "result": FakeAskResult("sql", sql="x", rows=[(2127,)])},
        "vec-01": {"text": VEC_Q["text"],
                   "result": FakeAskResult("vector", chunks=[chunk(101)])},
        "hyb-01": {"text": HYB_Q["text"],
                   "result": FakeAskResult("scoped", chunks=[chunk(201)])}}
    runs = tmp_path / "runs"
    records_path = runs / "r1" / "records.jsonl"

    with pytest.raises(KeyboardInterrupt):
        run_bank(bank, ["router"], run_id="r1", runs_dir=runs,
                 ask=FakeAsk(results), pool=FakePool(die_after=1))

    on_disk = {r["question_id"]: r for r in read_records(records_path)}
    assert len(on_disk) == 3                     # all three executed
    assert on_disk["sql-01"]["status"] == "executed"      # scored, no judging
    assert on_disk["vec-01"]["status"] == "judged"        # verdict landed
    assert on_disk["hyb-01"]["status"] == "executed"      # verdict did not
    assert needs_judge(on_disk["hyb-01"])

    # Resuming pays for the one missing verdict and nothing else.
    second = FakeAsk(results)
    meta = run_bank(bank, ["router"], run_id="r1", runs_dir=runs, resume=True,
                    ask=second, pool=FakePool())
    assert second.calls == []
    finished = {r["question_id"]: r for r in read_records(meta["records_path"])}
    assert finished["hyb-01"]["status"] == "judged"
    assert meta["n_unjudged"] == 0
    # vec-01 was judged once, not twice: its judge spend is one call's worth.
    assert finished["vec-01"]["spend"]["judge"]["calls"] == 1


def test_records_are_a_journal_and_the_latest_line_wins(tmp_path):
    bank = bank_of(tmp_path, VEC_Q)
    ask = FakeAsk({"vec-01": {"text": VEC_Q["text"],
                              "result": FakeAskResult("vector",
                                                      chunks=[chunk(101)])}})
    meta = run_bank(bank, ["router"], runs_dir=tmp_path / "runs",
                    ask=ask, pool=FakePool())
    path = meta["records_path"]
    raw = read_records(path, raw=True)
    assert [r["status"] for r in raw] == ["executed", "judged"]
    (collapsed,) = read_records(path)
    assert collapsed["status"] == "judged"
    # Every line is complete, not a patch over the one before it.
    assert all(r["run_id"] and r["models"] and r["text"] for r in raw)


def test_reusing_a_run_id_without_resume_is_refused(tmp_path):
    bank = bank_of(tmp_path, VEC_Q)
    results = {"vec-01": {"text": VEC_Q["text"],
                          "result": FakeAskResult("vector",
                                                  chunks=[chunk(101)])}}
    runs = tmp_path / "runs"
    run_bank(bank, ["router"], run_id="r1", runs_dir=runs,
             ask=FakeAsk(results), pool=FakePool())
    with pytest.raises(ValueError, match="already exists"):
        run_bank(bank, ["router"], run_id="r1", runs_dir=runs,
                 ask=FakeAsk(results), pool=FakePool())


def test_progress_is_reported_as_each_question_and_verdict_lands(tmp_path):
    bank = bank_of(tmp_path, SQL_Q, VEC_Q)
    results = {"sql-01": {"text": SQL_Q["text"],
                          "result": FakeAskResult("sql", sql="x",
                                                  rows=[(2127,)])},
               "vec-01": {"text": VEC_Q["text"],
                          "result": FakeAskResult("vector",
                                                  chunks=[chunk(101)])}}
    runs = tmp_path / "runs"
    seen = RecordingProgress()
    run_bank(bank, ["router"], run_id="r1", runs_dir=runs,
             ask=FakeAsk(results), pool=FakePool(), progress=seen)
    assert seen.events == [
        ("start", "sql-01"), ("done", "sql-01"),
        ("start", "vec-01"), ("done", "vec-01"),
        ("judging", 1), ("verdict", "vec-01", 1, 1),
        ("run_done", 2)]

    # On a resume the skipped questions say why they were skipped.
    resumed = RecordingProgress()
    run_bank(bank, ["router"], run_id="r1", runs_dir=runs, resume=True,
             ask=FakeAsk(results), pool=FakePool(), progress=resumed)
    assert [e[:2] for e in resumed.events if e[0] == "skip"] == [
        ("skip", "sql-01"), ("skip", "vec-01")]


def test_console_progress_writes_a_tailable_log(tmp_path):
    bank = bank_of(tmp_path, VEC_Q)
    ask = FakeAsk({"vec-01": {"text": VEC_Q["text"],
                              "result": FakeAskResult("vector",
                                                      chunks=[chunk(101)])}})
    runs = tmp_path / "runs"
    printed = []
    run_bank(bank, ["router"], run_id="r1", runs_dir=runs, ask=ask,
             pool=FakePool(),
             progress=ConsoleProgress(echo=lambda *a, **kw: printed.append(a)))
    log = (runs / "r1" / "progress.log").read_text(encoding="utf-8")
    assert "vec-01" in log and "PASS" in log
    assert "run r1" in log
    assert printed                                # and it reached the console


def test_resume_skips_recorded_pairs(tmp_path):
    bank = bank_of(tmp_path, SQL_Q, VEC_Q)
    results = {"sql-01": {"text": SQL_Q["text"],
                          "result": FakeAskResult("sql", sql="x",
                                                  rows=[(2127,)])},
               "vec-01": {"text": VEC_Q["text"],
                          "result": FakeAskResult("vector",
                                                  chunks=[chunk(101)])}}
    runs = tmp_path / "runs"
    first = FakeAsk(results)
    run_bank(bank, ["router"], ids=["sql-01"], run_id="r1", runs_dir=runs,
             ask=first, pool=FakePool())

    second = FakeAsk(results)
    meta = run_bank(bank, ["router"], run_id="r1", runs_dir=runs, resume=True,
                    ask=second, pool=FakePool())
    assert [c[0] for c in second.calls] == [VEC_Q["text"]]   # sql-01 skipped
    assert sorted(r["question_id"] for r
                  in read_records(meta["records_path"])) == ["sql-01", "vec-01"]


def test_record_carries_full_provenance(tmp_path):
    bank = bank_of(tmp_path, VEC_Q)
    ask = FakeAsk({"vec-01": {"text": VEC_Q["text"],
                              "result": FakeAskResult("vector",
                                                      chunks=[chunk(101)])}})
    meta = run_bank(bank, ["router"], runs_dir=tmp_path / "runs",
                    ask=ask, pool=FakePool())
    (r,) = read_records(meta["records_path"])
    assert r["models"]["generator"] == "claude-haiku-4-5-20251001"
    assert r["models"]["judge"] and r["models"]["embed"]
    assert r["versions"]["router_prompt"] == "r1:abc"
    assert r["run_id"] == meta["run_id"]
    assert meta["bank_hash"] and meta["ended"]
    assert r["score"]["thresholds"]["factual"]


# --- report ---

def test_report_renders_the_things_worth_reading(tmp_path):
    bank = bank_of(tmp_path, SQL_Q, VEC_Q, HYB_Q)
    ask = FakeAsk(
        {"sql-01": {"text": SQL_Q["text"],
                    "result": FakeAskResult("sql", sql="SELECT wrong",
                                            rows=[(1,)], columns=["n"])},
         "vec-01": {"text": VEC_Q["text"],
                    "result": FakeAskResult("vector", answer="a wrong answer",
                                            chunks=[chunk(101)])},
         "hyb-01": {"text": HYB_Q["text"],
                    "result": FakeAskResult("vector", chunks=[chunk(201)])}})
    pool = FakePool({"vec-01": FakeVerdict(False, factual=0.2, faith=0.3)})
    meta = run_bank(bank, ["router"], runs_dir=tmp_path / "runs",
                    ask=ask, pool=pool)

    report = (tmp_path / "runs" / meta["run_id"] / "report.md").read_text(
        encoding="utf-8")
    assert "# Bank run" in report
    assert "priced" in report.lower()                  # the cost caveat
    assert "1/3 passed" in report
    assert "1/3 misrouted" in report and "hyb-01" in report   # routed to vector
    assert "## Retrieval (topical questions)" in report
    assert "SELECT wrong" in report                    # the failing SQL
    assert "a wrong answer" in report                  # the failing answer
    # a failing answer is unreadable without what it was supposed to say
    assert f"**Reference** {VEC_Q['reference_answer']}" in report
    assert "claude-haiku-4-5-20251001" in report
    assert "## Every question" in report               # one line per question
    for qid in ("sql-01", "vec-01", "hyb-01"):
        assert f"`{qid}`" in report.split("## Every question")[1]


def test_report_survives_an_empty_run():
    assert "Bank run x" in render_report([], {"run_id": "x", "conditions": []})
