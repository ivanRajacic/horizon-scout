# Horizon Scout

*The plan. v5, 2026-08-03. This is the only plan doc - if something here
disagrees with an older file, this wins and the older file is wrong.*

v5 replaces four documents that had drifted into contradicting each other: the v4
research design (`docs/archive/horizon-scout-v4.md`), the goals rewrite
(`docs/archive/goals-2026-07-26.md`), the ship plan
(`docs/archive/ship-plan-2026-07-31.md`) and the retrieval note
(`docs/archive/retrieval-single-condition-2026-08-03.md`). They are kept as the
record of what was weighed. Nothing in them is live.

---

## 1. What this is and why it exists

Horizon Scout answers natural-language questions about the EU CORDIS
Horizon-research corpus by combining a SQL database and a vector index, and it
measures how well it does that.

Three reasons it exists, in order:

1. **Learn by building.** The next job very likely involves a combined SQL +
   vector system. This is the real thing, not a tutorial version: guardrailed
   text-to-SQL, hybrid retrieval with reranking, a router, an execution-verified
   question bank, an LLM judge, end-to-end tracing over 35,389 real projects.
2. **Have a stamp that it works.** Not "the demo ran" but measured evidence, and
   being able to say exactly why things fail rather than only that they pass.
3. **Portfolio.** A write-up someone can read and conclude the author can be
   trusted with a production retrieval/eval pipeline.

This is a directional learning study, not a pre-registered experiment. The v4
framing - five research questions, freeze-point schedule, ~113-question
allocation - front-loaded cost for a payoff that only landed at the very end.
Everything it produced (the bank, the judge, the runner, the tracing, the
authoring pipelines) carries over unchanged; the ceremony is dropped.

## 2. What is measured

**Three rounds, in order. Each is one graph.**

1. **Baseline.** The 58-question bank against the current system.
2. **Improved.** Named fixes applied, same bank, same judge, same axes. The
   result is per-change before/after.
3. **Agentic.** Runtime orchestration against the improved system - does
   deciding *during* execution buy anything over deciding once, upfront?

**A round IS the `router` condition over all 58 questions.** That is the system
as it ships, so that is what a round's number means.

`always-hybrid` is an EXTRA arm, run on top of a round when the contrast is
wanted: a router picks a capability per question, always-hybrid refuses to
choose and composes both every time, and that contrast is the one the
architecture poses. The agentic loop is an extra arm in the same sense (round
three). `force-sql` and `force-vector` stay in the runner as floors and
diagnostics; they are not part of the story.

**Retrieval is not measured.** One stack runs everywhere:
`config.RUNTIME_RETRIEVER = "hybrid_rerank"`. It was chosen on the 2026-07-29
ladder run (`data/runs/ladder-2026-07-29/report.md`, 10 vector questions x 4
conditions, ranking metrics off `gold_project_ids`): recall@20 0.875, against
hybrid 0.842, dense 0.839, lexical 0.706. That run is reported as **the pilot
that selected the stack, not as a result**. `bench-retrievers` and
`run-retrieval` stay in the codebase as diagnostics.

Named limitation, one sentence in the write-up: the stack was selected on a
10-question pilot ladder and never measured at bank scale, so an always-hybrid
loss cannot be fully separated from a badly configured retriever.

**Directional claims only.** At this bank size, report "hybrid went from 1/6 to
4/6 and here is why", never small percentage differences. Per-cell differences
under ~15 points are noise. Magnitude claims only at route or tier aggregates.

**Say how fixes were found.** Improvements designed by staring at specific
baseline failures are fine - the write-up says so, so the graphs do not quietly
become circular.

## 3. The bank

58 questions, every gold answer proven by execution at authoring time.
`eval/bank.jsonl`, schema v2 in `src/eval/bank.py`.

### Allocation

| | L1 | L2 | L3 | route total |
|---|---|---|---|---|
| SQL | 5 | 7 | 4 | 16 |
| Vector | 7 | 9 | 6 | 22 |
| Hybrid | 3 | 4 | 4 | 11 |
| Adversarial | | | | 9 |
| **Total** | | | | **58** |

`gap-report` parses this table live (`src/eval/batch.py:parse_allocation`), so it
is the binding target and not a description. Every cell is at target as of
2026-08-04: the three ladder routes were done on 2026-08-03, and the nine
adversarial questions (three per costume route, three per subtype, each twinned
to an answerable control) landed in batches K-M. Authoring is complete.

**Ambiguous and compositional are dropped (2026-08-04, user decision)** - the
bank is the four routes only: sql, vector, hybrid, and adversarial worn over
those routes. Adversarial overshot its original 5 to 9, which stands in for the
dropped 6 in headcount. What the two cells would have bought, recorded in §6.
**adversarial** remains the write-up's distinctive claim: absence proven by
execution, with every entry twinned to the answerable question it perturbs.

### 3.1 Levels

Level is pipeline difficulty **when the question is understood correctly** -
deliberately orthogonal to ambiguity.

**SQL.** L1: single table, single operation (no JOIN, <=1 non-trivial WHERE).
L2: a join **or** value-grounding (enum meaning, country code, funding format).
L3: >=2 JOINs, or GROUP BY + ranking, or a near-miss column trap.

**Vector.** Defined by `|gold_project_ids|`: L1 = 1, L2 = 2-4, L3 = 5+. This is a
definition, not a heuristic - `bank.py` enforces it.

**Hybrid.** By what the filter does to the evidence problem. L1: filter isolates,
answer from one survivor's text. L2: filter narrows, synthesis across ~5-20
survivors. L3: tight filter x wide evidence, or filter-then-compare.

Route-scoped `subtype` is required and enforced; `sql_comparison = ordered` iff
`subtype = rank`, both directions.

### 3.2 Labels that stay

`term_style` (exact-term | paraphrase) on topical questions, aimed ~50/50 within
the vector route and within hybrid. It is bank metadata and it fed the 07-29
crossover table (lexical recall@20 falls 0.890 -> 0.522 from exact-term to
paraphrase while dense holds 0.872 -> 0.807 - one table in the write-up).
`specification`, `compositional`, `adversarial` flags, `gold_sql` +
`answer_columns`, `gold_project_ids`, `reference_answer`, `schema_docs_hash`.

**Gold labelling stays pooled over all four retrieval conditions** even though
only one runs. All existing gold-labelled entries were pooled that way; a
narrower gold set on new entries would make the bank two instruments. Same for
adversarial absence proofs - absence under one condition is a weaker claim than
under four, and it is the claim the write-up leads with. `search_corpus` defaults
to `hybrid_rerank` but every gold and sweep call site passes `condition="pooled"`
explicitly.

### 3.3 The vector trim - DONE 2026-08-03

The bank had reached 67 because `gap-report` was reading v4's ~113 target. 18
vector questions were archived, not deleted, to
`eval/archive/bank-trimmed-2026-08-03.jsonl` via a deterministic
`archive-questions` command - the bank is never hand-edited. Bank 67 -> 49.

Survivors were chosen **blind to run results**, on properties recorded at
authoring time, because the 07-29 ladder and the pilot-router run had already
produced per-question performance data and choosing while able to see it would
quietly tune the benchmark. Priority: cell targets, then term_style balance, then
subtype spread, then topic distinctness, dropping first anything already carrying
a known note. Every dropped id carries its own recorded reason in the archive
file.

Result, exactly on target: L1 7 / L2 9 / L3 6, term_style 11 exact-term / 11
paraphrase, and all five vector subtypes still present (identify 4, detail 3,
comparison 5, synthesis 4, survey 6). Archived ids stay permanently taken, like
the vec-16 and vec-28 gaps - `batch.archived_ids` feeds `next_ids` and
`gap_report`, so neither the number nor the still-staged twin comes back.

### 3.4 How questions are authored

Two human-gated paths, both execution-verified, never a hand edit and never a
bulk import: the per-question confirm inside an interactive drafting skill, or
`/question-orchestrator` staging to `eval/drafts/` followed by a ticked report and
`promote-drafts`.

The authoring pipeline is **frozen**. It rests on four principles arrived at
through four audit rounds, and it is the part of this project worth publishing:

- **Split authority.** The drafter authors and self-verifies facts. The critic
  attacks and reports typed findings with no verdict and no kill power. A
  separate judge rules UPHELD/DISMISSED on every finding before deciding
  ACCEPT/FIX/ABANDON. The orchestrator is a message bus that judges nothing.
- **Deterministic-first.** Anything with a right answer is code, not a model - id
  assignment, validation, cross-checks, report generation, 100% re-execution of
  claimed evidence.
- **Typed append-only journals as the only state**, with canonical outputs
  generated by code from the journal. A killed run keeps its finished work.
- **Read-only subagents by construction**, bounded budgets, human-gated
  promotion.

No fifth re-architecture. Remaining effort goes into running measurements and
making plots.

## 4. The instrument

- 35,389 CORDIS projects in DuckDB; 190,248 vectors in FAISS
  (`data/processed/index_meta.json`, built 2026-07-22, config frozen there).
- Four retrieval conditions behind one interface; `hybrid_rerank` is what runs.
- Guardrailed text-to-SQL (`validate_sql` enforces a single read-only SELECT,
  judged on the statement with comments stripped and string literals blanked).
- Router -> sql / vector / scoped; synthesis; `ask.py` end to end. The scoped
  route's narrowing step translates the router's extracted constraint list and
  checks every value it writes against its own column before filtering (§6).
- Every prompt carries a version label **and** a content hash; every run logs
  model and prompt versions; every LLM call is priced at its transport gate.
  `claude -p` has one (`src/claude_cli.py`, one process-wide semaphore, cap 16,
  authoring-era); the external API backends replacing it at run time (§5, §8.2)
  must record usage through `src/eval/usage.py` the same way - a run whose
  spend is unknowable after the fact is the gap that module exists to close.
- Two runners, checkpointed and resumable, reports generated from append-only
  records: `run-bank` varies the condition, `run-retrieval` varies the retriever.

How to drive a run: `docs/running-the-bank.md`.

**Everything recorded before 2026-08-03 was measured on a dense-only stack** -
`ask.py` built a bare `VectorSearcher` and gave it to the scoped path too, so
neither topical route had ever touched BM25 or the reranker. Rows in
`data/logs/ask.jsonl` with no `versions.retriever` are pre-change and are not
comparable to anything after it.

## 5. Judging

**Continuous scores, no pass-rate gate.** This settles a contradiction that sat
open across three files. The pilot showed the metric ranks answers correctly -
`factual_correctness` tracks gold coverage at rho = 0.718, p = 0.013 - but only 1
of 11 answers cleared the 0.75 threshold, and a gate that fails 10 of 11 under
every condition cannot show a difference between conditions. So means and
distributions are reported; the threshold stops mattering and is not calibrated.

- **Judge: a cheap external API, frozen for the whole study.** Judging measured at
  $0.48 per answer on Sonnet against $0.028 to generate - about 10x - and the
  blocker was never money but the 5-hour Max window. On a cheap API the whole
  bank costs single-digit euros.
- **The seats are decided (2026-08-04; generator re-decided 2026-08-05 before
  any run): judge = DeepSeek V4 Flash, generator = gpt-5-nano.** The planned
  agreement
  calibration against the 40 Sonnet-judged pilot answers is dropped for budget
  and simplicity. Reasoning on record: the judge seat gets the most capable
  cheap model because a weak judge corrupts every number while a mediocre
  generator only makes all conditions equally harder, which the comparison
  survives; the generator seat gets the most boring reliable JSON emitter.
  The 2026-08-05 generator swap (Gemini 2.5 Flash-Lite -> gpt-5-nano) followed
  from a vendor-preference call against Google plus a fresh API sweep: nano is
  cheaper ($0.05/$0.40 vs $0.10/$0.40), strict-json_schema, and OpenAI's
  data-sharing free-token tier makes gen runs effectively free. Different
  vendors, temperature 0 on the judge (the GPT-5 family locks the generator's
  at 1), thinking/reasoning pinned to the floor on both ("minimal" is GPT-5's
  off-switch), no expiry date on the judge (gpt-5-mini lost the judge seat to
  its 2026-12-11 shutdown plus 4x the cost; its stronger published judge
  evidence is the disclosed tradeoff). The generator DOES sit in that retiring
  nano/mini tier - accepted because the seat only has to outlive the study,
  and a weak-or-retired generator never corrupts the comparison the way a
  judge would. Known risk, instrumented rather than ignored:
  DeepSeek has only loose JSON mode, and RAGAS returns None on parse failure
  which then scores 0.0 - so parse failures are counted and reported, never
  silent. Budget: the study is planned to run TWICE; on this stack two full
  studies price at ~8-9 EUR (~13 ceiling). Never change either seat mid-study.
- **Metrics.** RAGAS 0.4.3 (pinned) `factual_correctness` + `faithfulness`, with
  the one-paragraph NLI amendment `n1-pilot` - disclosed as "RAGAS with an
  amendment", not stock. **`factual_correctness` runs `mode="precision"`
  (2026-08-06), not the ragas default `f1`.** ragas' mode names are inverted
  relative to what they measure: it decomposes both texts and checks each
  direction, `tp` = reference claims the answer supports, `fp` = reference
  claims it omits, `fn` = answer claims the reference does not contain. So
  `precision` = tp/(tp+fp) is **coverage of the reference**, with extra content
  absent from the formula; `recall` = tp/(tp+fn) is the extra-content penalty
  alone; `f1` is the harmonic mean and sits between them. Coverage is the
  question this bank asks, and under f1 answer length was moving the number -
  on `full-2026-08-06` the shorter half of the answers scored 0.497 mean
  factual against 0.282 for the longer half. Measured on the same 33 answers,
  judged three times: **f1 0.374, recall 0.341, precision 0.438** (medians
  0.39 / 0.35 / 0.44). The tradeoff, one line in the write-up: coverage does
  not charge for invented content, so invention is caught elsewhere -
  `faithfulness` against the retrieved context, and `invented_results` in the
  adversarial rubric. Every number recorded before 2026-08-06 used f1 and is
  not comparable; the mode is logged with every verdict.
- **Route-aware dispatch, pre-registered by the bank's own flags, not decided at
  grading time.** SQL-route questions never reach the judge: they are scored by
  executing the generated query against the gold query, which is free and exact.
  Adversarial questions bypass RAGAS for a one-call refusal rubric, because
  claim-decomposition metrics are structurally blind to a correct refusal. The
  rubric (`j0.3`, 2026-08-05) passes iff the refusal is explicit and nothing is
  invented. A hedge - "the excerpts do not mention X" - fails on purpose: it
  reports an empty search, not an absence, and only the second is the capability
  under test. Coverage of the reference's near-miss forensics is recorded as a
  bonus and never gates.
- **Role separation, both seats external (changed 2026-08-04).** Opus authored
  the questions and references (done, on Max - authoring is over). At run time
  nothing runs on the subscription: generation - the router call, text-to-SQL,
  synthesis, and the agentic loop - moves off `claude -p` to a cheap external
  API, same as judging. This replaces "generation stays on `claude -p`" (Haiku
  on Max). Run-side cost is noise next to judging: ~18k in / ~1k out per
  question, so a full 58-question run prices at ~$0.20 on DeepSeek V4 Flash or
  ~$0.45 on gpt-5-mini (prices re-verified 2026-08-04). No model grades its own
  output: gpt-5-nano generates, DeepSeek V4 Flash judges. Both
  pinned in `src/config.py` and frozen before round one, identical across all
  three rounds.
- **References** are written from gold evidence only, never from system
  retrieval - the measuring stick is independent of the thing measured. SQL
  references are the executed gold result, free.

The judge is **not validated against human labels**. Results are judge-scored
comparisons between conditions, never accuracy.

## 6. What is settled, and what is dropped

Live: the three rounds, the router/always-hybrid contrast, the 58-question bank,
one retrieval stack, a frozen cheap external judge and generator, the authoring
pipeline.

Dropped, with the reason, so none of it is re-litigated:

| dropped | why | where the text lives |
|---|---|---|
| **RQ2** - the four-condition retrieval ladder as a study | the study is about routing, and the ladder is not; the stack is settled by a 10-question pilot | `docs/archive/horizon-scout-v4.md` §RQ2, `retrieval-single-condition-2026-08-03.md` |
| **RQ3** - generator-strength interaction | needed a genuinely weak generator; Haiku-vs-Sonnet is strong-vs-stronger | v4 §RQ3 |
| **RQ5** - judge validation | needed hand-grading, which went with manual review | v4 §RQ5 |
| **The ~113-question allocation** | front-loaded authoring cost for a payoff at the very end | v4 §1 |
| **The d1-d14 day plan and the freeze schedule** | a two-week pre-registered shape the project outgrew | v4 §3 |
| **Study 2 as four static conditions** | force-sql and force-vector are floors, not the story | v4 §5B |
| **Study 0.5 as a separate gate** | SQL execution accuracy is a column in every round, not its own study | v4 §5.0.5 |
| **Ambiguous-route cell (n=3)** | dropped 2026-08-04 with the bank otherwise complete: underspecified questions are imprecise by construction, and the router-stress signal was judged not worth 3 hand-authored interactive-only questions | this table; §3 Allocation |
| **Compositional cell (n=3)** | dropped 2026-08-04 with the ambiguous cell: 3 hand-authored diagnostics for the agentic round; the agentic round now shows itself on the 58 (its cost/latency columns and failure buckets carry the story) | this table; §3 Allocation |

Disclosed under the relaxed freeze so far, all before any baseline number was
recorded:

- The scoped route's `filter_note` (2026-08-04, `7891908`).
- The router rebuild (2026-08-05, `33b2b24`) that took misroutes from 12/58 to
  2/58. More than a prompt edit - the model now reports two facts and
  `src/router/router.py:derive_mode` picks the mode in code - so "one LLM call
  returning one mode" no longer describes it. `always-hybrid`, the thing it is
  compared against, is untouched. Every router prompt ever run stays in
  `ROUTER_PROMPTS`, switchable by one string.
- The SQL guardrail judging only what DuckDB would execute (2026-08-05,
  `5a7320d`): comments stripped, string literals blanked, and a model-written
  `LIMIT` replaced by the caller's bound on the narrowing path.
- The scoped route's narrowing rebuild (2026-08-05, `6d9345d`): it translates
  the router's extracted constraint list instead of re-reading the question,
  euroSciVoc joins the allowed dimensions, and a value gate checks every literal
  against its own column before the filter runs. Measured over the 11 hybrid
  questions plus their 3 adversarials: false `zero_match` 2 -> 0, hit@10 9/11 ->
  10/11. This is the fix the 2026-08-05 pilot named as blocking the baseline.
- `schema_docs.md` to `sd3` (2026-08-05, `6e421b2`): all 56 `fundingScheme`
  codes instead of 11 examples. Both the SQL prompt and the narrowing prompt
  paste the doc whole, so their labels moved with it - `q2-pilot` -> `q3-sd3`,
  `narrow-v3` -> `narrow-v4`.
- The adversarial refusal rubric to `j0.3` (2026-08-05, `82177f7`): pass is an
  explicit refusal with nothing invented; coverage became a bonus (§5). This
  moves a grading rule, so it is one of the two disclosures that touch the
  judge - it landed before round one and the pilot numbers graded under `v0.2`
  are not comparable to it.
- `factual_correctness` from `f1` to `mode="precision"` (2026-08-06, §5). The
  other judge-side change, and the larger one: it changes the scale every
  judged number sits on. Everything measured up to and including
  `full-2026-08-06` used f1. The mode is recorded on every verdict, so no
  reading of an old log has to guess which instrument produced it. Worth
  stating in the write-up rather than hiding: the same 33 answers were judged
  under all three modes before the choice was made.

Freeze discipline is relaxed: prompts and thresholds can move if the change is
disclosed in the write-up. Four things stay genuinely frozen because every
recorded number depends on them - the chunking and index config, the retrieval
stack (`config.RUNTIME_RETRIEVER`), and the judge (DeepSeek V4 Flash) and
generator (gpt-5-nano) decided in §5. The judge is pinned as of the 2026-08-05
smoke, the generator as of 2026-08-06 - its open question was answer shape, and
that was settled by decision rather than by a fix: answers stay uncapped (§8.5),
and the judge scores coverage of the reference, so length costs nothing. The
agentic scaffold freezes after its pilot so later runs stay comparable.

## 7. What ships

**Lead with the authoring factory and the verified bank. The QA system is the
instrument, not the contribution.**

The landscape research (2026-07-31, five parallel searches) is why:

- A router over text-to-SQL + vector + filtered-vector is a LlamaIndex tutorial
  from Oct 2023. Hybrid + rerank is called the minimum viable baseline in 2026
  practitioner writing. The system is table stakes, competently assembled.
- Mainstream synthetic-benchmark tools verify little: RAGAS testset generation has
  no validation step, DataMorgana sample-audited ~200 questions and said it chose
  not to focus on fidelity, YourBench still ships ~15% invalid after filtering.
  **DataMorgana publicly dropped unanswerable questions because it could not
  guarantee absence in a large corpus** - which is exactly what the adversarial
  protocol here proves by execution.
- A Jan 2026 audit found BIRD Mini-Dev 52.8% wrong and Spider 2.0-Snow 62.8%
  wrong, with corrections moving leaderboard rankings by up to 9 positions. The
  two canonical human-verified SQL benchmarks failed the year this bank was
  built. Framing: the code-domain verification standard applied to a retrieval
  corpus.
- The three-way split - critic with no kill power, separate judge ruling per
  finding - is rare; the search found essentially one commercial doc with the
  same shape. The motive is now measured: judges are up to ~50% likelier to
  wrongly pass their own work.
- **Zero QA benchmarks over CORDIS or any research-funding database exist.** One
  concurrent independent repo (`tavitatavi/cordis-mcp-server`, Jul 2026) is
  SQL-only with no text side and no authoring pipeline - cite it as convergence,
  not competition.
- Almost nobody reports eval cost at all. The measured ~10x judging-vs-generation
  figure is its own section.

Second candidate post, possibly separate: the system and its measured improvement
arc - built it, benchmarked it, improved it, showed the numbers.

**Disclosures, one line each:** judge unvalidated against human labels;
retrieval stack chosen on a 10-question pilot; bank at the size it froze at, with
the vector trim and its selection rule stated; RAGAS 0.4.3 with a one-paragraph
NLI amendment and `factual_correctness` on `mode="precision"`, not stock - so
the factual number is coverage of the reference, and invention is caught by
faithfulness and by the adversarial rubric rather than by it; generator, judge and reference author separated by
role AND by vendor (OpenAI / DeepSeek / Anthropic) - no model grades its own
work and no vendor holds two seats; the pre-baseline fixes landed as wiring, not
as improvements (scoped provenance, the SQL scorer, the judge retry, the router
rebuild, the narrowing value gate and the `j0.3` refusal rubric - §6 lists each
with its commit); no formal coverage argument for the bank's
question distribution; no ambiguous or compositional cells (dropped 2026-08-04,
§6) - but over-refusal on answerable questions IS controlled, by the nine
adversarial twins.

## 8. The order of work

Live state and what is next: `working-plan.md`.

1. ~~**Trim the bank to 49** via `archive-questions`~~ - **DONE 2026-08-03** (§3.3).
2. **Swap both seats to external APIs, first** - **BUILT 2026-08-04; smoked
   2026-08-05 at full scale (`round1-router`, 58 questions judged, $0.09).
   The judge froze on it; the generator froze on 2026-08-06**, when the answer
   shape was settled by decision - answers stay uncapped and the judge scores
   coverage, so length costs nothing. An OpenAI-compatible
   generation backend in `src/llm.py` beside `ClaudeCliLLM` pointed at
   gpt-5-nano, and the judge backend in `src/judge/ragas_backend.py` pointed
   at DeepSeek V4 Flash (§5). Pin both in `src/config.py`; the new backends
   log cost and prompt versions the same way `claude -p` did; smoke each on a
   handful of questions. This came before the fixes so nothing was built or
   verified against a transport that was about to be replaced.
3. ~~**Fix the three things that would corrupt a baseline**~~ - **DONE
   2026-08-05**: scoped provenance and the SQL scorer landed 2026-08-04
   (`7891908`, `9dc61c6`); the judge retry became empty-completion retry at
   the transport gate (loud, unretried failure when `finish_reason=length` -
   a token-cap misconfiguration) plus one bounded same-prompt re-ask on a
   judge completion without parseable JSON, both counted in the report's
   Judge health. All three are in `docs/pilot-router-findings.md` Part 2.
4. ~~**Author the last questions**~~ - **DONE 2026-08-04**: 9 adversarial
   authored through `/question-orchestrator` (batches K-M), ambiguous and
   compositional dropped (§6). Bank complete at 58.
5. **The four defects the 2026-08-05 seat smoke found** (`working-plan.md` has
   each one in full). Two are done: the scoped route could not express the
   bank's structured side, which blocked the baseline, and the adversarial pass
   rule could not pass a correct refusal. One was decided and left alone: the
   SQL scorer stays strict, so its number means "returned the right answer in
   the pinned shape" and that is what the write-up will say (2026-08-06,
   provisional - revisit after round one). The fourth was decided the same way:
   the answer-length cap is dead code and stays dead, because the judge now
   scores coverage of the reference and capping would shorten answers and lower
   it. **All four are closed, and nothing now blocks the rounds.**
6. **Build the agentic condition.** Edge policies fixed before implementation:
   max steps, stop conditions, and the four failure buckets - planning error,
   execution drift, non-termination, recovery win. Bucket counts are the
   evidence; trace anecdotes are appendix illustrations. Latency and call count
   are first-class result columns, reported as a multiplier rather than an
   apology.
7. **Run round one**, `--no-judge` first, read the answers, then `--resume` for
   verdicts.
8. **Round two** from what round one shows, plus the candidates already on the
   shelf in `docs/improvement-research-2026-07-27.md`.
9. **Round three**, then the write-up.

Perspective, for the days it feels like too much: the system took days and the
benchmark has taken longer, and that ratio is industry-accurate rather than a
failure. The eval is the product - that was line one of the first plan and it
survives every rewrite since.
