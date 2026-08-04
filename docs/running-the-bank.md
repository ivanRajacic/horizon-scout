# Running the bank

How to execute the question bank against the system and read what comes back.
The runner is `src/eval/run.py`, driven by `python -m src.cli run-bank`. This is
the real Study-2 runner; it is also what the smoke tests use, at a smaller size.

Study 1 has its own runner, `run-retrieval`, which varies the retriever instead
of the router. It is the last section of this file.

## Before you start

Two things must be up:

- the **embedder** (bge-base on :8080) - any question that retrieves needs it.
  A SQL-only selection (`--routes sql`) does not, and the runner skips the check
  in that case.
- the **API keys** (v5, 2026-08-04) - generation and judging both run on
  external APIs now: `GEMINI_API_KEY` for the generator (Gemini 2.5
  Flash-Lite) and `DEEPSEEK_API_KEY` for the judge (DeepSeek V4 Flash), both
  checked before anything is spent. No local generation server and no Claude
  CLI are needed; `GEN_BACKEND` defaults to `"api"`.

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
(which also fixes the order), `-k 10`, `--model deepseek|sonnet|haiku` for
the judge (deepseek is the v5 default; the claude keys are the retired v4
seats).

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

Since the v5 seat swap (2026-08-04) the dollar figures for generation and
judging are **billed for real**: the APIs return token counts, and
`src/openai_compat.py` prices them with the per-Mtok rates pinned in
`src/config.py`. Only rows from the retired `claude -p` backends keep the old
caveat - priced, not billed, about EUR 0 marginal on the Max subscription.

Rough shape on the v5 seats (plan §5, prices re-verified 2026-08-04): a full
58-question run prices at roughly $0.20 to generate, and two complete studies
at ~8-9 EUR total including judging. The old `claude -p` figures ($0.06-0.10
to generate, ~$0.77 to judge per question) are why `--no-judge` and
`--resume` exist; they remain useful even now that the absolute numbers are
two orders of magnitude smaller.

Historical note for old reports: a `claude -p` call was a whole Claude Code
session, so every call also spent a little Haiku on the harness's own
overhead - that is the small Haiku figure in a Sonnet-judged run's by-model
breakdown, real and not a role violation.

## The retrieval ladder (`run-retrieval`) - a diagnostic, not a study

**Status (2026-08-03): this already ran, on 2026-07-29, and its job is done.**
Retrieval is no longer measured (`horizon-scout.md` §2). The ladder picked
`hybrid_rerank` (recall@20 0.875, best of four), that is pinned at
`config.RUNTIME_RETRIEVER`, and the run is reported as the pilot that selected
the stack rather than as a result. `run-retrieval` stays in the codebase because
it is cheap and it is the evidence the choice rests on - but re-running it
changes nothing, and changing the stack it chose invalidates every run recorded
against it.

`run-retrieval` is the other runner, `src/eval/retrieval_run.py`. It puts the
bank's vector questions through four retrieval conditions - lexical (BM25),
dense (FAISS), hybrid (RRF fusion of the two), and hybrid plus cross-encoder
rerank - generates an answer per condition, and scores each condition's ranking.

### Before you start

- the **embedder** (:8080) for dense, hybrid and hybrid_rerank. A
  `--conditions lexical` run does not need it and does not check for it.
- the **reranker** (:8082) for hybrid_rerank only.
- the **API keys** always: `GEMINI_API_KEY` (generation) and
  `DEEPSEEK_API_KEY` (judging).
- the **FTS index** must be built, for anything with a lexical side. If it is
  missing, `LexicalRetriever` says so with the fix; the fix is
  `./.venv/Scripts/python.exe -m src.cli build-fts`.

All four are proven up before the first answer is generated.

### Fetch once, reuse everywhere

Per question there is exactly one FTS query, one embed call plus one FAISS
search, one rerank call, and four generator calls. All four conditions are
assembled from those same two deep lists, so the only thing that differs between
the rows of the ladder is the fusion and the rerank, never which chunks happened
to come back on that particular fetch.

At the default `--depth 100` the hybrid condition assembled this way is
identical to the shipped `HybridRetriever`, which is what makes the measurement
one of the real stack. `--k-gen 10` is what the generator sees; the ranking
metrics are scored off the full depth-100 list, so a gold project at rank 15
counts as found even though no generator saw it.

### Cost

4 conditions x 40 questions = 160 answers. On the `claude -p` seats this ran
on, fully judged was roughly $120 priced (Max subscription, about EUR 0
billed); on the v5 API seats it would be a few euros, billed.
Judging is the expensive half, so the normal shape is two stages: run phase A,
read the answers, then pay for verdicts.

### The commands you will actually type

One question, no judging, to prove the wiring:

```bash
./.venv/Scripts/python.exe -m src.cli run-retrieval \
    --ids vec-01 --no-judge --run-id ladder-smoke
```

The full ladder, judged:

```bash
./.venv/Scripts/python.exe -m src.cli run-retrieval --run-id ladder
```

Or the two-stage version of the same thing:

```bash
./.venv/Scripts/python.exe -m src.cli run-retrieval --run-id ladder --no-judge
# read data/runs/ladder/report.md, then:
./.venv/Scripts/python.exe -m src.cli run-retrieval --run-id ladder --resume
```

`--resume` behaves as it does for `run-bank`, keyed on (condition, question)
rather than question alone: it re-runs only the conditions a question is
missing, and judges whatever is still owed without paying for generation twice.
Narrowing works the same way too: `--ids`, `--limit`, `--conditions`.
`--routes` defaults to `vector` because that is the cell Study 1 measures.

### What to read afterwards

`data/runs/<run-id>/records.jsonl` and `report.md`, same as `run-bank`, with one
line per (condition, question) instead of one per question. Every record carries
its own `params`, deep-list `ranking` block and `retrieved_project_ids`, so
every number in the report can be recomputed from the records alone.

The ranking ladder at the top of the report is the headline - it is what picks
the stack for Study 2. The exact-term against paraphrase table below it is RQ2's
real result: where the lexical row crosses the dense row between the two halves
is the finding, not the overall winner.
