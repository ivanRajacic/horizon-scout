# Working plan - execution state

*The plan is `horizon-scout.md`. This file is only where things stand.*
*Opened 2026-08-03. History before that date:
`docs/archive/working-plan-through-2026-08-03.md` (64 KB of dated status entries -
every pipeline audit, every batch run, every promotion, with the reasoning).*

## Where things stand

**Built and done.** M1-M4: CORDIS ingest (35,389 projects in DuckDB), chunker and
the 190,248-vector FAISS index, four retrieval conditions behind one interface,
guardrailed text-to-SQL, router, synthesis, `ask.py` end to end with tracing. M5
infrastructure: the bank schema and validator, the RAGAS judge with the refusal
overlay, two checkpointed runners, cost telemetry at the one transport gate, and
both authoring pipelines (`/question-orchestrator`, `/explore-corpus`) with their
deterministic nodes and typed journals.

**The authoring pipeline is frozen.** Four audit rounds; no fifth
re-architecture. `optimization/` holds the numbered plans, all implemented except
`05-workspace-store.md`, which is an unapproved proposal.

**Bank: 58 questions, complete** (2026-08-04), all execution-verified - sql 16,
vector 22, hybrid 11, every ladder cell exactly at target, plus 9 adversarial
(three per costume route, three per subtype, each twinned to an answerable
control) from `/question-orchestrator` batches K-M. Ambiguous and compositional
are dropped for good (`horizon-scout.md` §6); no authoring work remains.

**The vector trim ran 2026-08-03**: 40 -> 22, the 18 archived to
`eval/archive/bank-trimmed-2026-08-03.jsonl` with a recorded reason each, through
a new `archive-questions` command rather than by hand. Landed exactly on the
cell targets with term_style at 11/11 and all five subtypes intact.

It surfaced two real defects in the batch tooling, both now fixed with
regression tests. `next_ids` counted only the bank and the staged drafts, so
archiving vec-42 handed **vec-42 straight back** to the next drafter - two
different questions, one id. And `gap_report` excluded banked-or-rejected ids
but not archived ones, so vec-25's still-on-disk staged twin reappeared as
pending work the batch was expected to finish. Both are the same defect class
already fixed once for promoted records and once for rejected ones; the fix is
`batch.archived_ids`, which both now consult. The batch tests were also leaking
into the real `eval/archive/` through default arguments - they now pass an
explicit archive dir.

**The runtime moved from dense-only to hybrid+rerank on 2026-08-03.** This was
not a config flip: `ask.py` had been building a bare `VectorSearcher` and handing
it to the scoped path too, so neither topical route had ever touched BM25 or the
reranker. It changes what the system answers, on both topical routes, and it
means every number recorded before that date was measured on a stack we no longer
run. Rows in `data/logs/ask.jsonl` with no `versions.retriever` are pre-change.

**Docs consolidated 2026-08-03.** Four documents each claiming to be the plan -
and disagreeing on bank size, what is measured, the retrieval story and the judge
- collapsed into `horizon-scout.md` v5. The other three plus the retrieval note
are in `docs/archive/`, unedited. One of the contradictions was load-bearing:
`gap-report` parses the allocation table live out of the plan doc, so the
retired ~113 target had been driving drafting the whole time.

## Next

The order is `horizon-scout.md` §8. Immediately:

1. ~~Trim the bank to 49.~~ **Done 2026-08-03** - see above.
2. **Both seats swap to external APIs, FIRST** - **BUILT 2026-08-04, smoke
   still owed.** Everything the item specified is in and tested (552
   passing): `src/openai_compat.py` is the one OpenAI-compatible transport -
   two frozen `ApiSeat`s (generator = `gemini-2.5-flash-lite` with
   `reasoning_effort "none"`, judge = `deepseek-v4-flash` with thinking
   disabled and temperature 0), each with its own semaphore, backoff on
   transient HTTP failures, and cost computed from per-Mtok prices pinned in
   `src/config.py` (these dollars are billed, unlike the priced `claude -p`
   figures). `ApiClient` sits beside `ClaudeClient` in `src/llm.py`
   (`GEN_BACKEND="api"` default); `OpenAICompatLLM` sits beside
   `ClaudeCliLLM` in `src/judge/ragas_backend.py` and counts completions
   without parseable JSON - the DeepSeek loose-JSON instrument - surfaced as
   the report's new `## Judge health` section (`JUDGE_DEFAULT="deepseek"`;
   the rubric overlay now routes through the recorded gate too, so ADV judge
   spend lands in usage, which the claude path never did). Both transports
   record through `src/eval/usage.py` at their one gate. The report moved
   from pass rates to continuous scores: judged routes show mean factual
   with min/median/max, sql stays exact execution, ADV stays the refusal
   rubric. **Remaining before item 3, the immediate next step:** set
   `GEMINI_API_KEY` and `DEEPSEEK_API_KEY` in the environment, then smoke
   both seats with a small judged run per route, e.g.
   `run-bank --routes sql --limit 3`, then `--routes vector --limit 3`
   (add an adversarial id to exercise the rubric overlay). Read the
   answers, the verdicts and the report's Judge health section - the point
   is to see both seats behave before anything depends on them. The seats
   freeze on that smoke; after it, never change either again.
3. **Three pre-baseline fixes**, all from `docs/pilot-router-findings.md` Part 2:
   scoped mode passes `rows_passed_to_gen = 0` so the generator hedges on its own
   filter (§1); the SQL scorer compares whole rows instead of `answer_columns`,
   and `columns_ok` is computed and never used (§3); judge calls have no retry -
   the pilot's `stop_reason:"tool_use"` class that killed 2 of 9 calls was
   `claude -p`-specific and dies with the swap, so the retry is written against
   the new backend's actual failure modes (§4). Each needs a test. The scoped
   fix changes what the system answers, so it is disclosed in the write-up as
   pre-baseline wiring, not as an improvement.
4. ~~**Author the last questions**~~ - **DONE 2026-08-04.** Adversarial was
   flipped into `/question-orchestrator` (bank schema v2.3: born-verified ADV
   with typed `absence_evidence` and `twin_id`) and 9 landed via batches K-M,
   overshooting the original 5. Ambiguous and compositional dropped for good
   (`horizon-scout.md` §6). Bank complete at 58.
5. **Build the agentic condition.** Nothing exists yet - there is no `src/agent/`
   and `run.py:CONDITIONS` has only router / force-sql / force-vector /
   always-hybrid. It loops over capabilities that already exist
   (`retrieval/sql_path.py`, the runtime retriever from `retrieval/registry.py`,
   `retrieval/scoped.py`) - no new retrieval. Fix the edge policies BEFORE
   implementing: max steps, stop conditions, and the four failure buckets
   (planning error / execution drift / non-termination / recovery win). Traced
   like everything else - versioned prompt, content hash, cost through
   `src/eval/usage.py`. Freeze the scaffold after its pilot.
6. **The three rounds** (`horizon-scout.md` §2). Round one: 58 questions x
   {router, always-hybrid}, `--no-judge` first, read the answers, then
   `--resume` for verdicts. Round two from what round one shows plus the
   candidates in `docs/improvement-research-2026-07-27.md`. Round three is the
   agentic condition against the improved system. Then the write-up.

## Committed 2026-08-04

A backlog spanning four days of work went in as five commits, one per unit:

1. `runtime: hybrid+rerank everywhere, replacing the dense-only stack`
2. `eval: run-retrieval, the four-condition retrieval ladder runner`
3. `bank: archive-questions, and the vector route trimmed 40 -> 22`
4. `records: batches D-J, the 07-28/29 exploration runs, corpus profile to cp8`
5. this doc consolidation

`src/cli.py` carried changes belonging to three of them and was staged by hunk.

## Open, carried forward

- **`/review-bank` is STALE** against the reframed critic's vocabulary - its
  report format still expects verdicts and severities that no longer exist. A
  note sits at the top of that file. Not blocking anything.
- **`## Distributions` in `corpus_profile.md` is unfilled.** It would make the
  explorer's orientation block free thereafter. Low priority now that the bank is
  nearly closed.
- **`tests/test_lexical.py` needs exclusive DB access.** Its session fixture
  calls `build_fts_index()`, which needs a read-write DuckDB handle, and any
  `horizon-draft` MCP server from another session holds the file open. A lock
  conflict, not a regression - the tests pass in a session that has not used the
  MCP tools.

## Known notes carried on promoted bank records

Recorded at promotion time, judged not worth a redraft, and worth knowing when
reading results.

All four vector notes (vec-27, vec-29, vec-33, vec-34) are gone - the trim's rule
dropped noted questions first, so they went in the 18. What remains is on the
hybrid route, which was not trimmed:

- **hyb-07**'s reference attributes the Nevado del Ruiz seismic tomography to
  OVSM and INGV, but 793811 pairs OVSM with the land gravity campaign and the
  tomography with INGV alone. Peripheral to what the question asks; it does not
  punish a correct answer.
- **hyb-07**'s `pooling_evidence` counts rejected survivors rather than rejected
  pooled candidates, so its arithmetic does not close.
- **hyb-09**'s Eternum 826866 clauses describe the project's stated method, not
  completed work.
- **vec-16** and **vec-28** are permanent id gaps. vec-28 is the one hand edit
  ever made to `eval/bank.jsonl`, on explicit instruction, after two batches
  independently drafted the same constructed-wetlands question.
