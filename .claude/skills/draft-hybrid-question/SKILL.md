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

When this skill is followed by a `question-drafter` subagent under `/question-orchestrator` (the prompt says so and carries a pre-assigned `question_id` plus a corpus-profile candidate block):

- **Read `src/eval/bank_brief.md` first.** It is the shared standard the drafter, the critic, and the judge all work from - what the bank is for, what "good" means, the route/level/subtype reference, and the role boundaries. The most useful thing in it for you: a defective question does not produce a wrong answer, it produces a wrong finding in a study.
- **The precheck gate.** Before you may emit a package, call `precheck_record(<your finished RECORD>)` and get `ok: true`. On this route it re-executes `filter_sql` and requires the live survivor set to match `survivor_ids` exactly, gold to sit inside it, every gold project to carry text, and the recorded `filter_evidence.schema_docs_hash` to be the live one. A FAIL is a fact; fix the draft and call again. Include the passing result in your package as the `PRECHECK` section.
- **You author and verify; you do not grade yourself.** An independent `question-reviewer` attacks the draft afterwards and an independent `question-judge` rules on what it finds. Emit no verdict, argue no case, and record any doubt you still hold in HISTORY rather than suppressing it.
- The orchestrator runs `python -m src.cli validate-record` on your RECORD the moment you return it, so the JSON must be schema-clean (subtype gold bounds, `survivor_count == len(survivor_ids)`, gold a subset of survivors); a validator error comes straight back to you.
- The candidate block is the subject and the batch order fixes subtype (=> level) and term_style - skip every propose-and-wait step, and skip the startup profile calls (`get_corpus_profile(section="hybrid")` and `frontier`): the candidate block IS your profile slice, and the frontier only matters when choosing a subject. Skip `get_bank_questions` too: near-duplicate detection belongs to the independent reviewer (which has its own access to the tool) and to the deterministic `batch-crosscheck` at close-out, and the coverage counts feed only the propose-and-wait steps you already skip. Still run the retrieval-server probe (with `snippet_chars=0` - a liveness check needs no text) and call `get_schema_docs()` (the filter needs its value notes).
- **Two-tier grounding - the candidate is part proven, part advisory.** Its `evidence` (the executed filter SQL + its survivor/total counts) and sample ids are proven-by-execution and merge-pass spot-checked: trust and confirm them - start from the candidate's filter SQL and re-execute it once to confirm the survivor count has not drifted, rather than re-sampling the filter's value space from scratch. Everything else it asserts - route/level/subtype, term_style, filter load-bearingness, and gold membership - is ADVISORY and re-verified in full: read the survivors' text (euroSciVoc leaf tags are noisy), run the discrimination check, adjudicate to gold, and check |gold| against the subtype's bound. Reject-at-birth and born-verified-this-pass are unchanged - the confirming re-execution happens in-pass.
- Use the pre-assigned `question_id`, never "next free".
- There is no user in the loop: skip the confirmation prompt, never append, never write any file, and skip the `validate-bank` shell step (promotion validates). Instead return the complete entry - every field from the append table - plus evidence and history, in the output contract of `.claude/agents/question-drafter.md`.
- Run Step 7 as the **orchestrated-mode checklist** (see Step 7): every gate and diagnostic except the items `precheck_record` and `validate-record` already settle, and the pure-judgment polish items the independent `question-reviewer` owns.
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
- `search_corpus(query, condition="pooled", k=10, scope_project_ids=[...], snippet_chars=N)` - project-level pooled retrieval. **Always pass `condition="pooled"` in this skill.** Since 2026-08-03 the tool's own default is `hybrid_rerank` (the one stack the system answers with); gold labelling and the filter-discrimination check both need every condition's view, and the bank's existing gold sets were all labelled pooled. With `scope_project_ids` (max 500) it searches WITHIN the survivors - the runtime scoped path's analog. Unscoped, it powers the filter-discrimination check. Returns the per-condition rank matrix, `scope_size`, and `index_meta.content_hash`. `snippet_chars` caps each best-chunk's text (a full chunk averages ~1,437 chars): `0` for the probe, `~400` for the discrimination check, `~600` for the scoped adjudication search - the steps below state which. Requires the embed AND reranker llama-servers; errors come back as results, and in pooled mode one dead condition fails the whole call.
- `get_schema_docs()` - schema + value notes for designing the filter; its `content_hash` is recorded as `filter_evidence.schema_docs_hash`.
- `get_bank_questions("hybrid")` - existing entries: id, text, level, subtype only.
- `get_corpus_profile(section=None)` - the exploration agent's corpus_profile.md (whole, or one section by key). Query-verified candidate topics (topic x filter combos with survivor counts already in the drafting windows) plus the `frontier` coverage table. An `{"error": ...}` result means the profile is not built yet - proceed without it.
- `precheck_record(record)` - re-executes the finished record's mechanical claims (here: `filter_sql` produces exactly `survivor_ids`, the set is enumerable, gold sits inside it, every gold project has text, the recorded schema_docs hash is live) and returns PASS/FAIL/N-A per check. Free to call as often as you like; a FAIL is a result, not an error.

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

1. Call `get_bank_questions("hybrid")`. Review existing questions for near-duplicates and subtype/term_style coverage. If subtype or term_style was not given: state current counts, propose the least-covered combination, wait for the pick. (Orchestrated mode skips this call - the reviewer and `batch-crosscheck` own duplicate detection.)
2. Call `get_corpus_profile(section="hybrid")` and `get_corpus_profile(section="frontier")`. If the profile is not built yet, note that and proceed without it. When the user names no filter or topic: propose a profile candidate on a **least-covered axis** (a filter dimension or topic branch no bank question touches yet; the frontier's `mapped`-but-not-`mined` buckets are the first place to look), not yet used by any bank question - least-covered axis beats least-covered subtype when they conflict. Candidates are advisory: their route/level/subtype/term_style and gold membership are re-verified in full (both sides adjudicated in this pass), while the executed filter SQL and its survivor count are only re-confirmed cheaply - see Orchestrated mode for the two-tier rule. (Orchestrated mode skips both these profile calls - the candidate block already carries the section.)
3. Probe the retrieval stack: `search_corpus("probe", condition="pooled", k=1, snippet_chars=0)` - a liveness check needs ranks, not chunk text. An error result means a server is down - report and end the pass. Record `index_meta.content_hash` for `pooling_evidence.index_fingerprint`.
3. Call `get_schema_docs()`. Record its `content_hash` for `filter_evidence.schema_docs_hash`; use its value notes when designing the filter.

## Step 1 - Fit gate (kill-shots first, before any investment)

Every check here is cheap (3-4 calls), executable, and can kill the candidate. They run BEFORE the survivor read, the drafting, and the adjudication, because a `DRAFT-FAILED` at call 4 is nearly free and the same failure discovered after a full grounding pass costs the whole pass. A fit-gate `DRAFT-FAILED` is a cheap, expected, correct outcome - the orchestrator holds three candidates per slot precisely so this is affordable.

1. **Execute the filter once** (drift check). **Orchestrated mode:** start from the candidate's filter SQL; skip re-sampling the value space the candidate already grounded. **Interactive mode:** design the filter first via `run_sql`, grounded in observed values (country codes, fundingScheme values, date ranges - schema_docs value notes are the map). Enumerate survivors in the same query (`SELECT id ...`, row_cap high enough for all of them); record `survivor_ids` and the true count.
2. **One-reading check on every euroSciVoc term the scope references.** Before wording a scope like "classified under `<term>`", run:
   ```sql
   SELECT DISTINCT euroSciVocPath, euroSciVocTitle, COUNT(DISTINCT projectID)
   FROM euroscivoc WHERE euroSciVocPath LIKE '%<term>%' GROUP BY 1, 2
   ```
   One row: the term is a leaf - the title reading and the subtree reading select the identical set, the scope has exactly one executable reading; proceed. Multiple rows: the term is a branch with children, the two readings diverge, and a question worded against it is ambiguous - either word the scope to name the branch explicitly ("classified anywhere under musicology - ethnomusicology and popular music studies included") or `DRAFT-FAILED`. This check killed nothing on hyb-09 candidate 2 (viticulture: 1 row, run as its second action) and would have killed candidate 1 (musicology: 3 rows, all gold on sub-leaves, the narrow reading has zero gold) at its first action instead of after ~519k tokens.
3. **Survivor count against the subtype window** (read 2-10, synthesize 5-20, compare 2-20, survey 5-60) - free, the count is already in hand. Count > 200: the set is not enumerable; tighten or fail. Outside the window: the cell no longer fits its own filter; re-scope or fail. `precheck_record` re-checks this later as SURVIVOR-WINDOW (WARN) - noticing it NOW is what the gate is for.
4. **Topic is never a structured filter.** The runtime scoped path whitelists the filterable columns (`src/retrieval/scoped.py`) and subject matter is not among them - the runtime CANNOT build a topic filter. A hybrid scope whose SQL half encodes the topic (euroSciVoc membership, LIKE over objectives) must be worded knowing the runtime's filter half will carry only the structural constraints and the topical part falls to retrieval. If the question only works when the topic is filterable, it fails at the runtime and `DRAFT-FAILED` now.

If any gate fails and cannot be repaired by re-wording the scope, `DRAFT-FAILED` now - never invest first and discover later.

## Step 2 - Ground (read enough to compose honestly)

Read survivors via `get_project_text` (batches <= 10) - enough of them to compose the question honestly, not yet all of them; the exhaustive read happens at adjudication (Step 5) where its cost buys verification. The question is composed from the filter plus what these texts actually say.

Present a grounding summary: the filter, its survivor count, which observed textual property of which survivors will carry the answer. If the survivors' texts do not support the intended shape (no shared theme to synthesize, nothing to contrast), `DRAFT-FAILED` before drafting.

## Step 3 - Draft

Present:

- **Question text** - one natural question requiring both sides; the filter constraints expressed as a user would say them ("Croatian projects funded after 2020...", not SQL-in-English), the textual ask composed from observed survivor text.
- **Declared subtype (=> level) and term_style**, with one sentence on why the evidence fits.
- **The filter SQL** that will be recorded as `filter_evidence.filter_sql`.

## Step 4 - Discrimination check (the second kill-shot, before adjudication)

Run the pooled search UNSCOPED (`search_corpus(question, "pooled", k=10, snippet_chars=400)` - its ~20 projects exist to surface counter-examples and are then discarded; no one reads them at length). Look for projects that satisfy the textual part but FAIL the filter.

- Found: record them as counter-examples proving the filter is load-bearing.
- None found: the topical part alone identifies the gold set - a vector question in disguise; rewrite the question or the filter NOW, before paying for the adjudication. This is a pure kill-shot costing one call; running it after the exhaustive read (where it used to sit) meant discovering a decorative filter only after the full grounding investment.

## Step 5 - Verify (adjudicate to gold)

Any edit to the question text or the filter SQL invalidates everything downstream of it - re-execute and re-search, never carry stale results. Because the expensive steps now come LAST, an edit at the draft stage invalidates almost nothing.

1. **Filter side:** if `filter_sql` is byte-identical to the SQL executed in the fit gate, carry that enumeration forward and record it - do not re-execute. Re-execute only if the SQL changed when the question was drafted. `precheck_record` re-executes it as the gate either way.
2. **Text side - adjudicate to gold:**
   - S <= 20: read EVERY survivor's text; each gets IN or OUT with a one-line justification grounded in its text. Exhaustive - no pooling gap exists. **Tier by direction, not by pass:** `fields=["acronym","title","objective","teaser"]` (~2.1k chars) is sufficient to justify an OUT; any survivor going IN, any borderline case, and any text that will feed the reference answer requires the full payload (~8.1k chars - `workPerformed`/`finalResults` are where "what the project actually did" lives, and real findings have come from exactly those fields). Never adjudicate an IN from the gist.
   - S in 21-200: scoped pooled search (`search_corpus(question, "pooled", k=10, scope_project_ids=survivors, snippet_chars=600)`); adjudicate every returned candidate - clear-off-topic ones from the truncated best-chunk text the search already returned (no fetch), plausible-IN or borderline ones from a full `get_project_text` read (collect ids, batch <= 10 per call). PLUS the non-embedding channel: `run_sql` keyword/LIKE sweep over the SURVIVORS' objectives on the question's key concepts and obvious synonyms; read and adjudicate its hits too.
   - `gold_project_ids` = accepted survivors. Gold is a subset of survivors by construction.
3. **Scoped retrievability:** every gold member should appear in at least one condition's scoped top-k. A gold member absent everywhere is a WARN - the runtime scoped path cannot connect the question to that evidence; consider rewording.
4. **Check the bounds:** |gold| against the subtype's bound (read = 1, synthesize/compare 2-4, survey >= 5). Mismatch: say so, and either rewrite (tighten/broaden filter or question) or re-subtype - the user chooses. Never append a question whose gold count contradicts its subtype.

## Step 6 - Reference answer

Written from the gold survivors' texts plus the filter facts (the filter is part of the question's truth) - never from rejected survivors or out-of-filter projects.

- Prose meaningfully paraphrased; acronyms, named entities, and numbers stay **verbatim**.
- Length by subtype: 1-2 sentences for `filter-read`; up to four for `filter-synthesize`/`filter-compare`; `filter-survey` states the group pattern plus named examples, not a full enumeration.
- `filter-compare` references contrast each gold project explicitly; `filter-read` references name the project and the fact.

## Step 7 - Reviewer (mandatory, every pass)

Re-read question, filter, survivor list, adjudications, discrimination counter-examples, reference. Every item gets an explicit PASS / FAIL / WARN plus one sentence.

**Interactive mode:** run every item below, skip nothing.

**Orchestrated mode:** two items are already settled by machine and re-running them by hand is pure duplication - skip `FILTER-EXECUTED` and `GOLD-WITHIN-SURVIVORS` (the `precheck_record` gate owns them, as FILTER-SURVIVORS and GOLD-SUBSET, and it is stricter: it demands the live survivor set match `survivor_ids` exactly). Four more belong to the independent `question-reviewer`, which reports them as LOW findings for the judge to note: `NO-TELEGRAPH`, `NEAR-DUPLICATE`, `GENERIC-FACT`, and `NATURAL-PHRASING` (dropped entirely - pure phrasing taste). Run every remaining item - including the `FILTER-LOAD-BEARING` and `TEXT-LOAD-BEARING` gates, which are exactly the judgement no machine can make - and report each as a fact with PASS/WARN/N-A, no verdict.

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

**Verdict (interactive mode only):** APPROVE / REVISE / REJECT

Then wait for the user. "confirm" appends; "confirm anyway" overrides a non-APPROVE verdict (recorded as `reviewer_override: true`); anything else is treated as revision instructions. In orchestrated mode there is no verdict and no `reviewer_override`: you report the checklist as facts, pass `precheck_record`, and return the package - a critic attacks it and a judge decides.

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
reference_answer       from Step 6
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
