---
name: draft-hybrid-question
description: Draft one hybrid-route (L1-L3) benchmark question for the Horizon Scout M5 bank. Two-sided by construction - a structured SQL filter AND a textual requirement answered from the filter's survivors; both sides verified by execution and adjudication, gold always a subset of the enumerated survivors; a mandatory reviewer checklist gates a confirmation-only append.
argument-hint: <level> [subtype] [term_style]
---

# /draft-hybrid-question

Draft one hybrid-route benchmark question for the Horizon Scout M5 bank.

**Arguments:** $ARGUMENTS
Format: `<level> [subtype] [term_style]` - e.g. `L1 filter-read exact-term` or `L3 filter-compare`. Subtypes are level-bound (one level each), so a bare subtype is also accepted and implies its level: `filter-read` = L1, `filter-synthesize` = L2, `filter-compare` = L3, `filter-survey` = L3. term_style values: `exact-term`, `paraphrase`. If subtype or term_style is omitted, propose one based on what the bank currently lacks and wait for the user to pick.

This skill authors `route=hybrid` questions at levels L1-L3 only. SQL questions have `/draft-sql-question`, vector questions have `/draft-vector-question`; ADV and ambiguous questions have their own skills - point the user there instead of stretching this one. One question per pass; never batch.

## Orchestrated mode (question-drafter subagents only)

When this skill is followed by a `question-drafter` subagent under `/draft-batch` (the prompt says so and carries a pre-assigned `question_id` plus a corpus-profile candidate block):

- The candidate block is the subject and the batch order fixes subtype (=> level) and term_style - skip every propose-and-wait step, and skip the startup profile calls (`get_corpus_profile(section="hybrid")` and `coverage-ledger`): the candidate block IS your profile slice, and the ledger only matters when choosing a subject. Still call `get_bank_questions("hybrid")`, run the retrieval-server probe, and call `get_schema_docs()` (the filter needs its value notes).
- **Two-tier grounding - the candidate is part proven, part advisory.** Its `evidence` (the executed filter SQL + its survivor/total counts) and sample ids are proven-by-execution and merge-pass spot-checked: trust and confirm them - start from the candidate's filter SQL and re-execute it once to confirm the survivor count has not drifted, rather than re-sampling the filter's value space from scratch. Everything else it asserts - route/level/subtype, term_style, filter load-bearingness, and gold membership - is ADVISORY and re-verified in full: read the survivors' text (euroSciVoc leaf tags are noisy), run the discrimination check, adjudicate to gold, and check |gold| against the subtype's bound. Reject-at-birth and born-verified-this-pass are unchanged - the confirming re-execution happens in-pass.
- Use the pre-assigned `question_id`, never "next free".
- There is no user in the loop: skip the confirmation prompt, never append, never write any file, and skip the `validate-bank` shell step (promotion validates). Instead return the complete entry - every field from the append table - plus evidence and history, in the output contract of `.claude/agents/question-drafter.md`.
- Run Step 5 as the **orchestrated-mode checklist** (see Step 5): every gate and diagnostic except the pure-judgment polish items the independent `question-reviewer` owns.
- Everything else applies unchanged, including "reject at birth": a dead candidate is reported as `DRAFT-FAILED`, never worked around by wandering to a new topic. Bound disagreements that would normally go to the user (|gold| contradicting the subtype) go into the returned package instead - as a `DRAFT-FAILED` if the requested cell cannot be met honestly.

Interactive invocations are unaffected: the per-question confirm gate stands.

## Both sides must be load-bearing

A hybrid question needs BOTH capabilities, and each side is checked:

- **The filter must matter.** Drop the filter, and the textual part alone must NOT identify the gold set - there must exist projects that satisfy the text but fail the filter. If none exist, this is a vector question in disguise.
- **The text must matter.** Drop the textual part, and the filter alone must NOT answer the question - the answer lives in free text (what survivors do, how, what they found), never in a stored column. If a column answers it, this is a SQL question in disguise.

Hybrid's structural advantage over vector: the survivor set is ENUMERABLE (the filter is SQL), so for small survivor sets the textual adjudication is exhaustive - every survivor read - which is strictly stronger than pooling. Use that whenever survivors <= 20.

## Tooling

All data access goes through the `horizon-draft` MCP server:

- `run_sql(query, row_cap=50)` - SELECT-only. Designs and executes the gold filter, enumerates survivors (raise row_cap up to 200), runs the survivor-scoped sweep.
- `get_project_text(project_ids)` - full free text (objective + report sections) for up to 10 projects per call; batch for more. The adjudication and reference channel.
- `search_corpus(query, condition="pooled", k=10, scope_project_ids=[...])` - project-level pooled retrieval. With `scope_project_ids` (max 500) it searches WITHIN the survivors - the runtime scoped path's analog. Unscoped, it powers the filter-discrimination check. Returns the per-condition rank matrix, `scope_size`, and `index_meta.content_hash`. Requires the embed AND reranker llama-servers; errors come back as results, and in pooled mode one dead condition fails the whole call.
- `get_schema_docs()` - schema + value notes for designing the filter; its `content_hash` is recorded as `filter_evidence.schema_docs_hash`.
- `get_bank_questions("hybrid")` - existing entries: id, text, level, subtype only.
- `get_corpus_profile(section=None)` - the exploration agent's corpus_profile.md (whole, or one section by key). Query-verified candidate topics (topic x filter combos with survivor counts already in the drafting windows) plus the coverage-axes ledger. An `{"error": ...}` result means the profile is not built yet - proceed without it.

There are no write tools. The append at the end is a confirmation-gated file edit, done by this skill directly.

## Level, subtype, and term_style reference

Subtypes are level-bound and carry gold-count bounds (validator-enforced both ways):

- **`filter-read` (L1)** - the filter isolates a few survivors; the answer is read from ONE survivor's text. |gold| = 1. Survivor guidance: 2-10. S = 1 is legal but suspicious - the filter alone nearly answers it; flag it and prefer a slightly looser filter.
- **`filter-synthesize` (L2)** - the filter narrows to ~5-20 survivors; one integrated answer drawn across 2-4 of them. |gold| in [2,4].
- **`filter-compare` (L3)** - filter, then contrast survivors explicitly (the plan doc's "filter-then-compare"). |gold| in [2,4], reference contrasts each.
- **`filter-survey` (L3)** - characterize the satisfying survivors as a group. |gold| >= 5.

**Hard authoring limit:** the survivor set must be enumerable - true count <= 200 (`run_sql`'s row ceiling). A looser filter gets tightened before drafting, not worked around.

**term_style** (required; feeds RQ2's crossover table): `exact-term` = the question reuses distinctive vocabulary observed in the gold texts; `paraphrase` = it deliberately avoids their key terms. Honesty heuristic: the SCOPED rank matrix - gold found by `lexical` implies real term overlap; gold found only by `dense` implies divergent wording. Heuristic only; the declared style just must not be contradicted by it.

## Startup (every invocation)

1. Call `get_bank_questions("hybrid")`. Review existing questions for near-duplicates and subtype/term_style coverage. If subtype or term_style was not given: state current counts, propose the least-covered combination, wait for the pick.
2. Call `get_corpus_profile(section="hybrid")` and `get_corpus_profile(section="coverage-ledger")`. If the profile is not built yet, note that and proceed without it. When the user names no filter or topic: propose a profile candidate on a **least-covered axis** in the ledger (a filter dimension or topic branch no bank question touches yet), not yet used by any bank question - least-covered axis beats least-covered subtype when they conflict. Candidates are advisory: their route/level/subtype/term_style and gold membership are re-verified in full (both sides adjudicated in this pass), while the executed filter SQL and its survivor count are only re-confirmed cheaply - see Orchestrated mode for the two-tier rule. (Orchestrated mode skips both these profile calls - the candidate block already carries the section.)
3. Probe the retrieval stack: `search_corpus("probe", condition="pooled", k=1)`. An error result means a server is down - report and end the pass. Record `index_meta.content_hash` for `pooling_evidence.index_fingerprint`.
3. Call `get_schema_docs()`. Record its `content_hash` for `filter_evidence.schema_docs_hash`; use its value notes when designing the filter.

## Step 1 - Ground (evidence first, two-sided)

- **Design the gold filter** via `run_sql`, grounded in observed values: sample the enum/code/range the filter uses (country codes, fundingScheme values, date ranges, funding percentiles - schema_docs value notes are the map). Execute the candidate filter; the TRUE survivor count (row_count) shapes the level: 2-10 for filter-read, ~5-20 for synthesize, tight-but-rich for compare/survey. Count > 200: tighten and re-run. **Orchestrated mode:** start from the candidate's filter SQL and re-execute it once to confirm the survivor count; skip re-sampling the value space the candidate already grounded.
- **Enumerate survivors:** `SELECT id ...` with row_cap high enough to capture all of them.
- **Read the survivors:** `get_project_text` in batches of <= 10 - ALL of them when S <= 20, otherwise a representative read now and full adjudication in Step 3. The question is composed from the filter plus what these texts actually say.

Present a grounding summary: the filter, its survivor count, which observed textual property of which survivors will carry the answer. If the survivors' texts do not support the intended shape (no shared theme to synthesize, nothing to contrast), pivot before drafting.

## Step 2 - Draft

Present:

- **Question text** - one natural question requiring both sides; the filter constraints expressed as a user would say them ("Croatian projects funded after 2020...", not SQL-in-English), the textual ask composed from observed survivor text.
- **Declared subtype (=> level) and term_style**, with one sentence on why the evidence fits.
- **The filter SQL** that will be recorded as `filter_evidence.filter_sql`.

## Step 3 - Verify

Every draft is verified in the same pass. Any edit to the question text or the filter SQL invalidates everything downstream of it - re-execute and re-search, never carry stale results.

1. **Filter side:** execute `filter_sql` via `run_sql`; record `survivor_ids` and the true count.
2. **Text side - adjudicate to gold:**
   - S <= 20: read EVERY survivor's text; each gets IN or OUT with a one-line justification grounded in its text. Exhaustive - no pooling gap exists.
   - S in 21-200: scoped pooled search (`search_corpus(question, "pooled", k=10, scope_project_ids=survivors)`); adjudicate every returned candidate - clear-off-topic ones from the best-chunk text the search already returned (no fetch), plausible-IN or borderline ones from a full `get_project_text` read (collect ids, batch <= 10 per call). PLUS the non-embedding channel: `run_sql` keyword/LIKE sweep over the SURVIVORS' objectives on the question's key concepts and obvious synonyms; read and adjudicate its hits too.
   - `gold_project_ids` = accepted survivors. Gold is a subset of survivors by construction.
3. **Filter-discrimination check (is the filter load-bearing?):** run the pooled search UNSCOPED (`k=10`) with the same question. Look for projects that satisfy the textual part but FAIL the filter. Found: record them as counter-examples proving the filter does work. None found: the topical part alone identifies the gold set - vector question in disguise; rewrite the question or the filter.
4. **Scoped retrievability:** every gold member should appear in at least one condition's scoped top-k. A gold member absent everywhere is a WARN - the runtime scoped path cannot connect the question to that evidence; consider rewording.
5. **Check the bounds:** |gold| against the subtype's bound (read = 1, synthesize/compare 2-4, survey >= 5). Mismatch: say so, and either rewrite (tighten/broaden filter or question) or re-subtype - the user chooses. Never append a question whose gold count contradicts its subtype.

## Step 4 - Reference answer

Written from the gold survivors' texts plus the filter facts (the filter is part of the question's truth) - never from rejected survivors or out-of-filter projects.

- Prose meaningfully paraphrased; acronyms, named entities, and numbers stay **verbatim**.
- Length by subtype: 1-2 sentences for `filter-read`; up to four for `filter-synthesize`/`filter-compare`; `filter-survey` states the group pattern plus named examples, not a full enumeration.
- `filter-compare` references contrast each gold project explicitly; `filter-read` references name the project and the fact.

## Step 5 - Reviewer (mandatory, every pass)

Re-read question, filter, survivor list, adjudications, discrimination counter-examples, reference. Every item gets an explicit PASS / FAIL / WARN plus one sentence.

**Interactive mode:** run every item below, skip nothing. **Orchestrated mode:** an independent `question-reviewer` attacks the draft afterward. It owns NO-TELEGRAPH, NEAR-DUPLICATE, and GENERIC-FACT as MINOR flags, and NATURAL-PHRASING is dropped entirely (pure phrasing taste), so skip all four here - run every other item (including the FILTER-LOAD-BEARING and TEXT-LOAD-BEARING gates) in full.

```
FILTER-EXECUTED       filter_sql ran this session; survivor_ids and true count recorded; count <= 200.
                      FAIL otherwise.
GOLD-WITHIN-SURVIVORS gold_project_ids is a subset of survivor_ids. FAIL otherwise.
ADJUDICATION-COMPLETE S<=20: every survivor read and adjudicated with a reason. S>20: scoped pooled
                      search AND survivor-scoped sweep both run, every hit adjudicated. FAIL otherwise.
FILTER-LOAD-BEARING   Unscoped check found text-satisfying projects outside the filter (counter-examples
                      recorded), or a written argument why none can exist. FAIL if the filter changes nothing.
TEXT-LOAD-BEARING     The textual requirement is not answerable from stored columns. FAIL otherwise.
SUBTYPE-GOLD-BOUNDS   |gold| satisfies the subtype's bound (read=1, synthesize/compare 2-4, survey >=5).
                      FAIL otherwise.
SCOPED-RETRIEVABLE    Every gold member in at least one condition's scoped top-k. WARN otherwise, stating
                      why the question was kept anyway.
TERM-STYLE-HONEST     Declared term_style consistent with the scoped rank matrix. WARN otherwise.
NATURAL-PHRASING      Reads as a user's question; filter constraints in user language, no SQL-in-English.
                      WARN otherwise.
ONE-QUESTION          A single ask; no "and" joining two questions (a filter plus a text ask is ONE
                      question; two text asks is two). FAIL if two-part.
NO-TELEGRAPH          Question betrays nothing about the answer's content from having seen the evidence.
                      WARN otherwise.
REFERENCE-FIDELITY    Reference derived only from gold texts + filter facts; prose paraphrased; entities
                      and numbers verbatim. FAIL otherwise.
GENERIC-FACT          Answer requires this corpus, not general knowledge. WARN only.
NEAR-DUPLICATE        Not a near-duplicate of an existing bank question. WARN, naming the colliding id.
```

**Verdict:** APPROVE / REVISE / REJECT

Then wait for the user. "confirm" appends; "confirm anyway" overrides a non-APPROVE verdict (recorded as `reviewer_override: true`); anything else is treated as revision instructions.

## On confirmation - append

Append one JSONL line to `eval/bank.jsonl` with every field:

```
question_id            next free hyb-NN
text                   the question
expected_route         "hybrid"
level                  implied by the subtype
subtype                filter-read | filter-synthesize | filter-compare | filter-survey
specification          "well-specified"       (this skill never authors underspecified)
term_style             exact-term | paraphrase
gold_project_ids       accepted survivors
filter_evidence        {filter_sql, survivor_count, survivor_ids, schema_docs_hash}
pooling_evidence       {conditions_run, k, pooled_candidate_count, accepted, rejected_count,
                        index_fingerprint, scope_size}
                       accepted = gold_project_ids exactly. For exhaustive-read passes the scoped
                       search still runs (retrievability + rank matrix); candidate counts come
                       from it, adjudication from the exhaustive read - note which in notes.
reference_answer       from Step 4
notes                  filter rationale + observed values, per-survivor adjudications (id: IN/OUT +
                       reason), discrimination counter-examples, sweep queries and outcome,
                       term_style rationale, anything a verifier needs
reviewer_override      only if "confirm anyway"
```

Then run `./.venv/Scripts/python.exe -m src.cli validate-bank` and show its output. A validation failure after append is a skill bug - fix the entry before ending the pass.

## Standing rules

- **Never append without explicit confirmation.** Never rewrite an existing bank entry without explicit instruction.
- **Both sides load-bearing.** A hybrid question that survives with either side dropped is a SQL or vector question wearing the wrong label - reject or rewrite.
- **Survivors enumerable.** No filter with more than 200 true survivors; tighten, never sample.
- **The label is born verified.** Filter executed this pass, every relevant survivor adjudicated this pass. Edits to question or filter invalidate everything downstream - re-run, never carry stale results.
- **Retrieval never decides truth.** Survivors enter gold because their text satisfies the question; scoped ranks only diagnose retrievability and term_style.
- **Levels are bound to subtypes, bounds are checked.** Disagreement between |gold| and the subtype goes to the user, not to silent relabeling.
- **Reject at birth rather than patch.** Non-discriminating filters, themeless survivor sets, gold counts that miss the bound - these end the draft.
- **One question, one fact-shape.** A filter plus one textual ask; never two textual asks.
- **The reviewer runs every time**, every item explicit, before any confirmation prompt.
