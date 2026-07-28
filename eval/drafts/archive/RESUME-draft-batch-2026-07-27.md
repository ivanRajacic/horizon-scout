# Resume brief - /draft-batch 2026-07-27 (vector x8)

Disposable working file, like the journal. Delete both after `promote-drafts`.

Halted 2026-07-27 by an Anthropic session limit that killed every in-flight agent at once.
**3 of 8 slots accepted. 5 unfinished.**

---

## 0. Launch

```bash
claude --effort medium "resume the /draft-batch run journaled at eval/drafts/draft-batch-journal-2026-07-27.jsonl - read eval/drafts/RESUME-draft-batch-2026-07-27.md first"
```

Medium, not high. The orchestrator routes, picks candidates and relays messages; none of that needs high effort, and the pilot's biggest cost leak was a high session effort amplifying re-reads of held evidence. Effort is set AT LAUNCH - a session cannot change its own.

## 1. Read, in this order

1. **This file.**
2. `.claude/skills/draft-batch/SKILL.md` - the loop you are re-entering. Do not improvise it.
3. `eval/drafts/draft-batch-journal-2026-07-27.jsonl` - the whole state. Line 0 is the batch header and now carries a `resume` block. Every later line is a slot; **latest line per `question_id` wins**. The five unfinished slots each carry a `resume` object with `blocked_by`, `state`, `next_action`, the candidate seed, `spawn_prompt_must_include`, and `agents`.

Pull the slot state out with:

```bash
./.venv/Scripts/python.exe -c "
import json
rows=[json.loads(l) for l in open('eval/drafts/draft-batch-journal-2026-07-27.jsonl',encoding='utf-8') if l.strip()]
latest={}
for r in rows[1:]: latest[r['question_id']]=r
for q in sorted(latest):
    r=latest[q]
    print('='*70); print(q, r['status'], 'cand', r.get('candidate_index'), r['cell'])
    if r.get('resume'): print(json.dumps(r['resume'], indent=2))
"
```

Do **not** read the `record` fields of the accepted slots into your context. They are large and you never need them again - `write-batch` reads them from the journal itself.

## 2. Environment

Both llama-servers must be up or every vector slot is blocked:

```bash
curl -s -m 5 -o /dev/null -w "embedder(8080): %{http_code}\n" http://127.0.0.1:8080/health
curl -s -m 5 -o /dev/null -w "reranker(8082): %{http_code}\n" http://127.0.0.1:8082/health
```

Both must return 200. Launch commands are pinned in `src/config.py:25` (embedder) and `:98` (reranker); the flags are load-bearing, do not tune them. If `/health` is 200 you do **not** need the health probe - the first pooled call just pays model-load cost, and the drafters pay it anyway. Last run lost ~7 minutes to probing.

The batch is pinned to index `be84cbad9182`, 190,248 vectors. If a drafter reports a different fingerprint, stop - the index was rebuilt and the accepted records' pooling evidence is stale.

## 3. First move: try the one salvageable agent

vec-14's candidate-1 drafter died mid-grounding with real work in its transcript:

```
SendMessage to ac44aaef20748499c: "You were cut off by a session limit mid-grounding. Carry on from where you stopped."
```

If it answers, the agent registry crossed sessions - try the other three the same way. If it errors, respawn all four from the journal. Either way this consumes the slot's single retry. The other three (`ae3a0af6414bc8da3` vec-11 critic, `ab18c5096bc3a1132` vec-13 drafter, `a2fa326dfbc2e3109` vec-15 drafter) had produced nothing when they died, so respawning is equivalent and simpler.

## 4. What each unfinished slot needs

| slot | cell | state | next action |
|---|---|---|---|
| vec-11 | L2 synthesis, paraphrase | candidate 1 **drafted and validated**; critic died before reporting | dispatch a fresh `question-reviewer` on the record, then a fresh `question-judge` |
| vec-13 | L2 comparison, exact-term | candidate 1 dispatched, drafter died with no output | respawn `question-drafter` (seed vector-48, carbon nitride) |
| vec-14 | L2 comparison, paraphrase | candidate 1 dispatched, drafter died mid-grounding | resume, else respawn (seed vector-07, computational creativity) |
| vec-15 | L3 survey, paraphrase | candidate 1 dispatched, drafter died with no output | respawn `question-drafter` (seed vector-22, lobbying) |
| vec-16 | L3 survey, paraphrase | candidate 2 **never launched** | dispatch `question-drafter` (seed vector-43, gig economy) |

Dispatch all five before handling any return, then handle returns in arrival order. Concurrency is unbounded.

**vec-11's record** is at a session-scoped scratchpad path recorded in its `resume` block and is probably gone. If so, re-run its drafter rather than reconstructing by hand - the journal's `record` field for vec-11 holds the validated record, so you can write it back out to a file and hand that path to the critic:

```bash
./.venv/Scripts/python.exe -c "
import json
rows=[json.loads(l) for l in open('eval/drafts/draft-batch-journal-2026-07-27.jsonl',encoding='utf-8') if l.strip()]
rec=[r for r in rows[1:] if r['question_id']=='vec-11'][-1]['record']
open('rec-vec-11.json','w',encoding='utf-8').write(json.dumps(rec))
" && ./.venv/Scripts/python.exe -m src.cli validate-record - < rec-vec-11.json
```

## 5. Budgets - all five are tighter than a fresh slot

| slot | passes spent | candidate | fallbacks left |
|---|---|---|---|
| vec-11 | 4 of 6 | 1 of 3 | 1 (dendrochronology) |
| vec-13 | 4 of 6 | 1 of 2 | 0 - **last candidate** |
| vec-14 | 4 of 6 | 1 of 2 | 0 - **last candidate** |
| vec-15 | 4 of 6 | 1 of 3 | 1 (human trafficking) |
| vec-16 | 4 of 6 | 2 of 3 | 0 - **last candidate** |

Each has room for one draft plus one fix round and little else. `fix_rounds_this_candidate` is 0 on all five - the current candidates are fresh.

## 6. Relaying the abandonment lessons

Four candidates were abandoned. Each slot's `spawn_prompt_must_include` carries its lesson already phrased correctly. **Relay the trap, never the verdict and never the dead question's content** - the verdict prejudices a node that must judge independently, the content anchors the new drafter. The lessons, in short:

- **vec-13:** write the membership criterion into the question in words a reader can apply to whatever text a retriever returns, not as a rule about which stored field a phrase sits in. Re-count the gold under every plausible reading of your own wording before settling.
- **vec-14:** check candidate gold members' texts are actually DISTINCT before building a comparison - two projects sharing a consortium can share report text near-verbatim. And check any absolute in the scope ("only", "without any") against what projects DELIVERED, not just proposed.
- **vec-15:** if the question has two arms, make both the same width and re-derive the gold count under each arm read separately. Check each member's claim survives in its delivered work, not only its stated aim.
- **vec-16:** test any exclusion clause against every member of your gold, not only against what you wrote it to exclude. Prefer a positive criterion that selects your gold over a negative one that fences out neighbours.

## 7. The loop, unchanged

Per slot: drafter -> `validate-record` -> critic -> judge -> ACCEPT / FIX / ABANDON.

- Every transition goes through `journal-append`. Never hand-edit the journal, never write it with a script.
- Records are **byte-identical** - what the drafter returned is what gets staged.
- Re-attacks after a fix go to the **same warm critic** with a plain statement of what changed, plus any `evidence_carried_forward` disclosure verbatim. Never send it the judge's rulings or the budget.
- Fixes go to the **same warm drafter**. Judges stay warm across their slot's rounds.
- You never overrule the judge. You may sharpen a FIX direction as you relay it, never reverse a disposition.

## 8. Gotchas that cost time last run

- **`jq` is not installed.** Merge a record into a payload with a python one-liner and pipe into `journal-append --payload -`.
- **Heredocs break the Bash tool** when payloads contain apostrophes. Write payload JSON to files and redirect stdin.
- **HTML entities.** Agent output arrives with `&gt;` for `>`. Decode before writing records - `journal-append` refuses entities at the boundary, which is the check working, not a bug.
- **`VECTOR_SUBTYPE_LEVELS`** (`src/eval/bank.py:58`): identify/detail are L1, comparison/synthesis are L2, survey is L3. I dispatched vec-16 as L3+synthesis, which is illegal; check any cell you touch.
- **Sweeps must cover `report_text`**, not just `project.objective`. The indexed corpus chunks report sections, and two separate slots had missed-gold findings that lived exactly there.

## 9. Close-out

Only once **all eight** slots are ACCEPTED, FAILED or BLOCKED:

```bash
./.venv/Scripts/python.exe -m src.cli write-batch eval/drafts/draft-batch-journal-2026-07-27.jsonl
./.venv/Scripts/python.exe -m src.cli agent-trace --orchestrator --steps --since 2026-07-27T00:29:17
```

`write-batch` refuses to overwrite an existing pair; ask before re-running with `--suffix -2`. `agent-trace` will only see agents from the resumed session - the original run's costs are not recoverable, so report that gap rather than presenting a partial trace as the batch total.

Then the user ticks the report and:

```bash
./.venv/Scripts/python.exe -m src.cli promote-drafts <report path>
```

## 10. Report to the user at close-out

Both output paths, the tally, any cell the judge flagged suspect (none so far - the four abandonments have unrelated causes), id gaps left by failed slots, the cross-check flag count, and the `agent-trace` rollups. Carry forward these two notes from the accepted three:

- **vec-17** carries a RECORDED finding: GOF2.0 (101017689) is a marginal gold member - its summary reads as validating others' COTS components while its objective names its own separation-assurance architecture. The cell is L3 at either 9 or 10 gold. Flagged for a promote-time veto.
- **vec-12** stores `Boele Ship` and `Koenigsberg` where the corpus writes them with diacritics. Consistent across both names, but a scoring-normalisation matter.
