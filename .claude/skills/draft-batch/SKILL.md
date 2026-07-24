---
name: draft-batch
description: Batch-draft Horizon Scout M5 bank questions toward the allocation quota - gap report first, user picks cells; then one question-drafter subagent per question (Opus, low effort, read-only) follows the route's drafting skill in orchestrated mode, each draft is adversarially reviewed by a question-reviewer in draft mode with bounded rectification, and accepted drafts are staged to eval/drafts/ plus a self-contained human-review report. Never touches eval/bank.jsonl; approved drafts are promoted later via `python -m src.cli promote-drafts`.
argument-hint: [output_dir]
---

# /draft-batch

Fill user-chosen quota cells of the M5 bank with drafted, adversarially reviewed, staged-for-human-review questions.

**Arguments:** $ARGUMENTS
Format: `[output_dir]` - optional directory for the output files (the two canonical outputs plus the working journal). Default: `eval/drafts/`.

This skill orchestrates and formats; it never drafts, never reviews, never edits the bank. Drafting logic lives in the three `draft-*-question` skills (followed by `question-drafter` subagents in orchestrated mode); reviewing logic lives in `review-question` (followed by `question-reviewer` subagents in draft mode). This skill writes THREE files - the two canonical outputs (the staged draft bank and the review report) plus an append-only working journal for crash recovery - and disposable temp files in the session scratchpad. The human gate for these questions is the report review + `promote-drafts`, not a per-question confirm (decision recorded in `working-plan.md`); interactive use of the drafting skills keeps its per-question confirm.

**Run this orchestrator session at low or medium reasoning effort.** Routing, judging over the two agents' outputs, and formatting need no high-effort reasoning; the pilot's biggest cost leak was a high session effort amplifying re-reads of held evidence.

## Procedure

**Working journal (write at every step of Step 4).** At batch start, create an append-only JSONL journal at `<output_dir>/draft-batch-journal-<YYYY-MM-DD>.jsonl`. Every slot state transition in Step 4 appends one line the moment it happens: the raw draft as soon as a drafter returns; the reviewer verdict and (on FATAL) the finding when review completes; the rectified draft when a fix returns; and the final disposition (accepted / failed / blocked). Each line carries the slot id, cell, candidate, current status, and the current content (the raw RECORD, the reviewer verdict + findings, the running history). It is **never validated mid-run** - it may hold half-finished or schema-invalid content by design; its only job is to leave something to work back from if the run is killed or crashes. Latest line per id wins. **Resume is manual** - you (or the user) hand this journal to an agent to continue; this skill adds no automatic resume logic. The journal is a working file, not a canonical output; it lives in the output dir and is disposable after promote.

### 1. Gap report, user picks (plain text)

1. Read `eval/bank.jsonl` and count questions per route x level x subtype. Read the allocation table in `horizon-scout.md` (section "Bank composition", the route x level table) LIVE - never from memory; the rebalance may have changed it.
2. Also scan `eval/drafts/draft-bank-*.jsonl` for staged-but-unpromoted questions and count them separately.
3. Print the gap report: per cell, filled / staged / target. Mark `ambiguous`, ADV-subtype, and `compositional` cells as "interactive only - not draftable by this batch"; their skills exist (`/draft-ambiguous-question`, `/draft-adversarial-question`, `/draft-compositional-question`) but stay outside the batch until the user explicitly flips them in (compositional never - it is interactive-only by design). Note the open v4 rebalance if `horizon-scout.md` still marks it open.
4. Ask the user, in plain text (never the multiple-choice window), which cells and how many questions each. Wait. The user's picks become the batch order; do not exceed any cell's target without the user saying so explicitly.

### 2. Candidates

1. Call `mcp__horizon-draft__get_corpus_profile` for `frontier` plus each needed route section. An `{"error": ...}` result means the profile is unbuilt - STOP and tell the user to run `/explore-corpus` first; this skill does not draft from thin air.
2. For each ordered slot pick one candidate whose `recommend:` matches the cell. **Prefer candidates from buckets the frontier marks `mapped` but not yet `mined`** - those are regions we have deliberately explored and not yet drawn from, which is what keeps the bank spread across the corpus instead of clustered. Break remaining ties on least-covered axes, and skip candidates whose axis values or named entities are already used by bank questions or by candidates picked for this batch. Keep one spare candidate per cell for replacement. Candidates are advisory seeds - the drafters re-verify everything.
3. If a candidate carries a `bucket:` line, pull that bucket's `## Corpus map` entry and pass its `good for:` / `thin for:` / `texture:` lines to the drafter with the candidate block. That is the region knowledge exploration paid for - it tells the drafter what shape of question the region can actually support (and what it cannot) before it starts grounding.

### 3. Pre-assign ids

Next free `sql-NN` / `vec-NN` / `hyb-NN` per route, counting BOTH `eval/bank.jsonl` AND every `eval/drafts/draft-bank-*.jsonl` (staged ids are taken even before promotion). Assign one id per slot up front so parallel drafters never collide. Failed slots leave id gaps - harmless.

### 4. Draft-review pipeline

Process slots with **max 5 subagents in flight in total** (drafters + reviewers combined - the MCP server is one stdio process over a single read-only DuckDB connection and the llama servers are local; more parallelism just queues and risks timeouts).

**Pre-flight health check (before spawning any drafter).** If the batch contains any topical slot (vector, hybrid, or topical ADV - the routes that need the llama-servers), run one `mcp__horizon-draft__search_corpus("probe", k=1)` as an environment health check, not authoring (the same kind of setup MCP call as Step 2's `get_corpus_profile`). If it comes back an error, the retrieval servers are down: do not dispatch any topical slot, mark the topical slots BLOCKED, run only SQL slots, and report the blocked slots to the user. A SQL-only batch skips this probe.

**Journal and close each slot as you go.** Write every transition to the working journal the instant it happens (see the working-journal note under Procedure). Once a slot is journaled, treat it as closed: do not re-quote, re-summarize, or re-reason over its evidence on later turns - reload it from the journal only when assembling the final report.

Per slot:

1. Spawn a `question-drafter` agent. Prompt: the pre-assigned `question_id`, the cell (route/level/subtype, term_style if topical), the candidate block verbatim, and the instruction to follow the route's drafting skill in orchestrated mode.
2. On a returned package (RECORD / CHECKLIST / EVIDENCE / WHY-GOOD / HISTORY): spawn a `question-reviewer` agent in draft mode. Prompt: a `DRAFT:` block containing the RECORD JSON and the EVIDENCE section verbatim.
3. Judge the verdict (you are the judge - the drafter drafts, the reviewer attacks, you decide):
   - **SOUND** (includes MINOR-only) -> accept the draft. Record any MINOR flags in the report; never redraft for a MINOR.
   - **FATAL-RECOVERABLE** -> send the reviewer's FATAL fix directions to the SAME drafter via SendMessage (its context is warm - never respawn for rectification); you may sharpen or reframe the direction as you relay. When the revised package returns, send it to the SAME reviewer via SendMessage for ONE re-review.
     - SOUND -> accept.
     - Still FATAL (recoverable or dead) -> do NOT grind another round; abandon this candidate and go to the spare (item 4). A fix that failed once is a weak bet; a fresh candidate is the better shot.
     You MAY instead override-accept over a FATAL only when the drafter's own executed evidence plainly refutes the finding - record the override and the refuting evidence in the report, AND set `reviewer_override: true` on the accepted RECORD (the one sanctioned edit to a RECORD - see Standing rules). Evidence-based, not a taste call, and never over a DEAD finding.
   - **FATAL-DEAD** -> abandon this candidate immediately and go to the spare (item 4). No rectification round - rounds never fix a dead candidate.
   - **SKIPPED** (reviewer, servers down) or **`DRAFT-FAILED - retrieval servers down`** (drafter) -> a server outage, not a bad candidate: stop dispatching topical slots, mark the remaining topical slots BLOCKED, report which slots are blocked, finish any SQL slots. Do NOT pull the spare (item 4) - a fresh candidate on dead servers is the same bet, not a better one.
   - **REVIEW-FAILED / dead agent** -> retry that agent once, then mark the slot FAILED.
4. **Spare-candidate fallback (one per slot).** Whenever a candidate is abandoned - a `DRAFT-FAILED` from the drafter (EXCEPT `retrieval servers down`, which is an outage handled above, not a candidate failure - it never pulls the spare), a `FATAL-DEAD` verdict, or a `FATAL-RECOVERABLE` whose one fix round did not reach SOUND - try the slot's spare candidate with a FRESH drafter (once). The spare runs the same draft -> review -> at-most-one-fix loop. Spare abandoned too -> mark the slot FAILED with both histories. Never invent a third candidate.

Judge over the two agents' outputs, do not run your own investigation: you weigh the reviewer's FATAL findings against the drafter's evidence and decide accept / rectify-once / abandon - but you never author, never re-review, and never open the MCP tools to form a finding of your own. Your judgment is over what the drafter and reviewer produced, not a third independent analysis (that would just re-buy the cost this design removes).

### 5. Validate the batch

Write existing `eval/bank.jsonl` content + all accepted RECORD lines to a temp file in the session scratchpad and run `./.venv/Scripts/python.exe -m src.cli validate-bank --bank <temp>`. Any violation goes back to a drafter for that slot - the still-warm drafter via SendMessage, or, if it is no longer warm, a fresh drafter seeded from the journal's draft plus the exact validation error (one round, then a re-review of the fixed record - this counts outside the cap because it is a schema fix, not a soundness fix); still invalid -> demote that slot to FAILED. Only schema-clean records are staged.

### 6. Write the two canonical outputs

Assemble both from the journal's accepted slots (read each slot's evidence back from the journal, not from held context):

- `<output_dir>/draft-bank-<YYYY-MM-DD>.jsonl` - one RECORD line per accepted draft, verbatim (with the `reviewer_override: true` stamp where the judge added it per Step 4.3), promotable as-is.
- `<output_dir>/draft-report-<YYYY-MM-DD>.md` - the review report (format below).

The working journal is not a canonical output - leave it in place (disposable after promote).

If either canonical file already exists, STOP and ask before overwriting - it may hold an unpromoted earlier batch (then prefer a `-2` suffix on both names, keeping them paired).

### 7. Close out

Final message: both paths, the tally (accepted / failed / blocked), id gaps left, and the promote command:
`./.venv/Scripts/python.exe -m src.cli promote-drafts <report path>`

## Report format

Self-containment is the point: the user reads this file cold, with no session transcript, and must be able to decide every question without asking for context.

```
# Draft batch - <YYYY-MM-DD>

Draft-bank-file: eval/drafts/draft-bank-<YYYY-MM-DD>.jsonl
Order: <the cells and counts the user asked for>
Corpus profile: <version> <hash> | schema_docs: <version> <hash> | index: <fingerprint or n/a>
Tally: <A> accepted / <F> failed (each with its reason: DEAD, failed-fix, or DRAFT-FAILED) / <B> blocked (servers down)

## Summary

| id | route/level/subtype | candidate topic | review verdict | decision |
|----|---------------------|-----------------|----------------|----------|
(one row per slot; failed/blocked rows say "-" in the decision column)

## <id> - <review verdict>

**Question:** "<full text>"  (route/level/subtype, term_style if any)
**Gold + evidence:** <in full, from the drafter's EVIDENCE section - executed SQL
result tables, or per-gold-project quoted passages, or filter_sql + survivor count
+ quoted passages + discrimination counter-examples>
**Reference answer:** "<verbatim>"
**Why this is a good question:** <the drafter's WHY-GOOD>
**Drafting history:** <the HISTORY bullets, plus reviewer findings and how each
was rectified>

Decision: [ ] APPROVE  [ ] REJECT

## <id> - FAILED

Same section minus the decision line, ending instead with the failure reason
(DEAD, failed-fix, or DRAFT-FAILED) plus the reviewer's surviving FATAL findings
and both candidates' histories - kept, not silently dropped.
```

Formatting rules:

- The `Draft-bank-file:` header and each `Decision: [ ] APPROVE  [ ] REJECT` line are machine-parsed by `promote-drafts` - exact format, never pre-ticked, one per accepted question, none elsewhere.
- Every accepted section restates question, gold, evidence, and reference in full - the reader never opens the jsonl to decide.
- Evidence comes from the drafter's own quoted output; if something load-bearing is missing there, ask that drafter for it via SendMessage rather than reconstructing it yourself.

## Standing rules

- **Two canonical outputs, plus one working journal.** The staged jsonl and the report are the only canonical outputs; the append-only journal is a working file for crash recovery. Never write `eval/bank.jsonl`, never skills, never agents, never the corpus profile.
- **Orchestrate, do not author.** No drafting, no reviewing, no editing of records at aggregation time. Records land in the jsonl byte-identical to the drafter's RECORD line, with exactly ONE sanctioned exception: the judge may set `reviewer_override: true` on a RECORD it override-accepts over a FATAL (Step 4.3).
- **Every slot accounted for.** N ordered slots in, N summary rows out - accepted, failed, or blocked. No silent gaps.
- **Bounded everything.** Max 5 agents in flight; 1 rectification round per candidate, then abandon to the spare - never grind rounds; 1 spare candidate per slot (two candidate attempts total); 1 retry per dead agent; 1 schema-fix round. When a bound is hit, record and move on - never loop.
- **The quota is the user's.** Cells and counts come from the gap-report conversation; never top up beyond the order because a candidate looked promising.
- **Existing output files are never overwritten without asking.**
