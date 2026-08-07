# Factory telemetry

Every number below is computed by `src/eval/telemetry.py` from the batch journals, the MCP log and the subagent transcripts. Counts merge every journal line per slot with content-level dedup of decisions and findings, because not every journal kept the envelope cumulative - the last line alone undercounts the runs that reset it per round.

## The funnel

| batches | slots | candidates tried | accepted | failed | FIX rounds | candidate abandons |
|---|---|---|---|---|---|---|
| 17 | 52 | 75 | 49 | 3 | 41 | 17 |

Adjudication rounds per slot (judge decisions until the slot closed): 1: 16, 2: 25, 3: 4, 4: 6, 5: 1

## Per run

| run | date | model | slots | accepted | FIX rounds | abandons |
|---|---|---|---|---|---|---|
| batchA | 2026-07-28 | unrecorded | 3 | 3 | 3 | 1 |
| batchB | 2026-07-28 | unrecorded | 3 | 3 | 2 | 0 |
| batchC | 2026-07-28 | unrecorded | 3 | 3 | 4 | 1 |
| batchD | 2026-07-28 | unrecorded | 3 | 3 | 2 | 1 |
| batchE | 2026-07-28 | unrecorded | 3 | 3 | 3 | 1 |
| batchF | 2026-07-28 | unrecorded | 3 | 3 | 3 | 0 |
| batchG | 2026-07-29 | unrecorded | 3 | 3 | 1 | 1 |
| batchH | 2026-07-29 | unrecorded | 3 | 3 | 2 | 0 |
| batchI | 2026-08-01 | unrecorded | 3 | 3 | 4 | 2 |
| batchJ | 2026-08-01 | unrecorded | 3 | 2 | 3 | 2 |
| batchK | 2026-08-04 | unrecorded | 3 | 3 | 1 | 0 |
| batchL | 2026-08-04 | unrecorded | 3 | 3 | 1 | 0 |
| batchM | 2026-08-04 | unrecorded | 3 | 3 | 2 | 0 |
| batch-2026-07-27-3 | 2026-07-27 | unrecorded | 4 | 4 | 6 | 3 |
| sonnet-probe/hyb | 2026-08-06 | claude-sonnet-5 | 3 | 2 | 1 | 3 |
| sonnet-probe/sql | 2026-08-06 | claude-sonnet-5 | 3 | 3 | 1 | 0 |
| sonnet-probe/vec | 2026-08-06 | claude-sonnet-5 | 3 | 2 | 2 | 2 |

## The critic's findings (terminal, deduplicated)

| severity | count |
|---|---|
| HIGH | 47 |
| LOW | 169 |
| MID | 148 |

Rulings on HIGH and MID findings (LOW is recorded, never adjudicated):

| severity/ruling | count |
|---|---|
| HIGH/RECORDED | 1 |
| HIGH/UPHELD | 27 |
| HIGH/unadjudicated | 19 |
| MID/DISMISSED | 6 |
| MID/RECORDED | 19 |
| MID/UPHELD | 73 |
| MID/unadjudicated | 50 |

Defect classes (typed; the OTHER:* long tail is 167 findings across 129 distinct labels):

| class | count |
|---|---|
| MISSED-GOLD | 49 |
| AMBIGUOUS-READING | 41 |
| REFERENCE-UNSUPPORTED | 33 |
| GOLD-WRONG | 32 |
| NEAR-DUPLICATE | 12 |
| TELEGRAPH | 8 |
| GENERIC-FACT | 7 |
| STALE-EVIDENCE | 4 |
| NEAR-MISS | 4 |
| ADV-PROOF-UNTYPED | 3 |
| FILTER-DECORATION | 2 |
| ROUTE-MISLABEL | 1 |

## What review changed

- Accepted slots with at least one UPHELD finding: **32**
- Accepted slots that went through at least one FIX round: **32**

Each of these is a question that entered the bank in a different state than its drafter first submitted - a defect or weakness the split-authority review caught before it shipped.

## The deterministic gates

- `precheck_record`: 226 executions, 30 reported at least one failure
- `precheck_candidate`: 125 executions, 23 reported at least one failure

MCP activity: 4152 calls over 11 days (2026-07-22 to 2026-08-06), 169 errored.

| tool | calls |
|---|---|
| run_sql | 1864 |
| search_corpus | 786 |
| get_project_text | 773 |
| precheck_record | 226 |
| get_schema_docs | 193 |
| get_bank_questions | 133 |
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
| question-drafter | 118 | 4515 | 276.6M | 228.9M | 3.4M | 2393 (1881 MCP) | 856.7m |
| question-reviewer | 107 | 3632 | 192.9M | 155.5M | 1.8M | 1945 (1480 MCP) | 521.1m |
| question-judge | 86 | 607 | 13.0M | 4.4M | 336k | 203 (0 MCP) | 91.1m |
| (orchestrator) | 25 | 3470 | 646.0M | 623.7M | 8.3M | 1949 (43 MCP) | 1724.6m |

Slots traceable to a question id: 79; median output tokens per slot: 54685.

### Cost in dollars, by model

| model | fresh input | cache read | cache write | output | cost |
|---|---|---|---|---|---|
| claude-opus-5 | 86k | 852.0M | 98.0M | 11.5M | $1327.58 |
| claude-opus-4-8 | 1k | 46.8M | 8.3M | 873k | $97.06 |
| claude-sonnet-5 | 2k | 78.2M | 7.8M | 872k | $65.72 |
| claude-fable-5 | 300 | 35.5M | 1.7M | 523k | $82.38 |

**Total factory cost: $1572.74** (drafter + critic + judge subagents plus their orchestrator sessions, priced at 2026-08-06 API rates; cache reads at 0.1x input, cache writes at the 1.25x 5-minute rate - with the 1-hour cache TTL writes bill 2x, which would raise the write component by 60%).

