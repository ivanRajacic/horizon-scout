# Pipeline optimization - execution plans

*Opened 2026-07-26, from analysis of the 2026-07-25 four-node `/draft-batch` A/B run
(`eval/drafts/ab-run-log-2026-07-25.md`). Successor to the proposals in
`drafting-pipeline-audit.md`, which called for "tighter upstream vetting so dead
candidates are caught before a drafter is spawned" - this folder measures that problem
and says exactly what to do about it.*

Each numbered plan is **self-contained and independently executable**. Hand one to a
fresh session; it does not need this README or the others in context.

## Baseline - the numbers every plan is measured against

From `eval/drafts/ab-run-log-2026-07-25.md` and `data/logs/draft_mcp.jsonl` (2026-07-25).
4 accepted questions, 0 failed.

| | value |
|---|---|
| Wall clock | 1 h 37 m 26 s |
| Total agent-time | 86 m (i.e. stretches with **no agent running**) |
| Total subagent tokens | 1,961,828 |
| Total subagent tool calls | 305 |
| Tokens per accepted question | ~490,000 |
| Spend split | drafter 1,058,094 (54%) / critic 676,462 (34%) / judge 227,272 (12%) |

MCP calls on 2026-07-25: `run_sql` 89, `search_corpus` 46, `get_project_text` 37,
`precheck_record` 31, `get_schema_docs` 15, `get_bank_questions` 12, `get_corpus_profile` 2.

Three measured facts drive everything here:

1. **`search_corpus` is the largest uncontrolled data channel.** 46 calls returned 563
   project entries, each carrying `best_chunk.text` verbatim at a corpus mean of 1,437
   chars - **~809k chars ≈ 200k tokens**, roughly 1.9x the entire `get_project_text`
   channel, with no caller control. 17 of those calls were `probe` at k=1, returning 68
   full chunks (~98k chars) to answer 17 booleans.
2. **Cost concentrates in late deaths and fix rounds, not across the batch.** hyb-09
   alone consumed 566,164 drafter tokens - **53% of all drafter spend for one accepted
   question**. Fix rounds are **541,041 of 1,058,094 drafter tokens (51%) on 31 of 149
   tool calls (21%)**. Candidate hyb-09 c1 cost ~519k across two drafter passes, two
   critics and two judge rounds, and was abandoned anyway - on a defect one SQL query
   would have surfaced before grounding began.
3. **The drafter has no bound on a loop the skills authorize.** ~12 places across the
   three route skills authorize rewrite/pivot/tighten/re-subtype; `hybrid:94` then forces
   full re-execution downstream. The only capped loop is the post-return FIX round. The
   `corpus-explorer` has a turn budget; the drafter has none.

## The plans

| # | Plan | Kind | Status | Depends on |
|---|---|---|---|---|
| 01 | [Deterministic gates](01-deterministic-gates.md) | code (`mcp_server.py`, `cli.py`) | approved | - |
| 02 | [Drafter early exit](02-drafter-early-exit.md) | prompt assets | approved | 01 |
| 03 | [Critic & judge economics](03-critic-judge-economics.md) | prompt assets | approved | - |
| 04 | [Orchestrator throughput](04-orchestrator-throughput.md) | code + prompt assets | approved | - |
| 05 | [Per-question workspace store](05-workspace-store.md) | code (architecture) | **proposal - decide before starting** | supersedes part of 04 |

**Recommended order: 01, then 02 and 03 and 04 in any order.** 01 first because 02's
skill edits reference the checks 01 adds. 03 and 04 are independent of both.

Plan 05 is a larger re-architecture that would subsume the `journal-append` node in 04.
If you intend to do 05, still do 04's other three items - only its item 1 is superseded.

## Two corrections to earlier analysis

Recorded because both were asserted confidently in conversation before being measured,
and one would have broken the bank.

- **The `get_project_text` `fields`/`max_chars` gap is mostly illusory.** 35 of 37 calls
  already passed `fields`; 16 also passed `max_chars`. The agents found the parameters in
  the tool's own docstring (`mcp_server.py:459-471`), which FastMCP puts in the schema
  they see. Remaining headroom is ~2-3% of drafter spend. Only the S<=20 exhaustive path
  (`draft-hybrid-question/SKILL.md:80`, `:98`) is untiered - handled in plan 02, item 6.
- **The `pooling_evidence` arithmetic invariant must not ship as first proposed.** See
  "Deferred" below.

## Deferred - do not start without a decision

**`pooling_evidence` arithmetic check** (`pooled == |accepted| + rejected`). Blocked on
two prerequisites, in order:

1. **`vec-05` in `eval/bank.jsonl` violates it** - `pooled=36, accepted=10, rejected=27`,
   sum 37. Shipping the check makes `validate-bank` fail on the live bank and blocks
   `promote-drafts`. The bank is a frozen artifact per `CLAUDE.md`; fixing it needs Ivan's
   explicit say-so.
2. **The convention is unsettled.** `draft-hybrid-question/SKILL.md:167-169` explicitly
   authorizes exhaustive-read passes to source candidate counts from the scoped pool and
   adjudication counts from the full survivor read - which is what hyb-07 (12 vs 8+7) and
   hyb-11 (11 vs 8+7) do. Decide whether the three fields are pool-relative always, and
   if so give the exhaustive denominator its own home.

Note the two candidate invariants select **opposite** records: the pool-relative one
passes hyb-06/hyb-10 and fails hyb-07/hyb-11; `|accepted| + rejected == survivor_count
when survivor_count <= 20` (the exhaustive-read claim as arithmetic) does the reverse.
Pick deliberately.

**Paraphrase-friendly hybrid seeds.** The corpus profile holds 10 hybrid seeds, only 3
marked `paraphrase`. hyb-09's fallback list was forced onto one topic (both viticulture)
and the slot had exactly one good fallback after its abandonment. This is an
`/explore-corpus` order, not a pipeline change.

## Explicitly rejected

Considered and decided against - recorded so they are not re-proposed.

- **Removing the per-drafter `search_corpus` probe.** It feeds the vector route's required
  `pooling_evidence.index_fingerprint` (`draft-vector-question/SKILL.md:71`); the
  orchestrator's setup probe is a dispatch-time gate, not a 97-minute liveness guarantee
  (this run had a cold-start hang *and* three `ENOTFOUND` deaths mid-run); and it triggers
  the outage path at `draft-batch/SKILL.md:194`, which deliberately does not consume a
  candidate. Its waste is the payload, not the call - fixed by plan 01 item 1.
- **A hard re-scope cap on the drafter.** Would not have blocked hyb-09 c1 (which
  re-scoped exactly once), does not price the actual cost (crossing from the S<=20
  exhaustive path to the S 21-200 pooled path), and risks suppressing hyb-09 c2's
  seed-framing rejection - the run's best drafter behaviour. Plan 01's `SURVIVOR-WINDOW`
  expresses the same constraint as a number instead of prose.
- **Naive gist-first survivor reading.** Two of the run's real findings lived in fields it
  would drop: hyb-09 c1's HIGH came from an eleven-term sweep over MEMORISING's full
  8,045-char text, and hyb-09 c2 round 4's MID came from Eternum's `workPerformed`. Tier
  by *direction* instead - plan 02 item 6.
- **A hard tool-call cap on first drafter rounds.** A drafter's output is indivisible;
  a cap converts into a lost candidate, and a fresh candidate costs ~118k tokens. Bound
  the fix round instead - plan 02 item 3.
- **Downgrading drafter or critic below Opus.** The critic produced 3 of 4 HIGH findings
  from angles a checklist does not contain; the drafter's errors cascade downstream. Both
  are already at `reasoningEffort: low`.

## Verification, common to all plans

```bash
./.venv/Scripts/python.exe -m pytest                    # must stay green
./.venv/Scripts/python.exe -m src.cli validate-bank     # must still pass
```

The real measurement is an end-to-end `/draft-batch` re-run on hybrid cells, compared
against the four baseline numbers above. `data/logs/draft_mcp.jsonl` gives the per-channel
breakdown; the `search_corpus` entry-count x mean-chunk-length product is the direct check
on plan 01.
