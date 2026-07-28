# Explorer pipeline - cost & quality audit

*ARCHIVED 2026-07-26. All decisions here are implemented (see `working-plan.md` items 3 and 6). Kept as the record of the decisions and their rationale.*

*Opened 2026-07-24, immediately after the drafting-pipeline pass (`drafting-pipeline-audit.md`). Same constraint: same safety, less spend. Decisions below were resolved with Ivan and are IMPLEMENTED unless marked otherwise.*

## Why this exists

`/explore-corpus` is the width mechanism for the bank: it decides which parts of the corpus the drafting skills ever see. Two problems, one structural and one economic:

1. **Exploration was not cumulative.** Every run re-derived the corpus from a cold start, and the Coverage ledger was *regenerated* each time ("Regenerated at cp2"). Nothing accumulated, and nothing told the next run where it had already been. That is a diversity problem before it is a cost problem - the bank's known failure mode is clustering (7/10 funding questions, AQUA*/EUROfusion/ERC repeats).
2. **The same class of token leaks the drafting pass had just fixed**, plus one it did not have (a growing artifact rewritten in full on every run).

## Measured baseline (from `data/logs/draft_mcp.jsonl`, cp1 + cp2 runs)

| Item | Measured |
|---|---|
| `run_sql` result payload | mean 885 chars (~220 tok); 99 queries = ~22k tok total |
| `get_project_text` per project | ~8.1k chars (~2.0k tok) full payload |
| ...`workPerformed` + `finalResults` share | ~3.9k chars (~983 tok), **48%** |
| ...`objective` + `teaser` alone | ~2.1k chars (~530 tok) |
| Projects read on 2026-07-23 | 95 (~190k tok), incl. **4 calls at the 10-id max** |
| `run_sql` in the cp2 run (2 subagents) | **54**, against a stated ~20/slice budget |
| `corpus_profile.md` at cp2 | 32.6k chars (~8k tok), read *and* rewritten every run |
| Zero-row queries traced to one wrong doc character | **8 of the 9 in the entire log** |

**The finding that reframed the whole audit: SQL result bytes are not the cost.** 99 queries returned ~22k tokens in total. The cost is *turns* - an Opus reasoning step per tool call over a growing context - plus project-text bulk, plus the whole-file rewrite. Every optimization below targets turns, bulk, or the rewrite; none targets row caps.

## The ground-truth bug (found during the audit, fixed first)

`schema_docs.md` documented `euroSciVocPath` as `'/engineering and technology/.../biofuels'`. **No row starts with a slash** - `SELECT COUNT(*) FROM euroscivoc WHERE euroSciVocPath LIKE '/%'` -> 0 of 111,614. The sibling column `euroSciVocCode` *does* (e.g. `/25/61/383`), which is why the wrong form read as plausible and survived review.

Consequences, in increasing order of seriousness:

- 8 of the 9 zero-row queries in the entire MCP log are this bug; both cp2 subagents hit it independently and had to spend recovery turns probing the real format.
- The cp2 run actually *discovered* it and recorded the correction inside `corpus_profile.md` - but a note in a data file does not fix the prompt asset, so the next run would have hit it again.
- `schema_docs.md` is embedded verbatim in the **runtime** SQL-path system prompt (`sql_path.py:80`). Haiku was being told a wrong literal format and would have generated zero-row SQL for any euroSciVoc question. This was a live correctness bug in the system under test, not merely wasted explorer turns.

**Resolution (Ivan: "fix the schema docs, that is quite literally the ground truth for everything"):** corrected, plus the 6 top-level branches with counts and the `split_part` level idiom. `SCHEMA_DOCS_VERSION` `sd1-pilot` -> `sd2`; `SQL_PROMPT_VERSION` `q1-pilot` -> `q2-pilot` (no prompt text changed there, but the fingerprint hashes the doc).

**Study 0.5 interaction, resolved deliberately.** `working-plan.md` pre-registers Study 0.5's single intervention as "value descriptions in the SQL prompt", and `schema_docs.md` is that variable. A wrong literal path format is a **bug fix belonging in the baseline**, not a value description - value descriptions explain what enum codes *mean*. Study 0.5 has not run, so no before-condition data existed to contaminate. The baseline is now pinned in `working-plan.md` as `sd2`/`q2-pilot`. The 17 bank entries authored earlier keep their `sd1-pilot` hash as honest provenance of what they were authored against; `bank.py:285` validates that field as a non-empty string and never re-checks it against the live doc, so `validate-bank` is unaffected (verified: 17 questions, OK).

A secondary correctness fix came out of the same thread: the skill's slice partition and width rule are stated in terms of "top-level branches", but cp1/cp2 used **second-level** categories under that name (`split_part(...,'/',2)`, 40 values) while there are only **6** top-level branches (`,'/',1`). Vocabulary now pinned in `schema_docs.md`, the skill, and `## Structural findings` (`sf-02`).

## Decision 1 - exploration becomes cumulative (the main change)

Ivan's framing: *"we want a markdown file that says, this part of the database is about that, and it could be good for these kinds of questions... we want to expand our knowledge of the database so we can have diverse questions"* - and, on granularity: *"we don't need really specific clusters, we just need a general way and abstraction to keep count of what we have explored and what we haven't."*

The whole mechanism is **one status table over one pre-existing partition**. No taxonomy is invented.

- **Denominator:** `euroscivoc` already covers the corpus. **46 buckets** = 40 named second-level categories + 5 top-level-only paths + 1 `(unclassified)` bucket (3,153 projects with no euroSciVoc row). Stated caveat: a project carries 1-5 euroSciVoc rows, so buckets overlap - a cover, not a strict partition. Acceptable for a checklist, and cheaper than inventing our own clustering.
- **Three statuses:** `unexplored` -> `mapped` (a `## Corpus map` entry exists) -> `mined` (a bank question was drawn from it).
- **`## Frontier`** - the table. **The only section needed to plan a run**: ~46 rows instead of the 8k-token whole-file read, and "where have we not been" is a filter on one column. `status`, `seeds` and `bank` are recomputed each run; `bank` is traced through `gold_project_ids` -> `euroscivoc`.
- **`## Corpus map`** - append-only region entries carrying `about:` / `texture:` / `good for:` / `thin for:`. Written from text that was READ, never from the tag: cp1 established that euroSciVoc leaf labels lie on interdisciplinary and MSCA projects (`ethnomycology` on an aquatic-fungi ecology project; `sustainable architecture` on district heating). An entry that paraphrases its own tag is worthless.
- **`## Structural findings`** - open list, no denominator, for the non-topical material (trap pairs, absences, dual-encoded facts, value inventories) that serves the SQL / Adversarial / Ambiguous routes. Seeded with `sf-01` (the leading-slash bug) and `sf-02` (the branch-level vocabulary).

Two properties beyond bookkeeping: **slice assignment now comes from the frontier**, so a run cannot re-explore where it has been; and **diversity is mechanical** - "prefer `mapped`-but-not-`mined`" is a column filter, not a judgment call, in both the explorer and `/question-orchestrator`.

**State at cp3:** `mapped 0/46 | mined 18/46 | unexplored 28/46`. 18 buckets carry cp1/cp2 candidate seeds but no map entry, so they read `unexplored` - seeds are not a map. Mapping them is the next run's first job.

## Decision 2 - the efficiency fixes

- **Explorer effort medium -> low** (`corpus-explorer.md`), matching drafter and reviewer (both opus/low). Justified because the explorer's judgment output is *advisory by contract*: the skill says so, and the V5 two-tier grounding decision has the drafter re-verify every advisory claim in full while trusting only the executed SQL, which the merge pass spot-checks. Nothing downstream trusts the explorer's reasoning. This is the same argument that justified the reviewer's pin, and it is stronger here.
- **`get_project_text` gains `fields` and `max_chars`** (`src/eval/mcp_server.py` - the only code change). Both default to off, so the drafting skills' full-evidence reads and the V2 batched-read decision are untouched. Measured on three real projects: full = 5,808 tok, `fields=["objective","teaser"]` = 1,761 tok (**-70%**), plus `max_chars=4000` = 1,084 tok (**-81%**), with the theme still legible. `max_chars` also fixes the unenforced "<= 3 ids per call" rule - it was prompt-only and the agent broke it 4 times out of 22 (10-id calls, ~20k tok each, overflowing to a file and forcing chunked re-reads). The global `PROJECT_TEXT_CAP` stays at 10 because the drafter deliberately batches; a char ceiling bounds any caller regardless of id count. 8 new tests.
- **Orientation block.** The orchestrator builds the shared corpus facts once (~6 queries) and pastes them into every spawn prompt. Both cp2 subagents had independently run the *identical* branch-inventory query (21:31:21 and 21:31:48) and both re-probed the path format; roughly 8-12 of each subagent's ~27 queries were orientation. ~1.5-2k prompt tokens buys back ~10 turns per subagent.
- **Turns, not rows.** The explorer's budget is reframed explicitly: one query per *question*, not per slice value, folding values into `GROUP BY` / `CASE` / `UNION ALL` / scalar sub-selects. The cp2 subagents already demonstrated the batched form themselves (21:32:29, 21:32:49, 21:33:32), so this is a rule change, not a new capability. Budget ~20 -> ~12 `run_sql` + ~4 `get_project_text`.
- **Write by insertion, never rewrite.** Expansion was already append-only *by contract*, but step 1 read the whole profile and step 3 re-emitted all eight sections with untargeted ones "carried over verbatim" - ~8k tok in and ~8k tok out today, and ~30-37k each way at full-run scale, for sections that did not change. Output is the expensive direction. New rule: `Grep` for the H2 line numbers, `Read` only the narrow ranges being changed (which also satisfies read-before-edit), then `Edit` in place. Removes the transcription-drift risk inside "carried over verbatim" as a side effect.
- **Always spawn; the inline exception is deleted.** A target <= 8 used to be explored inline in the orchestrator session - which the same file pinned at Opus HIGH, and whose context then had to survive the whole merge, spot-check and write phase. That ran the cheapest work in the most expensive place. Its stated rationale was transparency, now covered by the wave progress notes and the telemetry line.
- **Orchestrator effort HIGH -> medium.** Its jobs are partition, spawn, concatenate, renumber, dedup, update the frontier, spot-check, write. `drafting-pipeline-audit.md:21` had already flagged orchestrator high-effort as an unclosed leak on the drafting side.
- **Tool surface 8 -> 4** (`ToolSearch`, `run_sql`, `get_project_text`, `get_schema_docs`). Dropped `get_bank_questions` and `get_corpus_profile` (orchestrator concerns - the frontier and bank usage are maintained in the merge pass, and the section spec plus orientation block arrive in the prompt), and `Read`/`Grep`, which mostly invited wandering into repo files (`explore-corpus/SKILL.md` alone is 16k chars).
  - **Deviation from the approved plan, deliberate:** the plan said to drop `get_schema_docs` too, as redundant with the orientation block. It is kept, gated to structural slices and to columns the orientation block does not describe. The orientation block only covers *topical* facts; a structural subagent exploring column families and traps genuinely needs the schema, and pasting all 6k chars into every prompt unconditionally would cost the same tokens it saves.
- **Per-run telemetry.** The merge pass appends a line to the Header run log: subagents, `run_sql` count, projects read, duration, frontier delta. All derivable from `draft_mcp.jsonl`. The drafting audit had to reconstruct spend as "~70% of a 5-hour window"; this makes the next pass measurable.

## Decision 3 - drafting side (minimum touch)

`/question-orchestrator` step 2 now reads `frontier` alongside the route sections, **prefers candidates from `mapped`-but-not-`mined` buckets**, and passes the bucket's map entry (`good for:` / `thin for:` / `texture:`) to the drafter with the candidate block. That is the region knowledge exploration paid for, reaching the drafter so it knows what shape of question the region can support before it starts grounding. No change to the draft -> review -> fix loop.

## Files changed

`src/retrieval/schema_docs.md` (sd2), `src/retrieval/sql_path.py` (q2-pilot), `src/config.py` (both version bumps, cp3), `src/eval/mcp_server.py` + `tests/test_mcp_server.py` (the only code change), `.claude/agents/corpus-explorer.md`, `.claude/skills/explore-corpus/SKILL.md`, `.claude/skills/question-orchestrator/SKILL.md`, `src/retrieval/corpus_profile.md` (cp3 sections), `working-plan.md`.

## Not changed, and why

- **`run_sql` row caps.** 99 queries totalled ~22k tok, mean 885 chars. Tightening caps would cost quality for nothing - and the "turns, not rows" rule actively pushes toward *wider* results.
- **The concurrency cap of 4.** A serialization knob on one DuckDB connection, not a token knob.
- **The merge spot-check** (2 re-executions per section, ~220 tok each). The only correctness net on the artifact; kept.

## Decisions taken (2026-07-25) - deterministic nodes + typed state, IMPLEMENTED

Second pass, opened after the same lens that produced the `/question-orchestrator` four-node re-architecture was turned on the explorer. The cp3 pass fixed **efficiency**; it did not touch **who does what**, so all three defects that re-architecture diagnosed on the drafting side were still here:

1. **Expensive nodes doing work that has a right answer.** The Opus orchestrator recomputed the frontier, built the orientation block, assigned ids, dedupped, checked the width rule, sampled two queries per section as its only correctness net, counted telemetry out of a log file, and hand-wrote the insertions.
2. **Untyped state.** Subagents returned markdown; the orchestrator held every block through merge, spot-check *and* write, then re-emitted it - the context-bloat leak, and the failure mode the 2026 orchestrator-worker literature names (payload re-reads, ~15x a chat turn).
3. **The node that authors also certifies.** Nothing independent checked a candidate. Downstream the drafter paid: `hyb-02` burned a full grounding pass on a combo that was never viable - **P5**, open since the drafting audit.

**Five deterministic nodes, one typed journal, one topology change, one shared standard, and exactly one new model node.** Deliberately NOT added: a per-slice adversarial critic. It would roughly double explorer spend to duplicate checks code now performs exhaustively for free, and the explorer's output is advisory by contract (V5 has the drafter re-verify every advisory claim). The over-splitting anti-pattern applies squarely.

| Node | Where | Does |
|---|---|---|
| `frontier-report` | `src/cli.py` + `src/eval/explore.py` | the whole old startup in one call: 46-bucket frontier recomputed (status / seeds / bank, traced `gold_project_ids` -> `euroscivoc`), slice partition largest-first, orientation block, next free ids, and the seed standard |
| `precheck_candidate` | tool on `src/eval/mcp_server.py` | the explorer's in-loop self-gate - the same code close-out runs, so nothing can pass one and fail the other |
| `verify-evidence` | `src/cli.py` | re-executes EVERY claim in the journal and checks every recorded number reproduces |
| `explore-crosscheck` | `src/cli.py` | width, entity spread, near-duplicates vs run and profile, supply vs targets |
| `write-profile` | `src/cli.py` | journal -> insertions, frontier update, both version labels, telemetry line from `draft_mcp.jsonl` |

**`verify-evidence` is the capability win.** The old net was "re-execute at least two embedded queries per produced section", performed by the most expensive node at a sampling rate that would miss most defects. Every claim already carried its SQL and its key result by contract, which made the whole set machine-checkable - so it is now all checked, in a subprocess, at zero model cost. It also settles the map's own failure mode: entries carry a `read:` line of the project ids the `about:` was written from, and the checker confirms they exist, carry text and sit in the bucket, and flags an `about:` that is mostly the bucket label back. The honour rule became a gate.

**Typed journal** (`eval/exploration/journal-<date>.jsonl`), same discipline as the batch journal - envelope always valid, payload opaque, latest line per `slice_id` wins. `evidence` is `{sql, key_result}` (plus `expect_empty` when the absence IS the claim), and candidates carry `satisfying_count` / `survivor_count`: those two numbers are what let a level that contradicts its own count, or a survivor set outside its subtype's drafting window, fail before a drafter is ever spawned. **That closes P5.**

**Topology:** waves -> sliding window (dispatch on completion, 4 still in flight - the cap is a DuckDB-connection knob, not a token knob), with per-slice close-out. That delivers for the explorer the incremental checkpointing still deferred on the drafting side: a killed run keeps every verified slice.

**Shared standard:** `bank_brief.md` gained section 7 (Seeds) - `BANK_BRIEF_VERSION` `bb1` -> `bb2` - and `frontier-report` pastes it into every spawn prompt. The explorer decides which seeds the drafter, critic and judge ever see, and had never read the standard they are held to.

**One new model node:** `explore-critic` (opus/low, `.claude/agents/explore-critic.md`), once per run, reading only the journal's typed summaries - never payloads. It answers what no deterministic node can: what is *missing* - a region unread, a modality not run, a thin axis, an unserved cell. It authors `## Coverage notes`, reports, and has no gate and no re-spawn power. ~2-3k tokens, which the write phase alone more than pays for.

**Orchestrator effort medium -> low.** With partition, merge and write in code, what remains is parse, spawn, relay, present.

### Found by running it, not by reading it

- **The frontier was wrong at cp3: `mined 18/46` should have been 19.** `vec-03`'s gold project has no euroSciVoc row, so it sits in the `(unclassified)` bucket; the hand-tracing missed it. The recompute is now the authority and the hand-maintained column is gone.
- **A `GROUP BY fundingScheme` reports 57 values where schema_docs says 56** - the NULL row reads as a scheme. The orientation block now excludes and counts it separately, because a value count that disagrees with the schema docs is exactly what a subagent would waste a turn re-deriving.
- **Rehearsing `write-profile` into a scratch copy bumped the REAL `CORPUS_PROFILE_VERSION`.** The label describes the canonical profile, so the writer now refuses to move it when writing anywhere else. Caught by doing it.
- **The cross-check saw this run's own insertions**, so every new candidate was a near-duplicate of itself. Flags are now computed against the profile as it stood before insertion.
- **Verification fetched only 201 rows**, so a claim quoting a larger row count would have failed wrongly. Raised to 5,000 with an explicit truncation rule.

All five are regression-tested.

**Files:** `src/eval/explore.py` (new), `tests/test_explore.py` (new, 51 tests), `.claude/agents/explore-critic.md` (new), `src/cli.py` (4 subcommands), `src/eval/mcp_server.py` + `tests/test_mcp_server.py` (`precheck_candidate`; its private `_profile_sections` deleted in favour of the one in `explore.py`, so the splitter the MCP serves and the one the writer inserts by are one parser), `src/eval/batch.py` (`entities_in` extracted + public lexical aliases, so "too similar" means the same thing at both ends), `src/eval/bank_brief.md` + `src/config.py`, `.claude/skills/explore-corpus/SKILL.md`, `.claude/agents/corpus-explorer.md`, `src/retrieval/corpus_profile.md` (format only - `read:` and the typed evidence shape; no existing content rewritten).

**Not run:** a live `/explore-corpus`. `pytest` = **283 passed, 6 errors** - the 6 are `tests/test_lexical.py` (session fixture needs a read-write DuckDB handle, blocked by another session's MCP server holding a cached `LexicalRetriever` connection); confirmed not a regression by re-testing with this change stashed out. 289 tests total; re-run when the file is free. **The remaining work is tracked in `working-plan.md` optimization-track item 7** - that is the resume point.

## Next actions

- [ ] Run `/explore-corpus map=6` - the first run of the new design, and the new baseline. Confirm: no orientation re-derivation in `draft_mcp.jsonl`, frontier advances `mapped 0/46 -> 6/46`, every map entry carries real `read:` ids, `## Coverage notes` written by the critic, telemetry line present, and a deliberate mid-run kill leaves completed slices intact in the journal.
- [ ] Decide whether `## Distributions` should be filled in the same run (it serves Study 0.5 and would make the orientation block free thereafter).
- [ ] Still deferred from the drafting pass: **P2** incremental checkpointing in `/question-orchestrator` - now demonstrated on the explorer side, so the pattern to copy exists.
