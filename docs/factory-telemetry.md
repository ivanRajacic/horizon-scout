# Factory telemetry

Every number below is computed by `src/eval/telemetry.py` from the batch journals, the MCP log and the subagent transcripts. Counts are taken from the last journal line per slot, because the journal restates a slot's whole envelope on every event.

## The funnel

| batches | slots | candidates tried | accepted | failed | FIX rounds | candidate abandons |
|---|---|---|---|---|---|---|
| 14 | 43 | 58 | 42 | 1 | 22 | 6 |

Adjudication rounds per slot (judge decisions until the slot closed): 1: 22, 2: 18, 3: 1, 4: 1, 5: 1

## Per batch

| batch | date | slots | accepted | FIX rounds | abandons |
|---|---|---|---|---|---|
| batchA | 2026-07-28 | 3 | 3 | 0 | 0 |
| batchB | 2026-07-28 | 3 | 3 | 2 | 0 |
| batchC | 2026-07-28 | 3 | 3 | 4 | 1 |
| batchD | 2026-07-28 | 3 | 3 | 2 | 1 |
| batchE | 2026-07-28 | 3 | 3 | 0 | 0 |
| batchF | 2026-07-28 | 3 | 3 | 3 | 0 |
| batchG | 2026-07-29 | 3 | 3 | 1 | 1 |
| batchH | 2026-07-29 | 3 | 3 | 2 | 0 |
| batchI | 2026-08-01 | 3 | 3 | 4 | 2 |
| batchJ | 2026-08-01 | 3 | 2 | 0 | 1 |
| batchK | 2026-08-04 | 3 | 3 | 1 | 0 |
| batchL | 2026-08-04 | 3 | 3 | 1 | 0 |
| batchM | 2026-08-04 | 3 | 3 | 2 | 0 |
| batch-2026-07-27-3 | 2026-07-27 | 4 | 4 | 0 | 0 |

## The critic's findings (terminal, deduplicated)

| severity | count |
|---|---|
| HIGH | 9 |
| LOW | 91 |
| MID | 58 |

Rulings on HIGH and MID findings (LOW is recorded, never adjudicated):

| severity/ruling | count |
|---|---|
| HIGH/RECORDED | 1 |
| HIGH/UPHELD | 8 |
| MID/DISMISSED | 5 |
| MID/RECORDED | 16 |
| MID/UPHELD | 33 |
| MID/unadjudicated | 4 |

Defect classes (typed; the OTHER:* long tail is 82 findings across 76 distinct labels):

| class | count |
|---|---|
| REFERENCE-UNSUPPORTED | 17 |
| AMBIGUOUS-READING | 16 |
| MISSED-GOLD | 14 |
| GOLD-WRONG | 6 |
| TELEGRAPH | 6 |
| GENERIC-FACT | 5 |
| NEAR-DUPLICATE | 5 |
| NEAR-MISS | 2 |
| ADV-PROOF-UNTYPED | 2 |
| ROUTE-MISLABEL | 1 |
| STALE-EVIDENCE | 1 |
| FILTER-DECORATION | 1 |

## What review changed

- Accepted slots with at least one UPHELD finding: **26**
- Accepted slots that went through at least one FIX round: **20**

Each of these is a question that entered the bank in a different state than its drafter first submitted - a defect or weakness the split-authority review caught before it shipped.

## The deterministic gates

- `precheck_record`: 209 executions, 30 reported at least one failure
- `precheck_candidate`: 125 executions, 23 reported at least one failure

MCP activity: 3898 calls over 10 days (2026-07-22 to 2026-08-04), 165 errored.

| tool | calls |
|---|---|
| run_sql | 1763 |
| search_corpus | 728 |
| get_project_text | 722 |
| precheck_record | 209 |
| get_schema_docs | 172 |
| get_bank_questions | 127 |
| precheck_candidate | 125 |
| get_corpus_profile | 43 |
| get_bank_record | 9 |

## Bank linkage

- Bank today: 58 questions; 28 from batch runs, 30 authored interactively
- Trimmed 2026-08-03 to the v5 allocation: 18 (all still archived, ids permanently taken)
- Total questions that passed the pipeline: 76

## Authoring spend (Claude-side, from transcripts)

| agent | spawned | turns | input (total) | of which cache | output | tools | active |
|---|---|---|---|---|---|---|---|
| question-drafter | 103 | 4063 | 249.9M | 205.5M | 3.1M | 2132 (1716 MCP) | 794.8m |
| question-reviewer | 95 | 3380 | 182.4M | 147.2M | 1.7M | 1802 (1391 MCP) | 494.5m |
| question-judge | 73 | 553 | 11.9M | 4.1M | 314k | 185 (0 MCP) | 84.5m |
| (orchestrator) | 22 | 3137 | 598.3M | 577.6M | 7.8M | 1789 (43 MCP) | 1682.1m |

Slots traceable to a question id: 70; median output tokens per slot: 58792.

### Cost in dollars, by model

| model | fresh input | cache read | cache write | output | cost |
|---|---|---|---|---|---|
| claude-opus-5 | 86k | 852.0M | 98.0M | 11.5M | $1327.58 |
| claude-opus-4-8 | 1k | 46.8M | 8.3M | 873k | $97.06 |
| claude-fable-5 | 300 | 35.5M | 1.7M | 523k | $82.38 |

**Total factory cost: $1507.01** (drafter + critic + judge subagents plus their orchestrator sessions, priced at 2026-08-06 API rates; cache reads at 0.1x input, cache writes at the 1.25x 5-minute rate - with the 1-hour cache TTL writes bill 2x, which would raise the write component by 60%).

