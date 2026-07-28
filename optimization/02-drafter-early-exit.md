# Plan 02 - Drafter early exit

**Kind:** prompt assets only. `.claude/skills/draft-{hybrid,vector,sql}-question/SKILL.md`,
`.claude/agents/question-drafter.md`.
**Status:** IMPLEMENTED 2026-07-26 (commit `048d777`). Verified by the measured re-run: pending.
**Depends on:** plan 01 (items 5 and 7 below reference checks it adds). Everything else here
is independent and can be done first if 01 is not ready.

## Context

The `question-drafter` is 54% of a batch run's spend - **1,058,094 of 1,961,828 tokens, 149
of 305 tool calls, 41 of 86 agent-minutes** on 2026-07-25. Observed drafts ran 19-27 MCP
calls against a mandated floor of ~13-15.

Two measurements say the fix is *when the drafter stops*, not *how efficiently it works*:

- **hyb-09 alone was 566,164 drafter tokens - 53% of all drafter spend for one accepted
  question.** The other three slots averaged ~164k. Its candidate 1 cost ~519k in total and
  was abandoned.
- **Fix rounds are 541,041 of 1,058,094 drafter tokens (51%) on 31 of 149 tool calls (21%).**
  `question-drafter.md:20` already says to re-run only invalidated steps and "NOT the whole
  checklist"; hyb-09 c2's fix ran 26 tool calls anyway. That instruction is not binding.

The structural cause is an **ordering inversion**. `draft-hybrid-question/SKILL.md` grounds
by reading *all* survivors' full text (Step 1, :78-80), then drafts (Step 2, :84-90), then
verifies (Step 3, :92-103). The checks capable of killing a candidate live in Step 3; the
expensive irreversible investment happens in Step 1. So the drafter pays for the survivor
read, the adjudication and often the reference before running anything that can tell it the
candidate is dead - and once that invested, `hybrid:94`'s cascade rule makes a rewrite look
cheaper than a `DRAFT-FAILED`.

`docs/archive/drafting-pipeline-audit.md` already called for this ("Birth-failures are expensive...
Tighten upstream vetting so dead candidates are caught before a drafter is spawned"). This
plan is the drafter-side half; plan 01 item 3 is the upstream half.

**Economics note that shapes every item here:** a `DRAFT-FAILED` buys a fresh candidate at
~118k tokens. So early exit is only a win when it fires *early*. A `DRAFT-FAILED` at call 4
is nearly free; at call 25 it costs 144k plus the fresh candidate. Every item below moves
decision points forward rather than adding new ones.

---

## Item 1 - Front-load the kill-shots (the main change)

**File:** `draft-hybrid-question/SKILL.md`, Steps 1-3 (:76-103). Mirror in
`draft-vector-question` and `draft-sql-question` where the same shape applies.

Reorder so every cheap executable check that can kill the candidate runs **before** the
expensive investment. The new order:

1. **Fit gate** (cheap, ~3-4 calls, before any wording is committed):
   - re-execute the candidate's filter SQL once as a drift check - this already exists at
     `hybrid:78`, keep it
   - **one-reading check on each filter clause** - see item 2
   - survivor count against the subtype window - free, the count is already in hand
   - if any gate fails and cannot be repaired by re-wording the scope, `DRAFT-FAILED` now
2. **Sample read** - enough survivors to compose the question honestly, not all of them
3. **Draft the question text** (today's Step 2)
4. **Discrimination check immediately** - the unscoped pooled search currently at
   `hybrid:101`. It is a pure kill-shot, costs one call, and today runs *after* the
   exhaustive adjudication. If the text alone identifies the gold set, the filter is
   decoration and the question must change - discovering that now costs one call, not a
   full grounding pass.
5. **Then** the exhaustive survivor read and adjudication (today's Step 3.2)
6. **Then** the reference answer, the checklist, `precheck_record`

hyb-08's FILTER-DECORATION HIGH is the case in point: the drafter recorded its own doubt
about the gold's unscoped rank-1 result *before any critic saw it*, but by then had already
paid for the full grounding pass.

**Keep `hybrid:94`'s cascade rule** ("any edit to the question text or the filter SQL
invalidates everything downstream") - it is correct. Front-loading is what makes it cheap,
because an edit at step 4 invalidates almost nothing.

---

## Item 2 - The one-reading check, as a named gate

**Files:** `draft-hybrid-question/SKILL.md` (fit gate, item 1 above);
`.claude/agents/question-drafter.md:15`.

Before wording a scope that references a euroSciVoc term:

```sql
SELECT DISTINCT euroSciVocPath, euroSciVocTitle, COUNT(DISTINCT projectID)
FROM euroscivoc WHERE euroSciVocPath LIKE '%<term>%' GROUP BY 1, 2
```

One row: leaf term, the title reading and the subtree reading select the identical set, the
scope has exactly one executable reading - proceed. Multiple rows: the term is a branch with
siblings and the two readings diverge - either word the scope to name the branch explicitly
("classified anywhere under musicology - ethnomusicology and popular music studies
included") or `DRAFT-FAILED`.

This is the check that would have killed hyb-09 candidate 1 at its first action instead of
after ~519k tokens: `musicology` returns 3 rows, all three gold sit on sub-leaves, and the
narrow reading returns 23 survivors with zero gold. The hyb-09 candidate-2 drafter ran
exactly this query as its second action once the orchestrator relayed the lesson, and the
defect class did not recur.

Plan 01 item 3 puts the same check upstream in `precheck_candidate` so bad seeds never
reach a drafter. Keep both - the drafter may re-scope away from the seed, and then it owns
the check.

---

## Item 3 - Bound the fix round

**File:** `.claude/agents/question-drafter.md:20`.

Replace the soft wording with a hard, countable bound: **a fix round gets at most 8 tool
calls, `precheck_record` included. If the named fix cannot be completed within that, say so
plainly and return, and let the judge abandon.**

The escape hatch already exists in the same paragraph ("If the named fix turns out not to be
possible, say so plainly rather than substituting a different change: the judge will abandon
the candidate, which is a cheaper outcome than a question that quietly drifted").

**Apply to fix rounds only, never to first rounds.** A drafter's output is indivisible - a
cap on a first round converts into a lost candidate at ~118k, far more than the round it
would have saved. This is why there is no first-round call cap anywhere in this plan.

---

## Item 4 - Articulate the `DRAFT-FAILED` gates

**File:** `.claude/agents/question-drafter.md:15`.

Today the trigger is described as "a couple of cheap scoping queries" and "if a 'reject at
birth' case is already visible - empty results, a dead trap, unretrievable seeds, a
non-discriminating or user-inexpressible filter". That is a judgement call dressed as a
checklist, and it is why birth-failures happen late.

Replace with the named gates from item 1, each with its query and its pass condition, so the
fit gate is something the drafter *executes* rather than something it *decides*. State
plainly that a `DRAFT-FAILED` at the fit gate is a cheap, expected, correct outcome - the
orchestrator owns three candidates per slot precisely so this is affordable.

Add one gate the run showed is systematic rather than per-candidate: **topic is never a
structured filter.** `src/retrieval/scoped.py:57-67` whitelists the filterable columns and
subject matter is not among them, so the runtime cannot build a topic filter. Any hybrid
scope with a topical half must be worded knowing its topical part falls to retrieval, not to
SQL. This is what produced hyb-11's MISSED-GOLD HIGH, and it will recur on every topical
hybrid question until it is written down.

---

## Item 5 - Set `snippet_chars` on every `search_corpus` caller

**Depends on plan 01 item 1.**
**Files:** `draft-hybrid-question/SKILL.md:73` (probe), `:101` (discrimination), `:99`
(scoped adjudication); `draft-vector-question/SKILL.md:71` (probe), `:96`.

- **probe** → `snippet_chars=0`. A liveness check needs no text. 17 probe calls returned 68
  full chunks (~98k chars) on 2026-07-25 to answer 17 booleans.
- **unscoped discrimination check** → ~400. Its ~20 projects exist to surface
  counter-examples and are then discarded.
- **scoped adjudication search** → ~600. The skill already says off-topic triage runs off
  `best_chunk` text and borderline candidates get a full `get_project_text` read anyway
  (`hybrid:99`).

This is the single largest token item in plans 01-02 combined: ~200k tokens of chunk text
across the run, with the drafter its heaviest caller.

---

## Item 6 - Direction-tiered survivor reading

**File:** `draft-hybrid-question/SKILL.md:80` and `:98` - the S<=20 exhaustive path, the only
untiered reads left. `draft-vector-question` step 3.3 and `hybrid:99` already specify
tiering.

**Tier by direction, not by pass.** `["acronym","title","objective","teaser"]` (~2.1k chars)
is sufficient to justify an **OUT**. Any survivor going **IN**, and any text that will feed
`reference_answer`, requires the full payload (~8.1k).

> **Do not make this a gist-first pass.** Two of the run's real findings lived in exactly the
> fields a naive tiering would drop: hyb-09 c1's HIGH came from an eleven-term sweep over
> MEMORISING's full 8,045-char text, and hyb-09 c2 round 4's MID came from pulling Eternum's
> `workPerformed`. Those fields are ~48% of a payload and are where "what the project
> actually did" lives.

Direction-tiering keeps the exhaustive-read property intact (`hybrid:40`: "strictly stronger
than pooling"). With gold at 1-8 of S=7-18 in the observed slots, hyb-10 would pull full text
for 2 survivors and gist for 16.

Expected saving is modest - 35 of 37 `get_project_text` calls already passed `fields`, so
only this path is untiered. Do it for consistency, not for the number.

---

## Item 7 - Remove redundant mandated calls

**a. `get_bank_questions` - delete from orchestrated mode.**
Files: `draft-hybrid-question/SKILL.md:24`, `draft-vector-question/SKILL.md:24`,
`draft-sql-question/SKILL.md:24`.

12 calls on 2026-07-25 with no remaining consumer: `NEAR-DUPLICATE` moved to the independent
reviewer (`hybrid:119`, `vector:116`, `sql:120`), which has its own access to the tool; the
subtype/term_style coverage use feeds only the propose-and-wait branch that `hybrid:24`
already skips; cross-slot duplicates are covered deterministically by `crosscheck()`
(`src/eval/batch.py:361-449`). A **turn** saving, which is the right thing to be saving.

Residual risk, small: the drafter loses the ability to self-avoid a duplicate while
composing, so a duplicate becomes a LOW finding plus a close-out flag rather than never being
authored. With 2 promoted hybrid entries this is ~zero; revisit when any route passes ~30.

**b. Make hybrid Step 3.1 conditional.**
File: `draft-hybrid-question/SKILL.md:96`.

`filter_sql` executes three times per draft (Step 1 :78, enumeration :79, Step 3.1 :96)
before `precheck_record` runs it a fourth. Step 3.1 is not pure redundancy - it is where
`hybrid:94`'s cascade lands, because Step 2 (:90) may present a different filter than Step 1
ran. Make it conditional:

> If `filter_sql` is byte-identical to the SQL executed in the fit gate, carry that
> enumeration forward and record it - do not re-execute. Re-execute only if the SQL changed
> when the question was drafted. `precheck_record` re-executes it as the gate either way.

**c. Investigate the `precheck_record` call rate.** `precheck_record` ran **31 times across 9
drafter invocations** - 3.4x each - though `question-drafter.md:16` frames it as "the last
step before returning". Each call re-executes both `gold_sql` and `filter_sql`. Iterative use
may be correct, but it is 10% of all tool calls and nobody has looked at why. Read
`data/logs/draft_mcp.jsonl` for the actual pattern before changing anything here - this item
is *diagnose first*, and may end in "no change needed".

> **Diagnosed 2026-07-26: no change needed.** The log shows 21 of the 31 calls landed in one
> ~30-second window at 15:09-15:10, sweeping the EXISTING bank entries (sql-01..sql-10,
> vec-01..vec-05, hyb-01..hyb-07) - a validation sweep, not drafting. The batch's actual
> drafters (16:20-17:20) called `precheck_record` 10 times across 9 invocations: hyb-08 x2
> (draft + fix), hyb-10 x1, hyb-09 x5 (two candidates, four rounds), hyb-11 x2 - about one
> call per drafter round, which is exactly the "last step before returning" contract. The
> 3.4x-per-drafter framing divided the sweep's calls by the drafter count.

---

## Verification

Prompt-asset changes cannot be unit-tested. Verify by:

1. **Consistency sweep.** After editing, re-read each changed SKILL.md end to end and confirm
   the step numbering, the cross-references between steps, and the checklist item names
   (`FILTER-EXECUTED`, `ADJUDICATION-COMPLETE`, ...) still line up. The reorder in item 1
   touches numbering that other sections cite.
2. **Version bumps.** `CLAUDE.md` requires a version label and content hash bump on any
   meaningful edit to a versioned prompt asset. Check whether the edited skills are covered
   by `BANK_BRIEF_VERSION` / `SCHEMA_DOCS_VERSION` / `CORPUS_PROFILE_VERSION` in
   `src/config.py`, and bump what applies. The drafting skills themselves are not currently
   versioned - if that is still true, note it rather than inventing a scheme.
3. **Single-slot smoke run.** Run `/question-orchestrator` for **one** hybrid slot and read
   `data/logs/draft_mcp.jsonl` for that run. Expect: fewer total calls than the 19-27
   baseline, no `get_bank_questions` call, `snippet_chars` present on every `search_corpus`
   call, and the fit-gate queries appearing in the first 3-4 calls.
4. **Deliberate kill test.** Point a drafter at a branch-term seed (musicology, or any
   euroSciVoc term whose one-reading query returns >1 row) and confirm it returns
   `DRAFT-FAILED` within roughly 5 calls instead of grounding fully. This is the whole point
   of the plan; if it does not happen, item 1 or 4 has not landed.
5. **Full run comparison.** A 4-slot hybrid batch against the baseline: 1,961,828 total
   tokens, 1,058,094 drafter tokens, 305 tool calls, 1 h 37 m wall.

## Do not

- Cap first-round tool calls (see item 3).
- Remove the per-drafter `search_corpus` probe - it feeds the vector route's required
  `pooling_evidence.index_fingerprint` and triggers the outage path at
  `question-orchestrator/SKILL.md:194`. Fix its payload (item 5), not the call.
- Add a re-scope counter. Plan 01's `SURVIVOR-WINDOW` expresses that constraint as a number.
  hyb-09 c1's re-scope was *correct*; what was missing was noticing that 46 survivors no
  longer fit a `filter-synthesize` cell.
