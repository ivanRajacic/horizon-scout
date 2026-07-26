# Running the bank

How to execute the question bank against the system and read what comes back.
The runner is `src/eval/run.py`, driven by `python -m src.cli run-bank`. This is
the real Study-2 runner; it is also what the smoke tests use, at a smaller size.

## Before you start

Two things must be up:

- the **embedder** (bge-base on :8080) - any question that retrieves needs it.
  A SQL-only selection (`--routes sql`) does not, and the runner skips the check
  in that case.
- the **Claude CLI** - generation and judging both go through `claude -p`. No
  local generation server is needed; `GEN_BACKEND` defaults to `"claude"`.

The reranker on :8082 is only used by the rerank retrieval condition, which the
bank runner does not exercise.

Both checks run before anything is spent, so a missing server fails in the first
second rather than half way through a paid run.

## The shape of a run

Two phases, and the split is about safety, not speed.

**Phase A executes, one question at a time.** `VectorSearcher` holds a single
shared read-only DuckDB connection that is not safe to use from several threads,
and the embedder is one GPU that serialises anyway.

**Phase B judges, all at once.** That is what `JudgePool` is for and it is the
expensive half. SQL-route questions never reach it - they are scored by
executing the generated query against the gold query and comparing rows, which
is free.

Which scorer a question gets is decided by its **declared route in the bank**,
never by what the condition happened to produce. A SQL question that a
force-vector condition answered with prose scores a fail with reason `no-sql`.
That is not an error, it is the measurement.

## The commands you will actually type

Smoke it first. Three questions, one of each kind, judging on:

```bash
./.venv/Scripts/python.exe -m src.cli run-bank \
    --ids sql-01 vec-02 hyb-01 --run-id smoke
```

The whole bank through the router condition:

```bash
./.venv/Scripts/python.exe -m src.cli run-bank --run-id pilot-router
```

Execute now, judge later - phase A costs roughly a tenth of what judging costs,
so this is how you look at the answers before deciding to pay for verdicts:

```bash
./.venv/Scripts/python.exe -m src.cli run-bank --run-id pilot --no-judge
# read data/runs/pilot/report.md, then:
./.venv/Scripts/python.exe -m src.cli run-bank --run-id pilot --resume
```

One capability forced on every question (the Study-2 ladder):

```bash
./.venv/Scripts/python.exe -m src.cli run-bank --run-id ladder \
    --conditions router force-sql force-vector always-hybrid
```

Useful narrowing: `--routes vector hybrid`, `--limit N`, `--ids a b c`
(which also fixes the order), `-k 10`, `--model sonnet|haiku` for the judge.

## Watching it happen

One line per question as it lands, one line per verdict as it lands:

```
== router: executing 21 question(s) (phase A, sequential) ==
[  1/21] sql-01     sql/L1       sql       3.1s     $0.06  PASS
[  2/21] vec-02     vector/L2    vector    8.4s     $0.09  to-judge
[  3/21] hyb-01     hybrid/L2    scoped   11.2s     $0.08  to-judge

== router: judging 11 question(s) (phase B, concurrent - verdicts land out of order) ==
[  1/11] hyb-01     FAIL  factual=0.41 faith=0.79            $0.77  at 1m14s
[  2/11] vec-02     FAIL  factual=0.53 faith=0.83            $0.74  at 1m31s
```

`to-judge` means phase A finished and the verdict is still owed - not that
anything is wrong. Judging is concurrent, so verdicts arrive in whatever order
they finish; each line says how far through the batch it is rather than
implying a position, and `at 1m14s` is how far into the batch that one landed.

The same lines are written to `data/runs/<run-id>/progress.log`, so a run you
walked away from can be read afterwards, or tailed while it happens:

```bash
tail -f data/runs/pilot/progress.log
```

## If it dies

Nothing is held in memory waiting for a batch to finish. Every question is
written to `records.jsonl` the moment it executes, and every verdict the moment
it lands. So a killed run keeps everything already paid for, and:

```bash
./.venv/Scripts/python.exe -m src.cli run-bank --run-id pilot --resume
```

skips every question already executed and judges only the ones still owed a
verdict. Generation is never paid for twice - the judge case, including the
actual chunk texts synthesis used, is on disk.

`--resume` needs `--run-id`, because it resumes one specific run directory.
Pointing `--run-id` at an existing run WITHOUT `--resume` is refused rather than
appended to: two runs in one journal would collapse into each other and there
would be no way afterwards to tell which line came from which.

`--resume` does not retry questions that errored. An error is recorded with its
traceback and left alone; re-run those deliberately with `--ids` once you know
why they broke.

## What lands on disk

`data/runs/<run-id>/`:

- **`records.jsonl`** - the run. An append-only journal, latest line per
  `(condition, question_id)` wins, the same convention as the drafting journal
  in `src/eval/batch.py`. A question appears twice: once as `status: executed`
  and once as `status: judged`. Every line is complete - a new line replaces a
  question's state rather than patching it. Read it with
  `src.eval.run.read_records(path)` for current state, or `raw=True` for the
  history.
- **`report.md`** - GENERATED from those records, never written by hand. Pass
  rates per route and level, misroutes with the router's own reason, retrieval
  metrics, time and spend, then the failures with each answer beside its
  reference, then one line per question.
- **`progress.log`** - what you saw while it ran.

Every record also carries the full trace: the mode it ran in, the generated SQL,
the chunk ids, the retrieval scores, per-stage timings, and the tokens and
priced cost split into generation and judging.

`data/runs/` is gitignored. The records carry whole chunk texts and would bloat
history; a report worth keeping should be copied into the repo deliberately.

## About the cost figures

Every dollar figure in the report and the console is **priced, not billed**: it
is what those `claude -p` calls would have cost on the API. On the Max
subscription the marginal spend is about EUR 0. It is there to compare
conditions against each other, not to reconcile against an invoice.

Rough shape at the time of writing: about $0.06-0.10 per question to generate,
about $0.77 per judged question. Judging is roughly ten times generation, which
is the whole reason `--no-judge` and `--resume` exist.

A `claude -p` call is a whole Claude Code session, so every call also spends a
little Haiku on the harness's own overhead. That is why a Sonnet-judged run
shows a small Haiku figure in the by-model breakdown. It is real, and it is not
a role violation.
