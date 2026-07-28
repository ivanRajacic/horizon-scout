# Plan 05 - Per-question workspace store

**Kind:** code (architecture). `src/eval/mcp_server.py`, `src/eval/batch.py`,
`.claude/skills/question-orchestrator/SKILL.md`, all three agent files.
**Status: PROPOSAL - not approved. Decide before starting.**
**Relationship to other plans:** supersedes plan 04 item 1 (`journal-append`). Plans 01, 02,
03 and plan 04 items 2-4 are independent and stay valid either way.

## The idea

Ivan's, from the 2026-07-26 review: give the pipeline a small per-question database that
nodes write to and read from directly, instead of passing payloads through the orchestrator
as prose.

> "Maybe we should create like a little database for each question draft, and then they just
> talk to the database, and they just append the work they need, and then they can easily
> read what they need. So we just make sure that the drafter can read only what the drafter
> can read, the critic can read only what the critic can read, and the judge also reads only
> what he needs to read."

## Why this is the right shape

The journal is already a per-question database, badly implemented - a flat JSONL that the
orchestrator hand-marshals because the nodes have no way to write. That is why the run
produced 18 hand-written Python scripts (`ab-run-log-2026-07-25.md` §7.3, "the single largest
orchestrator burden"), why 11 of 97 wall-clock minutes had no agent running, and why HTML
entities reached staged records (§7.4, "the only failure mode observed in the run that is
both silent and permanent" - it exists precisely because the orchestrator must re-type agent
prose to disk).

Give the nodes somewhere to write and all three problems close at once:

- The orchestrator relays **control signals only** - status, disposition, fix targets, which
  candidate is live. Small messages, nothing to re-type, nothing to corrupt, nothing blocking
  the other three slots.
- `write-batch` generates the report from the workspace instead of from a hand-assembled
  journal - which it already does in spirit (`question-orchestrator/SKILL.md:231`: both canonical
  outputs are "written by `write-batch`, never by you").
- The drafter's fix round reads its own recorded adjudications instead of carrying the whole
  grounding transcript. Fix rounds are **51% of drafter spend** (541,041 of 1,058,094 tokens
  on 21% of the calls).
- The judge stops receiving a giant evidence blob pasted into its prompt and queries the
  slice it needs.

## What goes in it

Keyed by `question_id` + candidate index + round:

- survivor adjudications: id, IN/OUT, the one-line reason, the quoted passage that justifies
  it
- filter executions with their survivor sets
- pooled and scoped rank matrices
- gold evidence quotes
- findings, rulings, dispositions
- the drafter's checklist, why-good, history

Essentially today's journal `slot` schema (`question-orchestrator/SKILL.md:99-125`), but written by the
node that produced each part rather than transcribed by the bus.

## The constraint that must not be broken

**The critic must not read the drafter's summary in place of primary sources.**

This is the one place the idea needs a guardrail. The entire value the run produced came from
the critic going to raw data with its own wording - three of the four HIGH findings came from
angles that touched data the drafter never looked at:

- hyb-11's MISSED-GOLD: sweeping *outside* the filter, then grounding it in
  `src/retrieval/scoped.py:57-67`
- hyb-09 c1's AMBIGUOUS-READING: re-deriving `filter_sql` from the question text alone
- hyb-08's FILTER-DECORATION: an unscoped search in the critic's *own* paraphrase

A summary-mediated critic finds none of those. The workspace gives the critic the drafter's
claims **as targets to attack**, plus unchanged raw access - never a summary as a substitute
for the text. The saving is that it knows which 3 of 18 survivors are worth a full read
instead of re-reading all 18 blind. The hyb-10 critic did exactly this by instinct (a broader
`run_sql` keyword sweep instead of bulk text) and was the cheapest critic in the batch at
68,297 tokens.

Where a stored summary genuinely *does* replace raw text: the drafter's own fix round, the
judge (which has no tools by design), and the orchestrator (which should never see a payload
again).

## Architectural decision required

`CLAUDE.md` currently records the MCP server as having "deliberately no write tools; safety
enforced in code (SQL guard + read-only connection)". This plan changes that, and the change
must be conscious.

**Proposed narrow form:** keep the corpus connection read-only against
`data/processed/horizon.duckdb` exactly as it is (`ServerConfig.db_path`,
`mcp_server.py:102-119`), and put the workspace in a **separate writable DB file** that no
corpus query can reach. The invariant becomes:

> no node can write the corpus or the bank

which is the guarantee that actually matters, rather than "no node can write anything".

If that trade is not acceptable, do plan 04 item 1 instead and stop here.

## Open questions to settle before implementing

1. **Role scoping - enforced or conventional?** Ivan's framing is that each role reads only
   what it should. Enforcing that in code means the server must know which agent is calling,
   which it currently has no way to determine. Conventional scoping (separate tools per role,
   documented boundaries) is achievable today; enforced scoping needs a caller identity
   mechanism. Decide which, because "role-scoped" reads very differently in each case.
2. **Does the journal survive alongside it, or is it replaced?** Replacing it means
   `write-batch` and `src/eval/batch.py` change; keeping both means two sources of truth,
   which is worse than either.
3. **Lifecycle.** The journal is "disposable after promote"
   (`question-orchestrator/SKILL.md:129`). Same for the workspace? What happens to it on a failed slot,
   and is it a crash-recovery layer (the journal is) or just a cache?
4. **Concurrency.** Four slots writing at once to one DuckDB file - check what the write path
   does under contention, given the corpus connection is already a single stdio process.

## Recommendation

Worth doing, but **after** plans 01-03 land and are measured. Those are bounded, independently
verifiable, and together should take the run from ~2.0M tokens toward ~1.2-1.4M and from
1 h 37 m toward roughly an hour. This plan is a larger build whose payoff is mostly wall
clock and orchestrator burden - real, but easier to size once the cheaper wins are in and the
baseline has moved.

If it goes ahead, skip plan 04 item 1 rather than building `journal-append` twice.
