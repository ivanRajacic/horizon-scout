# Drafting pipeline - cost & quality audit

*ARCHIVED 2026-07-26. Every proposal here was decided and implemented (P1-P5 closed; see the Status sections in `working-plan.md`, 2026-07-24 through 2026-07-25). The successor doc is `optimization/README.md`. Kept as the record of the decisions and their rationale.*

*Opened 2026-07-24, triggered by the first `/question-orchestrator` pilot run. Working doc, not a locked plan - the optimizations below are PROPOSALS pending Ivan's decision.*

## Why this exists

The first `/question-orchestrator` run (5 ladder questions: vector L2/L3, hybrid L1/L2/L3) consumed **~70% of the 5-hour usage window** and after **~37 minutes had produced zero committed output**. That is far too much spend for the gain, and we do not yet know whether the drafted questions are even good. Before running `/question-orchestrator` again - and certainly before the ~97-question Step-3 bank build - the three pipeline components need auditing and optimizing, plus a quality check on what the pipeline actually produces.

## Evidence from the pilot run (measured, not guessed)

- **~153 MCP calls in ~37 min, 0 output files.** The batch writes its two files only at the very end (skill step 6), after every slot resolves - so it is **all-or-nothing**: a kill or crash mid-run loses every completed question (nothing is checkpointed to disk).
- **1 of 5 candidates failed at birth.** `hyb-02` (candidate `hybrid-08`, musicology x MSCA-IF) made 6 SQL calls, never ran a `search_corpus`, and stopped - a `DRAFT-FAILED`. The orchestrator correctly fell back to the slot's spare (`hybrid-03`, structural-health-monitoring x ES), which **doubles that slot's cost** (a second drafter + reviewer).
- **Reranker latency = ~1.2 s per `search_corpus` call** (`RERANK_DEPTH=50`); the other three conditions cost ~10 ms combined (dense embed ~8 ms, lexical/RRF negligible). But each question fires only ~2-4 `search_corpus` calls, so **retrieval is NOT the cost driver.**
- **The real cost driver is model spend:** Opus reasoning per step across many tool calls, and - the single biggest leak - the **reviewer subagents running at HIGH reasoning effort**.

## The three components to audit

### 1. Orchestrator - `.claude/skills/question-orchestrator/SKILL.md`
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
- **P2. Incremental checkpointing in `/question-orchestrator`** - stop losing everything on a kill; enables safe early-stop.
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

Deferred, NOT done: **P2** (incremental checkpointing in `/question-orchestrator`) and **P5** (candidate pre-vetting), plus the orchestrator's own high-effort context bloat - left for a later pass; `question-orchestrator/SKILL.md`, `promote.py`, and `bank.py` were untouched.

## Decisions taken (2026-07-24) - review-loop re-architecture, IMPLEMENTED

Second optimization pass, resolved with Ivan and applied to the prompt assets (markdown only; no code). Constraint held: same safety, less spend. This addresses the reviewer's *work* (not just its effort) and the review *loop*, and moves optimization-track items 1 (reviewer) and 2 (orchestrator/drafter) together - a lean reviewer without a downstream judge would make over-rectification worse.

The structural fix: the reviewer used to do two jobs (attack AND self-adjudicate) and the orchestrator did not judge at all - `/question-orchestrator` step 4.3 forced a rectification round on any FLAWED/BROKEN, even though FLAWED = "the question stands". So a valid question could cost a whole extra drafter+reviewer pass over a non-fatal objection.

- **Lean adversary** (`review-question/SKILL.md`, `question-reviewer.md`): severity collapsed to TWO buckets - FATAL (invalid / mislabeled / non-discriminating; the only thing that can trigger a redraft) and MINOR (everything else, incl. taste). Deleted the FATAL/MAJOR/NOTE taxonomy, the "balanced not maximal" prose, the "what is never a finding" section, and the standalone discrimination-power audit (its "too-easy / could be harder" calibration was the nitpick engine; genuine degeneracy folds into FATAL). The ~24-item catalog distilled to a route-specific FATAL-class core (the checks that caught the pilot defects: gold-alive, reference-truth, gold-satisfies, missed-gold, filter-rerun/filter-matters, ADV absence) + a 3-probe discretionary budget. The one hard rule kept: every finding cites executed evidence.
- **Recoverability is the adversary's call.** Each FATAL is classified RECOVERABLE (with a concrete fix direction) or DEAD (unsalvageable - e.g. hyb-02's user-inexpressible filter). Verdict vocabulary is now SOUND | FATAL-RECOVERABLE | FATAL-DEAD. The adversary just did the deep analysis, so it is best placed to say "don't bother" - this is what kills doomed loops.
- **Orchestrator = judge** (`question-orchestrator/SKILL.md` step 4): no longer a mechanical verdict->action map. SOUND/MINOR-only -> accept + record MINOR flags in the report (never redraft on taste). FATAL-DEAD -> abandon candidate, pull the spare. FATAL-RECOVERABLE -> ONE targeted fix round; if it does not reach SOUND, abandon to the spare rather than grinding. Plus a tight evidence-based override valve (accept over a FATAL the drafter's own evidence plainly refutes) so the adversary does not automatically win. The judge weighs the two agents' outputs; it never runs its own MCP investigation.
- **One round, then switch candidates** (supersedes the old "1 rectification round then REJECTED-BY-REVIEWER-and-stop"). The recoverable/dead split already separates fixable-draft from unsuitable-candidate; the spare candidate (abundant by design in `corpus_profile.md`, 2-3x allocation) is the real recovery mechanism and hard-bounds worst-case time. Today's spare-candidate fallback (which fired ONLY on DRAFT-FAILED) now also fires on DEAD and on a failed fix. Two candidate attempts per slot, one fix round each. Chosen over a 3-round cap because rounds 2-3 are where churn lives.
- **Drafter** (`question-drafter.md`): added an up-front topic-fit sanity check (reject-at-birth surfaced by a couple of cheap scoping queries BEFORE a full grounding pass - the musicology hyb-02 lesson, partially covering P5), and changed rectification to a FAST TARGETED fix (fix only what the finding names, re-verify only invalidated steps + touched checklist items - reverses the prior "re-run the full checklist").
- **Taste** (`review-question` + the three `draft-*-question` carve-out notes): kept as MINOR flags read off evidence already in hand - NO-TELEGRAPH, GENERIC-FACT, NEAR-DUPLICATE. NATURAL-PHRASING dropped entirely (pure phrasing nitpick, never caught a validity defect, nobody runs it now). No FAIL-gate moved; vector's identify-leak stays a drafter FAIL. Ivan keeps the promote-time veto via the report's recorded MINOR flags.

Files changed: `.claude/skills/review-question/SKILL.md`, `.claude/agents/question-reviewer.md`, `.claude/skills/question-orchestrator/SKILL.md`, `.claude/agents/question-drafter.md`, and the carve-out note in `.claude/skills/draft-{sql,vector,hybrid}-question/SKILL.md`.

Still deferred: **P2** (incremental checkpointing in `/question-orchestrator` - a kill still loses completed slots) and the orchestrator's raw context bloat at high session effort (the "judge over outputs, don't investigate" rule bounds it but does not eliminate it). `question-orchestrator` still writes its two files only at the end.

## Decisions taken (2026-07-24) - orchestrator audit + optimization, IMPLEMENTED

Third pass, resolved with Ivan one finding at a time. Prompt-asset only (all edits in `.claude/skills/question-orchestrator/SKILL.md`); no code - `promote.py`, `bank.py`, the report format, and the `promote-drafts` contract are untouched. This closes the two deferred orchestrator items (P2 + context bloat) and two coherence gaps this audit surfaced. Constraint held: same safety, less spend.

Whole-system coherence verdict first: the orchestrator, drafter, and reviewer gel at every interface (prompt in; package / DRAFT-FAILED out; `DRAFT:` block = RECORD + EVIDENCE to the reviewer; all five reviewer verdicts handled by the judge; warm-drafter one-round rectification matches the drafter contract; MINOR carve-outs consistent across the reviewer and the three drafting skills). Four gaps found and fixed:

- **F1 - servers-down handling (was a real waste bug).** The reviewer's `SKIPPED` had a "stop dispatching topical slots" handler; the drafter's identical `DRAFT-FAILED - retrieval servers down` did not, so it fell through to the spare-candidate fallback and burned BOTH candidates on the (same) dead servers, for every topical slot, before anything stopped. Fix = **Option C (both)**: a **pre-flight `search_corpus("probe", k=1)` health check** before any drafter is spawned (only when the batch has topical slots), which stops topical dispatch up front; PLUS a **reactive backstop** routing the drafter's servers-down DRAFT-FAILED to the same stop-dispatch path and explicitly NOT consuming the spare (covers servers dying mid-run, which the pre-flight cannot catch). Framed as an environment health check, consistent with "orchestrate, don't author".
- **F2 - P2 checkpointing, via a working journal (DONE).** Added a **third persistent file**: an append-only JSONL working journal in the output dir, written at every slot transition (raw draft on return; reviewer verdict + finding; rectified draft; final disposition). **Never validated mid-run** by design; **validation stays at the end** on accepted records only, then the two canonical outputs are produced from the journal's accepted slots (format unchanged, so `promote.py` untouched). **No automatic resume logic** - resume is a manual op (hand the journal to an agent); Ivan's call, to avoid persisting hidden in-run state. Supersedes the earlier "incremental append to the canonical files" sketch: the journal is a cleaner crash-recovery layer that leaves the canonical outputs end-of-run and validated.
- **F3 - context bloat (Option C).** Closed-slot discipline (once journaled, do not re-quote/re-reason over a slot's evidence; reassemble the report by reading the journal) + a launch-effort recommendation (run the orchestrator session at low/medium effort). Honest ceiling recorded: the one-time receipt of each drafter package is unavoidable while drafters stay read-only and the report restates evidence in full - this bounds bloat, does not remove it.
- **F4 - override-accept traceability (Option A).** The judge's evidence-based override valve now sets `reviewer_override: true` on the accepted RECORD (verified schema-safe and tallied in `bank.py`), so an overridden question is marked in the promoted bank exactly as the interactive "confirm anyway" path marks it - closing the trace gap. One narrow carve-out to the "byte-identical RECORD" standing rule; the override rationale stays in the report (parity with interactive).

Standing-rule edits: "Two files, ever" -> "two canonical outputs + one working journal"; the byte-identical rule gained the single `reviewer_override` carve-out; the opening paragraph now says three files; the report Tally line gained a `blocked` count.

## Decisions taken (2026-07-25) - four nodes, typed state, deterministic gates, IMPLEMENTED

Fourth pass, and the first to touch code after three prompt-asset-only passes. **Nothing was executed** - the changes were authored and left for Ivan to run (see "Handoff" below); `promote.py` and the `promote-drafts` report contract are untouched.

### Why: two conflicts of interest and a pile of hand-done mechanical work

The previous design put two jobs in each of two nodes:

1. **The reviewer both attacked and ruled.** It classified each FATAL `RECOVERABLE` or `DEAD`, and `DEAD` was binding - the orchestrator abandoned the candidate with no appeal. The adversary had unilateral kill power, so its findings were never actually weighed.
2. **The orchestrator both judged and paid.** It decided accept / fix / abandon, while every FATAL it upheld cost it a round, a spare candidate, or a FAILED slot against the quota. Accepting was cheap; upholding was expensive. Its evidence-based override valve sat exactly on that conflict.

Separately, expensive Opus nodes were doing work with a right answer (does the gold SQL execute and return rows, do the recorded survivors still match), and the orchestrator burned context re-reading every accepted slot's evidence to format a report that is a pure function of data it had already written.

The fix splits authority from execution, moves everything mechanical into code, and types the state that flows between nodes.

### The graph

```
SETUP     [D] gap-report -> [H] pick cells -> [A] orchestrator picks 3 candidates/slot
          -> [D] next-ids -> [D] health probe (topical batches only)

PER SLOT  budget: 3 candidates x (1 draft + 1 fix) = 6 drafter passes max

  [A] DRAFTER -> [D] precheck_record (internal gate) -> [D] validate-record
  -> [A] CRITIC (findings only) -> [A] JUDGE (rules, then ACCEPT|FIX|ABANDON)

CLOSE-OUT [D] write-batch (runs batch-crosscheck) -> [H] tick -> [D] promote-drafts
```

All agent-to-agent edges physically transit the orchestrator as a message bus; that is what keeps the judge warm across a slot's rounds without nesting agents.

### The nodes

- **Shared brief - new versioned prompt asset** (`src/eval/bank_brief.md`, `BANK_BRIEF_VERSION` = `bb1`, hashed by `src/llm.py:fingerprint`, same discipline as `schema_docs.md`). Read by drafter, critic, and judge so their standard cannot drift. Holds: what the bank is (M5's measuring instrument, not a quiz); *a defective question does not produce a wrong answer, it produces a wrong finding in a study*; the four properties of "good" here; the two failure modes that matter (measures nothing / measures the wrong thing); the route/level/subtype reference; the `HIGH|MID|LOW` definitions; and the role boundaries.
- **Critic** (`review-question/SKILL.md` + `question-reviewer.md`, names kept so `/review-question` and `/review-bank` do not ripple). Verdict and the `RECOVERABLE`/`DEAD` classification deleted entirely; fixability survives as an advisory `fix_direction`. This also makes the node **mode-independent** - previously the verdict's *meaning* changed between batch and bank mode, which is why the shared node was fragile. Severity is now `HIGH | MID | LOW`; the middle tier is safe precisely because a judge exists to weigh it (it was the nitpick engine when a mechanical map forced a round on it). The catalog stops being a mandatory procedure and becomes a **labelling vocabulary plus advice on what usually fails**: method free, output typed, tag every finding with a class or `OTHER:<slug>` (a recurring slug tells us the vocabulary needs a new entry). **Two items stay mandatory as bias controls, not defect lookups:** `BLIND-SOLVE` (write your own SQL before reading the gold) and `OWN-WORDING` (search with your own reformulations, never the author's). Budget: 3 attack angles. `SKIPPED` / `REVIEW-FAILED` survive as channel signals under a new `STATUS` line.
- **Judge - new node** (`question-judge.md` + `judge-question/SKILL.md`). Opus/low, read-only, **no MCP tools**: both sides' claims already carry executed evidence, so this is a logic check, not a third investigation. Must rule `UPHELD`/`DISMISSED` with a reason on every `HIGH` and `MID` finding *before* emitting a disposition - that ordering is the anti-cherry-pick mitigation, and it lands in the report for free. Warm across a slot's rounds, sees only its own slot, enforces the stop rules off typed state. Explicit rule: the budget is never an argument for ACCEPT, only for ABANDON.
- **Drafter** (`question-drafter.md`): self-verifies facts, **stops self-adjudicating quality** (that is the judge's job now; paying twice was the duplication). Gated by `precheck_record` - it cannot emit a package until the gate returns `ok`. One fix round per candidate; a named fix that turns out impossible is reported plainly rather than substituted.
- **Orchestrator** (`question-orchestrator/SKILL.md`): Step 4 is pure routing. Three irreducible jobs left - negotiate cells, pick candidates, relay messages. It judges nothing. `reviewer_override` is no longer written in batch mode: with a real judge there is nothing to override (the field stays for the interactive "confirm anyway" path, and `bank.py` still tallies it). Two candidates per slot became **three**; the end-of-batch schema-fix round is deleted, superseded by per-slot `validate-record`. Server-outage handling (pre-flight probe + reactive backstop) stays here and never reaches the judge - an outage is a channel signal, not a quality signal.
- **Route skills** (`draft-{sql,vector,hybrid}-question`): point at the brief, add the precheck gate, drop the checklist items a machine now owns (SQL: `EXECUTED-GOLD`, `PINNED-COLUMNS`, `SUBTYPE-LEGAL`; hybrid: `FILTER-EXECUTED`, `GOLD-WITHIN-SURVIVORS`), and update the carve-out notes to the LOW vocabulary. Interactive mode is otherwise unchanged - there, Ivan is the judge.

### Typed slot state

One append-only JSONL journal, one line per transition, latest line per `question_id` wins; line 0 is a batch header (order, budgets, corpus-profile / schema-docs / bank-brief / index versions and hashes). **The envelope is always valid; `record` is an opaque payload that may be schema-invalid mid-run** - that preserves the never-validated-mid-run rule while giving every node a typed contract. Who reads what: judge gets its own slot only; drafter on a fix gets only the named targets; critic gets `record` + `evidence` + the precheck result and is blind to budget and to prior rounds (so **a re-attack after a fix goes to a FRESH critic**); orchestrator writes every line and reads back only `ACCEPTED` ones.

**Stop rules** (judge-enforced, on typed state): same defect `class` upheld twice on one candidate -> next candidate; same `class` kills two candidates -> fail the slot and flag the cell suspect; 6 passes or 3 candidates exhausted -> fail the slot.

**Concurrency**: 3 drafters + 3 critics + 3 judges. The binding number is **6 MCP-touching agents**, up from today's 5 - which was itself never measured (the pilot ran at 3). Judges touch no MCP tools and are free.

**Model and effort**: every subagent Opus/low (`question-judge` included); the orchestrator session at **medium** (was "low or medium"), never high. Consistent with the recorded rationale that adversarial and judgment value comes from a *separate, independent* node, not from the effort dial.

### Deterministic nodes (the code)

| New | Where | Does |
|---|---|---|
| `precheck_record` | MCP tool, `src/eval/mcp_server.py` | gold SQL executes and is non-empty; `answer_columns` present in the result; every gold project exists and has text; `filter_sql` re-executes to exactly the recorded survivors (enumerable, gold inside); `schema_docs_hash` is live |
| `validate-record` | `src/cli.py` | schema-validates ONE record at slot close, via a new public `validate_record` wrapper in `src/eval/bank.py` |
| `gap-report` | `src/cli.py` | filled / staged / target per cell against the allocation table, parsed LIVE from `horizon-scout.md`; plus subtypes, term_style balance, next free ids |
| `next-ids` | `src/cli.py` + `src/eval/batch.py` | next free `sql-NN` / `vec-NN` / `hyb-NN`, counting bank + every staged draft file |
| `batch-crosscheck` | `src/cli.py` + `src/eval/batch.py` | near-duplicate (token/trigram overlap - **no embedder**, so close-out has no server dependency), gold-set overlap, named-entity and axis collision, spread |
| `write-batch` | `src/cli.py` + `src/eval/batch.py` | journal `ACCEPTED` lines -> `draft-bank-<date>.jsonl` + `draft-report-<date>.md`, format unchanged |

`precheck_record` is an **MCP tool, not a CLI**, because it runs inside the drafter's own loop and the drafter has no shell and must stay read-only by construction. It fits the server's charter exactly (read-only, SQL-guarded, every call logged), and the interactive drafting skills get the same gate free.

The writer closes audit finding **F3** properly rather than bounding it: the orchestrator's last contact with a slot's evidence becomes the relay, and the machine-parsed `Draft-bank-file:` / `Decision: [ ] APPROVE  [ ] REJECT` lines stop being a format a model must reproduce from prose. `batch-crosscheck` catches what no per-slot node can see - the critic's near-duplicate check reads `get_bank_questions`, which returns the *promoted* bank, so parallel slots can converge and nothing notices. Its output is **flags on the report**, never a gate and never a redraft.

### Found and deferred, not fixed

**`/review-bank` is stale.** Its report format still references the deleted `FLAWED` / `BROKEN` verdicts and `MAJOR` / `NOTE` severities, and it now also expects a `VERDICT` line the critic no longer emits. It is a separate orchestrator on a separate trigger (post-promotion audit of `eval/bank.jsonl`), so it was noted, not newly broken, and not bundled into this change. A STALE admonition was added at the top of that file with what updating it involves - including the open question of whether a post-promotion sweep wants a judge at all (it has no drafter to fix anything, so probably not: there, the user is the judge).

### Offline confirmation - RUN 2026-07-25, all green

Everything that can be checked without a live batch was run. Results:

- **Suite: 230 passing** (192 baseline + 38 new). Two things the run caught and that are now fixed:
  - a **test** bug - the multi-violation `validate_record` case set `level: "L9"`, which correctly suppresses the level-dependent ladder rules, so the expected `answer_columns` error never fired. Split into two cases; the suppression behaviour is now asserted on purpose (one loud root cause, not a cascade).
  - a **real gap-report bug** - it counted every record in `eval/drafts/draft-bank-*.jsonl` as "staged", including an already-promoted batch whose draft file stays on disk. vec-04/vec-05/hyb-01/hyb-03 were being double-counted as both filled and pending. Staged now means staged-but-UNPROMOTED (staged records whose id is already in the bank are excluded), with a regression test.
- **`gap-report`** against the live allocation table: bank 17, 4 unpromoted staged, all three routes' targets parsed. Next free ids `sql-11 / vec-06 / hyb-08` - `next-ids` correctly treats the unpromoted hyb-04..07 as taken.
- **`batch-crosscheck`** on the real staged batch: two true-positive `AXIS-COLLISION` flags (two slots on euroSciVoc+country, two on euroSciVoc+fundingScheme - exactly the spread that batch's own report described in prose), no near-duplicate, entity, or gold-overlap false positives. Signal-to-noise looks right on real data.
- **`validate-record`** on all four staged records: clean.
- **`precheck_record` over the whole promoted bank** (17 entries, real DuckDB): **zero substantive failures** - every gold SQL re-executes non-empty, every gold project has text, both hybrid filters still produce their recorded survivors. The only failures are 12 `SCHEMA-DOCS` on entries carrying the `sd1-pilot` hash, which is provenance, not a defect; the tool's docstring now says so explicitly, since it is a gate for records being authored now. Re-run on the four freshest staged records: fully clean, including `filter_sql` re-executing to exactly 7 / 13 / 18 / 15 survivors with gold inside each.
- **Refusals are legible**, not tracebacks: the pre-2026-07-25 journal is rejected by both `write-batch` (untyped envelope, no batch header) and `batch-crosscheck` (a guard added during this pass, so an old journal is not silently mistaken for a staged draft file).

Not covered offline, by nature: the writer against a real journal (no new-format journal exists yet), and everything above the CLI - the four-node loop, the critic's new output contract, and the judge.

### Handoff - Ivan still verifies (needs models or servers)

1. **Restart the session** so the `horizon-draft` MCP server relaunches with `precheck_record` - the drafter's gate silently degrades to "cannot call it" otherwise.
2. **Critic regression check.** `working-plan.md` records that the reviewer caught the `vec-05` completeness defect and both `hyb-02` invalidating defects; the histories are in `eval/drafts/draft-report-2026-07-24.md`. Replay those drafts through the reframed critic. If purpose-driven attack still finds them, the smaller skill wins outright; if one is missed, that specific item earns its way back to mandatory and we know which. This is how the catalog reframe gets settled by measurement instead of argument.
3. **Measured batch, SQL-only first** (needs no llama servers): 2-3 slots at 3/3/3. Record drafter passes per accepted question against the ~70%-of-a-5h-window-for-5-questions baseline above, and confirm the loop terminates on the stop rules rather than the hard cap. Then repeat with topical slots once the embedder (:8080) and reranker (:8082) are up, watching for MCP and rerank contention at 6 MCP-touching agents. The first such run also produces the first new-format journal, which is the only way to exercise `write-batch` end to end.
4. `./.venv/Scripts/python.exe -m src.cli validate-bank` on existing bank + staged records before any promote.

## Next actions

- [x] Let the current run finish; capture `eval/drafts/draft-bank-2026-07-24.jsonl` + report.
- [x] Read the drafted questions - quality verdict = GOOD (see "Quality verdict" above).
- [x] Ivan: APPROVE/REJECT the 4 drafts, then `promote-drafts` the approved ones. (Promoted 2026-07-24; bank at 17.)
- [x] Decide P1-P5 with Ivan, under the "same safety, less spend" constraint. (See "Decisions taken" above; P1 reframed as opus/low, P2/P5 deferred, P4 dropped.)
- [x] Apply agreed changes to drafter + reviewer (k=10, V1-V5). Orchestrator (P2 checkpointing) still pending.
- [x] Review-loop re-architecture: lean adversary + orchestrator-as-judge + one-round-then-spare + drafter topic-fit/fast-fix (see "Decisions taken - review-loop re-architecture" above).
- [x] Four-node re-architecture: split authority (critic reports / judge rules), typed journal state, deterministic gates and generated outputs (2026-07-25; changes authored, NOT run - see "Decisions taken (2026-07-25)").
- [ ] **Ivan:** run the handoff list under "Decisions taken (2026-07-25)" - pytest, the critic regression replay on vec-05 / hyb-02, then a measured SQL-only batch against the ~70%-of-a-5h-window-for-5-questions baseline; then a topical batch at 6 MCP-touching agents.
- [ ] `/review-bank` report format is stale against the critic's new output (noted 2026-07-25, deliberately deferred).
- [x] P2 checkpointing in `/question-orchestrator` - DONE (2026-07-24) via the append-only working journal; see "Decisions taken - orchestrator audit + optimization". Also closed F1 (servers-down), F3 (context bloat), F4 (override stamp).

## Status of the triggering run - COMPLETE

Finished 12:32. Tally: **4 accepted / 1 rejected-by-reviewer (hyb-02) / 0 failed**. `validate-bank` on existing-bank + 4 accepted = OK, 17 questions. Files: `eval/drafts/draft-bank-2026-07-24.jsonl`, `eval/drafts/draft-report-2026-07-24.md`. Not yet promoted (awaiting Ivan's APPROVE/REJECT).
