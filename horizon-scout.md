# Horizon Scout — M5 Evaluation Plan (v3)
*Derived from planning conversations, July 2026. Companion to the project journal.*
*v3: adds five pre-registered research questions, Study 0.5 (SQL path validation), two-week scope tightening, the complexity specification with allocation table, freeze-point schedule, and the thesis expansion path. Supersedes v2. Literature anchors: TAG-Bench, SUQL, STaRK, BIRD.*

---

## 0. Principles carried over

- The eval is the product. Every component exists to be measured.
- Loud failure over silent wrongness — applied to labels, judges, and interpretation, not just code.
- Frozen, versioned eval sets. Any question fix → new version → re-run baseline on that version.
- Throttle guard before every timed run (local, rented, anywhere).
- Programmatic metrics first; LLM judge only where programmatic can't reach.
- Predictions written down **before** each experiment. Confirmed predictions = understanding; surprises = write-up material.
- **New:** the binding budget is labeling/verification hours. Every question authored is a promise to label and verify it.

## 0b. Positioning & framing

**The organizing idea: an orchestration ladder** — the main results table is one ordered axis, *degrees of orchestration freedom*:

1. **Forced single-path** (force-SQL, force-vector) — zero freedom. Floors/diagnostics.
2. **Router** — one decision per question, made once, upfront.
3. **Always-hybrid** — no decision; fixed composition every time. (2↔3 are a pair: the router *chooses*, always-hybrid *refuses to choose*.)
4. **Agentic** — decisions during execution, informed by intermediate results. Iteration substitutes for composition.

Each rung buys capability and costs something measurable. Present as a spectrum for locating the tradeoff, not a hierarchy to climb.

**The thesis sentence, if the data cooperates (RQ1+RQ3+RQ4):** *generator strength moves you up the ladder* — routing is an accuracy intervention for weak generators and only an efficiency intervention for strong ones; agency is a capability that may only switch on above some planner strength. No prior work makes this claim.

**Literature square (orthogonal by design — each paper covers one piece, none covers the ladder):**
- **TAG-Bench:** taxonomy (match/comparison/ranking/aggregation × knowledge/reasoning) + motivation (no single-paradigm baseline >20%; hand-written hybrid pipelines 55%). Their "RAG" embeds serialized table rows (hence 0.00 on aggregation); their winners are hand-authored per query; they never combine paths automatically. Numbers don't transfer.
- **SUQL:** the unified-language antithesis — SQL + `ANSWER`/`SUMMARY`, one composed query, retrieval as compiler optimization. Explicitly argues against router architectures; our always-hybrid ≈ their position, our router ≈ what they dismiss. **The routing ablation adjudicates a live disagreement.** SUQL sits off-axis: composition at parse time. Not built here — unified languages presume strong parsers (GPT-4; 22% of their errors still parse failures); routing degrades gracefully with small local models. Their 49%+ finding (real questions need both structured and unstructured knowledge) anchors why hybrid exists.
- **STaRK:** retrieval-only precedent (Study 1's problem, full stop). Construction pipeline (relational template → textual property extraction → two-LLM synthesis → multi-LLM answer filtering, gold verification rates 86.6–98.9%) is §2's method precedent. Findings: BM25 beats several dense retrievers; rerankers lift Hit@1 but Recall@20 stays <60%; dense retrievers overrank on metadata-keyword repetition (their Fig. 6). Synthesized queries harder than human ones but miss real-user idiom — cite for the authored-questions limitation.
- **BIRD:** the force-SQL rung, deepest treatment. Difficulty is four-dimensional (question ambiguity, knowledge/value reasoning, data complexity, SQL syntax) — syntax is the least of it. Error taxonomy: schema linking 41.6%, value misunderstanding 40.8%, knowledge misuse 17.6%, **syntax only 3%**. Value descriptions in the prompt = +10–20 points for every model (their biggest lever). Result sets compared as unordered hash-sets (ORDER BY caveat noted). Double-blind annotation is the two-person gold standard our solo protocol bounds. VES (efficiency alongside accuracy) is the lineage of our mandatory latency/call-count columns. Their absolute numbers are a hostile-regime floor, not our ceiling (95 unfamiliar DBs vs. one deeply documented one).
- **Contribution statement:** an empirical test of *where composition logic should live* — choosing a capability (router) vs always using both (hybrid) vs composing at runtime (agentic) — conditioned on generator strength, on a real structured+unstructured corpus (CORDIS), with automatic (not hand-written) pipelines and an eval methodology more rigorous than any of the four precedents.
- Scope-out sentence: TAG-Bench's parametric-world-knowledge query type is excluded (or covered by 2–3 questions, e.g. "Benelux countries" where the DB stores country codes).

---

## RQ. Research questions & pre-registered hypotheses

All hypotheses, sub-predictions, quadrant tables, and decision rules below are duplicated into `predictions.md`, committed **before Study 1 runs**; the commit hash is cited in the write-up. This section is the canonical statement.

### RQ1 — Routing vs. always-hybrid (primary; Study 2)

*Does a router that selects one capability per question outperform always-hybrid composition on end-to-end quality — in particular, does the answer depend on generator strength?*

**H1:** With the small local generator, the router **beats** always-hybrid on accuracy, concentrated in clean-route cells (unambiguous L1–L2 SQL and vector questions), where hybrid's extra retrieved context acts as distraction for a weak model. The gap grows with question simplicity.

- Mechanism signature (required, not optional): always-hybrid failures on SQL-route questions show synthesis referencing irrelevant retrieved-chunk content → trace bucket **`synthesis-contaminated-by-irrelevant-retrieval`**. The gap must be attributable to this bucket, not just observed.
- Falsification checks: if always-hybrid wins, check the misroute rate first — a high misroute rate means the router *implementation* failed, not the router *concept*. If the router wins, confirm via the contamination bucket, not narrative.
- Key covariate: `rows/chunks passed to gen` per trace, ideally split into passed vs. plausibly-relevant (post-hoc judgeable on the topical subset).

### RQ2 — Retrieval ladder (instrumentation; Study 1)

*Which retrieval configuration — dense-only, BM25-only, RRF fusion, fusion+rerank — best retrieves relevant CORDIS projects, and do components contribute where the literature says?*

**H2:** Fusion dominates overall (recall@k, MRR) because BM25 and dense fail on disjoint subsets — BM25 wins exact-term/acronym questions, dense wins paraphrase-distance questions. Rerank lifts **MRR/precision-at-top, not recall@20** (STaRK's shape; mechanically forced — a reranker can't add documents).

- Sub-predictions: (1) the per-category crossover table is the real result — requires the `term_style` tag (exact-term | paraphrase) on topical questions, assigned at generation; (2) dense-only's characteristic failure is metadata-keyword contamination (STaRK Fig. 6) — depends on the chunk-metadata decision (§Freeze); (3) L3-vector (5+ gold projects) is where recall differences bite; this cell also feeds RQ1's force-vector prediction.
- Surprise condition: dense-only matching fusion overall → authored questions reuse corpus vocabulary; itself a finding, one limitation sentence.
- Epistemic role: freezing the *best* stack is what makes RQ1's result defensible — nobody can attribute an always-hybrid loss to bad retrieval. Scope guard: the ladder measures topical retrieval only; it says nothing about the SQL path (see Study 0.5).

### RQ3 — Generator-strength conditional (novelty core)

*Is the architecture choice itself a function of generator capability — does the router's advantage shrink when the same pipeline runs on a stronger model?*

**H3:** An interaction, not two main effects — the router-vs-hybrid gap under the local Qwen substantially exceeds the gap under the ~70B API model, plausibly shrinking to within the detectable margin. What survives the swap is only the router's latency/cost advantage.

| | Weak generator (local Qwen) | Strong generator (70B API) |
|---|---|---|
| Router | baseline | baseline |
| Always-hybrid | *worse* (distraction) | *≈ tie* (robust to noise) |

- Mechanism check: contamination-bucket rate drops on **identical logged retrieval outputs** re-run through the strong model — same inputs, different model. Distraction predicts wrong-info-used errors; plumbing (truncation) predicts missing-info errors; traces distinguish them.
- Design: pre-committed stratified subset (~30 questions, clean-route L1–L2 heavy), committed before local results are seen. Router + always-hybrid only. One run, one table.
- Confound (pre-registered): the strong condition differs in model **and** serving stack. Within-column comparisons are clean; the gap-of-gaps carries the model-plus-stack bundle — which is the bundle a deployer chooses anyway, so the practical claim survives either mechanism; only the mechanistic sentence carries the caveat. Pre-identified robustness check: same-llama.cpp-stack 70B on a rented A100 (~€5), amortized across RQ3+RQ4c, to be run if the interaction becomes the headline.
- Two models ≠ a curve: claim is "the gap depends on strength," not a scaling law.

### RQ4 — Agentic orchestration (rung 4; droppable tail)

*Does agentic orchestration recover capabilities the static architectures lack, and at what price?*

**H4a (extensive margin, weak model):** on the 3 compositional questions, the agent succeeds where all static conditions fail structurally. Near-true-by-construction; the cells demonstrate the capability class exists (n≈3, qualitative diagnostics only).
**H4b (intensive margin, weak model):** on ordinary questions the agent roughly ties the router on accuracy at a multiple of latency/calls ("X points better, Yx slower," X small). Compounding per-step error with a weak planner (~0.9⁴≈0.66) outweighs recovery ability; expect scattered losses from over-iteration and mis-planning.
**H4c (interaction, strong model — OUT of two-week scope, revivable):** the intensive margin flips to a small real win, driven by the recovery-win bucket growing and planning-error/over-iteration shrinking; compositional-cell reliability improves. Mirrors RQ3's structure one rung up.

- Pre-registered agentic failure taxonomy (fixed before implementation): **planning error** · **execution drift** · **non-termination/over-iteration** · **recovery win** (the positive bucket that must account for any intensive-margin surprise).
- Discipline: edge policies (max steps, stop conditions) fixed before implementation. The agentic scaffold is frozen after the weak-model pilot and identical across models — otherwise H4c measures prompt iteration. Bucket counts are the evidence; trace anecdotes are appendix illustrations only.
- Latency and call-count are first-class columns.
- Graceful degradation: H4a's cells run standalone in ~an hour and survive any schedule collapse; the pre-written sentence for a dropped H4b: "the agentic condition was scoped but not run; the compositional cells establish the capability gap it would address."

### RQ5 — The judge (methodology as a result; cannot be dropped)

*Can a pre-registered, reference-based, subscription-tier LLM judge reproduce solo human grading reliably enough to carry the end-to-end evaluation, and which tier suffices?*

**H5a:** the better of Haiku/Sonnet reaches ≥85% agreement (or equivalent κ) with blind human grades on the hand-graded set — reference-based judging converts open-ended quality judgment into checklist matching.
**H5b:** Haiku closes most of the gap to Sonnet for the same reason; residual Sonnet edge concentrates on the hardest cases (zero-match correctness, partial-coverage weighing). Haiku trailing badly = the rubric isn't capability-flattening; also a finding.
**H5c:** disagreements are non-uniform — predicted to concentrate in (1) unsupported-claims leniency, (2) zero-match/false-presupposition (judges reward *an* answer over a correct refusal), (3) partial-coverage boundaries. Disagreement on plain coverage instead → rubric or reference problem, not judge problem.

- **Self-consistency ceiling:** intra-annotator re-grade (~10 questions, one week later, target ~90%+) is the ceiling calibration — judge-human agreement is reported *next to* human-self agreement.
- **Directional-bias check (protects RQ1–4):** disagreements checked for correlation with experimental condition; judge is blind to condition; per-condition disagreement split reported.
- **Pre-registered escalation rule:** both judges <~75% → expand hand-graded set, diagnose via disagreement profile, revise rubric (re-grading earlier items), or in the worst case shift headline claims to programmatic subsets and demote judged results to supporting evidence.
- Hand-graded set is a **stratified** draw (routes × conditions × adversarial types), over-drawing zero-match/false-presupposition so H5c's cells are populated.
- Standalone value: "solo researcher, pre-registered rubric, subscription-tier judge, agreement table with self-consistency ceiling" is the most reusable artifact in the project.

**RQ architecture:** RQ2 builds the instrument · RQ5 validates the measurement · RQ1 asks the core question · RQ3/RQ4 condition it on capability.

---

## 1. Question bank — **bank v1.0** (one bank, multiple measurements)

Single file/schema. Labels are columns; each metric script filters on which labels exist. **v1.0 is the two-week directional bank (~100 questions); expansion produces v1.1+ (append-only), and any expanded results re-run baselines on the new version per the versioning rule.**

**Per-question fields:**
- `question_id`, `text`
- `expected_route`: sql | vector | hybrid | ambiguous (ambiguous → acceptable-set of routes)
- `complexity`: L1/L2/L3 per the specification below
- `specification`: well-specified (default) | underspecified (overlay flag on ~15 questions, spread across routes — **not extra questions**)
- `term_style` (topical subset only): exact-term | paraphrase — feeds RQ2's crossover table; assigned at generation
- `compositional`: flag; 3 questions answerable only by composing capabilities iteratively (SQL top-k → per-item LLM scoring → sort). Impossible for all static routes by construction; RQ4a's diagnostic cells. Scored with partial credit (top-k set overlap; TAG's ranking lesson), never exact match.
- `gold_sql_answer` (SQL subset): computed reference, **answer columns pinned**; result-set comparison is **unordered hash-set** (BIRD's choice; ORDER BY-dependent questions get an ordered-comparison flag or are avoided). Solo protocol is a bounded version of BIRD's double-blind standard — cite as lineage; note even their rigor left label errors (with SUQL's 23.6% and STaRK's 86.6–98.9% as the three-point label-quality literature).
- `gold_project_ids` (vector/hybrid subset): project-level labels, never chunk-level. **Pooling:** label the union of all retrieval conditions' top-k.
- `reference_answer` (judged subset): written from gold evidence — §4.
- Adversarial flags: zero-match-by-design, false-presupposition, unanswerable, data-absent.

### Complexity specification

**Complexity = pipeline difficulty when the question is understood correctly.** Deliberately orthogonal to ambiguity/specification (separate axes) — folds BIRD's four difficulty dimensions into three levels while keeping ambiguity out of the ladder.

**SQL route:**
- **L1** — single table, single operation. *Test: no JOIN, ≤1 non-trivial WHERE.*
- **L2** — join **or** value-grounding (enum meaning, country code, funding format — BIRD's two dominant failure modes placed at the tier boundary). *Test: ≥1 JOIN or dependence on a schema_docs value note.*
- **L3** — multi-join, aggregation+ranking, or disambiguation trap. *Test: ≥2 JOINs, or GROUP BY+ranking, or a near-miss column trap.* **Conditional cell:** pilot SQL smoke test decides whether L3 exists for the local model; if Qwen fails L2 joins, L3 is a pre-registered zero-cell — author 3–4 as diagnostics only.

**Vector route:**
- **L1** — single-project evidence. *Test: |gold_project_ids| = 1.*
- **L2** — 2–4 projects. *Test: |gold_project_ids| ∈ [2,4].*
- **L3** — 5+ projects (RQ2's recall stress cell; RQ1's force-vector-craters cell). *Test: |gold_project_ids| ≥ 5.* **Capped at ~7 questions** — most expensive tier per question under pooled verification.

**Hybrid route** (single ladder by what the filter does to the evidence problem; survivor counts queryable free at generation):
- **L1** — filter isolates, text answers: constraint narrows to few survivors; answer from one survivor's text.
- **L2** — filter narrows, synthesis across survivors (~5–20 survivors, several texts).
- **L3** — tight filter × wide evidence, or filter-then-compare. **RQ1's distraction-mechanism cell** — where `rows/chunks passed to gen` earns its keep.
- *Test: survivors-of-gold-filter × projects the reference draws on.*

### Allocation (~100 questions, RQ-weighted, not uniform)

| | L1 | L2 | L3 | route total |
|---|---|---|---|---|
| SQL | 8 | 10 | 4* | 22 |
| Vector | 8 | 10 | 7 | 25 |
| Hybrid | 7 | 10 | 7 | 24 |
| Ambiguous-route | — spread — | | | 10 |
| Adversarial (zero-match, false-presup., data-absent) | | | | 12 |
| Compositional | | | | 3 |
| **Total** | | | | **~96** |

\* conditional on the pilot smoke test.

Skew rationale: L2 is modal everywhere (realistic + router-fair). **L1-SQL and L1-vector stay fat — they are RQ1's distraction cells; under-populating them starves the primary hypothesis.** Adversarial+ambiguous do double duty (route-quality analysis + RQ5's H5c cells; hand-grading over-draws from them).

**Granularity honesty:** per-cell differences under ~15 points are unresolvable; the route×complexity table *locates* effects and checks predicted signs; magnitude claims only at route/tier aggregates (n≈22–25). Never narrate single-question swings.

Judged subset: **~35** (down from 40–50), stratified across routes.
Topical (projectID-labeled) subset: **~50**, pooling as above; 40 is the floor.
SQL gold answers: **~30** (feeds Study 0.5 and force-SQL cells).

## 2. Question generation pipeline

1. **Corpus exploration first** → query-verified "what's in this database" doc (sibling to schema_docs): topic spread, funding percentiles, per-country/programme distributions, report-summary coverage, genuine absences (zero-match material), fields present in both structured columns AND free text (where routing is genuinely hard). **Doubles as the value-description source for the SQL prompt (Study 0.5).**
2. **Category spec from that exploration**, aligned to literature vocabulary: TAG-Bench query types; BIRD difficulty **noting our tiers deliberately fold BIRD's four dimensions into three levels with ambiguity as a separate axis**; HotpotQA-lineage hop types; SUQL/HybridQA Type I–VI as the compositional vocabulary; **STaRK's construction pipeline as the synthesis-method precedent.**
3. **Generation skill + MCP** on the real database → Opus generates per category, grounded in real data. STaRK's pattern adapted: sample structured slice → extract textual properties from a gold project → compose a question requiring both → **multi-LLM verification of which other projects also satisfy it** (the mechanism for honest pooled `gold_project_ids`).
4. **Adversarial analyze skill** reviews generated questions against gold evidence.
5. **Human verification, evidence-gated:** full engagement on a random 20% + all flagged. Near-zero rejection in sample → trust the rest (measured trust). Problems → widen.
6. **Hand-write what generators won't produce:** ambiguous-route, underspecified, zero-match, false-presupposition, compositional.
7. Track and report rejection/correction rates, with the three-point literature comparison (SUQL 23.6% gold errors; STaRK 86.6–98.9% verification; BIRD's double-blind still imperfect).

## 3. Sequencing — the two-week plan

**Scope decisions (explicit, so mid-crunch doesn't make them implicitly):**
- **CUT for the two weeks:** RQ4c (strong-model agentic); all §5C deferred experiments (source-filtering, router prompt iterations, thinking-budget); chunk_target sweep (already cut, prior stands).
- **SHRUNK:** bank 120–150 → ~100; judged subset 40–50 → ~35; RQ3 to its minimal pre-committed subset.
- **REVIVABLE afterward (parking lot):** RQ4c · §5C experiments · A100 same-stack rerun · mini-SUQL hand-parsed cell (~10 questions) · bank v1.1 expansion.
- **Drop-order under schedule pressure (pre-decided):** compositional cells weak-model (near-free, always runs) → full weak-agentic H4b → RQ3 strong-static subset. RQ5 cannot be dropped (≈€0 marginal, runs on data needed anyway).

**Day plan:**
- **d1–2 — Pilot** (in progress): ~12–15 questions on the dev slice, every pipeline stage incl. judge; **now includes the SQL smoke test** — 2–3 SQL questions through the path, failure modes checked against BIRD's taxonomy (expect schema-linking/value errors, ~0% syntax; surprise = model weakness signal). Smoke test decides the L3-SQL cell. Pilot fixes land in schema/scripts/skill, not memory. **Router prompt frozen at pilot end** (§Freeze).
- **d3 — Corpus exploration + category spec.**
- **d4–6 — Bank v1.0 build** (~100 questions, labels per §1, verification per §2).
- **d7 — Study 0.5: SQL path validation.** The ~30 gold-SQL questions through the SQL path alone, execution accuracy. Then **one pre-registered BIRD-endorsed fix, applied once:** value descriptions (enum meanings, formats, code conventions from the exploration doc) added to the SQL generation prompt. Before/after on the same 30. That is the entire SQL tuning budget — no iteration loop. Acceptable after (≥60–70% on L1–L2) → freeze and proceed. Catastrophic after → pre-registered scope decision point: RQ1 caveat or SQL-tier simplification. *Write-up framing: both paths receive one bounded validation pass before the frozen stack — retrieval via the four-condition ladder, SQL via an execution-accuracy gate with one literature-endorsed intervention. The asymmetry (comparative study vs. validation gate) is stated once.*
- **d8 — Study 1 (RQ2):** retrieval ladder on the topical subset, programmatic metrics, per-category crossover via `term_style`. **Freeze the winner** — the single coupling point. Full 188k index built before this (coverage matters from the first real measurement).
- **d9–10 — References + judge selection (RQ5 setup):** Opus writes references from gold evidence; human-verify; Haiku AND Sonnet over the hand-graded set; agreement + bias tables.
- **d11 — Study 2 (RQ1):** full bank, all four static conditions, frozen stack.
- **d12 — Hand-grading + RQ5 tables** (blind, stratified draw, intra-annotator re-grade scheduled for +1 week).
- **d13 — RQ3 subset + weak-agentic (H4a/H4b) if alive.**
- **d14 — Analysis buffer + failure-analysis pass** (trace taxonomy: misroute / SQL error / retrieval miss / retrieved-but-unsatisfied / synthesis-contaminated / synthesis-other; agentic buckets separately).

## Freeze points & pre-registration mechanics

| Artifact | Frozen when | Note |
|---|---|---|
| `predictions.md` | **before Study 1 (end of d7)** | All RQ hypotheses, quadrant tables, sub-predictions, decision rules, drop-order. Commit hash cited in write-up. |
| Router prompt | end of pilot (d2) | Pilot-misroute fixes are wiring; anything later is tuning on the eval set. Versioned like the judge prompt. **Misroute rate reported as a first-class number beside RQ1's result.** |
| Chunking + index config | before the 188k build (≤d7) | Chunk size from the 300–500 prior; **metadata-in-chunk decision made consciously now** — it is an input to RQ2's contamination prediction, whichever way it goes. |
| End-to-end judged metric | before Study 2 (d10) | Rubric yields per-question pass/fail (adequate coverage AND no unsupported claims); cell scores are pass rates; sub-scores retained for failure analysis. Thresholds live in the versioned rubric doc. |
| SQL prompt (post-Study-0.5) | d7 | After the single value-description intervention. |
| Retrieval stack | d8 | Study 1's winner. |
| Agentic scaffold | after weak-model pilot of the agent | Identical across models thereafter (protects H4c if revived). |
| Bank v1.0 | d6 | Append-only thereafter; expansion = v1.1+ with baseline re-runs. |

## 4. Reference answers (judged subset)

- SQL questions: reference = computed query result. Free.
- Vector/hybrid: **Opus writes the reference from gold evidence only** (via gold_project_ids + MCP) — never from system retrieval; measuring stick independent of the thing measured.
- Human-verify every reference; regenerate bad ones; record the rejection rate.
- Judge instruction: reference = "key facts that should appear," not "the only acceptable answer." Score coverage + absence-of-unsupported-claims, not textual similarity.
- Writer ≠ judge tier: Opus writes, Haiku/Sonnet judges.

## 5. Experiments

**A. Retrieval ladder (RQ2, 4 conditions):** dense-only → BM25-only (DuckDB FTS) → RRF fusion → fusion + cross-encoder rerank. Predictions in RQ2. Check reranker GGUF runs on llama-server **before** designing around it; CPU cross-encoder over top-50 is the fallback.

**0.5. SQL path validation:** see d7. One measurement, one intervention, one gate.

**B. Routing ablation (RQ1, rungs 1–3):** force-all-SQL vs force-all-vector vs router vs always-hybrid, per route × complexity, frozen stack.

**Pre-registered prediction set for B (duplicated in predictions.md):**
- Router beats always-hybrid on accuracy with the weak generator, concentrated in clean-route L1–L2 cells (H1).
- Latency increases monotonically up the ladder.
- Force-vector craters on L3-vector/aggregation-flavored questions (TAG's mechanism); not cratering = surprise.
- SQL failures dominated by schema-linking and value errors, ~0% syntax (BIRD's taxonomy); syntax-dominated = local-model weakness signal.
- Hybrid failure bucket includes `retrieved-but-unsatisfied` (SUQL's parking example) and `synthesis-contaminated` (H1's mechanism).
- Context-overflow risk: trace field `rows/chunks passed to gen` (split: passed vs. plausibly relevant where judgeable).
- Rerank (if in frozen stack) lifted precision-at-top more than recall (STaRK's shape, checked in Study 1).
- Dense-only overranked on metadata-keyword matches (STaRK Fig. 6) — conditional on the chunk-metadata decision.

**C. (Two-week scope: empty.)** Parking lot: source-filtering; router prompt iteration from misroute few-shots; thinking-budget toggle. First revived candidate if the SQL bucket fails post-gate: further value-grounding work (BIRD-endorsed).

**E. Agentic condition (RQ4; H4a/H4b only in-scope):** static router vs agentic orchestration, frozen stack, last. Failure taxonomy, edge policies, scaffold freeze, latency/call-count columns per RQ4. H4c + strong-model run: parking lot.

**D. Optimization loop discipline:** baseline → failure analysis from traces (taxonomy above) → targeted change per biggest bucket → re-measure. Per-route metrics, at least one negative result, never tune on the eval set.

## 6. Judging (RQ5 operational detail)

- **Judge = Claude via `claude -p` on Max subscription.** Pin full model string, `--output-format json`, log model version per trace, version the judge prompt. Transport = one function (billing change → API swap, ~€3).
- **Judge selection empirical:** Haiku AND Sonnet over the hand-graded set; use the better, or report both — the agreement table is itself a result (H5a/H5b).
- Different family from Qwen (generator) by construction. ✔
- Judge **blind to experimental condition**; evidence-only inputs; structured rubric; JSON output; per-question reasoning retained.
- **Directional-bias check:** disagreements × condition correlation; per-condition disagreement split reported.
- Route-aware rubrics: pure-SQL skips the judge (execution accuracy IS end-to-end); zero-match answers correct when they say so; citation-aptness claim-vs-cited-chunk at judging time; compositional/ranking scored with partial credit.

**Hand-grading protocol (solo, done properly):**
- Rubric written before grading; mid-grading changes → re-grade earlier items.
- Blind: never see judge scores or condition first.
- **Stratified draw** across routes × conditions × adversarial types, over-drawing zero-match/false-presupposition (H5c cells).
- Intra-annotator: re-grade 10 questions one week later; ~90%+ self-agreement; **reported beside judge agreement as the ceiling.**
- Escalation rule per RQ5 (both judges <~75%).
- Named limitation: "single annotator, pre-registered rubric, intra-annotator agreement X%." Cheap upgrade (thesis-mandatory, see Expansion): a second person grades 10 overlapping questions.

## 7. Compute & cost

- Generation, programmatic metrics, index builds, retrieval ladder: **local** (~30–60 min per full end-to-end run; overnight batch ≈ 10 conditions). Agentic runs multiply call-count — report the multiplier as a result, not an apology.
- Judge: Max subscription (≈€0 marginal).
- RQ3 strong-model subset (~30 q × 2 conditions) + any revived RQ4c (~30–35 q × ~5 calls): open-model API. **Revised external spend: ~€3–6** (was <€5; RQ4c revival is the driver).
- A100 same-stack rerun: ~€5, parking lot, pre-identified robustness check amortized across RQ3+RQ4c; run if either interaction is the headline.
- Speed numbers never reported from unverified/rented hardware without saying so.

## 8. Write-up commitments

- Predictions-then-results throughout; `predictions.md` commit hash cited.
- Headline: end-to-end per route × complexity, **organized as the orchestration ladder**, with latency/call-count columns for every rung (VES lineage: BIRD first scored efficiency beside accuracy). SUQL named as the off-axis compile-time alternative.
- Positioning per §0b; framing: Study 1 instrumentation, Study 0.5 validation gate, Study 2 + agentic = contribution. One-sentence limitations: routing conclusions conditional on the frozen stack (the deployable configuration, hence defensible); SQL path received a gate not a ladder; authored questions vs real user idiom (STaRK's finding); solo annotation with reported self-agreement; small-n granularity; compositional cells are n≈3 diagnostics; world-knowledge queries scoped out; two capability points ≠ a curve; model-plus-stack bundle caveat on gap-of-gaps claims.
- Report: label rejection/correction rates (three-point literature comparison), judge-human agreement beside human-self agreement, per-condition bias split, misroute rate beside RQ1, negative results, the always-hybrid comparison.
- Candidate public posts: the routing ablation framed as router-vs-SUQL-position; the generator-strength interaction ("routing is an accuracy intervention for weak generators"); the retrieval ladder; "what building ground truth with LLM assistance actually costs"; the judge-agreement recipe (solo + subscription-tier + self-consistency ceiling).

## 9. Pilot suite — definition of done (updated)

~12–15 questions on the dev slice: 2–3 per route incl. 1 ambiguous, 1 zero-match, 1 underspecified, **1 compositional (traced through static routes to confirm it fails there for the right reason), 2–3 SQL smoke-test questions with BIRD-taxonomy failure coding**; ≥1 L2+ per route; 2–3 hand-written references.
Done when: bank schema holds every §1 field (incl. `term_style`, `compositional`) · every metric script runs and emits numbers · router/SQL/retrieval/judged paths exercised · judge returns valid rubric JSON for every judged question · traces capture model versions, prompt versions, route decisions, **rows/chunks-passed-to-gen** · router prompt frozen and versioned · one deliberately-broken question fails loudly · **SQL smoke test verdict recorded (L3-SQL cell: live | zero-cell).**
Nothing from the pilot counts as a result.

## 10. Expansion path (directional study → masters thesis)

The two-week study is the pilot-plus-directional phase; bank v1.0 results are framed as such. Thesis completion (~2–2.5 months on top), in priority order:

1. **Bank v1.1+:** expand to ~200–300 questions (append-only, baselines re-run per versioning rule); optionally a second corpus to show findings aren't CORDIS artifacts. Biggest gap, most mechanical to close — the pipeline makes this labeling hours, not new design.
2. **Capability curve:** add 1–2 mid-sized models (14B/32B) to RQ3/RQ4c, turning the interaction contrast into a trend. Cheap at API prices; upgrades the central claim from contrast to relationship.
3. **Run the parked tail:** RQ4c (strong-model agentic, frozen scaffold), §5C experiments, A100 same-stack robustness rerun, mini-SUQL hand-parsed cell.
4. **Second annotator:** a colleague grades ~10 overlapping questions — inter-annotator agreement, near-mandatory at thesis stakes.

Everything in the two-week plan is already thesis-shaped; this section is the proposal skeleton.