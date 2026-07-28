# Horizon Scout — M5 Evaluation Plan (v4)
*Derived from planning conversations, July 2026. Companion to the project journal.*
*v3: adds five pre-registered research questions, Study 0.5 (SQL path validation), two-week scope tightening, the complexity specification with allocation table, freeze-point schedule, and the thesis expansion path. Supersedes v2. Literature anchors: TAG-Bench, SUQL, STaRK, BIRD.*
*v4 (2026-07-22): generator swapped from local Qwen3-8B to Claude Haiku (via `claude -p`, Max subscription); judge fixed to Sonnet; Opus authors questions and references — one hat per model. RQ3 (generator-strength interaction) and RQ5 (judge validation) scratched; tombstones kept so numbering stays stable. H1 and H4b revised for a capable generator. Manual review / hand-grading dropped; the study is reframed as a directional learning study, not a thesis pilot. Supersedes v3.*

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

**The organizing question (RQ1+RQ4):** *where should composition logic live when the generator is capable* — does choosing a route (router) beat refusing to choose (always-hybrid) on anything besides cost, and does runtime composition (agentic) buy real capability at an acceptable price? With a capable generator the expected headline is an efficiency argument, not an accuracy one; the accuracy discrimination is expected to live in the trap/value-grounded/adversarial cells. (v3's thesis sentence — "generator strength moves you up the ladder" — required the weak-generator condition and is parked with RQ3.)

**Literature square (orthogonal by design — each paper covers one piece, none covers the ladder):**
- **TAG-Bench:** taxonomy (match/comparison/ranking/aggregation × knowledge/reasoning) + motivation (no single-paradigm baseline >20%; hand-written hybrid pipelines 55%). Their "RAG" embeds serialized table rows (hence 0.00 on aggregation); their winners are hand-authored per query; they never combine paths automatically. Numbers don't transfer.
- **SUQL:** the unified-language antithesis — SQL + `ANSWER`/`SUMMARY`, one composed query, retrieval as compiler optimization. Explicitly argues against router architectures; our always-hybrid ≈ their position, our router ≈ what they dismiss. **The routing ablation adjudicates a live disagreement.** SUQL sits off-axis: composition at parse time. Not built here — unified languages presume strong parsers (GPT-4; 22% of their errors still parse failures); routing degrades gracefully with small local models. Their 49%+ finding (real questions need both structured and unstructured knowledge) anchors why hybrid exists.
- **STaRK:** retrieval-only precedent (Study 1's problem, full stop). Construction pipeline (relational template → textual property extraction → two-LLM synthesis → multi-LLM answer filtering, gold verification rates 86.6–98.9%) is §2's method precedent. Findings: BM25 beats several dense retrievers; rerankers lift Hit@1 but Recall@20 stays <60%; dense retrievers overrank on metadata-keyword repetition (their Fig. 6). Synthesized queries harder than human ones but miss real-user idiom — cite for the authored-questions limitation.
- **BIRD:** the force-SQL rung, deepest treatment. Difficulty is four-dimensional (question ambiguity, knowledge/value reasoning, data complexity, SQL syntax) — syntax is the least of it. Error taxonomy: schema linking 41.6%, value misunderstanding 40.8%, knowledge misuse 17.6%, **syntax only 3%**. Value descriptions in the prompt = +10–20 points for every model (their biggest lever). Result sets compared as unordered hash-sets (ORDER BY caveat noted). Double-blind annotation is the two-person gold standard our solo protocol bounds. VES (efficiency alongside accuracy) is the lineage of our mandatory latency/call-count columns. Their absolute numbers are a hostile-regime floor, not our ceiling (95 unfamiliar DBs vs. one deeply documented one).
- **Contribution statement:** an empirical test of *where composition logic should live* — choosing a capability (router) vs always using both (hybrid) vs composing at runtime (agentic) — with a capable small-tier generator (Claude Haiku), on a real structured+unstructured corpus (CORDIS), with automatic (not hand-written) pipelines, scored on accuracy and cost together.
- Scope-out sentence: TAG-Bench's parametric-world-knowledge query type is excluded (or covered by 2–3 questions, e.g. "Benelux countries" where the DB stores country codes).

---

## RQ. Research questions & pre-registered hypotheses

All hypotheses, sub-predictions, quadrant tables, and decision rules below are duplicated into `predictions.md`, committed **before Study 1 runs**; the commit hash is cited in the write-up. This section is the canonical statement.

### RQ1 — Routing vs. always-hybrid (primary; Study 2)

*With a capable generator, does a router that selects one capability per question outperform always-hybrid composition on end-to-end quality — or is routing only an efficiency intervention?*

**H1 (v4, revised for the Haiku generator):** router **≈ ties** always-hybrid on accuracy in clean-route cells (unambiguous L1–L2 SQL and vector questions) — a capable generator is robust to hybrid's extra retrieved context — while the router wins clearly on latency, tokens, and call count. Efficiency columns are therefore co-primary results, not supporting detail. Any accuracy gap that does appear is expected in trap/value-grounded/adversarial cells, not clean cells.

- Mechanism signature (still required): if always-hybrid loses anywhere, its failures must show synthesis referencing irrelevant retrieved-chunk content → trace bucket **`synthesis-contaminated-by-irrelevant-retrieval`**. Pre-registered expectation: this bucket is **near-empty** for Haiku; a fat contamination bucket is a surprise and write-up material.
- Falsification checks: if always-hybrid wins on accuracy, check the misroute rate first — a high misroute rate means the router *implementation* failed, not the router *concept*. If the router wins on accuracy, confirm via the contamination bucket, not narrative.
- Key covariate: `rows/chunks passed to gen` per trace, ideally split into passed vs. plausibly-relevant (post-hoc judgeable on the topical subset).
- Lineage: v3's H1 predicted a router accuracy win driven by weak-model distraction; that prediction is void with the generator swap and recorded as such in predictions.md.

### RQ2 — Retrieval ladder (instrumentation; Study 1)

*Which retrieval configuration — dense-only, BM25-only, RRF fusion, fusion+rerank — best retrieves relevant CORDIS projects, and do components contribute where the literature says?*

**H2:** Fusion dominates overall (recall@k, MRR) because BM25 and dense fail on disjoint subsets — BM25 wins exact-term/acronym questions, dense wins paraphrase-distance questions. Rerank lifts **MRR/precision-at-top, not recall@20** (STaRK's shape; mechanically forced — a reranker can't add documents).

- Sub-predictions: (1) the per-category crossover table is the real result — requires the `term_style` tag (exact-term | paraphrase) on topical questions, assigned at generation; (2) dense-only's characteristic failure is metadata-keyword contamination (STaRK Fig. 6) — depends on the chunk-metadata decision (§Freeze); (3) L3-vector (5+ gold projects) is where recall differences bite; this cell also feeds RQ1's force-vector prediction.
- Surprise condition: dense-only matching fusion overall → authored questions reuse corpus vocabulary; itself a finding, one limitation sentence.
- Epistemic role: freezing the *best* stack is what makes RQ1's result defensible — nobody can attribute an always-hybrid loss to bad retrieval. Scope guard: the ladder measures topical retrieval only; it says nothing about the SQL path (see Study 0.5).

### RQ3 — Generator-strength conditional — **SCRATCHED (v4)**

The interaction design required a genuinely weak generator (local Qwen3-8B) as its baseline condition. With the swap to Haiku there is no weak condition, and Haiku-vs-Sonnet is strong-vs-stronger — too little contrast to surface the interaction. Parked, revivable by adding a weak generator condition back (see §10). The number is retained so existing references stay stable; v3's full RQ3 text lives in git history.

### RQ4 — Agentic orchestration (rung 4)

*Does agentic orchestration recover capabilities the static architectures lack, and at what price?*

**H4a (extensive margin):** on the 3 compositional questions, the agent succeeds where all static conditions fail structurally. Near-true-by-construction; the cells demonstrate the capability class exists (n≈3, qualitative diagnostics only).
**H4b (intensive margin, v4 — revised for a capable planner):** on ordinary questions the agent scores a **small real accuracy win** over the router, driven by the recovery-win bucket (re-query after an empty result, self-correction after a bad first SQL), at a multiple of latency/calls — "X points better, Yx slower," X small but positive. This is v3's H4c prediction moved into scope; v3's weak-planner compounding prediction (~0.9⁴ ≈ 0.66, scattered over-iteration losses) is void with the generator swap. If the agent instead *ties or loses*, the failure taxonomy must say why — that outcome would be the more interesting finding.

- Pre-registered agentic failure taxonomy (fixed before implementation): **planning error** · **execution drift** · **non-termination/over-iteration** · **recovery win** (the positive bucket that must account for any intensive-margin win).
- Discipline: edge policies (max steps, stop conditions) fixed before implementation; the agentic scaffold is frozen after its pilot so later runs stay comparable. Bucket counts are the evidence; trace anecdotes are appendix illustrations only.
- Latency and call-count are first-class columns.
- Graceful degradation: H4a's cells run standalone in ~an hour and survive any schedule collapse; the pre-written sentence for a dropped H4b: "the agentic condition was scoped but not run; the compositional cells establish the capability gap it would address."

### RQ5 — The judge — **SCRATCHED (v4)**

Judge validation required hand-grading, which is dropped along with manual review. The judge is now **fixed by decision, not selection**: Sonnet, frozen prompt and thresholds, and *unvalidated* — all judged results are reported as **"Sonnet-judged pass rates," never as accuracy**. Retained safeguards, all cheap: the judge stays blind to experimental condition; SQL-route references are execution-grounded (the gold query ran), so that entire route is anchored by construction; pure-SQL cells skip the judge entirely (execution accuracy IS the metric); surprising verdicts get eyeballed ad hoc when reading results — a habit, not a protocol. Named limitation: generator (Haiku), judge (Sonnet), and reference author (Opus) are separate models with separate roles but all Anthropic — the vendor-monoculture caveat is disclosed, not measured. v3's full RQ5 text lives in git history; revivable with a hand-graded sample (see §10).

**RQ architecture (v4):** RQ2 builds the instrument · RQ1 asks the core question · RQ4 tests runtime composition. (RQ3/RQ5 scratched — tombstones above.)

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
- **L3** — multi-join, aggregation+ranking, or disambiguation trap. *Test: ≥2 JOINs, or GROUP BY+ranking, or a near-miss column trap.* **Conditional cell:** pilot SQL smoke test decides whether L3 is live; with Haiku it is expected live — the smoke test now guards against harness bugs, not model capability.

**Vector route:**
- **L1** — single-project evidence. *Test: |gold_project_ids| = 1.*
- **L2** — 2–4 projects. *Test: |gold_project_ids| ∈ [2,4].*
- **L3** — 5+ projects (RQ2's recall stress cell; RQ1's force-vector-craters cell). *Test: |gold_project_ids| ≥ 5.* **Capped at ~7 questions** — most expensive tier per question under pooled verification.

**Hybrid route** (single ladder by what the filter does to the evidence problem; survivor counts queryable free at generation):
- **L1** — filter isolates, text answers: constraint narrows to few survivors; answer from one survivor's text.
- **L2** — filter narrows, synthesis across survivors (~5–20 survivors, several texts).
- **L3** — tight filter × wide evidence, or filter-then-compare. **RQ1's contamination-diagnostic cell** — where `rows/chunks passed to gen` earns its keep (v4 expects the bucket near-empty; this cell is where that expectation gets tested hardest).
- *Test: survivors-of-gold-filter × projects the reference draws on.*

### Allocation (~100 questions, RQ-weighted, not uniform)

| | L1 | L2 | L3 | route total |
|---|---|---|---|---|
| SQL | 7 | 11 | 5* | 23 |
| Vector | 12 | 16 | 12 | 40 |
| Hybrid | 6 | 10 | 7 | 23 |
| Ambiguous-route | — spread — | | | 10 |
| Adversarial (zero-match, false-presup., data-absent) | | | | 14 |
| Compositional | | | | 3 |
| **Total** | | | | **~113** |

\* conditional on the pilot smoke test.

Skew rationale: L2 is modal everywhere (realistic + router-fair). **L1-SQL and L1-vector stay fat — they are RQ1's clean-route cells, where the v4 tie prediction is tested; under-populating them makes a tie indistinguishable from noise.** Adversarial+ambiguous do double duty (route-quality analysis + refusal-overlay cells). With a capable generator, the trap/value-grounded/L3/adversarial cells carry the discrimination — weight authoring effort there.

**v4 rebalance — RESOLVED (2026-07-23, before any cell filled past v3 counts):** the modest shift toward the discriminating cells is adopted; the table above is the binding allocation. Deltas vs v3 (in git history): SQL L1 8→7, L2 10→11 (the extra is value-grounded), L3 4→5 (the extra is a trap); vector L1 8→7; hybrid L1 7→6; adversarial 12→14 (~4–5 per subtype). Rationale: Haiku is expected near ceiling on clean cells, so marginal L1 questions carry little information, while trap/value-grounded/adversarial are where H1 predicts any real gap AND are the cheapest cells to author (execution-verified, no pooling). Clean-cell aggregate drops only 36→35, so the tie stays measurable at the route/tier level where magnitude claims live. No further stripping of L1 — beyond this, a tie becomes indistinguishable from noise.

**Vector raised to 40 - RESOLVED (2026-07-28, user's decision, at vector 24 filled):** the vector route goes 24 -> 40, split 12 / 16 / 12 across L1 / L2 / L3 - the v4 proportions held and scaled, so the L2-modal shape and the fat L1 that RQ1's tie prediction needs both survive. Reason: vector is the route the retrieval study actually measures - the four conditions are only separable on topical questions, and 24 was sized for a bank that would carry its weight in SQL and hybrid. Bank total ~97 -> ~113. Nothing else in the table moves.

**term_style balance (added 2026-07-23):** RQ2's crossover table needs both legs — aim ~50/50 exact-term/paraphrase WITHIN vector and WITHIN hybrid, tracked in the drafting gap report; a drift past ~60/40 in either route gets corrected by the next questions authored, never by relabeling.

**Granularity honesty:** per-cell differences under ~15 points are unresolvable; the route×complexity table *locates* effects and checks predicted signs; magnitude claims only at route/tier aggregates (n≈22–25). Never narrate single-question swings.

Judged subset: **~35** (down from 40–50), stratified across routes.
Topical (projectID-labeled) subset: **~50**, pooling as above; 40 is the floor.
SQL gold answers: **~30** (feeds Study 0.5 and force-SQL cells). Arithmetic fixed 2026-07-23: the SQL route supplies 23; the remainder comes from ambiguous questions whose `acceptable_routes` include sql — those are authored WITH an executed `gold_sql` (they need one anyway to define the sql-acceptable answer), bringing the pool to ~30.

## 2. Question generation pipeline

1. **Corpus exploration first** → query-verified "what's in this database" doc (sibling to schema_docs): topic spread, funding percentiles, per-country/programme distributions, report-summary coverage, genuine absences (zero-match material), fields present in both structured columns AND free text (where routing is genuinely hard). **Doubles as the value-description source for the SQL prompt (Study 0.5).**
2. **Category spec from that exploration**, aligned to literature vocabulary: TAG-Bench query types; BIRD difficulty **noting our tiers deliberately fold BIRD's four dimensions into three levels with ambiguity as a separate axis**; HotpotQA-lineage hop types; SUQL/HybridQA Type I–VI as the compositional vocabulary; **STaRK's construction pipeline as the synthesis-method precedent.**
3. **Generation skill + MCP** on the real database → Opus generates per category, grounded in real data. STaRK's pattern adapted: sample structured slice → extract textual properties from a gold project → compose a question requiring both → **multi-LLM verification of which other projects also satisfy it** (the mechanism for honest pooled `gold_project_ids`).
4. **Adversarial analyze skill** reviews generated questions against gold evidence.
5. **Verification, evidence-gated at authoring time (v4):** every question passes through the drafting skills — grounding queries against the real DB, gold executed in-pass, mandatory reviewer checklist, explicit per-question confirm before append. This replaces the post-hoc random-20% human pass, which is dropped with manual review; the per-question confirm at authoring time is the human gate. **Amendment (2026-07-23, batch drafting):** for questions authored via `/draft-batch`, the human gate moves — same evidence discipline (the drafting skills run unchanged in orchestrated mode, plus a mandatory adversarial `question-reviewer` pass per draft), but the confirm is batched: drafts are staged to `eval/drafts/` with full gold evidence and drafting history in a review report, and enter the bank only through the user's ticked APPROVE boxes executed by `promote-drafts` (deterministic, re-validates before appending). Interactive drafting keeps the per-question confirm. Details and bounds in working-plan.md Step 3.
6. **Hand-write what generators won't produce:** ambiguous-route, underspecified, zero-match, false-presupposition, compositional.
7. Track and report rejection/correction rates, with the three-point literature comparison (SUQL 23.6% gold errors; STaRK 86.6–98.9% verification; BIRD's double-blind still imperfect).

## 3. Sequencing — the two-week plan

**Scope decisions (explicit, so mid-crunch doesn't make them implicitly):**
- **CUT:** RQ3 and RQ5 (v4 — see tombstones); all §5C deferred experiments (source-filtering, router prompt iterations, thinking-budget); chunk_target sweep (already cut, prior stands). H4c no longer exists as a separate item — its prediction became v4's H4b.
- **SHRUNK:** bank 120–150 → ~100; judged subset 40–50 → ~35.
- **REVIVABLE afterward (parking lot):** RQ3 (needs a weak generator condition) · RQ5 (needs a hand-graded sample) · §5C experiments · mini-SUQL hand-parsed cell (~10 questions) · bank v1.1 expansion.
- **Drop-order under schedule pressure (pre-decided):** compositional cells (near-free, always run) → full agentic H4b.

**Day plan:**
- **d1–2 — Pilot** (in progress): ~12–15 questions on the dev slice, every pipeline stage incl. judge; **now includes the SQL smoke test** — 2–3 SQL questions through the path, failure modes checked against BIRD's taxonomy (expect schema-linking/value errors, ~0% syntax; surprise = model weakness signal). Smoke test decides the L3-SQL cell. Pilot fixes land in schema/scripts/skill, not memory. **Router prompt frozen at pilot end** (§Freeze).
- **d3 — Corpus exploration + category spec.**
- **d4–6 — Bank v1.0 build** (~100 questions, labels per §1, verification per §2).
- **d7 — Study 0.5: SQL path validation.** The ~30 gold-SQL questions through the SQL path alone, execution accuracy. Then **one pre-registered BIRD-endorsed fix, applied once:** value descriptions (enum meanings, formats, code conventions from the exploration doc) added to the SQL generation prompt. Before/after on the same 30. That is the entire SQL tuning budget — no iteration loop. Acceptable after (≥60–70% on L1–L2) → freeze and proceed. Catastrophic after → pre-registered scope decision point: RQ1 caveat or SQL-tier simplification. **v4 expectation:** Haiku is likely near-ceiling on L1–L2 *before* the fix, making the before/after a null — that's fine; the gate is a sanity check, not a study, and the interesting SQL failures are expected in the trap/value-grounded cells. *Write-up framing: both paths receive one bounded validation pass before the frozen stack — retrieval via the four-condition ladder, SQL via an execution-accuracy gate with one literature-endorsed intervention. The asymmetry (comparative study vs. validation gate) is stated once.*
- **d8 — Study 1 (RQ2):** retrieval ladder on the topical subset, programmatic metrics, per-category crossover via `term_style`. **Freeze the winner** — the single coupling point. Full 188k index built before this (coverage matters from the first real measurement).
- **d9–10 — References (v4):** Opus writes references from gold evidence via the drafting/reference skills (SQL references are the executed gold results, free). Judge is fixed — Sonnet, frozen prompt + thresholds — no selection study.
- **d11 — Study 2 (RQ1):** full bank, all four static conditions, frozen stack.
- **d12 — Results assembly + surprising-verdict pass:** skim judge verdicts that look wrong while reading results (a habit, not a grading protocol); anything systematic → note as a judge limitation.
- **d13 — Agentic condition (RQ4, H4a/H4b).**
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
| Agentic scaffold | after the agent's pilot | Frozen thereafter so later runs stay comparable (and protects RQ3 revival if a second generator is ever added). |
| Bank v1.0 | d6 | Append-only thereafter; expansion = v1.1+ with baseline re-runs. |

## 4. Reference answers (judged subset)

- SQL questions: reference = computed query result. Free.
- Vector/hybrid: **Opus writes the reference from gold evidence only** (via gold_project_ids + MCP) — never from system retrieval; measuring stick independent of the thing measured.
- References verified at authoring time through the drafting skills (execution-grounded where possible); the post-hoc human-verify pass is dropped (v4).
- Judge instruction: reference = "key facts that should appear," not "the only acceptable answer." Score coverage + absence-of-unsupported-claims, not textual similarity.
- Role separation (v4): **Opus writes references, Haiku generates, Sonnet judges** — no model wears two hats. All three are Anthropic models; the vendor-monoculture caveat is a named limitation.

## 5. Experiments

**A. Retrieval ladder (RQ2, 4 conditions):** dense-only → BM25-only (DuckDB FTS) → RRF fusion → fusion + cross-encoder rerank. Predictions in RQ2. Check reranker GGUF runs on llama-server **before** designing around it; CPU cross-encoder over top-50 is the fallback.

**0.5. SQL path validation:** see d7. One measurement, one intervention, one gate.

**B. Routing ablation (RQ1, rungs 1–3):** force-all-SQL vs force-all-vector vs router vs always-hybrid, per route × complexity, frozen stack.

**Pre-registered prediction set for B (duplicated in predictions.md):**
- Router ≈ ties always-hybrid on accuracy in clean-route L1–L2 cells; router wins clearly on latency/tokens/calls (v4 H1). Any accuracy gap concentrates in trap/value-grounded/adversarial cells.
- Latency increases monotonically up the ladder.
- Force-vector craters on L3-vector/aggregation-flavored questions (TAG's mechanism); not cratering = surprise.
- SQL failures dominated by schema-linking and value errors, ~0% syntax (BIRD's taxonomy); overall SQL accuracy near-ceiling on L1–L2 (v4, capable generator) — discrimination expected from traps and value-grounding.
- Hybrid failure bucket includes `retrieved-but-unsatisfied` (SUQL's parking example) and `synthesis-contaminated` (H1's mechanism).
- Context-overflow risk: trace field `rows/chunks passed to gen` (split: passed vs. plausibly relevant where judgeable).
- Rerank (if in frozen stack) lifted precision-at-top more than recall (STaRK's shape, checked in Study 1).
- Dense-only overranked on metadata-keyword matches (STaRK Fig. 6) — conditional on the chunk-metadata decision.

**C. (Two-week scope: empty.)** Parking lot: source-filtering; router prompt iteration from misroute few-shots; thinking-budget toggle. First revived candidate if the SQL bucket fails post-gate: further value-grounding work (BIRD-endorsed).

**E. Agentic condition (RQ4, H4a/H4b):** static router vs agentic orchestration, frozen stack, last. Failure taxonomy, edge policies, scaffold freeze, latency/call-count columns per RQ4.

**D. Optimization loop discipline:** baseline → failure analysis from traces (taxonomy above) → targeted change per biggest bucket → re-measure. Per-route metrics, at least one negative result, never tune on the eval set.

## 6. Judging (operational detail; v4 — judge fixed, unvalidated)

- **Judge = Sonnet via `claude -p` on Max subscription.** Pin full model string, `--output-format json`, log model version per trace, version the judge prompt. Transport = one function (billing change → API swap, ~€3).
- Judge fixed by decision, not selection (RQ5 scratched). **Unvalidated against human grades — all judged results are "Sonnet-judged pass rates," never "accuracy."** Frozen prompt + thresholds before Study 2; no post-hoc tuning.
- Role separation: Sonnet judges answers Haiku generated against references Opus wrote — no model grades its own output. Same-vendor caveat disclosed (§4).
- Judge **blind to experimental condition**; evidence-only inputs; structured rubric; JSON output; per-question reasoning retained.
- Route-aware rubrics: pure-SQL skips the judge (execution accuracy IS end-to-end); zero-match answers correct when they say so; citation-aptness claim-vs-cited-chunk at judging time; compositional/ranking scored with partial credit.
- **Anchors in place of validation:** SQL-route references are execution-grounded (hard anchor by construction); surprising verdicts eyeballed ad hoc while reading results — systematic patterns noted as limitations, not silently absorbed.

## 7. Compute & cost

- Generation (router/SQL/synthesis) = **Haiku via `claude -p`** on the Max subscription (≈€0 marginal, bounded by Max usage limits — batch runs sized accordingly). Embeddings, reranker, index builds, retrieval ladder: **local** llama-servers. Agentic runs multiply call-count — report the multiplier as a result, not an apology.
- Judge (Sonnet) + reference authoring (Opus): Max subscription (≈€0 marginal).
- External API spend: **~€0** in scope; parking-lot revivals (RQ3 weak-generator condition, A100 rerun) carry their own budgets if ever run.
- Speed numbers never reported from unverified/rented hardware without saying so; cloud-model latency numbers reported as measured through `claude -p`, transport overhead disclosed.

## 8. Write-up commitments

- Predictions-then-results throughout; `predictions.md` commit hash cited.
- Headline: end-to-end per route × complexity, **organized as the orchestration ladder**, with latency/call-count columns for every rung (VES lineage: BIRD first scored efficiency beside accuracy). SUQL named as the off-axis compile-time alternative.
- Positioning per §0b; framing: Study 1 instrumentation, Study 0.5 validation gate, Study 2 + agentic = contribution. One-sentence limitations: routing conclusions conditional on the frozen stack (the deployable configuration, hence defensible); SQL path received a gate not a ladder; authored questions vs real user idiom (STaRK's finding); **unvalidated LLM judge — Sonnet-judged pass rates, not accuracy**; **same-vendor generator/judge/reference-author (all Anthropic), roles separated but family shared**; small-n granularity; compositional cells are n≈3 diagnostics; world-knowledge queries scoped out; single generator — no capability-strength claims (RQ3 scratched).
- Report: label rejection/correction rates (three-point literature comparison), misroute rate beside RQ1, negative results, the always-hybrid comparison, latency/token/call columns for every rung.
- Candidate public posts: the routing ablation framed as router-vs-SUQL-position ("does routing still matter when the generator is capable?"); the retrieval ladder; "what building ground truth with LLM assistance actually costs"; the drafting-skill recipe (execution-verified benchmark authoring with an LLM).

## 9. Pilot suite — definition of done (updated)

~12–15 questions on the dev slice: 2–3 per route incl. 1 ambiguous, 1 zero-match, 1 underspecified, **1 compositional (traced through static routes to confirm it fails there for the right reason), 2–3 SQL smoke-test questions with BIRD-taxonomy failure coding**; ≥1 L2+ per route; 2–3 hand-written references.
Done when: bank schema holds every §1 field (incl. `term_style`, `compositional`) · every metric script runs and emits numbers · router/SQL/retrieval/judged paths exercised · judge returns valid rubric JSON for every judged question · traces capture model versions, prompt versions, route decisions, **rows/chunks-passed-to-gen** · router prompt frozen and versioned · one deliberately-broken question fails loudly · **SQL smoke test verdict recorded (L3-SQL cell: live | zero-cell).**
Nothing from the pilot counts as a result.

## 10. Expansion path (directional study → masters thesis)

The study is a directional learning study; bank v1.0 results are framed as such. If it is ever grown toward a thesis or portfolio piece, in priority order:

1. **Bank v1.1+:** expand to ~200–300 questions (append-only, baselines re-run per versioning rule); optionally a second corpus to show findings aren't CORDIS artifacts. Biggest gap, most mechanical to close — the pipeline makes this labeling hours, not new design.
2. **Revive RQ3:** add a genuinely weak generator condition (local small model or a weak API tier) and re-run the pre-committed subset — turns the scratched interaction back into a live question, and adding a mid-tier makes it a curve.
3. **Revive RQ5:** hand-grade a stratified sample and report judge agreement with a self-consistency ceiling — converts "Sonnet-judged pass rates" back into defensible accuracy claims. A second annotator on ~10 overlapping questions upgrades it further.
4. **Run the parked tail:** §5C experiments, mini-SUQL hand-parsed cell.

Everything in the two-week plan is already thesis-shaped; this section is the proposal skeleton.