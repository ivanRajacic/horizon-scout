# Working plan - execution state

*The plan is `horizon-scout.md`. This file is only where things stand.*
*Opened 2026-08-03. History before that date:
`docs/archive/working-plan-through-2026-08-03.md` (64 KB of dated status entries -
every pipeline audit, every batch run, every promotion, with the reasoning).*

## Decisions taken 2026-08-06 (the wrap-up)

Four calls, all made after reading round one. They close the study.

1. **The agentic round is cut.** Item 7 below, and `horizon-scout.md` §2. Never
   built; the last item needing new architecture. The write-up's headline is the
   authoring pipeline, not a third graph of the QA system.

2. **No re-judge of `full-2026-08-06`, and the precision verdicts stay in the
   log.** `records.jsonl` holds the f1 verdicts because that was the mode when
   the run was judged; `precision-judge.log` holds the 33 precision verdicts
   from the same stored answers. Re-judging them into the records would cost
   $0.06 and produce a THIRD set of numbers, because the judge has measured
   noise - 0.081 mean absolute drift on faithfulness at temperature 0, on
   identical inputs. The log verdicts are the precision baseline. The write-up
   computes its tables from the log and says so; `report.md` in that run
   directory still shows f1 aggregates and is not to be quoted.

3. **The factory's own record is analysed instead of running anything new.**
   `src/eval/telemetry.py` computes every number from the batch journals, the
   MCP log and the subagent transcripts: the funnel (43 slots, 58 candidates, 42
   accepted, 1 failed), the critic's 158 terminal findings by class and ruling,
   the deterministic gates, and the cost - **$1,507 of API-equivalent compute,
   about $20 per accepted question, against $0.08 for a full judged run of the
   finished bank**. Written to `docs/factory-telemetry.md` plus a JSON sidecar.
   Three narrative episodes in `docs/factory-exemplars.md` - hyb-13 (a wrong
   gold caught and repaired), hyb-14 (the one abandoned slot, killed on the stop
   rule), vec-31 (a critic finding the judge dismissed). Bank membership was
   verified for both accepted exemplars before they were written up.

4. **Re-running the pipeline on a cheaper model is deferred, not rejected.**
   Sonnet 5 would reprice the same token profile at roughly $850 against
   Opus 5's $1,507 - about $11 per question. Not run, for two reasons. The cost
   driver is architecture, not model: 934M of the tokens are cache reads from
   warm agents re-reading held context across FIX rounds, and the orchestrator
   sessions alone outspent the drafter, critic and judge combined. And the
   comparison would not be clean - the Opus telemetry spans 14 batches run while
   the pipeline itself was changing, so a matched Opus arm would be needed too.
   The claim it would test is real and belongs in the write-up as a stated
   implication: because every fact is verified by execution and every count is
   code, the pipeline should tolerate a weaker model.

## Where things stand

**Built and done.** M1-M4: CORDIS ingest (35,389 projects in DuckDB), chunker and
the 190,248-vector FAISS index, four retrieval conditions behind one interface,
guardrailed text-to-SQL, router, synthesis, `ask.py` end to end with tracing. M5
infrastructure: the bank schema and validator, the RAGAS judge with the refusal
overlay, two checkpointed runners, cost telemetry at the one transport gate, and
both authoring pipelines (`/question-orchestrator`, `/explore-corpus`) with their
deterministic nodes and typed journals.

**The authoring pipeline is frozen.** Four audit rounds; no fifth
re-architecture. `optimization/` holds the numbered plans, all implemented except
`05-workspace-store.md`, which is an unapproved proposal.

**Bank: 58 questions, complete** (2026-08-04), all execution-verified - sql 16,
vector 22, hybrid 11, every ladder cell exactly at target, plus 9 adversarial
(three per costume route, three per subtype, each twinned to an answerable
control) from `/question-orchestrator` batches K-M. Ambiguous and compositional
are dropped for good (`horizon-scout.md` §6); no authoring work remains.

**The vector trim ran 2026-08-03**: 40 -> 22, the 18 archived to
`eval/archive/bank-trimmed-2026-08-03.jsonl` with a recorded reason each, through
a new `archive-questions` command rather than by hand. Landed exactly on the
cell targets with term_style at 11/11 and all five subtypes intact.

It surfaced two real defects in the batch tooling, both now fixed with
regression tests. `next_ids` counted only the bank and the staged drafts, so
archiving vec-42 handed **vec-42 straight back** to the next drafter - two
different questions, one id. And `gap_report` excluded banked-or-rejected ids
but not archived ones, so vec-25's still-on-disk staged twin reappeared as
pending work the batch was expected to finish. Both are the same defect class
already fixed once for promoted records and once for rejected ones; the fix is
`batch.archived_ids`, which both now consult. The batch tests were also leaking
into the real `eval/archive/` through default arguments - they now pass an
explicit archive dir.

**The runtime moved from dense-only to hybrid+rerank on 2026-08-03.** This was
not a config flip: `ask.py` had been building a bare `VectorSearcher` and handing
it to the scoped path too, so neither topical route had ever touched BM25 or the
reranker. It changes what the system answers, on both topical routes, and it
means every number recorded before that date was measured on a stack we no longer
run. Rows in `data/logs/ask.jsonl` with no `versions.retriever` are pre-change.

**Docs consolidated 2026-08-03.** Four documents each claiming to be the plan -
and disagreeing on bank size, what is measured, the retrieval story and the judge
- collapsed into `horizon-scout.md` v5. The other three plus the retrieval note
are in `docs/archive/`, unedited. One of the contradictions was load-bearing:
`gap-report` parses the allocation table live out of the plan doc, so the
retired ~113 target had been driving drafting the whole time.

**The seats ran on 2026-08-05, and the pilot found four defects.**
`run-bank --run-id round1-router` put all 58 questions through the router
condition, executed then judged, for $0.09. Judge health came back clean - 198
completions, 0 without parseable JSON, 0 parse retries, 0 NaN - so the DeepSeek
loose-JSON instrument is negative and the re-ask can be deleted if round one
repeats it. Retrieval is not the bottleneck either: hit@k 0.939, recall@k 0.761.
What the run found instead:

1. **The scoped route cannot express the bank's structured side.** 9 of the 11
   hybrid gold filters narrow on `euroscivoc`; the other 2 use `objective
   ILIKE`. `build_id_narrowing_prompt` omits `euroscivoc` from its allowed
   dimensions and orders the model to drop the research area, and
   `uses_subject_filter` strips the `objective` route. Exactly 1 of the 12
   narrowing queries in the run was correct. hyb-03 returned 219 projects
   against a gold of 4, from an unbracketed `A OR B AND C`; hyb-07 returned
   7,899; hyb-06 invented a coordinator role where the question says
   participant; adv-06 invented a bare `LIMIT 100`; sql-11 returned
   `organisationID` where the contract says `p.id`, which nothing checks.
   **FIXED 2026-08-05** in `5a7320d` + `6d9345d` + `6e421b2` - see "The
   scoped route was rebuilt" below.
2. **The 512-token answer cap is dead code.** `Ask.__init__` passes its shared
   client into `Synthesizer`, so `make_llm(max_tokens=ANSWER_TOKENS)` never
   runs and `ApiClient` sends no cap at all. Answers average 2,082 characters
   against references of 1,149. RAGAS `FactualCorrectness` runs `mode='f1'`,
   so the extra length costs precision: the shorter half of the judged
   questions scored 0.497 mean factual, the longer half 0.282.
   **DECIDED 2026-08-06: leave it uncapped.** The judge moved to coverage of
   the reference the same day, so length no longer costs score - and capping
   would shorten answers and lower coverage, moving a measured number the wrong
   way. The code now says so at both places (`ANSWER_TOKENS` is a budget
   reservation, `Synthesizer.__init__` records that the branch never runs), so
   nobody fixes it later by accident. **This releases the generator pin**: the
   only thing the seat was waiting on was the unresolved answer shape.
3. **The adversarial rule cannot pass a correct refusal.** `derive_pass`
   requires `coverage == "full"`, but an ADV reference carries near-miss
   forensics no correct refusal would reproduce. adv-06 refused correctly and
   failed for not naming the 62 volcanology projects. `ragas_judge.py`'s own
   docstring states the intended rule as "nothing matches and invents
   nothing", and since rubric v0.2 the second half is not checked at all.
   **FIXED 2026-08-05** in `82177f7` - see "The adversarial rubric grades the
   refusal now" below.
4. **One SQL failure is a scorer artifact.** sql-02 returns the right five
   projects in the right order and fails `columns_unmatched` for carrying two
   extra columns and aliasing one pinned column;
   `project_to_answer_columns` accepts all-names-present or an exact count
   match and nothing between. Changing it moves a recorded metric, so it needs
   a decision first. **STILL OPEN** - the code is unchanged at
   `src/retrieval/sql_path.py:355-360`.
   **Corrected 2026-08-06: sql-15 is NOT the same case.** Its generated query
   was `SELECT AVG(endDate - startDate) ... WHERE fundingScheme LIKE 'ERC-%'`
   with no `GROUP BY` - one row against a gold of seven, for a question asking
   per grant type. It is a wrong answer that the strict projection also
   catches. The defect is one question, not two.

**The router was rebuilt the same day, and misroutes fell 21% -> 3%.**
`r1-pilot` defined "structured" by arithmetic ("Counts, sums, averages,
rankings"), so the router read a subject classification, a funding scheme or a
project name as topic text: 12 of 58 misrouted, 7 hybrid questions to `vector`
and 5 sql questions to `scoped`. In 5 of those 7 the model NAMED the scheme or
country in its own reason and then denied a constraint existed.

Two prompts followed, both kept in `ROUTER_PROMPTS` and switchable by changing
`ROUTER_PROMPT_VERSION` alone; `_parse` accepts both contracts so an archived
prompt still runs.

- `r2-columns` defines structured as "the value sits in a column", names
  euroSciVoc beside country / date / money / scheme / role / status, and splits
  on whether the project's own words are needed rather than on whether anything
  is counted. 3 of 57 misrouted. All 3 stated the right facts and then chose a
  mode contradicting them.
- `r3-fields` (active) stops asking for the conclusion. The model reports
  `needs_project_text` and `structured_constraints`, and `derive_mode` picks
  the mode in code - the same split `judge.py:derive_pass` already uses. **2 of
  58 misrouted, no errors, sql exact 12/16.** `derive_mode` was right on all
  58 given the facts it received; both residual misroutes are reading failures,
  and one of the two routes correctly when re-asked (the seat runs at
  temperature 1.0, so neither the router nor the SQL path is deterministic -
  read the routing count, never a single question flipping).

Also fixed: the router's `max_tokens` was 128, which on the gen seat covers
reasoning tokens too. Under `r2-columns` the longest bank question (sql-16)
spent the whole budget and returned no content, failing loud through the
`finish_reason=length` path. It is 384 now.

Runs: `data/runs/round1-router` (r1-pilot, judged),
`data/runs/r2-columns-phaseA`, `data/runs/r3-fields-phaseA` (both `--no-judge`).
`data/runs/` is gitignored.

**Open from the router work:** `RouteDecision` carries the two facts, but
`ask.py` logs only `router_reason` and `router_fallback`, so neither
`ask.jsonl` nor `records.jsonl` records them. A future misroute therefore
cannot be told apart - reading failure or `derive_mode` failure - without
re-asking the router by hand, which is how the paragraph above was written.
About three lines in `ask.py` plus two fields on `AskResult`; it changes the
trace schema, so it is not done yet.

**The scoped route was rebuilt on 2026-08-05, and pilot defect 1 is closed.**
Three commits, in order, each one a layer under the next:

- `5a7320d` **judges only what DuckDB would execute.** Comments are stripped
  before validation, so a model explaining itself in a trailing comment is no
  longer rejected for the `;` or the SET inside it - 14 of 25 narrowing calls in
  `r3-fields-phaseA` were exactly that false positive. The second-statement and
  forbidden-keyword checks run on the statement with string literals blanked, so
  `'DROP-IN centre'` is data rather than a write. Two hooks for the narrowing
  caller: `replace_limit` swaps a model-written trailing `LIMIT` for the
  caller's bound (a filter set is a set, and `LIMIT 1` had silently truncated
  hyb-13 and hyb-15 to one project), and `prompt_label` lets the caller version
  its own prompt instead of reporting under the SQL route's label.
- `6d9345d` **translates a constraint list, and checks every value it writes.**
  `Router.extract` is split out of `route()`, so the router condition reuses its
  own decision's list and `always-hybrid` calls the extractor itself - both
  study arms feed narrowing identical input. An empty list skips the narrowing
  call entirely. euroSciVoc joins the allowed dimensions (9 of the 11 hybrid
  gold filters need it), ORs must be parenthesized (hyb-03's `A OR B AND C`
  silently dropped a threshold), and the value gate looks up every literal
  written against a closed-set column - fundingScheme, status, activityType,
  role, country, euroSciVoc - in that column before the filter runs. A dead
  value gets one hinted re-ask; a still-dirty re-ask drops the filter and
  searches unfiltered with `degraded=value_not_found`. The gate sits BEFORE the
  zero-ids branch, so a misspelling can never become a refusal, while a genuine
  empty intersection of valid values still refuses. Also rejected structurally:
  a comparison whose RESULT is tested with `IS NULL`, which DuckDB reads as
  `(col = 'x') IS NULL` and which matches nothing - hyb-06's filter.
- `6e421b2` **schema docs to `sd3`**: all 56 `fundingScheme` codes, not 11
  examples. hyb-09 had written `'SME Instrument phase 1'` where the column
  stores `'SME-1'`. Both the SQL prompt and the narrowing prompt paste the doc
  whole, so their labels follow it: `q2-pilot` -> `q3-sd3`, `narrow-v3` ->
  `narrow-v4`.

Measured `always-hybrid` over the 11 hybrid questions and the 3 hybrid-route
adversarials (`data/runs/hyb-valuegate-20260805`, `hyb-nullguard-20260805`):
false `zero_match` on answerable questions 2 -> 0, hit@10 9/11 -> 10/11,
hyb-09's filter exactly the bank's 14 projects. The only refusal left is
adv-06's genuinely empty intersection, which is correct.

**Still banned, by design:** `objective`, `title`, `keywords`, `topics` as
narrowing filters. That leaves hyb-13 and hyb-16 - the two hybrid golds that
narrow on `objective ILIKE` - unable to reproduce their gold filter through the
scoped route. **Decided 2026-08-06: accept it, the guard is working as
intended.** The two questions measure the fallback rather than the filter, and
the write-up names it as a property of the bank, not a system defect. What the
last hybrid run actually did with them: hyb-16 kept the legal half
(`fundingScheme = 'ERC-STG'`) and left autism to the vector side, which is the
behaviour the architecture asks for; hyb-13 invented the scheme code `'MSMF'`
and the value gate dropped its filter.

**The adversarial rubric grades the refusal now (`j0.3`, `82177f7`), and pilot
defect 3 is closed.** The pass rule was `coverage == "full"`, so a correct
refusal also had to reproduce the reference's near-miss forensics - counts,
near-miss projects, the true value behind a false premise - which are written as
proof for a human reader. adv-06 refused correctly and failed for not listing
the 62 volcanology projects. The penalty would have grown: round two exists to
make the system refuse correctly more often, so the grader would have absorbed
the improvement it was there to measure.

`j0.3` asks for a refusal level instead - explicit, hedged, or none - plus the
claims that supply the missing thing anyway. Pass iff explicit and nothing
invented. A hedge fails on purpose: "the excerpts do not mention X" reports an
empty search, which is a different statement from "X is not there", and only the
second is the capability under test. Naming what IS true ("the coordinator is
French, not Norwegian") counts as explicit. `coverage` and `missing_facts` are
still asked for, still recorded, and never gate; `refusal`, `invented_results`
and `bonus_coverage` reach the record and the report, so a run says WHY an
adversarial question failed.

Re-judged the nine (`data/runs/adv-j03-20260805`, $0.004): **3 pass**, at bonus
coverage full, partial and none - which is the proof coverage stopped deciding.
Five of the six failures are one defect, the system hedging rather than stating
an absence. The sixth is adv-04 reporting EUR 1,181,520,971.57 as money
"actually paid out" when only commitments are recorded.

**The full router run landed 2026-08-06, and the judge changed the same day.**
`run-bank --run-id full-2026-08-06 --conditions router` put all 58 questions
through the router condition, executed then judged, for $0.08. Factual 0.374
(n=33, median 0.39, max 0.77), sql exact 12/16, adversarial 2/9, misroutes 2/58,
judge health clean (0 unparseable of 33, 0 errors), retrieval hit@k 0.971 /
recall@k 0.807. By route: vector 0.40 (L1 0.49, L2 0.41, L3 0.29), hybrid 0.32
(L1 0.19, L2 0.39, L3 0.35). **The hybrid route improved under correct
routing**: the pilot's 4 correctly-scoped hybrid questions scored 0.247, and now
all 11 reach `scoped` and score 0.32. The run also carries 13 stray
`always-hybrid` records from a first launch that was stopped, which inflate the
run's cost and time tables (71 records, not 58) but not its scores.

**`factual_correctness` moved from `f1` to `mode="precision"` the same day**,
after the same 33 answers were judged under all three modes. Nothing was
re-generated, so the only thing that differed was the mode.

| mode | mean | median | max | 1.00s | zeros |
|---|---|---|---|---|---|
| f1 (was) | 0.374 | 0.39 | 0.77 | 0 | 4 |
| recall | 0.341 | 0.35 | 0.74 | 0 | 4 |
| precision (now) | 0.438 | 0.44 | 1.00 | 3 | 4 |

**ragas' mode names are inverted relative to what they measure**, which cost one
wrong attempt. `tp` = reference claims the answer supports, `fp` = reference
claims it omits, `fn` = answer claims the reference does not contain. So
`precision` = tp/(tp+fp) is coverage of the reference with extra content absent
from the formula; `recall` = tp/(tp+fn) is the extra-content penalty alone; `f1`
sits between them. The first attempt set `recall`, which is stricter about
verbosity than f1 - the mean fell. `precision` is the one that makes length
free. The counters are written out over `FACTUAL_MODE` in
`src/judge/ragas_judge.py`, the mode is logged with every verdict, and a
regression test pins both.

Against f1, precision moved 21 questions up, 6 down, 6 unchanged. The same four
score 0 under all three modes (hyb-08, vec-07, vec-15, hyb-12) - the instrument
agreeing at the bottom. The three new 1.00s are short answers that covered the
whole reference and used to be charged for saying more. Length is free but not
rewarded: vec-14, the longest at 4,540 chars, sits at 0.38 because it misses
reference content. Logs: `recall-judge.log` and `precision-judge.log` in
`data/runs/full-2026-08-06/`.

**Every judged number above, `full-2026-08-06` included, is an f1 number and is
not comparable to anything judged from now on.**

**Judge repeatability, measured for free by the same exercise.** Faithfulness is
untouched by the mode, so it should have come back identical across re-judges.
It did not: mean absolute change 0.081, max 0.35, 10 of 31 moved by more than
0.1 - at temperature 0, because claim decomposition varies between calls. No
single question's delta means anything on its own; hyb-09's -0.53 is that noise,
not a finding. This is a measured version of the plan's "differences under ~15
points are noise" rule and belongs in the write-up.

What none of this fixes: the dead answer cap (defect 2). Precision stops
punishing the verbosity, it does not stop the verbosity - and the same day that
became the reason to keep it uncapped, since a shorter answer covers less of the
reference.

## Next

The order is `horizon-scout.md` §8. Immediately:

1. ~~Trim the bank to 49.~~ **Done 2026-08-03** - see above.
2. **Both seats swap to external APIs, FIRST** - **BUILT 2026-08-04, SMOKED
   2026-08-05; the judge is pinned, the generator is not.** Everything the
   item specified is in and tested (562 passing at that build, including the
   item-3 retry tests; 620 now):
   `src/openai_compat.py` is the one OpenAI-compatible transport -
   two frozen `ApiSeat`s (generator = `gpt-5-nano` with
   `reasoning_effort "minimal"`, temperature locked at 1 and
   `max_completion_tokens` as its cap parameter - re-decided 2026-08-05 from
   `gemini-2.5-flash-lite`, before any run; judge = `deepseek-v4-flash` with thinking
   disabled and temperature 0), each with its own semaphore, backoff on
   transient HTTP failures, and cost computed from per-Mtok prices pinned in
   `src/config.py` (these dollars are billed, unlike the priced `claude -p`
   figures). `ApiClient` sits beside `ClaudeClient` in `src/llm.py`
   (`GEN_BACKEND="api"` default); `OpenAICompatLLM` sits beside
   `ClaudeCliLLM` in `src/judge/ragas_backend.py` and counts completions
   without parseable JSON - the DeepSeek loose-JSON instrument - surfaced as
   the report's new `## Judge health` section (`JUDGE_DEFAULT="deepseek"`;
   the rubric overlay now routes through the recorded gate too, so ADV judge
   spend lands in usage, which the claude path never did). Both transports
   record through `src/eval/usage.py` at their one gate. The report moved
   from pass rates to continuous scores: judged routes show mean factual
   with min/median/max, sql stays exact execution, ADV stays the refusal
   rubric. **The smoke ran 2026-08-05** and went straight to full scale:
   `round1-router`, all 58 questions judged, $0.09. Both seats behaved. The
   judge behaved perfectly (0 unparseable completions of 198). The generator
   did not: its answer-length cap turned out to be dead code, and under the
   f1 judge the verbosity that follows was costing most of the factual score
   (defect 2 above). **Both seats are pinned as of 2026-08-06.** The judge
   pinned on the smoke. The generator pinned when the answer shape was settled
   by decision rather than by a fix: answers stay uncapped, and the judge now
   scores coverage of the reference, so length costs nothing. Nothing else
   about either seat was ever in question.
3. ~~**Three pre-baseline fixes**~~ - **DONE 2026-08-05**, all from
   `docs/pilot-router-findings.md` Part 2, each with regression tests. Two had
   already landed on 2026-08-04 without this item being updated: the scoped
   provenance fix (§1) in `7891908` - synthesis gets a `filter_note` naming
   the survivor count and quoting the narrowing SQL, prompt at
   `s2-provenance`, and `rows_passed_to_gen` stays 0 on purpose because the
   covariate means DB rows in an LLM prompt and a note is not rows - and the
   SQL scorer (§3) in `9dc61c6` - both gold and generated results project to
   `answer_columns`, with `projection` and `columns_ok` recorded on the
   score. The judge retry (§4) landed 2026-08-05 as two changes against the
   new backend's actual failure modes: `call_api_gated` retries empty
   completions through the existing backoff ladder (the envelope is recorded
   in usage first, because those tokens were billed; empty at
   `finish_reason=length` is a token-cap misconfiguration and fails loud with
   no retry), and `OpenAICompatLLM` re-asks once, same prompt, when a judge
   completion has no parseable JSON - `parse_retries` and
   `parse_retry_recovered` join Judge health, and if recovered stays 0
   through the smoke and round one the re-ask gets deleted. The scoped fix
   changes what the system answers, so it is disclosed in the write-up as
   pre-baseline wiring, not as an improvement.
4. ~~**Author the last questions**~~ - **DONE 2026-08-04.** Adversarial was
   flipped into `/question-orchestrator` (bank schema v2.3: born-verified ADV
   with typed `absence_evidence` and `twin_id`) and 9 landed via batches K-M,
   overshooting the original 5. Ambiguous and compositional dropped for good
   (`horizon-scout.md` §6). Bank complete at 58.
5. ~~**Fix the router**~~ - **DONE 2026-08-05**, misroutes 12/58 -> 2/58. See
   above. `r3-fields` is active, `r1-pilot` and `r2-columns` are archived in
   `ROUTER_PROMPTS` and still switchable. Disclosed in the write-up under §6's
   relaxed freeze: the router now returns two facts and `derive_mode` picks the
   mode in code, so "one call returning one mode" is no longer an accurate
   description of it. What it is compared against, `always-hybrid`, is
   untouched.
6. **The four pilot defects.** Two down, two left:
   1. ~~**The euroSciVoc filter.**~~ **DONE 2026-08-05** - `5a7320d`,
      `6d9345d`, `6e421b2`; the narrowing rebuild and the value gate, measured
      above. The one open piece is closed too: `objective` stays banned as a
      narrowing column, and hyb-13 and hyb-16 losing their gold filter is
      accepted (user decision 2026-08-06, reasoning above).
   2. ~~**The dead answer cap**~~ - **DECIDED 2026-08-06: uncapped, on
      purpose.** `ask.py` passes its shared client into `Synthesizer`, so
      `make_llm(max_tokens=ANSWER_TOKENS)` never runs and no answer has ever
      been capped. Kept, because the judge now scores coverage of the reference
      and a shorter answer covers less. Both places carry a comment saying so.
      Prompt tightening is no longer coupled to this; if it happens it is a
      round-two improvement like any other.
   3. ~~**The adversarial pass rule**~~ - **DONE 2026-08-05**, `82177f7`
      (`j0.3`), re-judged above.
   4. **The SQL column projection** - **left as is, 2026-08-06 (provisional).**
      The scorer stays strict: the pinned columns must be findable by name, or
      the column count must match exactly, and anything else fails. So the SQL
      number means "returned the right answer in the pinned shape", and sql-02
      is counted wrong while holding the correct five projects in the correct
      order. Reason for not loosening it: the system is never shown
      `answer_columns` (they appear only in the scoring code), so salvaging
      sql-02 means guessing which of its four returned columns is the money
      column, and a wrong guess would pass a wrong answer - the unsafe
      direction. Revisit after round one if more than one or two questions land
      on `unmatched`. Cheap option if it stays: `projection` and `columns_ok`
      are already on every score record, so the report can show "right rows,
      wrong shape" as its own line without touching the scorer.
7. ~~Build the agentic condition.~~ **CUT 2026-08-06.** Nothing was ever built -
   there is no `src/agent/`, and `run.py:CONDITIONS` still has only router /
   force-sql / force-vector / always-hybrid. It was the last item that needed
   new architecture, a pilot and a freeze, and `horizon-scout.md` §7 already
   holds that the QA system is the instrument and not the contribution. The
   write-up's headline is the authoring pipeline, and a third graph of the
   instrument does not serve it. Recorded in `horizon-scout.md` §2 and §5.
8. **The rounds** (`horizon-scout.md` §2, now two not three). Round one is the
   `router` condition over all 58 questions - that is the round, and it is DONE:
   `data/runs/full-2026-08-06`. `always-hybrid` is an extra arm run on top of it
   when the contrast is wanted. Round two is optional as of 2026-08-06 - see the
   decision block below. Then the write-up.
   **Item 6.1 has landed, so nothing blocks a baseline any more.** The fear it
   was blocking on: correct routing sends all 11 hybrid questions into the
   filter, and before the rebuild the 7 that reached `vector` by mistake scored
   0.370 mean factual against 0.247 for the 4 that reached `scoped` correctly.
   The filter now returns survivors where it used to refuse (false `zero_match`
   2 -> 0, hit@10 10/11), but no round has been judged on it yet - the hybrid
   route's factual score under correct routing is still unmeasured, and if it
   comes in below the misrouted 0.370 that is the honest baseline, not a
   regression to chase.

## Committed 2026-08-04

A backlog spanning four days of work went in as five commits, one per unit:

1. `runtime: hybrid+rerank everywhere, replacing the dense-only stack`
2. `eval: run-retrieval, the four-condition retrieval ladder runner`
3. `bank: archive-questions, and the vector route trimmed 40 -> 22`
4. `records: batches D-J, the 07-28/29 exploration runs, corpus profile to cp8`
5. this doc consolidation

`src/cli.py` carried changes belonging to three of them and was staged by hunk.

## Open, carried forward

- **`/review-bank` is STALE** against the reframed critic's vocabulary - its
  report format still expects verdicts and severities that no longer exist. A
  note sits at the top of that file. Not blocking anything.
- **`## Distributions` in `corpus_profile.md` is unfilled.** It would make the
  explorer's orientation block free thereafter. Low priority now that the bank
  is complete.
- **`tests/test_lexical.py` can still hit a DB lock, but only on a stale
  index.** Since `17a554c` the session fixture reuses the production FTS index
  read-only whenever `fts_index_is_fresh()` proves it matches the chunk table
  and config (sidecar `data/processed/horizon.fts-meta.json`). Only a genuinely
  stale index triggers the read-write rebuild, which errors while a
  `horizon-draft` MCP server from another session holds the file open. A lock
  conflict, not a regression.

## Known notes carried on promoted bank records

Recorded at promotion time, judged not worth a redraft, and worth knowing when
reading results.

All four vector notes (vec-27, vec-29, vec-33, vec-34) are gone - the trim's rule
dropped noted questions first, so they went in the 18. What remains is on the
hybrid route, which was not trimmed:

- **hyb-07**'s reference attributes the Nevado del Ruiz seismic tomography to
  OVSM and INGV, but 793811 pairs OVSM with the land gravity campaign and the
  tomography with INGV alone. Peripheral to what the question asks; it does not
  punish a correct answer.
- **hyb-07**'s `pooling_evidence` counts rejected survivors rather than rejected
  pooled candidates, so its arithmetic does not close.
- **hyb-09**'s Eternum 826866 clauses describe the project's stated method, not
  completed work.
- **vec-16** and **vec-28** are permanent id gaps. vec-28 is the one hand edit
  ever made to `eval/bank.jsonl`, on explicit instruction, after two batches
  independently drafted the same constructed-wetlands question.
