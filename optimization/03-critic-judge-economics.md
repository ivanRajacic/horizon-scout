# Plan 03 - Critic and judge economics

**Kind:** prompt assets only. `.claude/agents/question-reviewer.md`,
`.claude/agents/question-judge.md`, `.claude/skills/{review,judge}-question/SKILL.md`,
`.claude/skills/draft-batch/SKILL.md`.
**Status:** approved.
**Depends on:** nothing.

## Context

Critics were **676,462 tokens (34%)** and judges **227,272 (12%)** of the 2026-07-25 run.
The judge is already near-optimal - 12 tool calls across 9 invocations, no MCP tools by
design, and it holds all the authority. **Do not add tools to the judge.**

The cost is in the critic, and specifically in the four post-fix re-attacks. Every fix round
today dispatches a **fresh** critic (`draft-batch/SKILL.md:80`). Ledger:

| round | tokens | what it produced |
|---|---|---|
| hyb-08 r2 | 52,111 | 3 LOW, one *arguing against* round 1's finding |
| hyb-09 c1 r2 | 83,790 | AMBIGUOUS-READING HIGH - killed the candidate |
| hyb-11 r2 | 88,648 | 1 MID dismissed (its fix would have reverted the judge's own order), 1 repeat class → note, 3 LOW |
| hyb-09 c2 r4 | 76,868 | 1 MID upheld then accepted anyway as a note; found one out-of-filter satisfier |

**301,417 tokens; one of four changed an outcome.**

But that one matters enormously - hyb-09 c1's HIGH prevented a question that would have
scored a correctly-routed, `schema_docs`-faithful system as having found nothing. And the
mechanism was specific: it came from a **blind-solve of the filter wording**, re-deriving
`filter_sql` from the question text alone. The round-1 critic on that slot never blind-solved
the filter - it spent all three angles on the reference. **Round 2 was a first look at that
surface, not a re-look.**

That is the design insight this plan rests on: the load-bearing property is not that the
*agent* is cold, it is that the **anti-anchoring protocols get a fresh draw**. A warm critic
handed "the reference was rewritten, attack that" would check the reference and stop - which
is precisely the churn we are trying to remove, seen from the other side.

---

## Item 1 - Warm the critic, but make freshness follow the diff

**Files:** `.claude/skills/draft-batch/SKILL.md:80` (the "fresh critic per round" rule and
the "Who reads what" table at :73-78); `.claude/agents/question-reviewer.md`;
`.claude/skills/review-question/SKILL.md` (the two mandatory protocols, :49-71).

Change the rule from *a fresh critic per round* to *a warm critic per slot, with mandatory
protocol re-draws keyed to what actually changed*:

- **The question text or the filter wording changed** → the critic **must** re-run
  BLIND-SOLVE and OWN-WORDING with a *new* derivation, not a recollection of its earlier one.
  This is the hyb-09 c1 case and it must survive.
- **Only the reference answer, `notes`, or a provenance field changed** → no protocol
  re-draw. Attack the changed text and report.

State the reason in the skill so it is not re-litigated: the protocols are anti-anchoring
controls (`review-question/SKILL.md:51`), and their value is a fresh derivation, which a
warm agent can produce on demand but will not produce spontaneously.

**Update the "Who reads what" table** (`draft-batch/SKILL.md:73-78`). The critic's "Never
sees" column currently includes "prior rounds' findings"; that is no longer true for its own
findings. It must still never see **the budget** or **what the judge wants** - those are the
severity-calibration hazards, and they are the real content of the isolation rule. Say so
explicitly, because the current one-line justification at :80 ("a critic that knows a fix
round already ran starts calibrating severity to what it thinks you can afford") is the part
worth keeping.

**Honest caveat to record in the skill:** a warm critic accumulates its own transcript, so
per-round token counts stop being independent and the saving is smaller than 4 x ~77k. The
larger critic saving is item 4.

---

## Item 2 - A "recorded and accepted" finding state

**Files:** `.claude/skills/judge-question/SKILL.md`, `.claude/agents/question-judge.md`,
and the journal schema in `draft-batch/SKILL.md:115-119`.

Today a MID the judge deliberately lets ride into the report is invisible to later rounds, so
a subsequent critic re-discovers it and the judge must re-rule on it. Observed twice:
hyb-11's GOLD-WRONG was upheld in both rounds and rode as a note both times; hyb-09 c2's
planned-versus-performed observation was raised as LOW in round 3 and re-raised as a MID in
round 4.

Add a terminal `RECORDED` state a judge can assign to a finding, meaning *real, accepted,
will ride into the report, do not re-rule*. Carry it on the journal's `findings` entries
alongside `ruling` / `ruling_why`. Once a class is `RECORDED` on a candidate, a later
re-discovery of the same class is noted and not re-ruled.

This is the run log's own §8.3 recommendation and it is what actually kills the churn - item
1 alone does not, because a warm critic can still legitimately re-find a defect it never
reported.

---

## Item 3 - Rewrite the within-candidate stop-rule trigger

**File:** `.claude/skills/draft-batch/SKILL.md` stop-rules table (~:221-225), and
`judge-question/SKILL.md` where the judge enforces it.

Current trigger: "the same defect `class` upheld twice on one candidate". Intent: *a fix that
did not take*. hyb-11's judge upheld GOLD-WRONG twice and **declined to fire it**, reasoning
explicitly that no fix round had ever targeted that class, so the second UPHELD was a fresh
critic re-finding a recorded note rather than a failed repair - and that reading it literally
"would make 'let the MID ride into the report' impossible in practice".

That reasoning is correct and it was made out loud, which is the design working. But every
judge will now have to re-derive it. Rewrite the trigger as:

> the same class is upheld again **after a fix round that targeted that class**

Interacts with item 2: with a `RECORDED` state, the ambiguous case largely stops arising.

---

## Item 4 - Give the critic `snippet_chars` discipline

**Depends on plan 01 item 1.**
**File:** `.claude/skills/review-question/SKILL.md:33` (the `search_corpus` tooling entry),
`:45` (the probe).

The critic is the pipeline's second-heaviest `search_corpus` caller, and OWN-WORDING
(`:60-71`) requires it to search with its own reformulations - so it makes *more* searches
than the drafter per unit of work, not fewer.

Set the same values plan 02 item 5 sets for the drafter: probe → `snippet_chars=0`;
exploratory OWN-WORDING sweeps → ~400-600; full `get_project_text` for anything it intends to
quote as evidence.

**Do not restrict what the critic may read.** The saving is payload-per-call, never
call-count or channel access. Three of the four HIGH findings came from angles that touched
raw data the drafter never looked at - hyb-11's MISSED-GOLD came from sweeping *outside* the
filter, hyb-09 c1's from re-deriving the filter independently. Those must stay free.

---

## Item 5 - Relay the lesson on candidate advance

**File:** `.claude/skills/draft-batch/SKILL.md`, step 5's ABANDON branch (~:190).

When hyb-09 advanced to its fallback, the orchestrator passed the abandoned candidate's
**lesson** ("check where your topic sits in the euroSciVoc path before wording the scope;
word it so it has exactly one executable reading") without its content. The next drafter ran
the one-reading check as its second action and the defect class did not recur.

This is not currently in the skill. Make it an explicit orchestrator duty: **relay the
lesson, never the verdict and never the content.** The distinction matters - the lesson is a
known trap, the verdict would prejudice a node that is supposed to judge independently, and
the content would anchor the new drafter on a dead question.

Plan 02 item 2 makes the one-reading lesson permanent in the skill, so this rule is for the
*next* trap, not this one.

---

## Item 6 - `evidence_carried_forward` on fix rounds

**Files:** `.claude/agents/question-drafter.md:20`, `.claude/skills/draft-batch/SKILL.md`
step 5's FIX branch (~:189).

hyb-11's drafter did not re-run its pooled searches after a text edit, because
`pooling_evidence` had to stay byte-identical - and **disclosed it**. The orchestrator passed
the disclosure to the critic, which re-measured rather than trusting it and reproduced
`pooled_candidate_count = 11` with all 8 gold retrievable. That worked, but only because the
disclosure was voluntary.

Require an explicit `evidence_carried_forward` flag on any fix round that does not re-run its
measurements, naming which evidence was carried, and require the orchestrator to pass it to
the critic.

---

## Verification

Prompt-asset changes; verify behaviourally.

1. **Consistency sweep.** After editing, re-read `draft-batch/SKILL.md` end to end. Items 1,
   2, 3, 5 and 6 all touch it, and the "Who reads what" table, the graph at :42-65, the
   per-slot loop at :171-190 and the stop-rules table must agree with each other. This file
   is the one with the most cross-references in the repo.
2. **The judge keeps no tools.** Confirm `question-judge.md` still lists `Read` and nothing
   else. Item 2 adds state, not capability.
3. **Warm-critic dispatch actually changed.** Run a batch with at least one fix round and
   confirm from the journal that the same critic agent handled both rounds, and that a
   question-text edit triggered a fresh BLIND-SOLVE while a reference-only edit did not.
4. **The hyb-09 c1 case still gets caught.** This is the regression test for item 1. Re-run
   the musicology candidate (or any branch-term seed) through a fix round that edits the
   question wording, and confirm the warm critic re-derives `filter_sql` and finds the
   ambiguity. If it does not, item 1's protocol re-draw rule has not landed and the change
   should be reverted rather than tuned.
5. **Full run comparison** against baseline: critics 676,462 tokens across 9 invocations.

## Do not

- Give the judge MCP tools. Its no-tools constraint is why it costs 12% while holding all the
  authority, and `question-judge.md:20` states the reasoning: "your job is a logic check over
  the record, not a third investigation."
- Let the critic see the budget, the remaining passes, or the judge's preferences. That is the
  isolation that matters and item 1 does not touch it.
- Collapse critic and judge back together. The run log's §9 records four concrete instances
  where the separation changed an outcome, including the judge dismissing a MID whose
  suggested fix would have reverted the judge's own prior order.
