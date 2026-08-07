"""Run the question bank end to end and record what happened.

This is the runner for Study 2 (RQ1), invoked with one condition at a time. A
condition is just the `mode` argument to Ask.ask: the router picking for itself,
or one capability forced on every question.

    router        mode=None      the router chooses
    force-sql     mode="sql"     every question down the SQL path
    force-vector  mode="vector"  every question down unfiltered retrieval
    always-hybrid mode="scoped"  every question through the filtered path

The retrieval stack under force-vector and always-hybrid is the same one:
config.RUNTIME_RETRIEVER (hybrid_rerank since 2026-08-03), recorded in the run
meta via ask.versions. What separates those two conditions is the SQL id filter,
not the retriever - "hybrid" in the condition name means the scoped path, not
lexical+dense fusion. Both senses of the word are now true at once, which is
worth knowing when reading a report header.

Two phases, and the split is about safety, not speed.

PHASE A executes, sequentially. VectorSearcher and LexicalRetriever each hold a
shared read-only DuckDB connection that is not safe to hit from several threads,
and the embedder and reranker are a single GPU that serialises anyway.
--concurrency exists but defaults to 1; raising it needs per-thread cursors
first. Note that since the runtime went hybrid_rerank there are two such
connections per Ask and a rerank call per question, so Phase A is slower than
the dense-only runs recorded before 2026-08-03.

PHASE B judges, concurrently, in one JudgePool batch. That is what JudgePool is
built for and it is already proven at concurrency 8. Judging is the expensive
half and it is the half that parallelises.

Scoring is decided by the question's DECLARED route, never by what the condition
happened to produce:

  - sql-route questions are scored by execution accuracy against gold_sql and
    never see the judge (horizon-scout.md RQ5: "pure-SQL cells skip the judge
    entirely"). Their answers are templated strings - "Result: 9789883.64" - so
    RAGAS against a prose reference would measure nothing. A condition that
    produced no SQL scores a fail with reason `no-sql`, which is the honest
    reading of force-vector meeting a SQL question.
  - vector/hybrid-route questions go to the judge, with contexts = the chunks
    synthesis actually used.
  - ADV questions carry adversarial=True and JudgePool sends them to the rubric
    refusal overlay instead of RAGAS - whatever their expected_route. A
    SQL-route ADV question has no gold_sql (its gold is an absence), so
    execution accuracy has nothing to execute and the refusal rubric is the
    scorer.

Retrieval quality is computed alongside, free, wherever gold_project_ids exist:
the run's own chunks deduplicated to projects and scored with src/eval/metrics.
It costs nothing and it is the first real signal on the retrieval side.

Outputs go to data/runs/<run_id>/: records.jsonl, plus a GENERATED report.md.
Same discipline as the drafting and exploration journals - the canonical output
is rendered from the record, never written by hand.

records.jsonl is an append-only journal, latest line per (condition,
question_id) wins, exactly as src/eval/batch.py's draft journal works. Phase A
appends a line the moment a question returns (`status: executed`); phase B
appends the same record again carrying the verdict (`status: judged`);
read_records collapses. Every line is COMPLETE - a new line REPLACES a
question's state rather than patching it - which costs some duplication and buys
a file that is never half-written and can be read by anything that can read one
line at a time.

That is here because a run is expensive and was previously lost: 21 questions
executed and ~$1.30 of priced generation vanished on a kill, because records
were held in memory until a whole condition finished.

The judge case - question, reference, answer, and the actual chunk TEXTS
synthesis used - is persisted on the `executed` line, not just the chunk ids.
So --resume judges what was already executed instead of re-running generation
for it, and `--no-judge` becomes a real two-stage workflow: execute cheap, read
the answers, then --resume later to judge them. It also means the verdict can
always be re-read against the contexts the judge actually saw.
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from src.config import (EMBED_MODEL, JUDGE_DEFAULT, JUDGE_MODELS,
                        JUDGE_PASS_FACTUAL, JUDGE_PASS_FAITHFULNESS, ROOT)
from src.eval import usage
from src.eval.bank import (ROUTE_TO_MODE, BankQuestion, BankValidationError,
                           load_bank, load_bank_with_errors)
from src.eval.metrics import METRICS, dedup_projects, score_ranking
from src.llm import fingerprint
from src.retrieval.sql_path import (columns_match, project_to_answer_columns,
                                    rows_match)

RUNS_DIR = ROOT / "data" / "runs"

# Condition -> the mode forced on Ask.ask (None = let the router decide).
CONDITIONS: dict[str, str | None] = {
    "router": None,
    "force-sql": "sql",
    "force-vector": "vector",
    "always-hybrid": "scoped",
}

# A record's place in the two phases. There is no "pending" - a question with no
# line in the journal has not been attempted, and that absence is the state.
STATUS_ERROR = "error"          # execution raised; terminal
STATUS_EXECUTED = "executed"    # phase A done. SQL routes are already scored
#                                 here; topical ones are waiting for the judge
STATUS_JUDGED = "judged"        # phase B done; terminal


def new_run_id() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def needs_judge(record: dict) -> bool:
    """Is there judging still owed on this record?

    True for a topical question that executed but has no verdict yet - whether
    it was killed mid-judging, deliberately left unjudged by --no-judge, or its
    judge attempt errored (a judge error keeps status `executed` so it is
    retried, never buried). This is the one predicate --resume decides on, so
    it lives in one place.
    """
    return (record.get("status") == STATUS_EXECUTED
            and bool(record.get("judge_case")))


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def score_sql_question(q: BankQuestion, res, sql_path) -> dict:
    """Execution accuracy: the generated query's rows against the gold query's.

    The gold SQL is re-executed here rather than trusted from the bank - the
    corpus is static but the point of an execution metric is that it executed.
    """
    if not q.gold_sql:
        return {"method": "execution", "passed": None,
                "reason": "no gold_sql on the bank entry"}
    try:
        want_columns, want_rows = sql_path.execute_trusted(q.gold_sql)
    except Exception as e:                                   # noqa: BLE001
        return {"method": "execution", "passed": None,
                "reason": f"gold_sql failed to execute: {type(e).__name__}: {e}"}

    common = {"method": "execution", "comparison": q.sql_comparison,
              "gold_rows": len(want_rows), "gold_columns": want_columns}

    if res.mode != "sql":
        # force-vector / always-hybrid meeting a SQL question. Not an error -
        # it is the measurement.
        return {**common, "passed": False, "reason": "no-sql",
                "detail": f"condition ran in mode={res.mode}, no query to score"}
    if res.degraded == "sql_failed" or res.sql is None:
        return {**common, "passed": False, "reason": "sql_failed",
                "detail": res.trace.get("error", "")}

    # Compare what the bank pinned as the answer, not the whole result. A right
    # answer carrying id and title alongside is right (pilot sql-02); a gold_sql
    # returning more than it pins is normal too (sql-15), so both sides project.
    want_projected, _ = project_to_answer_columns(
        want_columns, want_rows, q.answer_columns)
    if want_projected is None:
        # The bank's own gold does not contain what the bank pinned. That is a
        # defect in the entry, not a wrong answer - say so instead of failing
        # the system for it. precheck_record gates this at authoring time.
        return {**common, "passed": None,
                "reason": "gold_answer_columns_absent",
                "detail": f"answer_columns {q.answer_columns} not in gold "
                          f"result columns {want_columns}"}

    got_projected, how = project_to_answer_columns(
        res.columns, res.rows, q.answer_columns)
    scored = {**common, "got_rows": len(res.rows), "projection": how,
              "columns_ok": columns_match(q.answer_columns, res.columns)}
    if got_projected is None:
        # Neither the names nor the counts line up, so which column holds the
        # answer is unknowable. A different failure from a wrong value.
        return {**scored, "passed": False, "reason": "columns_unmatched",
                "detail": f"answer_columns {q.answer_columns} not in result "
                          f"columns {res.columns}"}

    passed = rows_match(want_projected, got_projected, q.sql_comparison)
    return {**scored, "passed": passed,
            "reason": "" if passed else "rows_differ"}


def judge_case_for(q: BankQuestion, res) -> dict:
    """The {question, reference, answer, contexts} case JudgePool consumes.

    contexts are the chunk texts synthesis actually used - the real pipeline
    output, held in memory. ask.jsonl logs only chunk_ids and stays a trace.

    The scoped route's filter note joins them because contexts must be
    everything the generator was given. Faithfulness scores the answer against
    the contexts, and once the answer stops hedging and asserts the filter's
    predicate ("this is an SME Instrument phase 1 project") that claim lives in
    the filter, not in any chunk - so omitting it would penalise the more
    correct answer.
    """
    contexts = [c.text for c in (res.chunks or [])]
    note = getattr(res, "filter_note", None)
    if note:
        contexts.append(note)
    return {"question_id": q.question_id, "question": q.text,
            "reference_answer": q.reference_answer or "",
            "answer": res.answer,
            "contexts": contexts,
            "adversarial": q.is_adversarial}


def retrieval_scores(q: BankQuestion, res, k: int) -> dict | None:
    """Project-level ranking metrics off the run's own chunks. Free."""
    if not q.gold_project_ids or not res.chunks:
        return None
    ranked = dedup_projects(res.chunks)
    scores = score_ranking(ranked, set(q.gold_project_ids), k)
    return {"k": k, "mode": res.mode, "projects_retrieved": len(ranked),
            "gold_size": len(q.gold_project_ids),
            **{name: round(scores[name], 4) for name in METRICS}}


# --------------------------------------------------------------------------
# phase A - execute
# --------------------------------------------------------------------------

def _spend_dict(records) -> dict:
    return usage.total(records).as_dict()


def execute_question(ask, q: BankQuestion, condition: str, k: int,
                     sql_path) -> dict:
    """One question through one condition. Never raises: a broken question is
    recorded with its traceback and the run continues."""
    mode = CONDITIONS[condition]
    record = {
        "condition": condition, "question_id": q.question_id, "text": q.text,
        "expected_route": q.expected_route, "level": q.level,
        "subtype": q.subtype, "term_style": q.term_style,
        "specification": q.specification, "adversarial": q.is_adversarial,
        "gold_project_ids": q.gold_project_ids,
        # Kept on the record, not only inside the judge case: reading a failure
        # means reading the answer BESIDE what it was supposed to say, and a
        # SQL-route record has no judge case to hold it.
        "reference_answer": q.reference_answer or "",
    }
    started = time.perf_counter()
    try:
        with usage.stage(q.question_id, "gen"):
            res = ask.ask(q.text, k=k, mode=mode)
    except Exception as e:                                   # noqa: BLE001
        record.update({
            "status": STATUS_ERROR,
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc(limit=6),
            "wall_s": round(time.perf_counter() - started, 3),
            "spend": {"gen": _spend_dict(usage.take(q.question_id))},
            "judge_case": None,
            "score": None,
        })
        return record
    wall = time.perf_counter() - started

    expected_mode = ROUTE_TO_MODE.get(q.expected_route)
    record.update({
        "status": STATUS_EXECUTED,
        "error": None,
        "mode": res.mode,
        # An ambiguous question has an acceptable SET of routes, so "misroute"
        # is undefined for it until that vocabulary exists - None, not False.
        "misroute": (None if expected_mode is None else res.mode != expected_mode),
        "expected_mode": expected_mode,
        "router_reason": res.router_reason,
        "router_fallback": res.router_fallback,
        "sql": res.sql,
        "n_rows": len(res.rows),
        "degraded": res.degraded,
        "weak_filter": res.weak_filter,
        "citation_violations": res.citation_violations,
        # The scoped path's narrowing input, verbatim. None on other modes.
        "structured_constraints": res.trace.get("constraints"),
        "constraints_source": res.trace.get("constraints_source"),
        "rows_passed_to_gen": res.trace.get("rows_passed_to_gen", 0),
        "chunks_passed_to_gen": res.trace.get("chunks_passed_to_gen", 0),
        "dropped_for_budget": res.trace.get("dropped_for_budget", 0),
        "retrieved_project_ids": dedup_projects(res.chunks or []),
        "chunk_ids": [c.chunk_id for c in (res.chunks or [])],
        "retrieval": retrieval_scores(q, res, k),
        "answer": res.answer,
        "timings_s": {name: round(v, 3) for name, v
                      in (res.trace.get("timings") or {}).items()},
        "wall_s": round(wall, 3),
        "spend": {"gen": _spend_dict(usage.take(q.question_id))},
    })

    if q.expected_route == "sql" and not q.is_adversarial:
        # ADV stays out of this branch even on the sql route: its gold is an
        # absence, there is no gold_sql to execute, and the refusal rubric is
        # the scorer.
        record["score"] = score_sql_question(q, res, sql_path)
        record["judge_case"] = None
    else:
        record["score"] = None                    # filled by phase B
        record["judge_case"] = judge_case_for(q, res)
    return record


# --------------------------------------------------------------------------
# phase B - judge
# --------------------------------------------------------------------------

def apply_verdict(record: dict, verdict) -> None:
    """Merge one verdict (or the exception that replaced it) onto its record.

    A judge exception marks that one record unscored and says why; it never
    reads as a fail, because "the judge broke" and "the answer was wrong" are
    different findings.
    """
    if isinstance(verdict, Exception):
        record["score"] = {"method": "judge", "passed": None,
                           "reason": f"judge error: {type(verdict).__name__}"
                                     f": {verdict}"}
        return
    record["score"] = {
        "method": "judge", "passed": verdict.passed,
        "judge_path": verdict.path, "judge_model": verdict.model,
        "factual_correctness": verdict.factual_correctness,
        "faithfulness": verdict.faithfulness,
        "detail": verdict.detail,
        "thresholds": {"factual": JUDGE_PASS_FACTUAL,
                       "faithfulness": JUDGE_PASS_FAITHFULNESS},
    }
    if verdict.path == "ragas":
        # Which factual_correctness scale produced this number ("precision"
        # since 2026-08-06, ragas-default "f1" before - different scales,
        # not comparable). On the record because a run directory must say
        # this about itself, not defer to data/logs/judge.jsonl. Never on
        # the overlay path: the rubric does not use the metric, and stamping
        # it there would imply it did. Past runs are never backfilled.
        from src.judge.ragas_judge import FACTUAL_MODE
        record["score"]["factual_mode"] = FACTUAL_MODE
    if verdict.path == "overlay":
        # The ADV grade IS refusal (j0.3); coverage rides along as the bonus
        # it became, so a run can report how much detail correct refusals
        # carried without that ever having decided pass or fail.
        record["score"].update({
            "refusal": verdict.refusal,
            "invented_results": verdict.invented_results,
            "bonus_coverage": verdict.coverage})


def judge_pending(records: list[dict], pool, *, records_path: Path,
                  meta: dict, condition: str = "",
                  progress: "RunProgress | None" = None) -> list[dict]:
    """Judge everything still owed a verdict, in ONE concurrent batch, and
    checkpoint each record the moment its verdict lands.

    The whole batch is in flight at once - that is what JudgePool is for - but
    the results are NOT collected at the end. `on_verdict` fires per case as it
    completes, and each completion writes its own journal line, so a kill part
    way through a batch of eleven keeps the verdicts already paid for.

    Per-question judge spend is taken by label at the same moment, via
    usage.take, which removes only that question's calls and leaves the other
    ten in-flight judges' records alone.
    """
    pending = [r for r in records if needs_judge(r)]
    if not pending:
        return []
    by_qid = {r["question_id"]: r for r in pending}
    cases = [r["judge_case"] for r in pending]
    if progress:
        progress.judging_start(condition, len(cases))

    started = time.perf_counter()
    landed: list[dict] = []
    landed_ids: set[str] = set()
    write_failures: list[Exception] = []

    def on_verdict(case, verdict) -> None:
        record = by_qid.get(case.get("question_id"))
        if record is None:                       # not ours; cannot happen
            return
        qid = record["question_id"]
        landed_ids.add(qid)
        record.setdefault("spend", {})["judge"] = _spend_dict(usage.take(qid))
        apply_verdict(record, verdict)
        # A judge error stays `executed`: judge_case is still on the record, so
        # needs_judge holds and --resume retries this question instead of
        # skipping past it. Only a real verdict is terminal.
        record["status"] = (STATUS_EXECUTED if isinstance(verdict, Exception)
                            else STATUS_JUDGED)
        # Judging is concurrent, so per-question judge wall clock is not
        # measurable. What IS measurable is how far into the batch this verdict
        # landed, which is the honest version of the same number.
        record["judged_after_s"] = round(time.perf_counter() - started, 3)
        landed.append(record)
        try:
            checkpoint(records_path, record, meta)
        except Exception as e:                               # noqa: BLE001
            # Ten other judges are mid-flight and their cost is already spent;
            # killing the batch over one failed write would throw that away.
            # Collected and raised once the batch is home instead - deferred,
            # never swallowed.
            write_failures.append(e)
        if progress:
            progress.verdict(record, len(landed), len(cases))

    pool.judge_all(cases, on_verdict=on_verdict)
    elapsed = time.perf_counter() - started

    # A verdict that never landed means judge_batch broke its own contract of
    # one result per case. Say so on the record - and leave it `executed`, the
    # same as a judge error, so --resume retries it rather than moving on.
    for record in pending:
        if record["question_id"] in landed_ids:
            continue
        record["score"] = {"method": "judge", "passed": None,
                           "reason": "judge returned no verdict for this "
                                     "case"}
        checkpoint(records_path, record, meta)
        landed.append(record)

    if write_failures:
        raise write_failures[0]
    if progress:
        progress.judging_done(condition, landed, elapsed)
    return landed


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

_APPEND_LOCK = threading.Lock()


def _stamp(record: dict, meta: dict) -> dict:
    """Fill in the bookkeeping every appended line carries.

    Latest-line-wins means a line must be COMPLETE, so this runs before every
    append and is idempotent: the `judged` line restates the run id, the models
    and the prompt versions rather than relying on the `executed` line before
    it.
    """
    spend = record.setdefault("spend", {})
    gen = spend.get("gen") or {}
    judge = spend.get("judge") or {}
    spend["total_cost_usd"] = round(gen.get("cost_usd", 0.0)
                                    + judge.get("cost_usd", 0.0), 6)
    record["run_id"] = meta["run_id"]
    record["models"] = meta["models"]
    record["versions"] = meta["versions"]
    record["ts"] = datetime.now().isoformat(timespec="seconds")
    return record


def checkpoint(path: Path, record: dict, meta: dict) -> dict:
    """Stamp a record and append it as one journal line. Flushed and fsynced.

    The point of a checkpoint is that it survives the process dying a moment
    later, so this does not leave the line sitting in an OS buffer. Judging is
    concurrent, so appends are serialised - a torn line would be unreadable
    JSON and would take the whole file with it.
    """
    _stamp(record, meta)
    line = json.dumps(record, ensure_ascii=False, default=str)
    with _APPEND_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
    return record


# --------------------------------------------------------------------------
# watching a run happen
# --------------------------------------------------------------------------

class RunProgress:
    """What a run reports as it happens. Every method is a no-op by default, so
    the runner can call them unconditionally and a caller that wants nothing
    passes nothing.

    The runner never prints. It says what happened; how that reads is the
    reporter's business (ConsoleProgress below, or a recording stub in a test).
    """

    def run_start(self, meta: dict, out_dir: Path) -> None: ...
    def condition_start(self, condition: str, n: int) -> None: ...
    def question_start(self, condition: str, i: int, n: int,
                       q: BankQuestion) -> None: ...
    def question_done(self, record: dict, i: int, n: int) -> None: ...
    def question_skipped(self, record: dict, i: int, n: int,
                         why: str) -> None: ...
    def judging_start(self, condition: str, n: int) -> None: ...
    def verdict(self, record: dict, done: int, n: int) -> None: ...
    def judging_done(self, condition: str, records: list[dict],
                     elapsed_s: float) -> None: ...
    def run_done(self, meta: dict) -> None: ...


def _dur(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds) // 60}m{int(seconds) % 60:02d}s"


def _verdict_word(record: dict) -> str:
    """Where one record stands, in a fixed-width word.

    A topical question after phase A is not unscored, it is not scored YET, and
    those read very differently at 3am watching a run - hence `to-judge` rather
    than a blank.
    """
    if record.get("error"):
        return "ERROR"
    passed = (record.get("score") or {}).get("passed")
    if passed is True:
        return "PASS"
    if passed is False:
        return "FAIL"
    return "to-judge" if needs_judge(record) else "unscored"


class ConsoleProgress(RunProgress):
    """One line per question as it executes, one line per verdict as it lands.

    Judging is concurrent, so verdicts arrive out of the order they were sent;
    each line therefore says how far through the batch it is rather than
    implying a position. The same lines are appended to progress.log in the run
    directory, so a run you walked away from can be read afterwards or tailed
    while it happens.
    """

    def __init__(self, echo=print):
        self.echo = echo
        self.log_path: Path | None = None
        self._started = time.perf_counter()
        self._cost = 0.0
        self._open_line = ""

    # -- output ----------------------------------------------------------
    def _say(self, text: str = "") -> None:
        self.echo(text)
        self._log(text)

    def _log(self, text: str) -> None:
        if self.log_path is None:
            return
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            self.log_path = None      # never let logging be what fails a run

    # -- events ----------------------------------------------------------
    def run_start(self, meta: dict, out_dir: Path) -> None:
        self.log_path = out_dir / "progress.log"
        out_dir.mkdir(parents=True, exist_ok=True)
        models = meta.get("models", {})
        self._say(f"run {meta['run_id']} -> {out_dir}")
        self._say(f"  bank      {meta.get('bank')}  "
                  f"({meta.get('n_questions')} question(s), "
                  f"hash {meta.get('bank_hash')})")
        self._say(f"  condition {', '.join(meta.get('conditions', []))}   "
                  f"k={meta.get('k')}")
        self._say(f"  generator {models.get('generator')}   judge "
                  f"{models.get('judge') or 'SKIPPED (--no-judge)'}")

    def condition_start(self, condition: str, n: int) -> None:
        self._say("")
        self._say(f"== {condition}: executing {n} question(s) "
                  f"(phase A, sequential) ==")

    def question_start(self, condition: str, i: int, n: int,
                       q: BankQuestion) -> None:
        # No newline: the line is finished by question_done, so one question is
        # one line and you can see which one is currently running.
        self._open_line = (f"[{i:>3}/{n}] {q.question_id:<10} "
                           f"{q.expected_route + '/' + q.level:<12} ")
        self.echo(self._open_line, end="", flush=True)

    def question_done(self, record: dict, i: int, n: int) -> None:
        cost = (record.get("spend") or {}).get("total_cost_usd", 0.0)
        self._cost += cost
        if record.get("error"):
            tail = f"ERROR  {record['error']}"
        else:
            tail = (f"{record.get('mode', '?'):<7} "
                    f"{_dur(record.get('wall_s', 0)):>6}  "
                    f"{_money(cost):>8}  {_verdict_word(record)}")
            if record.get("misroute"):
                tail += f"  MISROUTE (wanted {record.get('expected_mode')})"
            if record.get("degraded"):
                tail += f"  degraded={record['degraded']}"
        self.echo(tail)
        self._log(self._open_line + tail)

    def question_skipped(self, record: dict, i: int, n: int, why: str) -> None:
        self._say(f"[{i:>3}/{n}] {record.get('question_id', '?'):<10} "
                  f"skip - {why}")

    def judging_start(self, condition: str, n: int) -> None:
        self._say("")
        self._say(f"== {condition}: judging {n} question(s) "
                  f"(phase B, concurrent - verdicts land out of order) ==")

    def verdict(self, record: dict, done: int, n: int) -> None:
        score = record.get("score") or {}
        cost = (record.get("spend") or {}).get("judge", {}).get("cost_usd", 0.0)
        self._cost += cost
        detail = ""
        if score.get("judge_path") == "ragas":
            detail = (f"factual={score.get('factual_correctness')} "
                      f"faith={score.get('faithfulness')}")
        elif score.get("passed") is None:
            detail = str(score.get("reason", ""))[:60]
        self._say(f"[{done:>3}/{n}] {record['question_id']:<10} "
                  f"{_verdict_word(record):<5} {detail:<34} "
                  f"{_money(cost):>8}  at {_dur(record.get('judged_after_s', 0))}")

    def judging_done(self, condition: str, records: list[dict],
                     elapsed_s: float) -> None:
        passed = sum(1 for r in records
                     if (r.get("score") or {}).get("passed") is True)
        self._say(f"   judged {len(records)} in {_dur(elapsed_s)}: "
                  f"{passed} passed")

    def run_done(self, meta: dict) -> None:
        self._say("")
        self._say(f"run {meta['run_id']}: {meta.get('n_records')} record(s) in "
                  f"{_dur(time.perf_counter() - self._started)}, "
                  f"{_money(self._cost)} priced")
        if meta.get("n_errors"):
            self._say(f"  {meta['n_errors']} errored - see the report, then "
                      f"re-run just those with --ids")
        if meta.get("n_unjudged"):
            self._say(f"  {meta['n_unjudged']} still unjudged - "
                      f"--run-id {meta['run_id']} --resume will judge them "
                      f"without re-running generation")
        self._say(f"  records   {meta.get('records_path')}")
        self._say(f"  report    {meta.get('report_path')}")
        if self.log_path:
            self._say(f"  progress  {self.log_path}")


def select_questions(questions: list[BankQuestion], ids: list[str] | None,
                     routes: list[str] | None,
                     limit: int | None) -> list[BankQuestion]:
    if ids:
        by_id = {q.question_id: q for q in questions}
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise ValueError(f"no such question id(s) in the bank: {missing}")
        return [by_id[i] for i in ids]
    picked = questions
    if routes:
        picked = [q for q in picked if q.expected_route in routes]
    return picked[:limit] if limit else picked


def run_bank(bank_path: Path, conditions: list[str], *, k: int = 10,
             judge: bool = True, ids: list[str] | None = None,
             routes: list[str] | None = None, limit: int | None = None,
             run_id: str | None = None, runs_dir: Path = RUNS_DIR,
             judge_model: str = JUDGE_DEFAULT, resume: bool = False,
             strict_bank: bool = True,
             ask=None, pool=None, progress: RunProgress | None = None) -> dict:
    """Execute (phase A) then judge (phase B). Returns the run's meta dict;
    every record is written to <runs_dir>/<run_id>/records.jsonl the moment it
    lands, so whatever the run finished is on disk even if it is killed."""
    bad = [c for c in conditions if c not in CONDITIONS]
    if bad:
        raise ValueError(f"unknown condition(s) {bad}; "
                         f"choose from {sorted(CONDITIONS)}")

    # strict_bank=False loads records the validator rejects. The violations
    # are carried into meta and printed in the report, because a number off an
    # unvalidated bank has to arrive with that fact attached.
    loaded, bank_errors = load_bank_with_errors(bank_path, strict=strict_bank)
    if bank_errors and strict_bank:
        raise BankValidationError(bank_errors)
    questions = select_questions(loaded, ids, routes, limit)
    run_id = run_id or new_run_id()
    out_dir = Path(runs_dir) / run_id
    records_path = out_dir / "records.jsonl"
    progress = progress or RunProgress()

    # Two runs appending to one journal would collapse into each other by
    # (condition, question_id) and there would be no way to tell afterwards
    # which line came from which. Refuse rather than mix.
    if records_path.is_file() and not resume:
        raise ValueError(
            f"{records_path} already exists. Pass --resume to continue that "
            f"run (it skips what is recorded and judges what is owed), or "
            f"--run-id <something-else> to start a new one.")

    prior: dict[tuple[str, str], dict] = {}
    if resume:
        prior = {(r.get("condition"), r.get("question_id")): r
                 for r in read_records(records_path)}

    if ask is None:
        from src.ask import Ask
        ask = Ask()
    sql_path = getattr(ask, "sql_path", None)

    meta = {
        "run_id": run_id,
        "started": datetime.now().isoformat(timespec="seconds"),
        "bank": str(bank_path),
        "bank_hash": fingerprint(Path(bank_path).read_text(encoding="utf-8")),
        "bank_validation": "strict" if strict_bank else "BYPASSED",
        "bank_violations": bank_errors,
        "conditions": conditions,
        "k": k,
        "judged": judge,
        "resumed": bool(prior),
        "n_questions": len(questions),
        "models": {
            "generator": getattr(getattr(ask, "llm", None), "model", "?"),
            "judge": JUDGE_MODELS.get(judge_model, judge_model) if judge else None,
            "embed": EMBED_MODEL,
        },
        "versions": dict(getattr(ask, "versions", {})),
    }
    progress.run_start(meta, out_dir)

    for condition in conditions:
        progress.condition_start(condition, len(questions))
        batch: list[dict] = []
        for i, q in enumerate(questions, 1):
            previous = prior.get((condition, q.question_id))
            if previous is not None:
                # Already executed. If it is still owed a verdict it goes into
                # this run's judge batch - phase A for it is already paid for
                # and must not be paid for twice.
                batch.append(previous)
                progress.question_skipped(
                    previous, i, len(questions),
                    "executed, judging owed" if needs_judge(previous)
                    else f"already recorded ({previous.get('status')})")
                continue
            progress.question_start(condition, i, len(questions), q)
            record = execute_question(ask, q, condition, k, sql_path)
            checkpoint(records_path, record, meta)
            batch.append(record)
            progress.question_done(record, i, len(questions))

        if judge and any(needs_judge(r) for r in batch):
            if pool is None:
                from src.judge.ragas_judge import JudgePool
                pool = JudgePool(model_key=judge_model)
            judge_pending(batch, pool, records_path=records_path, meta=meta,
                          condition=condition, progress=progress)

    meta["ended"] = datetime.now().isoformat(timespec="seconds")
    meta["out_dir"] = str(out_dir)
    meta["records_path"] = str(records_path)
    if pool is not None:
        # Parse-health counters from the judge backend (api backend only):
        # DeepSeek's loose JSON mode can fail silently inside ragas, so the
        # count of unparseable completions goes on the run's own record.
        stats = getattr(pool, "stats", None)
        health = stats() if callable(stats) else {}
        if health:
            meta["judge_health"] = health

    # Rebuild from disk so the report always reflects the file, resumed rows
    # included - the report is a view of the record, never of memory.
    on_disk = read_records(records_path)
    report = render_report(on_disk, meta)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    meta["report_path"] = str(out_dir / "report.md")
    meta["n_records"] = len(on_disk)
    meta["n_errors"] = sum(1 for r in on_disk if r.get("error"))
    meta["n_unjudged"] = sum(1 for r in on_disk if needs_judge(r))
    progress.run_done(meta)
    return meta


def read_records(path: Path, *, raw: bool = False) -> list[dict]:
    """The journal, collapsed: latest line per (condition, question_id) wins.

    Order is first appearance, i.e. the order the questions were executed in,
    so a resumed run reads in the same order as the run it continues rather
    than putting the resumed rows last. `raw=True` returns every line, which is
    how you see the history of a record rather than its current state.
    """
    if not Path(path).is_file():
        return []
    lines = [json.loads(line) for line
             in Path(path).read_text(encoding="utf-8").splitlines()
             if line.strip()]
    if raw:
        return lines
    collapsed: dict[tuple, dict] = {}
    for record in lines:
        collapsed[(record.get("condition"), record.get("question_id"))] = record
    return list(collapsed.values())


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def _pass_cell(records: list[dict]) -> str:
    """passed/scored, blank when nothing landed in the cell."""
    scored = [r for r in records
              if (r.get("score") or {}).get("passed") is not None]
    if not scored:
        return "-" if not records else f"0/0 ({len(records)} unscored)"
    passed = sum(1 for r in scored if r["score"]["passed"])
    return f"{passed}/{len(scored)}"


def _score_cell(records: list[dict]) -> str:
    """Mean factual_correctness (n) for a judged cell. Unscored rows - NaN
    verdicts, judge errors, not-yet-judged - are counted beside the mean so
    the cell cannot look healthier than it is."""
    have = [(r.get("score") or {}).get("factual_correctness")
            for r in records]
    have = [v for v in have if v is not None]
    if not records:
        return "-"
    if not have:
        return f"- ({len(records)} unscored)"
    cell = f"{sum(have) / len(have):.2f} (n={len(have)})"
    if len(have) < len(records):
        cell += f" +{len(records) - len(have)} unscored"
    return cell


def _dist(vals: list[float]) -> str:
    """mean (n) with min/median/max, because a mean over a bimodal judge is
    a lie of omission."""
    if not vals:
        return "- (n=0)"
    vs = sorted(vals)
    n = len(vs)
    median = vs[n // 2] if n % 2 else (vs[n // 2 - 1] + vs[n // 2]) / 2
    return (f"{sum(vs) / n:.3f} (n={n}, min {vs[0]:.2f}, "
            f"median {median:.2f}, max {vs[-1]:.2f})")


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def _money(x: float) -> str:
    return f"${x:,.2f}" if x >= 0.005 else f"${x:.4f}"


def _sum_cost(records: list[dict]) -> float:
    return sum((r.get("spend") or {}).get("total_cost_usd", 0.0)
               for r in records)


def render_report(records: list[dict], meta: dict) -> str:
    out: list[str] = [f"# Bank run {meta['run_id']}", ""]

    # A bypassed validator is the first thing a reader must see, above the
    # numbers it produced - not a footnote under them.
    if meta.get("bank_validation") == "BYPASSED":
        out += ["> **BANK VALIDATION BYPASSED.** This run loaded records the "
                "schema validator rejects, so its numbers are not comparable "
                "to a strict-bank run without saying so. Violations carried:",
                ""]
        out += [f"> - {e}" for e in (meta.get("bank_violations") or [])] or \
               ["> - (none recorded)"]
        out += [""]

    models = meta.get("models", {})
    out += [
        f"- **bank**: `{meta.get('bank')}` ({meta.get('n_questions', '?')} "
        f"question(s) selected, hash `{meta.get('bank_hash')}`)",
        f"- **conditions**: {', '.join(meta.get('conditions', []))}   "
        f"**k**: {meta.get('k')}",
        f"- **generator**: `{models.get('generator')}`",
        f"- **judge**: `{models.get('judge')}`"
        + ("" if meta.get("judged") else "  (judging SKIPPED this run)"),
        f"- **embedder**: `{models.get('embed')}`",
        f"- **started** {meta.get('started')}  **ended** {meta.get('ended')}",
        "",
        "**Prompt versions** (label:content-hash - a silent edit without a "
        "version bump is still visible here):",
        "",
    ]
    for name, value in sorted((meta.get("versions") or {}).items()):
        out.append(f"- `{name}` = {value}")

    total_cost = _sum_cost(records)
    out += ["",
            f"**Cost: {_money(total_cost)}** over "
            f"{len(records)} record(s), computed from each call's token "
            "counts and the prices pinned in src/config.py. External-API "
            "calls (the v5 seats) are billed for real; `claude -p` rows are "
            "priced, not billed - their marginal spend on the Max "
            "subscription is ~EUR 0.",
            ""]

    ok = [r for r in records if not r.get("error")]
    broken = [r for r in records if r.get("error")]

    # --- headline -----------------------------------------------------------
    # Continuous scores, not a pass-rate gate (plan §5): the pilot's 0.75
    # threshold failed 10 of 11 answers under every condition, so a pass rate
    # cannot show a difference between conditions - means and spread can.
    # sql stays exact (execution against gold is free and binary) and
    # adversarial stays the rubric's refusal grade for the same reason.
    out += ["## Result", "",
            "Judged routes report the score distribution, not a pass rate; "
            "sql is exact execution scoring and adversarial is the refusal "
            "rubric, both binary by nature.", ""]
    for condition in meta.get("conditions", []):
        cond = [r for r in ok if r.get("condition") == condition]
        if not cond:
            continue
        ragas = [r for r in cond
                 if (r.get("score") or {}).get("judge_path") == "ragas"]
        factual = [r["score"]["factual_correctness"] for r in ragas
                   if r["score"].get("factual_correctness") is not None]
        faith = [r["score"]["faithfulness"] for r in ragas
                 if r["score"].get("faithfulness") is not None]
        line = (f"**{condition}**: factual {_dist(factual)}; "
                f"faithfulness {_dist(faith)}")
        sql_exec = [r for r in cond
                    if (r.get("score") or {}).get("method") == "execution"
                    and r["score"].get("passed") is not None]
        if sql_exec:
            line += (f"; sql exact "
                     f"{sum(1 for r in sql_exec if r['score']['passed'])}"
                     f"/{len(sql_exec)}")
        adv = [r for r in cond
               if (r.get("score") or {}).get("judge_path") == "overlay"]
        if adv:
            line += (f"; adversarial "
                     f"{sum(1 for r in adv if r['score']['passed'])}"
                     f"/{len(adv)} refused correctly")
        out.append(line)
        if adv:
            # WHY the adversarial ones failed, not just how many. A hedge and
            # an invented answer are different defects and the pass count
            # cannot tell them apart.
            levels = [r["score"].get("refusal") for r in adv]
            invented = sum(1 for r in adv if r["score"].get("invented_results"))
            bonus = sum(1 for r in adv
                        if r["score"].get("bonus_coverage") == "full")
            out.append(
                f"  adversarial refusals: "
                f"{levels.count('explicit')} explicit, "
                f"{levels.count('hedged')} hedged, "
                f"{levels.count('none')} answered anyway; "
                f"{invented} supplied the missing thing; "
                f"{bonus}/{len(adv)} also carried the reference's detail "
                f"(bonus, never required)")
    if broken:
        out.append(f"**{len(broken)} errored.**")
    out.append("")

    for condition in meta.get("conditions", []):
        cond_records = [r for r in records if r.get("condition") == condition]
        if not cond_records:
            continue
        out += [f"### condition: {condition}", ""]
        rows = []
        for route in ("sql", "vector", "hybrid", "ambiguous"):
            in_route = [r for r in cond_records
                        if r.get("expected_route") == route]
            if not in_route:
                continue
            # sql cells are exact pass fractions; judged cells are mean
            # factual_correctness - a pass fraction there would resurrect
            # the threshold the plan retired.
            cell = _pass_cell if route == "sql" else _score_cell
            cells = [cell([r for r in in_route if r.get("level") == lvl])
                     for lvl in ("L1", "L2", "L3")]
            rows.append([route, *cells, cell(in_route)])
        adv = [r for r in cond_records if r.get("level") == "ADV"]
        if adv:
            rows.append(["adversarial", "-", "-", "-", _pass_cell(adv)])
        out += _table(["route", "L1", "L2", "L3", "route total"], rows) + [""]

        # --- routing --------------------------------------------------------
        routable = [r for r in cond_records if r.get("misroute") is not None]
        if routable:
            misrouted = [r for r in routable if r["misroute"]]
            out += [f"**Routing:** {len(misrouted)}/{len(routable)} misrouted "
                    f"({100 * len(misrouted) / len(routable):.0f}%).", ""]
            for r in misrouted:
                out.append(f"- `{r['question_id']}` ({r['expected_route']}) "
                           f"-> **{r['mode']}**: {r.get('router_reason', '')}")
            fallbacks = [r for r in cond_records if r.get("router_fallback")]
            if fallbacks:
                out.append(f"- router FELL BACK on: "
                           + ", ".join(f"`{r['question_id']}`" for r in fallbacks))
            out.append("")

    # --- judge health --------------------------------------------------------
    # Parse failures counted, never silent (plan §5): DeepSeek's loose JSON
    # mode can hand ragas a completion it cannot parse, and the visible
    # residue is a fix-format retry, a NaN score, or - worst - a silent 0.0
    # from an empty claims list. Every layer of that is counted here.
    ragas_judged = [r for r in ok
                    if (r.get("score") or {}).get("judge_path") == "ragas"]
    if meta.get("judged") and (ragas_judged or meta.get("judge_health")):
        nan_factual = [r for r in ragas_judged
                       if r["score"].get("factual_correctness") is None]
        nan_faith = [r for r in ragas_judged
                     if "faithfulness undefined"
                     in (r["score"].get("detail") or "")]
        errors = [r for r in ok
                  if str((r.get("score") or {}).get("reason", ""))
                  .startswith("judge error")]
        out += ["## Judge health", ""]
        health = meta.get("judge_health") or {}
        if health:
            line = (f"- judge completions: {health.get('completions')}, "
                    f"without parseable JSON: "
                    f"{health.get('unparseable_json')} "
                    f"(`{health.get('model')}`)")
            # Older runs' meta predates the parse-retry counters; render
            # the fragment only when the backend reported them.
            if "parse_retries" in health:
                line += (f", parse retries: {health.get('parse_retries')} "
                         f"(recovered "
                         f"{health.get('parse_retry_recovered')})")
            out.append(line)
        modes = sorted({(r.get("score") or {}).get("factual_mode")
                        for r in ragas_judged} - {None})
        if not modes:
            from src.judge.ragas_judge import FACTUAL_MODE
            modes = [FACTUAL_MODE]
        out.append(f"- factual_correctness mode: {', '.join(modes)}")
        out += [f"- factual_correctness undefined (NaN): {len(nan_factual)} "
                f"of {len(ragas_judged)} ragas-judged",
                f"- faithfulness undefined (NaN): {len(nan_faith)}",
                f"- judge errors (exception; --resume retries them): "
                f"{len(errors)}", ""]

    # --- retrieval ----------------------------------------------------------
    topical = [r for r in ok if r.get("retrieval")]
    if topical:
        out += ["## Retrieval (topical questions)", "",
                "Project-level metrics off each run's own chunks, against the "
                "bank's `gold_project_ids`. **Far too few questions to "
                "conclude anything** - this is here to show the machinery "
                "works, not to answer RQ2. Study 1 runs the four-condition "
                "ladder on a filled vector cell.", ""]
        rows = []

        def metric_row(name: str, group: list[dict]) -> list[str]:
            def avg(metric):
                vals = [r["retrieval"][metric] for r in group]
                return f"{sum(vals) / len(vals):.3f}"
            return [name, str(len(group))] + [avg(m) for m in METRICS]

        rows.append(metric_row("all", topical))
        for route in ("vector", "hybrid"):
            group = [r for r in topical if r.get("expected_route") == route]
            if group:
                rows.append(metric_row(route, group))
        for style in ("exact-term", "paraphrase"):
            group = [r for r in topical if r.get("term_style") == style]
            if group:
                rows.append(metric_row(style, group))
        header = ["group", "n"] + [f"{m}@k" for m in METRICS]
        out += _table(header, rows) + [""]

    # --- efficiency ---------------------------------------------------------
    out += ["## Time and spend", ""]
    rows = []
    for route in ("sql", "vector", "hybrid", "ambiguous"):
        group = [r for r in ok if r.get("expected_route") == route]
        if not group:
            continue
        wall = sum(r.get("wall_s", 0.0) for r in group)
        gen = sum((r.get("spend") or {}).get("gen", {}).get("cost_usd", 0.0)
                  for r in group)
        judge_cost = sum((r.get("spend") or {}).get("judge", {}).get("cost_usd", 0.0)
                         for r in group)
        gen_calls = sum((r.get("spend") or {}).get("gen", {}).get("calls", 0)
                        for r in group)
        judge_calls = sum((r.get("spend") or {}).get("judge", {}).get("calls", 0)
                          for r in group)
        rows.append([route, str(len(group)), f"{wall / len(group):.1f}s",
                     f"{gen_calls}", f"{judge_calls}",
                     _money(gen), _money(judge_cost),
                     _money(gen + judge_cost)])
    out += _table(["route", "n", "mean answer time", "gen calls",
                   "judge calls", "gen cost", "judge cost", "total"],
                  rows)
    by_model: dict[str, float] = {}
    for r in records:
        for phase in ("gen", "judge"):
            for name, cost in ((r.get("spend") or {}).get(phase, {})
                               .get("cost_by_model", {}).items()):
                by_model[name] = by_model.get(name, 0.0) + cost
    if by_model:
        out += ["", "**By model** (a `claude -p` call is a whole Claude Code "
                "session, so every call also spends a little Haiku on the "
                "harness's own overhead - that is the small Haiku figure on a "
                "Sonnet-judged run, not a role violation):", ""]
        for name, cost in sorted(by_model.items(), key=lambda kv: -kv[1]):
            out.append(f"- `{name}` {_money(cost)}")

    out.append("")

    # --- what to actually read ---------------------------------------------
    if broken:
        out += ["## Errors", ""]
        for r in broken:
            out += [f"### `{r['question_id']}` ({r.get('condition')}) - "
                    f"{r['error']}", "",
                    "```", str(r.get("traceback", "")).strip(), "```", ""]

    failures = [r for r in ok if (r.get("score") or {}).get("passed") is False]
    if failures:
        out += ["## Failures - answer beside reference", "",
                "Reading these is the point of a smoke test.", ""]
        for r in failures:
            score = r["score"]
            head = (f"### `{r['question_id']}` {r.get('expected_route')}/"
                    f"{r.get('level')}/{r.get('subtype')} "
                    f"[{score.get('method')}]")
            out += [head, ""]
            if score["method"] == "judge":
                out.append(
                    f"factual={score.get('factual_correctness')} "
                    f"faithfulness={score.get('faithfulness')} "
                    f"(thresholds {JUDGE_PASS_FACTUAL}/"
                    f"{JUDGE_PASS_FAITHFULNESS}) path={score.get('judge_path')}"
                    + (f" - {score['detail']}" if score.get("detail") else ""))
            else:
                out.append(f"reason: {score.get('reason')} "
                           f"(comparison={score.get('comparison')}, "
                           f"projection={score.get('projection', '-')}, "
                           f"gold {score.get('gold_rows')} row(s), "
                           f"got {score.get('got_rows', '-')})")
                if r.get("sql"):
                    out += ["", "generated SQL:", "", "```sql", r["sql"], "```"]
            reference = r.get("reference_answer") or "(none on the bank entry)"
            out += ["", f"**Q** {r.get('text', '')}", "",
                    f"**Answer** {r.get('answer', '')}", "",
                    f"**Reference** {reference}", ""]

    unscored = [r for r in ok if (r.get("score") or {}).get("passed") is None]
    if unscored:
        out += ["## Unscored", ""]
        for r in unscored:
            out.append(f"- `{r['question_id']}`: "
                       f"{(r.get('score') or {}).get('reason', 'not judged')}")
        out.append("")

    # --- one line per question ----------------------------------------------
    if records:
        out += ["## Every question", "",
                "The whole run at a glance, in execution order.", ""]
        rows = []
        for r in records:
            score = r.get("score") or {}
            passed = score.get("passed")
            mark = {True: "PASS", False: "FAIL"}.get(passed, "-")
            note = ""
            if r.get("error"):
                mark, note = "ERROR", str(r["error"])[:60]
            elif score.get("judge_path") == "ragas":
                note = (f"factual={score.get('factual_correctness')} "
                        f"faith={score.get('faithfulness')}")
            elif score.get("method") == "execution":
                note = str(score.get("reason") or "")
            elif passed is None:
                note = str(score.get("reason") or "not judged")[:60]
            if r.get("misroute"):
                note = (note + "  MISROUTE").strip()
            rows.append([
                f"`{r.get('question_id')}`", str(r.get("condition", "")),
                f"{r.get('expected_route')}/{r.get('level')}",
                str(r.get("mode") or "-"), mark,
                _dur(r.get("wall_s", 0.0)),
                _money((r.get("spend") or {}).get("total_cost_usd", 0.0)),
                note])
        out += _table(["question", "condition", "route/level", "ran as",
                       "result", "time", "cost", "note"], rows) + [""]

    return "\n".join(out)
