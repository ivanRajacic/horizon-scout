# Pilot findings - the `pilot-router` run

**Status (2026-08-03): the findings stand; the "Suggested order" section at the
bottom is superseded by `horizon-scout.md` §8.** Two things it left open are now
decided there: the threshold contradiction is settled in favour of reporting
continuous scores with no pass gate (§5), and Part 2 fixes 1, 3 and 4 are
scheduled before the baseline run rather than after it, because a baseline
measured through them would not mean anything.

What the first end-to-end bank run showed, and what to do about it.

Run: `data/runs/pilot-router/` (records, report, progress log), executed
2026-07-26 18:31-18:48, judging finished 19:15 after a `--resume`. Analysed
2026-07-26. Nothing here is a result: this is Step 1 item 8 in
`working-plan.md`, the smoke run that gates the router-prompt freeze.

## What was run

- bank `eval/bank.jsonl`, hash `e1ea7093b54b`, all 21 questions
- one condition: `router`, k=10
- generator `claude-haiku-4-5-20251001`, judge `claude-sonnet-5`
- prompts `router_prompt=r1-pilot:aeb1fab1d603`, `synth_prompt=s1-pilot:298b9a9171e7`,
  `sql_prompt=q2-pilot:306cd16010c4`, `narrow_prompt=q2-pilot:80edf93be0b1`
- 9/21 passed: SQL 8/10, hybrid 1/6, vector 0/5
- $9.48 priced, of which $8.07 is judging

Three questions (`sql-01`, `vec-02`, `hyb-01`) were carried in from earlier
smoke runs and skipped by the resume logic, which is why the report's total
spend exceeds the $6.50 this run's console printed.

---

# Part 1 - the experiment and the judging

## The judge metric does discriminate

The first read of this run was that `factual_correctness` was failing correct
answers on verbosity. That is not what the data says. Measured against the one
objective proxy available - how many of the bank's `gold_project_ids` the answer
actually named:

```
factual vs gold-coverage      rho = 0.718   p = 0.013
factual vs answer/ref length  rho = 0.536   p = 0.089
```

The metric tracks gold coverage, significantly. The length effect is weaker and
not significant at n=11. A low score is not by itself evidence the metric is
broken, and "the answer looks right" is not evidence against it.

## The threshold is the problem, not the metric

Split the 11 topical questions by whether the answer named every gold project:

| group | n | factual range | mean |
|---|---|---|---|
| named all gold | 7 | 0.31 - 0.90 | 0.51 |
| named some gold | 4 | 0.12 - 0.37 | 0.23 |

Separation between the groups is 0.28. Scatter inside the group that found
everything is 0.59 - about twice the separation. Two rank inversions cross the
boundary: `hyb-08` (found its gold, faithfulness 1.0) at 0.31 and `vec-04` (all
3 gold) at 0.32 both sit below `vec-05`, which found 4 of 10, at 0.37.

That is a usable signal for a continuous comparison and a poor one for a
pass/fail gate. Only **1 of 11** clears `JUDGE_PASS_FACTUAL = 0.75`.

Study 2 runs four conditions over this bank. A gate that fails 10 of 11 under
`router` will fail roughly 10 of 11 under `force-vector` and `always-hybrid`
too, and RQ2 becomes "every condition scores near zero". A gate needs headroom
on both sides to show a difference between conditions; at 0.75 there is none.

Full per-question data:

| qid | factual | faith | gold | named | coverage | ans/ref len |
|---|---|---|---|---|---|---|
| `hyb-01` | 0.90 | 0.89 | 1 | 1 | 1.00 | 1.71 |
| `vec-01` | 0.62 | 0.80 | 1 | 1 | 1.00 | 2.98 |
| `vec-03` | 0.53 | 0.88 | 1 | 1 | 1.00 | 1.55 |
| `hyb-06` | 0.48 | 0.73 | 2 | 2 | 1.00 | 1.12 |
| `vec-02` | 0.43 | 0.60 | 1 | 1 | 1.00 | 2.25 |
| `vec-05` | 0.37 | 0.58 | 10 | 4 | 0.40 | 1.36 |
| `vec-04` | 0.32 | 0.29 | 3 | 3 | 1.00 | 1.53 |
| `hyb-08` | 0.31 | 1.00 | 1 | 1 | 1.00 | 4.78 |
| `hyb-07` | 0.26 | 1.00 | 8 | 2 | 0.25 | 0.69 |
| `hyb-09` | 0.18 | 0.57 | 2 | 1 | 0.50 | 1.13 |
| `hyb-03` | 0.12 | 0.36 | 4 | 1 | 0.25 | 1.10 |

Caveat on `coverage`: naming a gold project is not the same as answering well.
`hyb-06` named PEST-BIN only to say it could not confirm it qualified, so the
proxy flatters it. It is the closest objective measure available, not a grade.

## Why the pilot surprised us

`eval/judge_smoke.jsonl` is 4 cases with answers of 47 to 128 characters. The
real pipeline writes 881 to 1,928. Claim-level F1 behaves differently at those
lengths. The judge was verified in a regime it does not operate in.

## The docs contradict themselves on whether the threshold can move

- `src/config.py:117-123` - "PILOT DRAFT values ... v4: no hand-grade
  calibration (RQ5 scratched) - thresholds are frozen as-is and disclosed as
  such."
- `working-plan.md:74` - "frozen prompt + thresholds + NLI amendment + overlay
  rubric before Study 2 - no calibration study, no hand-graded set."
- `working-plan.md:24` - "pilot draft thresholds in config, **calibrated against
  hand grades** and frozen at d10."

Line 24 says the opposite of the other two. Which one holds decides whether
0.75 can move at all, so this needs a ruling before anything else in Part 1 is
actionable. **Open decision, owner: user.**

## This is not the pilot the plan specifies

`working-plan.md:37` pins the pilot at 13 questions: the 3x3 route/level ladder,
**2 ambiguous, 2 adversarial**, plus the deliberately-broken judge canary from
item 8. The bank as run is 21 questions, all `adversarial: None`, all
`specification: well-specified`, no ambiguous route, no canary. The ladder is
over-covered; the two cells that exist to test something specific are absent:

- **Adversarial** is the only thing that exercises the refusal-overlay dispatch
  (`src/judge/ragas_judge.py:135`) on real pipeline output. It has run against
  4 short synthetic cases and zero real ones.
- **Ambiguous** is described in the plan as what "stresses the router before its
  freeze". The router freeze is the output of this step (items 8 and 213), and
  it is on the verge of happening without the category chosen to probe it.

## What the run cannot tell us

- **One condition.** Router only. The four-condition ladder never ran, so RQ2 is
  untouched. The report's retrieval section says this itself.
- **No variance.** Every question ran once. We do not know whether a PASS/FAIL
  flips between two identical runs. For a study whose output is pass rates that
  number matters more than any single verdict. Cheapest answer: run the router
  condition twice and count flips.
- **term_style is lopsided.** 8 exact-term, 3 paraphrase, 10 unset. The
  exact-term/paraphrase crossover is a study axis with 3 questions on one side.
- **n=11 topical**, which the report correctly calls far too few.

## The cheap way to settle the judging question

Re-judge rather than argue. The 11 judge cases are on disk in `records.jsonl`
**including the exact chunk texts synthesis used**, so re-scoring costs no
generation - roughly $8 priced.

1. Hand-label the 11 as correct / partial / wrong.
2. Re-score the same cases under `mode="f1"` (current), `mode="recall"`, and a
   terse-synthesis variant.
3. Keep the configuration whose ranking best matches the labels, and put the
   threshold where it separates them.

This is the deterministic-first move the project uses everywhere else, and it
produces exactly the hand-graded set `working-plan.md:24` claims already exists.
It is blocked on the contradiction above.

---

# Part 2 - system changes

Nothing listed here is frozen yet. The router prompt freeze is the output of
this step, the bank freezes at d6, the retrieval stack in Study 1, the judge
thresholds at d10.

## 1. Scoped mode must tell the generator what the filter did

Highest value fix in the run. `rows_passed_to_gen = 0` on all seven scoped
questions. The SQL side selects surviving project ids, chunks come back, and the
synthesizer sees prose only. It is never told the survivors are pre-filtered, so
it refuses to assert the filter's own predicate:

- `hyb-09` - "does not explicitly state it is an SME Instrument phase 1 project"
- `hyb-07` - "do not confirm which volcanology projects explicitly include
  Italian-based consortium members"
- `hyb-06` - "PEST-BIN does not explicitly identify Swedish participants"

`hyb-09` and `hyb-06` both had **retrieval recall 1.0**: every gold project was
in the context window. The model hedged with the right documents in hand.

Fix: pass the survivor rows into synthesis, or at minimum one provenance line -
"every project below satisfies `fundingScheme = 'SME-1'`". Plumbing, not
modelling. Should move three of the six hybrid questions on its own.

## 2. Budget by project, not by chunk, for surveys

| qid | gold projects | retrieved | recall |
|---|---|---|---|
| `vec-05` | 10 | 5 | 0.40 |
| `hyb-07` | 8 | 2 | 0.25 |
| `hyb-03` | 4 | 1 | 0.25 |

Every L1 and L2 topical question had recall 1.0. Ten chunks cannot carry ten
projects when one project's text spans several chunks.

Deduplicating to one chunk per project before filling the budget is better than
raising k alone, because it makes the budget match what the metric counts.
Measure first (`bench-retrievers` at k=10/20/30 over the L3 questions) - this
touches the stack Study 1 freezes.

## 3. Project to `answer_columns` in the SQL scorer

`src/eval/run.py:148` compares whole rows. `sql-02`'s generated query is correct
- same five projects, same order - and scores `rows_differ` only because it
returned `id, acronym, title, ecMaxContribution` against a two-column gold.
`columns_ok` is computed on line 151 and never used.

Fix: select `answer_columns` out of the result before `rows_match`, and fail
only when those columns are absent. SQL is really 9/10, not 8/10.

## 4. Retry judge calls in process

2 of 9 judge calls died with `claude -p exited 1 ... stop_reason:"tool_use"`
(`vec-04`, `hyb-09`). `--resume` recovered both at 19:15 and nothing was lost,
so the checkpointing design is proven. But a ~20% per-call failure that needs a
human is not something to carry into a four-condition run over ~97 questions.
`src/judge/ragas_backend.py` already backs off on transient failures; this class
needs to join it.

## 5. Decide whether topic classification counts as structured

The run's one misroute: `sql-08` ("how many machine-learning projects are
coordinated in Spain") went `scoped`, router reason "topic search (machine
learning) combined with structured filters". euroSciVoc is a table, so the bank
calls it `sql`. The answer then failed with `no-sql`.

Routing was 20/21 otherwise, so this is small in isolation - but it is a
one-sentence prompt clarification now, and a documented exception after the
freeze.

---

# Suggested order

1. Resolve the threshold-calibration contradiction (`working-plan.md:24` vs
   `:74` vs `config.py:117`). Gates everything in Part 1.
2. Re-judge the 11 cases already on disk against hand labels.
3. Part 2 fix 1 (scoped filter provenance).
4. Part 2 fixes 3 and 4 (SQL scorer projection, judge retry). Independent of
   everything else; can run alongside 2 and 3.
5. Draft the missing ambiguous and adversarial questions, then freeze the router
   prompt - including fix 5 if it is taken.
6. Measure fix 2 (retrieval budget) before Study 1 freezes the stack.
