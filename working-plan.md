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

**The seats ran on 2026-08-05, and the pilot found four defects.**
`run-bank --run-id round1-router` put all 58 questions through the router
condition, executed then judged, for $0.09. Judge health came back clean - 198
completions, 0 without parseable JSON, 0 parse retries, 0 NaN - so the DeepSeek
loose-JSON instrument is negative and the re-ask can be deleted if round one
repeats it. Retrieval is not the bottleneck either: hit@k 0.939, recall@k 0.761.
What the run found instead:

1. **The scoped route cannot express the bank's structured side.** 9 of the 11
   hybrid gold filters narrow on `euroscivoc`; the other 2 use `objective
   ILIKE`. `build_id_narrowing_prompt` omits `euroscivoc` from its allowed
   dimensions and orders the model to drop the research area, and
   `uses_subject_filter` strips the `objective` route. Exactly 1 of the 12
   narrowing queries in the run was correct. hyb-03 returned 219 projects
   against a gold of 4, from an unbracketed `A OR B AND C`; hyb-07 returned
   7,899; hyb-06 invented a coordinator role where the question says
   participant; adv-06 invented a bare `LIMIT 100`; sql-11 returned
   `organisationID` where the contract says `p.id`, which nothing checks.
   **This is the next fix and it blocks the baseline.**
2. **The 512-token answer cap is dead code.** `Ask.__init__` passes its shared
   client into `Synthesizer`, so `make_llm(max_tokens=ANSWER_TOKENS)` never
   runs and `ApiClient` sends no cap at all. Answers average 2,082 characters
   against references of 1,149. RAGAS `FactualCorrectness` runs `mode='f1'`,
   so the extra length costs precision: the shorter half of the judged
   questions scored 0.497 mean factual, the longer half 0.282.
3. **The adversarial rule cannot pass a correct refusal.** `derive_pass`
   requires `coverage == "full"`, but an ADV reference carries near-miss
   forensics no correct refusal would reproduce. adv-06 refused correctly and
   failed for not naming the 62 volcanology projects. `ragas_judge.py`'s own
   docstring states the intended rule as "nothing matches and invents
   nothing", and since rubric v0.2 the second half is not checked at all.
4. **Two SQL failures are scorer artifacts.** sql-02 returns the right five
   projects in the right order and fails `columns_unmatched` for carrying two
   extra columns and aliasing one pinned column;
   `project_to_answer_columns` accepts all-names-present or an exact count
   match and nothing between. sql-15 is the same. Changing it moves a recorded
   metric, so it needs a decision first.

**The router was rebuilt the same day, and misroutes fell 21% -> 3%.**
`r1-pilot` defined "structured" by arithmetic ("Counts, sums, averages,
rankings"), so the router read a subject classification, a funding scheme or a
project name as topic text: 12 of 58 misrouted, 7 hybrid questions to `vector`
and 5 sql questions to `scoped`. In 5 of those 7 the model NAMED the scheme or
country in its own reason and then denied a constraint existed.

Two prompts followed, both kept in `ROUTER_PROMPTS` and switchable by changing
`ROUTER_PROMPT_VERSION` alone; `_parse` accepts both contracts so an archived
prompt still runs.

- `r2-columns` defines structured as "the value sits in a column", names
  euroSciVoc beside country / date / money / scheme / role / status, and splits
  on whether the project's own words are needed rather than on whether anything
  is counted. 3 of 57 misrouted. All 3 stated the right facts and then chose a
  mode contradicting them.
- `r3-fields` (active) stops asking for the conclusion. The model reports
  `needs_project_text` and `structured_constraints`, and `derive_mode` picks
  the mode in code - the same split `judge.py:derive_pass` already uses. **2 of
  58 misrouted, no errors, sql exact 12/16.** `derive_mode` was right on all
  58 given the facts it received; both residual misroutes are reading failures,
  and one of the two routes correctly when re-asked (the seat runs at
  temperature 1.0, so neither the router nor the SQL path is deterministic -
  read the routing count, never a single question flipping).

Also fixed: the router's `max_tokens` was 128, which on the gen seat covers
reasoning tokens too. Under `r2-columns` the longest bank question (sql-16)
spent the whole budget and returned no content, failing loud through the
`finish_reason=length` path. It is 384 now.

Runs: `data/runs/round1-router` (r1-pilot, judged),
`data/runs/r2-columns-phaseA`, `data/runs/r3-fields-phaseA` (both `--no-judge`).
`data/runs/` is gitignored.

**Open from the router work:** `RouteDecision` carries the two facts, but
`ask.py` logs only `router_reason` and `router_fallback`, so neither
`ask.jsonl` nor `records.jsonl` records them. A future misroute therefore
cannot be told apart - reading failure or `derive_mode` failure - without
re-asking the router by hand, which is how the paragraph above was written.
About three lines in `ask.py` plus two fields on `AskResult`; it changes the
trace schema, so it is not done yet.

## Next

The order is `horizon-scout.md` §8. Immediately:

1. ~~Trim the bank to 49.~~ **Done 2026-08-03** - see above.
2. **Both seats swap to external APIs, FIRST** - **BUILT 2026-08-04, smoke
   still owed.** Everything the item specified is in and tested (562
   passing, including the item-3 retry tests):
   `src/openai_compat.py` is the one OpenAI-compatible transport -
   two frozen `ApiSeat`s (generator = `gpt-5-nano` with
   `reasoning_effort "minimal"`, temperature locked at 1 and
   `max_completion_tokens` as its cap parameter - re-decided 2026-08-05 from
   `gemini-2.5-flash-lite`, before any run; judge = `deepseek-v4-flash` with thinking
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
   rubric. **The smoke ran 2026-08-05** and went straight to full scale:
   `round1-router`, all 58 questions judged, $0.09. Both seats behaved. The
   judge behaved perfectly (0 unparseable completions of 198). The generator
   did not: its answer-length cap turned out to be dead code, and the
   verbosity that follows is costing most of the factual score (defect 2
   above). **So the seats do NOT freeze yet.** The pin waits until that is
   fixed and one more run shows the answer shape settled. Nothing else about
   either seat is in question.
3. ~~**Three pre-baseline fixes**~~ - **DONE 2026-08-05**, all from
   `docs/pilot-router-findings.md` Part 2, each with regression tests. Two had
   already landed on 2026-08-04 without this item being updated: the scoped
   provenance fix (§1) in `7891908` - synthesis gets a `filter_note` naming
   the survivor count and quoting the narrowing SQL, prompt at
   `s2-provenance`, and `rows_passed_to_gen` stays 0 on purpose because the
   covariate means DB rows in an LLM prompt and a note is not rows - and the
   SQL scorer (§3) in `9dc61c6` - both gold and generated results project to
   `answer_columns`, with `projection` and `columns_ok` recorded on the
   score. The judge retry (§4) landed 2026-08-05 as two changes against the
   new backend's actual failure modes: `call_api_gated` retries empty
   completions through the existing backoff ladder (the envelope is recorded
   in usage first, because those tokens were billed; empty at
   `finish_reason=length` is a token-cap misconfiguration and fails loud with
   no retry), and `OpenAICompatLLM` re-asks once, same prompt, when a judge
   completion has no parseable JSON - `parse_retries` and
   `parse_retry_recovered` join Judge health, and if recovered stays 0
   through the smoke and round one the re-ask gets deleted. The scoped fix
   changes what the system answers, so it is disclosed in the write-up as
   pre-baseline wiring, not as an improvement.
4. ~~**Author the last questions**~~ - **DONE 2026-08-04.** Adversarial was
   flipped into `/question-orchestrator` (bank schema v2.3: born-verified ADV
   with typed `absence_evidence` and `twin_id`) and 9 landed via batches K-M,
   overshooting the original 5. Ambiguous and compositional dropped for good
   (`horizon-scout.md` §6). Bank complete at 58.
5. ~~**Fix the router**~~ - **DONE 2026-08-05**, misroutes 12/58 -> 2/58. See
   above. `r3-fields` is active, `r1-pilot` and `r2-columns` are archived in
   `ROUTER_PROMPTS` and still switchable. Disclosed in the write-up under §6's
   relaxed freeze: the router now returns two facts and `derive_mode` picks the
   mode in code, so "one call returning one mode" is no longer an accurate
   description of it. What it is compared against, `always-hybrid`, is
   untouched.
6. **The four pilot defects, in this order.** 1 first - it is the only one that
   blocks a baseline:
   1. **The euroSciVoc filter.** Add the classification to
      `build_id_narrowing_prompt`'s allowed dimensions with the path-prefix
      idiom the schema docs already document, draw the line at "classified
      under X" rather than at "research area", and check in code that the
      narrowing query returns one column of project ids. Keep the ban on
      `objective` / `title` / `keywords`, which leaves hyb-13 and hyb-16
      unreachable by design - decide whether to accept that.
   2. **The dead answer cap**, plus a decision on whether prompt tightening
      is pre-baseline wiring or a round-two improvement. The cap itself is a
      bug either way.
   3. **The adversarial pass rule** - grade the refusal, record the near-miss
      facts without requiring them.
   4. **The SQL column projection** - needs your call, it moves a recorded
      metric. One line in `schema_docs.md` about acronym casing goes with it.
7. **Build the agentic condition.** Nothing exists yet - there is no `src/agent/`
   and `run.py:CONDITIONS` has only router / force-sql / force-vector /
   always-hybrid. It loops over capabilities that already exist
   (`retrieval/sql_path.py`, the runtime retriever from `retrieval/registry.py`,
   `retrieval/scoped.py`) - no new retrieval. Fix the edge policies BEFORE
   implementing: max steps, stop conditions, and the four failure buckets
   (planning error / execution drift / non-termination / recovery win). Traced
   like everything else - versioned prompt, content hash, cost through
   `src/eval/usage.py`. Freeze the scaffold after its pilot.
8. **The three rounds** (`horizon-scout.md` §2). Round one: 58 questions x
   {router, always-hybrid}, `--no-judge` first, read the answers, then
   `--resume` for verdicts. Round two from what round one shows plus the
   candidates in `docs/improvement-research-2026-07-27.md`. Round three is the
   agentic condition against the improved system. Then the write-up.
   **No baseline before item 6.1 lands.** Correct routing sends all 11 hybrid
   questions into the broken filter, so the hybrid route gets WORSE first -
   the 7 that reached `vector` by mistake scored 0.370 mean factual against
   0.247 for the 4 that reached `scoped` correctly, and under `r3-fields`
   hyb-06 and hyb-08 now narrow to zero projects and refuse outright. That
   drop is expected and is not a regression to chase.

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
  explorer's orientation block free thereafter. Low priority now that the bank
  is complete.
- **`tests/test_lexical.py` can still hit a DB lock, but only on a stale
  index.** Since `17a554c` the session fixture reuses the production FTS index
  read-only whenever `fts_index_is_fresh()` proves it matches the chunk table
  and config (sidecar `data/processed/horizon.fts-meta.json`). Only a genuinely
  stale index triggers the read-write rebuild, which errors while a
  `horizon-draft` MCP server from another session holds the file open. A lock
  conflict, not a regression.

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
