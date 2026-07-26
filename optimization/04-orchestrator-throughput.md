# Plan 04 - Orchestrator throughput

**Kind:** code (`src/eval/batch.py`, `src/cli.py`) + prompt assets
(`.claude/skills/draft-batch/SKILL.md`).
**Status:** approved.
**Depends on:** nothing.
**Note:** plan 05 (workspace store) would supersede **item 1 only**. Items 2-4 survive it
unchanged. If plan 05 is going ahead, skip item 1 and do the rest.

## Context

This plan is about **wall clock**, not tokens.

The 2026-07-25 run took **1 h 37 m 26 s** of wall clock against **86 minutes of total
agent-time**. That comparison is the finding: with a concurrency cap of 6 MCP-touching
agents (`draft-batch/SKILL.md:167`) and only 4 slots, four independent drafter→critic→judge
chains should have overlapped heavily. Summing per-invocation durations into per-slot chains:

| slot | chain duration |
|---|---|
| hyb-09 (2 candidates, 4 rounds) | ~40 m 37 s |
| hyb-11 | ~22 m 01 s |
| hyb-08 | ~13 m 49 s |
| hyb-10 | ~9 m 32 s |

Under genuine 4-wide dispatch the floor is hyb-09's chain at **~41 minutes**, plus setup.
We spent 97. Since agent-time (86 m) is *less* than wall clock (97 m), there were stretches
with **no agent running at all**.

The cause is identified in the run log's §7.3: every journal transition required
hand-writing a Python script to append one JSONL line carrying `record`, `evidence`,
`checklist`, `why_good`, `findings`, `judge_decisions` and `history` verbatim. **Eighteen
such scripts** (`j2.py` .. `j18.py`, plus three patch scripts). Each is a compose-run-verify
cycle on the orchestrator's single thread - while it marshals JSON for slot A, slots B, C and
D are idle. The pipeline is designed to be concurrent and the journal forces it into
lockstep.

---

## Item 1 - `journal-append` CLI node

**Files:** `src/eval/batch.py` (implementation), `src/cli.py` (subcommand registration
alongside the existing `gap-report` / `next-ids` / `validate-record` / `batch-crosscheck` /
`write-batch` at ~:817-857), `.claude/skills/draft-batch/SKILL.md` (the per-slot loop, and
the permissions block at :16-25).

> **Skip this item if plan 05 is going ahead** - the workspace store removes payload
> marshalling from the orchestrator entirely rather than making it one call.

Add a command that takes the node's returned package plus a status and does the envelope
construction and latest-line-wins bookkeeping in code:

```
python -m src.cli journal-append <journal> --id hyb-08 --status REVIEWING [--findings -]
```

Requirements:

- **Payload on stdin via a quoted heredoc**, the pattern `validate-record` already uses
  (`draft-batch/SKILL.md:176-183`) - so a payload containing quotes or `$` cannot break the
  shell.
- **Enforce the typed envelope.** `kind`, `question_id`, `status` and `cell` must be present
  and well-formed on every line; `record` stays **opaque** and may be schema-invalid mid-run.
  That distinction is deliberate (`draft-batch/SKILL.md:127`) - preserve it exactly.
- **Latest-line-wins per `question_id`** is the existing read semantics; the appender must
  not break `write-batch`'s expectations. Read `write-batch` / `src/eval/batch.py` before
  designing the interface.
- **Unescape HTML entities on the way in**, or reject them - see plan 01 item 4. This is the
  same transcription boundary, and doing it here kills the run log's §7.3 and §7.4 together.

Update the skill's per-slot loop to call it at each transition instead of describing a
hand-written append, and add it to the permissions block at :16-25.

---

## Item 2 - Warm-up before the health probe

**File:** `.claude/skills/draft-batch/SKILL.md:161-163` (step 4).

The first `search_corpus("probe", k=1)` **hung past the 120 s tool timeout**, had to be
backgrounded, then stopped with `TaskStop` and re-issued; the retry returned promptly with
all four conditions live. Diagnosis: cold-start model load on the embedder and reranker,
serialised behind a pooled call that runs all four conditions. **~7 minutes of wall clock.**

Add a cheap warm-up before the probe - a per-condition call, or a documented expectation that
the first pooled call after server start is slow and should be given a longer window rather
than treated as a failure. State which, so the next orchestrator does not repeat the
stop-and-retry dance.

Pairs well with plan 01 item 1: the probe should also pass `snippet_chars=0`.

---

## Item 3 - Document resume-from-transcript as the preferred retry

**Files:** `.claude/skills/draft-batch/SKILL.md:186` (the critic retry bound) and the
`Bounded everything` standing rule at :235.

Three agents - the hyb-08 fix drafter, the hyb-09 critic and the hyb-11 critic - died
simultaneously on `API Error: Unable to connect to API (ENOTFOUND)`. A transport failure, not
a servers-down signal, so the outage path correctly did not apply.

**What worked:** resuming each from its transcript via `SendMessage` with a short "you were
cut off, carry on from X" note. All three resumed exactly where they stopped and completed
normally - the hyb-08 drafter had already said "both sides re-measured, running the precheck
gate" and picked up there. No candidate was consumed and no work was lost.

The skill currently writes the bound as "retry that agent once", which implies a respawn -
and a respawn would have thrown away a completed grounding pass worth ~140k tokens. Write
**resume-from-transcript as the preferred form of the retry, with respawn as the fallback
when the transcript is unusable.**

---

## Item 4 - Make concurrent dispatch explicit

**File:** `.claude/skills/draft-batch/SKILL.md:165-167`.

The cap is 6 MCP-touching agents; with 4 slots the cap was never the binding constraint. What
was missing is an instruction to **dispatch all slots' drafters before handling any return**,
and to handle returns as they arrive rather than in slot order.

State it plainly in the per-slot loop: the loop is written per-slot for readability, but slots
run concurrently, and the orchestrator must never block slot B's dispatch on slot A's
bookkeeping. With item 1 (or plan 05) removing the bookkeeping cost, this becomes achievable
rather than aspirational.

Also worth stating: the cap of 6 "was itself never measured" (the skill says so at :167).
Record the observed contention from the next run - with real 4-wide dispatch, the MCP server's
single stdio process over one read-only DuckDB connection, and the single-GPU embedder and
reranker, are the things to watch.

---

## Verification

1. **`journal-append` round-trip.** Build a journal with the new command from a saved set of
   node packages, then run `write-batch` on it and confirm the two canonical outputs are
   byte-identical to what a hand-built journal produced. `eval/drafts/draft-batch-journal-2026-07-25.jsonl`
   (20 lines: 1 batch header + 19 slot transitions) is the fixture to replay.
2. **Envelope enforcement.** A line missing `cell` or with a malformed `status` must be
   rejected by the appender, not by `write-batch` at close-out. An opaque, schema-invalid
   `record` must be **accepted**.
3. **Entity handling.** A package containing `&lt;` is unescaped or rejected at append time -
   whichever plan 01 item 4 settled on - never silently written.
4. **Unit tests** alongside `tests/test_batch.py`.
5. **Wall-clock measurement, the real one.** Re-run a 4-slot hybrid batch and compare:
   baseline 1 h 37 m 26 s wall against 86 m agent-time. Success is wall clock approaching the
   longest single slot chain (~41 m on the baseline shape), and agent-time *exceeding* wall
   clock - which is what real concurrency looks like.

## Do not

- Add automatic resume to the journal. `draft-batch/SKILL.md:129` states resume is manual by
  design; the journal is a disposable working file, not a canonical output.
- Let the orchestrator adjudicate anything it appends. It is a message bus
  (`draft-batch/SKILL.md:29`, :232); `journal-append` must not compute, compare or judge -
  only marshal.
- Write `eval/bank.jsonl` from any of this. The two canonical outputs come from `write-batch`
  and promotion stays a separate human-gated step.
