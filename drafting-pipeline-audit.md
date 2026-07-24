# Drafting pipeline - cost & quality audit

*Opened 2026-07-24, triggered by the first `/draft-batch` pilot run. Working doc, not a locked plan - the optimizations below are PROPOSALS pending Ivan's decision.*

## Why this exists

The first `/draft-batch` run (5 ladder questions: vector L2/L3, hybrid L1/L2/L3) consumed **~70% of the 5-hour usage window** and after **~37 minutes had produced zero committed output**. That is far too much spend for the gain, and we do not yet know whether the drafted questions are even good. Before running `/draft-batch` again - and certainly before the ~97-question Step-3 bank build - the three pipeline components need auditing and optimizing, plus a quality check on what the pipeline actually produces.

## Evidence from the pilot run (measured, not guessed)

- **~153 MCP calls in ~37 min, 0 output files.** The batch writes its two files only at the very end (skill step 6), after every slot resolves - so it is **all-or-nothing**: a kill or crash mid-run loses every completed question (nothing is checkpointed to disk).
- **1 of 5 candidates failed at birth.** `hyb-02` (candidate `hybrid-08`, musicology x MSCA-IF) made 6 SQL calls, never ran a `search_corpus`, and stopped - a `DRAFT-FAILED`. The orchestrator correctly fell back to the slot's spare (`hybrid-03`, structural-health-monitoring x ES), which **doubles that slot's cost** (a second drafter + reviewer).
- **Reranker latency = ~1.2 s per `search_corpus` call** (`RERANK_DEPTH=50`); the other three conditions cost ~10 ms combined (dense embed ~8 ms, lexical/RRF negligible). But each question fires only ~2-4 `search_corpus` calls, so **retrieval is NOT the cost driver.**
- **The real cost driver is model spend:** Opus reasoning per step across many tool calls, and - the single biggest leak - the **reviewer subagents running at HIGH reasoning effort**.

## The three components to audit

### 1. Orchestrator - `.claude/skills/draft-batch/SKILL.md`
- **All-or-nothing output (step 6).** Both files are written only after every slot resolves; a kill loses all completed work. -> **Add incremental checkpointing**: append each accepted RECORD to the staged jsonl as it completes and grow the report incrementally, so a run can be stopped or resumed without total loss, and early-stop becomes safe.
- **Concurrency capped at 3** by the single read-only-connection MCP server (already logged under "Deferred optimizations" in `working-plan.md`).
- **Context bloat:** the orchestrator holds every subagent's full return package (the EVIDENCE sections quote a lot of project text) and re-reads them each turn - amplified at high session effort.

### 2. Drafter - `.claude/skills/draft-{sql,vector,hybrid}-question/SKILL.md` + `.claude/agents/question-drafter.md`
- Already Opus at **low** effort (good), but many tool calls with Opus reasoning between each -> minutes per question.
- **Birth-failures are expensive.** Musicology burned a whole drafter pass before being rejected. Check whether candidate pre-vetting (in `corpus_profile.md` / the batch's candidate step) is too weak, so drafters discover unsuitability only after a full grounding pass. Tighten upstream vetting so dead candidates are caught before a drafter is spawned.
- **Verification-step necessity:** are all steps (pooled verify + non-embedding completeness sweep + full checklist) warranted at pilot scale, or can some be trimmed/deferred?

### 3. Reviewer - `.claude/skills/review-question/SKILL.md` + `.claude/agents/question-reviewer.md`
- **No `reasoningEffort` override -> inherits the session effort (was HIGH).** Adversarial review at max-effort Opus is the biggest single cost center, and it runs once per question. -> set explicit `low`/`medium`.
- **Doubles the subagent count** (one reviewer per question), plus the extra reviewer on the failed slot's spare.
- **Model = Opus.** Review is closer to *judging* than *authoring*; Sonnet (already the judge model) could do it far cheaper. NOTE: this touches the **locked v4 role-separation** (Opus authors, Sonnet judges, Haiku generates) - needs Ivan's explicit decision, not a silent change.

## Proposed optimizations (prioritized - NOT yet decided)

- **P1. Reviewer effort down (low/medium)** - or launch the batch session at lower effort so reviewers inherit it. Biggest, safest win; no methodology change.
- **P2. Incremental checkpointing in `/draft-batch`** - stop losing everything on a kill; enables safe early-stop.
- **P3. For throwaway pilots, skip the reviewer subagent** - rely on the drafter's mandatory self-checklist, and exercise the reviewer path on only 1-2 questions. Roughly halves the subagent count.
- **P4. Reviewer model = Sonnet** - large structural saving, but requires the role-separation decision.
- **P5. Stronger candidate pre-vetting** so drafters do not waste full passes on birth-failures.

## Quality verdict (2026-07-24) - RESOLVED, and it reshapes the optimization

Read the full report by hand. **The pipeline produces good questions**, and the expensive review step earned its keep:

- **4 accepted are high quality:** evidence-first, execution-verified, honestly labelled, faithful references. vec-04 shows the drafter correcting a wrong seed by reading text (dropped the euroscivoc false-positive BOPNIE, re-grounded to the true gold set). Minor honest NOTES only (vec-04's two fluid projects are same-PI companions; hyb-03's HERO membership is marginal but filter-defined and honestly framed) - none blocking.
- **The reviewer caught 2 real, subtle, benchmark-invalidating defects** that no eyeball pass would catch:
  - vec-05: first draft had a broken/inconsistent completeness rule (gold=11, "LakeMP IN / HypoTRAIN OUT" indefensible) -> BROKEN -> the one rectification round fixed it (narrowed to two crisp strands, gold=10).
  - hyb-02: rejected outright. Primary filter `fundingScheme='MSCA-IF-EF-ST'` is a coding artifact no user could express; the SHM spare defined gold by a hidden, inconsistently-applied euroSciVoc tag while written in paraphrase, so a correct system would be graded wrong. Refused rather than mislabelled.

**Implication for the optimizations:** the review is NOT wasted spend - it prevented two corrupt questions out of five. So P3 (skip the reviewer for pilots) and an aggressive P1 (reviewer at *low* effort) are risky - they'd remove the net that caught exactly these validity failures. Reweight the savings toward: the **orchestrator's** high effort (routing/formatting needs none), **checkpointing** (P2), and **Sonnet-for-review** (P4, still a capable judge) - keep review effort at least **medium**, not low. The cost target is "same safety, less spend," not "less safety."

## Decisions taken (2026-07-24) - IMPLEMENTED

Resolved with Ivan and applied to the prompt assets (markdown only; no code touched). Constraint held: same safety, less spend. All drafter-side changes below are scoped to **orchestrated (batch) mode** unless noted; interactive drafting is unchanged.

- **Reviewer effort = opus/low, matching the drafter** (`.claude/agents/question-reviewer.md` now pins `model: opus` / `reasoningEffort: low`). This **overrides** the "keep review effort at least medium" recommendation in the Quality-verdict section above. Rationale (Ivan): the adversarial value comes from a *separate, independent* agent attacking the draft, not from the effort dial - two separate agents is the net. The pilot's reviewer leak was that it inherited HIGH session effort; pinning low removes the leak without removing the reviewer. Supersedes P4 (Sonnet-for-review), which is not adopted.
- **k=20 -> k=10** in all `search_corpus` calls (vector/hybrid), coupled with promoting the vector L3 completeness sweep from advisory to **mandatory** (the non-embedding sweep, not pool depth, is the real completeness net - vec-05's NaToxAq ranked outside top-20).
- **V1** - adjudicate clear-OUT pooled candidates from the best-chunk text `search_corpus` already returns; full `get_project_text` read only for IN/borderline. Vector Step 3; hybrid S 21-200 only (the S<=20 exhaustive read is untouched).
- **V2** - batch the `get_project_text` reads.
- **V3** - orchestrated mode skips the two `get_corpus_profile` startup calls (the candidate block already carries the section; the ledger is subject-selection only).
- **V4** - orchestrated-mode Step-5 self-checklist skips the pure-judgment polish items (NATURAL-PHRASING, NEAR-DUPLICATE, GENERIC-FACT, general NO-TELEGRAPH); the independent reviewer owns them. All FAIL-gates and the retrievability/term-style diagnostics stay (conservative reading of "lose as little quality as possible").
- **V5 (from Ivan: "topics come from the explore agent")** - two-tier grounding in orchestrated mode: the candidate's executed evidence (SQL + counts + sample_ids, merge-pass spot-checked) is trust-and-confirmed with one drift-check re-execution, while advisory claims (route/level/subtype, term_style, gold membership) are re-verified in full - reading text to catch noisy euroSciVoc tags, pooled/scoped verification, the sweep, level recomputed from evidence. Reconciles the prior contradiction between the skills ("run in full regardless") and `question-drafter.md` ("do not re-derive").

Files changed: `.claude/agents/question-reviewer.md`, `.claude/agents/question-drafter.md`, and the three `draft-{sql,vector,hybrid}-question/SKILL.md`.

Deferred, NOT done: **P2** (incremental checkpointing in `/draft-batch`) and **P5** (candidate pre-vetting), plus the orchestrator's own high-effort context bloat - left for a later pass; `draft-batch/SKILL.md`, `promote.py`, and `bank.py` were untouched.

## Decisions taken (2026-07-24) - review-loop re-architecture, IMPLEMENTED

Second optimization pass, resolved with Ivan and applied to the prompt assets (markdown only; no code). Constraint held: same safety, less spend. This addresses the reviewer's *work* (not just its effort) and the review *loop*, and moves optimization-track items 1 (reviewer) and 2 (orchestrator/drafter) together - a lean reviewer without a downstream judge would make over-rectification worse.

The structural fix: the reviewer used to do two jobs (attack AND self-adjudicate) and the orchestrator did not judge at all - `/draft-batch` step 4.3 forced a rectification round on any FLAWED/BROKEN, even though FLAWED = "the question stands". So a valid question could cost a whole extra drafter+reviewer pass over a non-fatal objection.

- **Lean adversary** (`review-question/SKILL.md`, `question-reviewer.md`): severity collapsed to TWO buckets - FATAL (invalid / mislabeled / non-discriminating; the only thing that can trigger a redraft) and MINOR (everything else, incl. taste). Deleted the FATAL/MAJOR/NOTE taxonomy, the "balanced not maximal" prose, the "what is never a finding" section, and the standalone discrimination-power audit (its "too-easy / could be harder" calibration was the nitpick engine; genuine degeneracy folds into FATAL). The ~24-item catalog distilled to a route-specific FATAL-class core (the checks that caught the pilot defects: gold-alive, reference-truth, gold-satisfies, missed-gold, filter-rerun/filter-matters, ADV absence) + a 3-probe discretionary budget. The one hard rule kept: every finding cites executed evidence.
- **Recoverability is the adversary's call.** Each FATAL is classified RECOVERABLE (with a concrete fix direction) or DEAD (unsalvageable - e.g. hyb-02's user-inexpressible filter). Verdict vocabulary is now SOUND | FATAL-RECOVERABLE | FATAL-DEAD. The adversary just did the deep analysis, so it is best placed to say "don't bother" - this is what kills doomed loops.
- **Orchestrator = judge** (`draft-batch/SKILL.md` step 4): no longer a mechanical verdict->action map. SOUND/MINOR-only -> accept + record MINOR flags in the report (never redraft on taste). FATAL-DEAD -> abandon candidate, pull the spare. FATAL-RECOVERABLE -> ONE targeted fix round; if it does not reach SOUND, abandon to the spare rather than grinding. Plus a tight evidence-based override valve (accept over a FATAL the drafter's own evidence plainly refutes) so the adversary does not automatically win. The judge weighs the two agents' outputs; it never runs its own MCP investigation.
- **One round, then switch candidates** (supersedes the old "1 rectification round then REJECTED-BY-REVIEWER-and-stop"). The recoverable/dead split already separates fixable-draft from unsuitable-candidate; the spare candidate (abundant by design in `corpus_profile.md`, 2-3x allocation) is the real recovery mechanism and hard-bounds worst-case time. Today's spare-candidate fallback (which fired ONLY on DRAFT-FAILED) now also fires on DEAD and on a failed fix. Two candidate attempts per slot, one fix round each. Chosen over a 3-round cap because rounds 2-3 are where churn lives.
- **Drafter** (`question-drafter.md`): added an up-front topic-fit sanity check (reject-at-birth surfaced by a couple of cheap scoping queries BEFORE a full grounding pass - the musicology hyb-02 lesson, partially covering P5), and changed rectification to a FAST TARGETED fix (fix only what the finding names, re-verify only invalidated steps + touched checklist items - reverses the prior "re-run the full checklist").
- **Taste** (`review-question` + the three `draft-*-question` carve-out notes): kept as MINOR flags read off evidence already in hand - NO-TELEGRAPH, GENERIC-FACT, NEAR-DUPLICATE. NATURAL-PHRASING dropped entirely (pure phrasing nitpick, never caught a validity defect, nobody runs it now). No FAIL-gate moved; vector's identify-leak stays a drafter FAIL. Ivan keeps the promote-time veto via the report's recorded MINOR flags.

Files changed: `.claude/skills/review-question/SKILL.md`, `.claude/agents/question-reviewer.md`, `.claude/skills/draft-batch/SKILL.md`, `.claude/agents/question-drafter.md`, and the carve-out note in `.claude/skills/draft-{sql,vector,hybrid}-question/SKILL.md`.

Still deferred: **P2** (incremental checkpointing in `/draft-batch` - a kill still loses completed slots) and the orchestrator's raw context bloat at high session effort (the "judge over outputs, don't investigate" rule bounds it but does not eliminate it). `draft-batch` still writes its two files only at the end.

## Decisions taken (2026-07-24) - orchestrator audit + optimization, IMPLEMENTED

Third pass, resolved with Ivan one finding at a time. Prompt-asset only (all edits in `.claude/skills/draft-batch/SKILL.md`); no code - `promote.py`, `bank.py`, the report format, and the `promote-drafts` contract are untouched. This closes the two deferred orchestrator items (P2 + context bloat) and two coherence gaps this audit surfaced. Constraint held: same safety, less spend.

Whole-system coherence verdict first: the orchestrator, drafter, and reviewer gel at every interface (prompt in; package / DRAFT-FAILED out; `DRAFT:` block = RECORD + EVIDENCE to the reviewer; all five reviewer verdicts handled by the judge; warm-drafter one-round rectification matches the drafter contract; MINOR carve-outs consistent across the reviewer and the three drafting skills). Four gaps found and fixed:

- **F1 - servers-down handling (was a real waste bug).** The reviewer's `SKIPPED` had a "stop dispatching topical slots" handler; the drafter's identical `DRAFT-FAILED - retrieval servers down` did not, so it fell through to the spare-candidate fallback and burned BOTH candidates on the (same) dead servers, for every topical slot, before anything stopped. Fix = **Option C (both)**: a **pre-flight `search_corpus("probe", k=1)` health check** before any drafter is spawned (only when the batch has topical slots), which stops topical dispatch up front; PLUS a **reactive backstop** routing the drafter's servers-down DRAFT-FAILED to the same stop-dispatch path and explicitly NOT consuming the spare (covers servers dying mid-run, which the pre-flight cannot catch). Framed as an environment health check, consistent with "orchestrate, don't author".
- **F2 - P2 checkpointing, via a working journal (DONE).** Added a **third persistent file**: an append-only JSONL working journal in the output dir, written at every slot transition (raw draft on return; reviewer verdict + finding; rectified draft; final disposition). **Never validated mid-run** by design; **validation stays at the end** on accepted records only, then the two canonical outputs are produced from the journal's accepted slots (format unchanged, so `promote.py` untouched). **No automatic resume logic** - resume is a manual op (hand the journal to an agent); Ivan's call, to avoid persisting hidden in-run state. Supersedes the earlier "incremental append to the canonical files" sketch: the journal is a cleaner crash-recovery layer that leaves the canonical outputs end-of-run and validated.
- **F3 - context bloat (Option C).** Closed-slot discipline (once journaled, do not re-quote/re-reason over a slot's evidence; reassemble the report by reading the journal) + a launch-effort recommendation (run the orchestrator session at low/medium effort). Honest ceiling recorded: the one-time receipt of each drafter package is unavoidable while drafters stay read-only and the report restates evidence in full - this bounds bloat, does not remove it.
- **F4 - override-accept traceability (Option A).** The judge's evidence-based override valve now sets `reviewer_override: true` on the accepted RECORD (verified schema-safe and tallied in `bank.py`), so an overridden question is marked in the promoted bank exactly as the interactive "confirm anyway" path marks it - closing the trace gap. One narrow carve-out to the "byte-identical RECORD" standing rule; the override rationale stays in the report (parity with interactive).

Standing-rule edits: "Two files, ever" -> "two canonical outputs + one working journal"; the byte-identical rule gained the single `reviewer_override` carve-out; the opening paragraph now says three files; the report Tally line gained a `blocked` count.

## Next actions

- [x] Let the current run finish; capture `eval/drafts/draft-bank-2026-07-24.jsonl` + report.
- [x] Read the drafted questions - quality verdict = GOOD (see "Quality verdict" above).
- [x] Ivan: APPROVE/REJECT the 4 drafts, then `promote-drafts` the approved ones. (Promoted 2026-07-24; bank at 17.)
- [x] Decide P1-P5 with Ivan, under the "same safety, less spend" constraint. (See "Decisions taken" above; P1 reframed as opus/low, P2/P5 deferred, P4 dropped.)
- [x] Apply agreed changes to drafter + reviewer (k=10, V1-V5). Orchestrator (P2 checkpointing) still pending.
- [x] Review-loop re-architecture: lean adversary + orchestrator-as-judge + one-round-then-spare + drafter topic-fit/fast-fix (see "Decisions taken - review-loop re-architecture" above).
- [ ] Re-run a small batch and measure the new cost against this baseline (~70% of a 5-hour window for 5 questions); confirm the lean reviewer still flags a genuine defect and the loop terminates fast.
- [x] P2 checkpointing in `/draft-batch` - DONE (2026-07-24) via the append-only working journal; see "Decisions taken - orchestrator audit + optimization". Also closed F1 (servers-down), F3 (context bloat), F4 (override stamp).

## Status of the triggering run - COMPLETE

Finished 12:32. Tally: **4 accepted / 1 rejected-by-reviewer (hyb-02) / 0 failed**. `validate-bank` on existing-bank + 4 accepted = OK, 17 questions. Files: `eval/drafts/draft-bank-2026-07-24.jsonl`, `eval/drafts/draft-report-2026-07-24.md`. Not yet promoted (awaiting Ivan's APPROVE/REJECT).
