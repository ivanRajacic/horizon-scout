---
name: review-bank
description: Sweep the entire Horizon Scout M5 bank through the /review-question adversarial reviewer - one question-reviewer subagent per question, max 3 concurrent - and write the collected verdicts and self-contained fix recommendations to a report file the user reviews by hand, approving or rejecting each finding. Writes exactly one file (the report); never touches eval/bank.jsonl.
argument-hint: [output_path]
---

# /review-bank

Run the adversarial reviewer over every question in `eval/bank.jsonl` and write the results to one report file for manual approve/reject review.

**Arguments:** $ARGUMENTS
Format: `[output_path]` - optional path for the report. Default: `eval/review/bank-review-<YYYY-MM-DD>.md` (today's date).

This skill orchestrates and formats; it does not review. The reviewing logic lives entirely in `.claude/skills/review-question/SKILL.md`, executed by `question-reviewer` subagents - one per question, each in its own context, because a full attack catalog per question is too heavy to share one context across the bank. This skill writes EXACTLY ONE file: the report. It never edits `eval/bank.jsonl`, the drafting skills, the review skill, or anything else. Approved fixes are applied later by the user through the drafting skills - the bank is never hand-edited.

## Procedure

1. **Load the bank.** Read `eval/bank.jsonl`. List every question_id with route/level/subtype. Duplicate ids: report them and stop - the sweep does not run against a bank the validator would reject.
2. **Probe once if needed.** If any entry is route vector/hybrid (or ADV on a topical route): call `search_corpus("probe", k=1)`. Down servers do NOT abort the sweep - dispatch those entries anyway; each agent's own probe will return them as SKIPPED, and the report lists them under "Skipped / failed - re-run when servers are up".
3. **Dispatch.** Spawn one `question-reviewer` agent per question, prompt = the question_id. **Max 3 concurrent** - the MCP server is one stdio process over a single read-only DuckDB connection and the llama servers are local; more parallelism just queues and risks timeouts. Launch a batch of 3, wait for all to finish, launch the next 3.
4. **Collect.** Take each agent's report and RECOMMENDATION blocks as-is - do not re-litigate findings; the orchestrator only formats. An agent that dies or returns no `VERDICT` line is retried once, then recorded as `REVIEW-FAILED` in the report. Never silently drop a question.
5. **Write the report** in the format below. Create `eval/review/` if missing. If the target file already exists, stop and ask before overwriting.
6. **Close out.** Final message: the report path, the verdict tally, and the reminder that approvals are applied through the drafting skills, not by editing the bank.

## Report format

Self-containment is the point: the user reads this file cold, with no session transcript, and must be able to decide every item without asking for context.

```
# Bank review - <YYYY-MM-DD>

Bank: eval/bank.jsonl (<N> questions) | schema_docs: <version> <hash> | index: <fingerprint or n/a>
Verdicts: <X> SOUND / <Y> FLAWED / <Z> BROKEN / <W> SKIPPED / <V> REVIEW-FAILED

## Summary

| id | route/level/subtype | verdict | findings | action needed |
|----|---------------------|---------|----------|---------------|
(one row per question, bank order; SOUND rows say "none" and get NO detail section)

## <id> - <VERDICT>

**Question:** "<full question text>"
**Current gold:** <gold SQL, or gold_project_ids, or filter_sql + gold>
**Reference answer:** "<full reference text>"

### Finding 1 - <FATAL|MAJOR>

**Problem:** <from the agent's RECOMMENDATION block>
**Why it matters:** ...
**Recommended fix:** ...
**How to apply:** ...
**Decision:** [ ] APPROVE  [ ] REJECT
Notes: _____

(repeat per finding)

**Notes for the record:** <the question's NOTE-severity observations, collapsed into one short
paragraph, no decision boxes - only FATAL and MAJOR findings get decisions>

## Skipped / failed

<one line per SKIPPED or REVIEW-FAILED question: id, reason, what to do (re-run /review-question
<id> when servers are up, etc.)>
```

Formatting rules:

- Detail sections only for questions with at least one FATAL or MAJOR finding (or SKIPPED/REVIEW-FAILED status, which go in the last section). SOUND questions live in the summary table only - do not pad them into fake findings; the review skill's balance calibration survives aggregation.
- Every detail section restates the question, gold, and reference in full - the reader never opens bank.jsonl to understand a finding.
- If an agent's recommendation block leans on session context ("the query above", an unexplained attack-item name), rewrite it from the agent's own quoted evidence - never from imagination. If the evidence is not in the agent's output, that finding is downgraded to REVIEW-FAILED for that question rather than presented half-explained.
- Decision boxes are for the user only. This skill never pre-ticks them and never acts on them; executing approved fixes is a separate, user-initiated pass through the drafting skills.

## Standing rules

- **One file, ever.** The report is the only write. Never bank.jsonl, never skills, never agents.
- **Orchestrate, do not review.** Findings come from the `question-reviewer` agents; this skill formats and tallies. No new findings are invented at aggregation time, and none are dropped.
- **Every question accounted for.** N questions in, N rows out - SOUND, FLAWED, BROKEN, SKIPPED, or REVIEW-FAILED. No silent gaps.
- **Self-contained or not at all.** A finding the user cannot understand from the report alone is not presented for decision.
- **Bounded concurrency.** Max 3 agents at a time; one retry per failed agent.
- **Existing report files are never overwritten without asking.**
