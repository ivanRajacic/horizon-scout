---
name: draft-ambiguous-question
description: Draft one ambiguous-route benchmark question for the Horizon Scout M5 bank. Genuinely routable more than one way - every route in acceptable_routes is independently defended by execution; when sql is acceptable the entry carries an executed gold_sql (feeding Study 0.5's pool); a mandatory reviewer checklist gates a confirmation-only append.
argument-hint: [level] [routes]
---

# /draft-ambiguous-question

Draft one ambiguous-route benchmark question for the Horizon Scout M5 bank.

**Arguments:** $ARGUMENTS
Format: `[level] [routes]` - e.g. `L2 sql+vector` or `L3 hybrid+vector`. Level values: `L1`, `L2`, `L3` (advisory difficulty - see below). Routes: two or three of `sql`, `vector`, `hybrid`, joined with `+`; they become `acceptable_routes`. If either is omitted, propose based on what the bank currently lacks and wait for the user to pick.

This skill authors `route=ambiguous` questions. Single-route questions belong to `/draft-sql-question`, `/draft-vector-question`, `/draft-hybrid-question`; ADV questions have `/draft-adversarial-question` - point the user there instead of stretching this one. One question per pass; never batch.

**No subtype.** The ambiguous route has no subtype vocabulary (schema decision); this skill must not invent one. The interpretation split lives in notes, not in a label.

## Orchestrated mode (question-drafter subagents only)

When this skill is followed by a `question-drafter` subagent under `/draft-batch` (the prompt says so and carries a pre-assigned `question_id` plus a corpus-profile candidate block):

- The candidate block is the subject and the batch order fixes level and acceptable_routes - skip every propose-and-wait step. All grounding, per-leg verification, reference, and reviewer steps run unchanged and in full.
- Use the pre-assigned `question_id`, never "next free".
- There is no user in the loop: skip the confirmation prompt, never append, never write any file, and skip the `validate-bank` shell step (promotion validates). Instead return the complete entry - every field from the append table - plus evidence and history, in the output contract of `.claude/agents/question-drafter.md`.
- Everything else applies unchanged, including "reject at birth": a candidate with a degenerate leg is reported as `DRAFT-FAILED`, never worked around by wandering to a new topic.

Interactive invocations are unaffected: the per-question confirm gate stands.

## Every leg must stand on its own (load-bearing)

An ambiguous question is one a competent reader can legitimately parse more than one way - as a structured query, as a semantic search, or as a filter-then-read - depending on which constraint they treat as primary. It scores ROUTING, not retrieval: at runtime the router's choice is checked against `acceptable_routes`, so the label is honest only if every listed route is genuinely defensible and every unlisted route is genuinely unreasonable.

Ambiguity is therefore verified, not asserted: each leg gets its own reading AND its own executed evidence that the reading yields a sensible answer. A leg that cannot be defended is dropped or the draft dies - never left in `acceptable_routes` on vibes. Symmetrically, if one leg is so dominant that no competent reader would pick the others, this is a single-route question in disguise; hand it to that route's skill.

**The router is never probed.** Ambiguity is argued from the question's shape and the data, never measured against the live router - tuning questions to the router's behavior would leak into a frozen artifact and corrupt the experiment.

## Tooling

All data access goes through the `horizon-draft` MCP server:

- `run_sql(query, row_cap=50)` - SELECT-only, read-only, ~10s timeout. Grounds the topic and executes the sql leg's gold_sql. SQL failures come back as an `{"error": ...}` result, not a tool error.
- `search_corpus(query, condition="pooled", k=20)` - project-level pooled retrieval. The vector/hybrid legs' sanity probe: shows what the semantic reading would surface. Requires the embed AND reranker llama-servers; a down server comes back as an `{"error": ...}` result. Only needed when `acceptable_routes` touches vector or hybrid.
- `get_project_text(project_ids)` - full free text for up to 10 projects. Reads probe hits to confirm the semantic leg's answer is real, and feeds the reference.
- `get_schema_docs()` - schema + value notes for the sql leg; its `content_hash` is recorded as `schema_docs_hash` when a gold_sql is carried.
- `get_bank_questions("ambiguous")` - existing entries: id, text, level only (no subtype exists).
- `get_corpus_profile(section=None)` - the exploration agent's corpus_profile.md. The `ambiguous` section holds facts verified to live in BOTH a structured column and free text - the raw material of routing ambiguity. An `{"error": ...}` result means the profile (or section) is not built yet - proceed without it.

There are no write tools. The append at the end is a confirmation-gated file edit, done by this skill directly.

## Level and shape reference

There is no gold-count arithmetic here - level is an advisory difficulty judgment, stated with a one-sentence rationale (breadth of the topic, number of constraints in tension), and the user has the final word. What is NOT advisory:

- **`acceptable_routes`**: two or three of sql/vector/hybrid, validator-enforced (>= 2). Every listed route defended by execution (Step 3); every unlisted route argued unreasonable in one sentence.
- **The sql leg is special.** When `sql` is in `acceptable_routes`, the entry MUST carry an executed `gold_sql` with `answer_columns`, `sql_comparison`, and `schema_docs_hash` - locked decision: these entries feed Study 0.5's ~30-question gold-SQL pool, and the sql-acceptable answer is undefined without one. `sql_comparison` is `set` unless the sql reading is inherently a ranking, then `ordered`.
- **`term_style`** (exact-term | paraphrase): required judgment when `acceptable_routes` touches vector or hybrid (validator allows it only then); the pooled probe's rank matrix is the honesty heuristic, exactly as in the vector skill.
- **`gold_project_ids`**: not carried. The validator does not require it off-ladder, and a full pooled gold per leg would triple the cost of a question whose score is about routing, not retrieval. The probe evidence in notes documents the semantic legs instead.

Classic ambiguous shapes (from the archived pilot): count-over-topic ("how many projects focus on X" - sql count vs vector topic vs hybrid), ranking-plus-topic ("largest X projects by funding" - hybrid vs vector if the ranking is treated as incidental), filter-vs-theme ("summarise objectives of projects coordinated in Y" - hybrid vs vector if the filter is treated as incidental).

## Startup (every invocation)

1. Call `get_bank_questions("ambiguous")`. Review existing questions to avoid near-duplicates and see level and route-set coverage. If level or routes were not given: state current counts, propose the least-covered combination, wait for the pick.
2. Call `get_corpus_profile(section="ambiguous")` and `get_corpus_profile(section="structural-findings")`. If the profile or section is not built yet, note that and proceed without it. When the user names no topic: propose one from a profile candidate on a least-covered axis; candidates are advisory - every leg is re-verified in this pass regardless.
3. Call `get_schema_docs()` if sql will be acceptable. Record its `content_hash` for `schema_docs_hash`.
4. If vector or hybrid will be acceptable, probe the retrieval stack: `search_corpus("probe", condition="pooled", k=1)`. An error result means a server is down - report it and end the pass before any drafting work.

## Step 1 - Ground

Find a fact-shape that genuinely lives on both sides of the structured/textual divide, via `run_sql` and text reads:

- Confirm the structured side: the column(s) exist, the values are real, a count/rank/filter over them is meaningful (small sample queries).
- Confirm the textual side: the same subject matter appears in objectives/report text (spot-read 1-3 projects via `get_project_text` or a quick keyword query over objectives).
- For a hybrid leg: confirm a filter+read parse is natural (a structured constraint and a textual ask coexist in the topic).

Present a short grounding summary: the topic, where it lives structurally, where it lives textually, and which route readings the evidence supports. If the topic only really lives on one side, pivot before drafting - that is a single-route question.

## Step 2 - Draft

Present:

- **Question text** - one natural question in which neither reading is telegraphed: no schema echo (which forces sql), no "summarise/describe" framing that forces vector, no explicit filter-then-read scaffolding that forces hybrid. The ambiguity must survive careful reading.
- **The per-route readings** - one per acceptable route: "as sql: ...", "as vector: ...", "as hybrid: ..." - each stating what that route would compute or retrieve and what its answer looks like. These go into notes verbatim.
- **Excluded routes** - one sentence each on why the remaining route(s) are NOT acceptable (a competent reader would not parse it that way).
- **Declared level and (if topical) term_style**, each with a one-sentence rationale.

## Step 3 - Defend every leg

Every draft is verified in the same pass. Any edit to the question text invalidates prior verification - re-run every leg, never carry stale results.

1. **sql leg (when acceptable):** author the gold_sql that answers the sql reading; execute it via `run_sql`. Error: fix and re-execute. Empty result: the sql reading has no answer - drop the leg or redraft. Pin `answer_columns` from the executed result; set `sql_comparison` (`ordered` only for an inherently ranked reading, then run the tie check at the cutoff exactly as `/draft-sql-question` does).
2. **vector leg (when acceptable):** `search_corpus(question_text, condition="pooled", k=20)`. Read the top hits (`get_project_text`, batches of <= 10) far enough to confirm ON-TOPIC projects exist whose text answers the semantic reading - a sanity adjudication with one-line justifications, not a full gold-set completeness sweep. No on-topic hits: the vector reading is empty - drop the leg or redraft.
3. **hybrid leg (when acceptable):** execute the filter the hybrid reading implies (`run_sql`), record the survivor count, and confirm the textual ask is answerable from survivors (read 1-3). A filter with zero survivors or a textual ask answered by a stored column kills the leg.
4. **Degeneracy check, both directions:** (a) every leg's evidence is real - a leg kept "for coverage" with thin evidence is dropped; (b) no leg is so dominant that the others are unreasonable parses - if the legs' answers are wildly different in kind AND one parse is clearly forced by the phrasing, this is a single-route question; reject at birth or rephrase toward genuine balance. Dropping a leg below two acceptable routes kills the draft.
5. **term_style honesty (topical legs):** check the declared style against the probe's rank matrix (lexical-found on-topic hits vs dense-only), exactly the vector skill's heuristic.

## Step 4 - Reference answer

Written from the executed evidence of the legs - the sql result and/or the read texts, nothing else.

- Built on the strongest leg's answer; when the legs genuinely diverge (a count vs a characterization), the reference states the primary answer and acknowledges the other reading in one clause - the judge must not fail a system for picking a legitimate parse.
- Prose meaningfully paraphrased; acronyms, named entities, and numbers stay **verbatim**.
- Length: one to four sentences depending on the readings' shapes.

## Step 5 - Reviewer (mandatory, every pass)

Re-read question, per-route readings, every leg's executed evidence, reference answer. Every item gets an explicit PASS / FAIL / WARN plus one sentence. Skip nothing; items marked with a condition apply only when it holds, and must be answered N/A otherwise.

```
GENUINE-AMBIGUITY    Every route in acceptable_routes has a stated reading AND executed
                     evidence from THIS session that the reading yields a sensible answer.
                     FAIL otherwise.
NO-DEGENERATE-LEG    No leg is carried on thin evidence, and no single leg is so dominant
                     that the others are unreasonable parses. FAIL either way.
ROUTE-SET-MINIMAL    Every route NOT in acceptable_routes has a one-sentence argument why a
                     competent reader would not parse it that way. FAIL if an excluded route
                     is actually defensible.
SQL-LEG-EXECUTED     (sql acceptable) gold_sql executed this session, non-empty, answer_columns
                     pinned, sql_comparison set (tie check run if ordered), schema_docs_hash
                     recorded. FAIL otherwise.
NO-SUBTYPE           The entry carries no subtype and invents no ambiguity taxonomy. FAIL otherwise.
NO-TELEGRAPH         Phrasing forces no single reading - no schema echo, no route-scaffolding
                     language; the ambiguity survives careful reading. FAIL if one parse is
                     forced by the wording.
TERM-STYLE-HONEST    (vector/hybrid acceptable) Declared term_style consistent with the probe's
                     rank matrix. WARN otherwise.
NATURAL-PHRASING     Reads as a real user's question. WARN otherwise.
ONE-QUESTION         A single ask; multiple READINGS of one ask is the point, multiple ASKS is
                     a defect. FAIL if two-part.
REFERENCE-FIDELITY   Reference derived only from the legs' executed evidence; divergent readings
                     acknowledged; entities and numbers verbatim. FAIL otherwise.
GENERIC-FACT         Answer requires this corpus, not general knowledge. WARN only.
NEAR-DUPLICATE       Not a near-duplicate of an existing bank question (any route). WARN,
                     naming the colliding id.
```

**Verdict:** APPROVE / REVISE / REJECT

Then wait for the user. "confirm" appends; "confirm anyway" overrides a non-APPROVE verdict (recorded as `reviewer_override: true`); anything else is treated as revision instructions.

## On confirmation - append

Append one JSONL line to `eval/bank.jsonl` with every field:

```
question_id            next free amb-NN
text                   the question
expected_route         "ambiguous"
acceptable_routes      the defended routes (>= 2 of sql|vector|hybrid)
level                  L1 | L2 | L3            (advisory difficulty, rationale in notes)
specification          "well-specified"        (ambiguity of ROUTE, not of meaning - an
                                                underspecified question belongs elsewhere)
term_style             exact-term | paraphrase  (only when acceptable_routes touches
                                                vector/hybrid; omit otherwise)
gold_sql               (sql acceptable) the executed query
sql_comparison         (sql acceptable) set | ordered
answer_columns         (sql acceptable) pinned list
schema_docs_hash       (sql acceptable) from startup
reference_answer       from Step 4
notes                  per-route readings verbatim, per-leg evidence (queries + key results,
                       probe hits + adjudications, survivor counts), excluded-route arguments,
                       level rationale, term_style rationale, anything a verifier needs
reviewer_override      only if "confirm anyway"
```

No subtype, no gold_project_ids, no level_evidence, no pooling_evidence, no filter_evidence. Do not invent them.

Then run `./.venv/Scripts/python.exe -m src.cli validate-bank` and show its output. A validation failure after append is a skill bug - fix the entry before ending the pass.

## Standing rules

- **Never append without explicit confirmation.** Never rewrite an existing bank entry without explicit instruction.
- **Every leg defended by execution.** No route enters acceptable_routes on plausibility alone; no leg's evidence is carried from a previous draft of the text.
- **The sql leg always carries its gold.** sql in acceptable_routes without an executed gold_sql is a validation-passing lie - Study 0.5 depends on these entries.
- **The router is never probed.** Ambiguity is argued from shape and data, never measured against the live router.
- **No invented taxonomy.** No subtype, no ad-hoc ambiguity categories - the vocabulary is undefined by decision, and notes carry the nuance.
- **Reject at birth rather than patch.** A dead leg, a dominant leg, a forced parse - these end the draft; they are not fixed by shrinking acceptable_routes to whatever survived.
- **One question, many readings.** Multiple readings of one ask is the point; multiple asks is a defect.
- **The reviewer runs every time**, every item explicit, before any confirmation prompt.
