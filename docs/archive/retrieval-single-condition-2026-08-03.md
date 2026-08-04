# Note: moving the project to hybrid+rerank only

**Status: RESOLVED 2026-08-03. The change was made; the recommendation below was
overruled, deliberately.** Written 2026-08-02 at the user's request, with the bank at
67 questions. Kept as the record of what was weighed, not as live advice - do not
re-argue it.

**What was decided.** RQ2 is dropped outright (the second of the two coherent positions
in "The one thing to decide first"), because the study is about routing and the
four-condition ladder is not. The RQ2 tombstone and the write-up's limitation sentence
are in `horizon-scout.md`; the implementation is in the 2026-08-03 status entry in
`working-plan.md`.

**Where this note was right, and it was kept:** §1 and §2. Pooled four-condition
labelling and pooled `search_corpus` in the drafting pipeline SURVIVE - `search_corpus`
now defaults to `hybrid_rerank` but the gold-labelling and adversarial-absence call
sites still pass `condition="pooled"` explicitly, for exactly the reasons argued below.

**Where this note was wrong:** §3's table says "the code barely needs to change" and
that `ask.py` "already runs one stack at runtime". True as far as it goes, and
misleading - the stack it ran was **dense-only**, not hybrid+rerank. `Ask` built a bare
`VectorSearcher` and gave that same object to `ScopedRetriever`. So the change was not
a no-op on the runtime; it changed what the system answers, on both topical routes, for
the first time.

## The proposal

Stop running four retrieval conditions (`lexical`, `dense`, `hybrid`, `hybrid_rerank`)
everywhere and use `hybrid_rerank` alone - at runtime, in the drafting pipeline, and in
the study.

## The one thing to decide first

**RQ2 *is* the four conditions.** The retrieval ladder - dense-only, BM25-only, RRF
fusion, fusion+rerank - is not instrumentation attached to the study, it is the whole of
Study 1 (`horizon-scout.md` §RQ2, §Study A). Running only `hybrid_rerank` does not
simplify RQ2; it deletes it, and asserts its answer.

That matters because of what RQ2 was for. Its stated epistemic role is: "freezing the
*best* stack is what makes RQ1's result defensible - nobody can attribute an
always-hybrid loss to bad retrieval." RQ1 is the primary question. If the stack is
assumed rather than measured, the first reviewer objection to any RQ1 result is exactly
the one RQ2 existed to close off, and there is no longer an answer to it.

So the decision is not "four conditions or one". It is **"do we still want RQ1 to be
defensible?"** Two coherent positions:

- **Keep RQ2, then collapse.** Run the ladder once on the topical subset (d8 in the
  timeline), freeze the winner, and run everything after that on the winner alone. This
  is what the plan already says to do, and it is already a one-condition project from d8
  onward. If this is what is wanted, almost nothing needs to change - see "If the goal
  is cost" below.
- **Drop RQ2 outright.** The study becomes RQ1 + RQ4 on an assumed stack, and the
  write-up carries an explicit limitation sentence saying the retrieval stack was chosen
  by prior, not measured. Cheaper and smaller; the cost is that "always-hybrid lost" and
  "our hybrid was badly configured" become indistinguishable.

## What breaks, concretely

### 1. The 51 gold labels were pooled over all four conditions

Every gold-labelled question in the bank records
`pooling_evidence.conditions_run = ["lexical","dense","hybrid","hybrid_rerank"]` - all 51
of them, checked, no exceptions. The bank's labelling rule is "label the union of all
retrieval conditions' top-k" (`horizon-scout.md` §Schema).

The existing 51 stay valid: pooling over four conditions and then evaluating one is
conservative, because the gold set can only be *more* complete than a single condition
would have made it. The danger is forward, not backward. If new questions pool only
`hybrid_rerank`, their gold sets are systematically narrower than the old ones, and the
bank stops being one instrument - a recall number computed across it would mix two
labelling standards. **If the change is made, pooled labelling should stay at four
conditions even if evaluation drops to one.** They are separate decisions that happen to
touch the same code, and collapsing both is the mistake to avoid.

### 2. The drafting pipeline uses the four conditions as evidence, not as a bake-off

This is the non-obvious one, and hyb-11 from today's run is the worked example.

A hybrid question is only legitimate if its structural filter is load-bearing - if
dropping the filter changes which project the text picks out. The drafter and critic
prove that by running pooled retrieval and showing a competitor outranks the gold in some
condition once the filter is gone. For hyb-11 (eye-tracking x ERC-STG, gold EVOLOR), the
gold still ranked 1 in dense, hybrid **and** hybrid_rerank with the filter dropped;
**only the lexical channel** put a competitor (AGE-MEMORY) above it. Lexical was the sole
evidence that the filter did anything, and on that thin basis I recommended holding the
question - you rejected it.

Under a hybrid_rerank-only pipeline that evidence channel does not exist. hyb-11 would
have shown a clean rank-1 gold, no visible competitor, and sailed through. The four
conditions are doing real adversarial work in authoring that has nothing to do with RQ2,
and dropping them makes the drafting gates weaker in a way that is invisible - questions
get *easier* to accept, not harder.

Recommendation: **keep `search_corpus` pooled at four conditions regardless of what the
study runs.** It costs an embedder call and a rerank call per draft, which is nothing
against the cost of a bad bank entry.

### 3. Code inventory

Where the four conditions live, if the change goes ahead anyway:

| file | what it does | change |
|---|---|---|
| `src/retrieval/registry.py` | `RETRIEVERS` tuple, `build_retriever` by name | leave the registry intact; it is the cheapest thing in the repo and deleting contestants buys nothing |
| `src/eval/mcp_server.py` | `search_corpus` pooled mode; "ANY condition failing fails the whole call" | **do not touch** - see §2 |
| `src/eval/bank.py` | `pooling_evidence` validation, `POOLING_EVIDENCE_KEYS` | **do not touch** - see §1 |
| `src/cli.py` | `bench-retrievers` already takes `--retrievers` to run a subset | nothing needed; the subset flag exists |
| `src/eval/retrieval_run.py` | untracked, new this session | check before assuming anything about it |
| `horizon-scout.md` | §RQ2, §Study A, §Freeze, d8/d11 in the timeline | the real edit, and it is a frozen-artifact edit needing an explicit decision |

Note what this table says: **the code barely needs to change.** `bench-retrievers`
already accepts `--retrievers hybrid_rerank`. `ask.py` and the router already run one
stack at runtime. The four conditions only appear together in two places - the bake-off
command and the drafting pipeline's pooled search - and the second of those should stay.

## If the goal is cost

If the motive is that four conditions are slow or expensive, the four-condition work is
not where the money goes. Measured this session: two orchestrator tabs producing five
accepted questions is the expensive thing (`agent-trace` and `src/eval/usage.py` have the
per-run numbers); pooled retrieval is four local llama-server calls with no API cost at
all. Collapsing to one condition saves local GPU seconds and no dollars.

If the motive is that the study is too big to finish, the cheaper cut is the one the plan
already contemplates: run RQ2 once on the topical subset, freeze the winner, and stop
running the ladder from d8. That gets the same "one condition everywhere" outcome for
every expensive downstream stage while keeping RQ1 defensible.

## Recommendation

Do not make this change as stated. Instead:

1. Run RQ2's ladder once, on the topical subset, as planned at d8.
2. Freeze the winner and run every later stage - RQ1's Study 2, RQ4 - on that one stack.
3. Keep pooled four-condition labelling in the bank and pooled four-condition search in
   the drafting pipeline permanently, because they serve authoring quality, not RQ2.

That is a one-condition project from d8 onward, with RQ2 answered rather than assumed,
and it needs no code change at all - only `--retrievers` on the bake-off and the existing
freeze step in `working-plan.md`.

If you want the change anyway, the decision to record is the RQ2 tombstone (same form as
the RQ3 and RQ5 tombstones already in `horizon-scout.md` §RQ3), plus the limitation
sentence for the write-up. Say so and I will draft both.
