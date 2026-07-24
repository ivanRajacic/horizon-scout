---
name: draft-sql-question
description: Draft one SQL-route (L1-L3) benchmark question for the Horizon Scout M5 bank. Grounds the question in real data via the horizon-draft MCP tools, verifies the gold SQL by execution, runs a mandatory reviewer checklist, and appends to the bank only after explicit user confirmation.
argument-hint: <level> [subtype]
---

# /draft-sql-question

Draft one SQL-route benchmark question for the Horizon Scout M5 bank.

**Arguments:** $ARGUMENTS
Format: `<level> [subtype]` - e.g. `L2 value-grounded` or `L3`. Level values: `L1`, `L2`, `L3`. Subtype values: `lookup`, `aggregate`, `join-lookup`, `value-grounded`, `grouped-aggregate`, `multi-join`, `rank`, `trap`. `rank` is legal at every level; all other subtypes are level-bound as listed below. If subtype is omitted, propose one based on what the bank currently lacks and wait for the user to pick.

This skill authors `route=sql` questions at levels L1-L3 only. ADV, vector, hybrid, and ambiguous questions have their own skills; if the user asks for one of those, point them there instead of stretching this one. One question per pass; never batch.

## Orchestrated mode (question-drafter subagents only)

When this skill is followed by a `question-drafter` subagent under `/draft-batch` (the prompt says so and carries a pre-assigned `question_id` plus a corpus-profile candidate block):

- The candidate block is the subject and the batch order fixes level/subtype - skip every propose-and-wait step, and skip the startup profile calls (`get_corpus_profile(section="sql")` and `frontier`): the candidate block IS your profile slice, and the frontier only matters when choosing a subject. Still call `get_schema_docs()` (record the hash) and `get_bank_questions("sql")`.
- **Two-tier grounding - the candidate is part proven, part advisory.** Its `evidence` (executed SQL + its result) is proven-by-execution and merge-pass spot-checked: trust and confirm it - start from that SQL and re-execute once as a drift check, rather than re-sampling the value space from scratch. Everything else it asserts - route/level/subtype - is ADVISORY and re-verified in full: recompute level_evidence from the executed gold SQL and recompute the level from it, never from the candidate's recommendation. Reject-at-birth and born-verified-this-pass are unchanged - the confirming re-execution happens in-pass.
- Use the pre-assigned `question_id`, never "next free".
- There is no user in the loop: skip the confirmation prompt, never append, never write any file, and skip the `validate-bank` shell step (promotion validates). Instead return the complete entry - every field from the append table - plus evidence and history, in the output contract of `.claude/agents/question-drafter.md`.
- Run Step 5 as the **orchestrated-mode checklist** (see Step 5): every gate except the pure-judgment polish items the independent `question-reviewer` owns.
- Everything else applies unchanged, including "reject at birth": a dead candidate is reported as `DRAFT-FAILED`, never worked around by wandering to a new topic. Level disagreements that would normally go to the user go into the returned package instead - as a `DRAFT-FAILED` if the requested cell cannot be met honestly.

Interactive invocations are unaffected: the per-question confirm gate stands.

## Tooling

All data access goes through the `horizon-draft` MCP server:

- `run_sql(query, row_cap=50)` - SELECT-only, read-only, rows capped (hard ceiling 200), ~10s timeout. SQL failures come back as a `{"error": ...}` result, not a tool error - broken queries are data to reason about, which trap authoring depends on.
- `get_schema_docs()` - schema_docs.md verbatim plus `{version, content_hash}`.
- `get_bank_questions(route)` - existing entries for a route: id, text, level, subtype only.
- `get_corpus_profile(section=None)` - the exploration agent's corpus_profile.md (whole, or one section by key). Query-verified candidate topics plus the `frontier` coverage table. An `{"error": ...}` result means the profile is not built yet - proceed without it.

There are no write tools. The append at the end is a confirmation-gated file edit, done by this skill directly.

## Level and subtype reference

**Levels are computed from the gold SQL, never asserted.** The operational tests:

- **L1** - single table, single operation. *Test: no JOIN, at most 1 non-trivial WHERE.* Subtypes: `lookup` (fetch a stored fact about one entity by key), `aggregate` (one COUNT/SUM/AVG/MAX with a simple filter), `rank` (bare ORDER BY + LIMIT, no join).
- **L2** - join, value-grounding, or grouping. *Test: at least 1 JOIN, or dependence on a schema_docs value note, or a GROUP BY without ranking.* Subtypes: `join-lookup` (fact requiring one cross-table hop), `value-grounded` (filter whose meaning lives in a schema_docs value note, not the column name), `grouped-aggregate` (one GROUP BY, no ranking), `rank` (top-N with one join).
- **L3** - multi-join, aggregation+ranking, or a trap. *Test: at least 2 JOINs, or GROUP BY combined with ranking, or a near-miss trap.* Subtypes: `multi-join`, `rank` (GROUP BY + ORDER BY + LIMIT), `trap` (a plausible-wrong query exists that runs cleanly and returns a different answer - near-miss column, wrong entity grain, participant-vs-coordinator, totalCost-vs-ecMaxContribution).

A bare single-table top-N is L1 `rank`, not L3 - ORDER BY + LIMIT alone never satisfies the L3 test. If the executed gold SQL's evidence doesn't match the requested level, say so and either rewrite toward the requested level or relabel - the user chooses. Never append a question whose level_evidence contradicts its level.

`sql_comparison` is `ordered` iff subtype is `rank`, `set` otherwise. The skill sets it, never asks.

## Startup (every invocation)

1. Call `get_schema_docs()`. Read it. Record the returned `content_hash` - every appended entry carries it as `schema_docs_hash`.
2. Call `get_bank_questions("sql")`. Review existing questions (id, text, level, subtype) to avoid near-duplicates and to see subtype coverage. Keep them in mind throughout.
3. Call `get_corpus_profile(section="sql")` and `get_corpus_profile(section="frontier")`. If the profile is not built yet, note that and proceed without it. The frontier tells you which buckets are `mapped` but not yet `mined` - prefer a subject from one of those.
4. If no subtype was given: state the current per-subtype counts for the requested level and propose the least-covered subtype. Wait for the user's pick.
5. When the user names no subject: propose one from the profile - prefer a candidate on a **least-covered axis** (an axis or entity family the existing bank questions do not touch; the frontier's `mapped`-but-not-`mined` buckets are the first place to look), not yet used by any bank question. Least-covered axis beats least-covered subtype when they conflict: width across the corpus is the frontier's whole point. Profile candidates are advisory seeds: the route/level/subtype is re-verified in full (level_evidence recomputed from the executed gold SQL), while the candidate's executed `evidence` SQL is only re-confirmed cheaply - see Orchestrated mode for the two-tier rule. (Orchestrated mode skips both these profile calls - the candidate block already carries the section.)

## Step 1 - Ground

Explore the real data the question will touch, via `run_sql`, before drafting anything. Small targeted queries only. **Orchestrated mode:** start from the candidate's `evidence` SQL and re-execute it once to confirm it still returns the stated result; skip re-sampling the value space the candidate already grounded, and run only the extra targeted queries the specific subtype needs (e.g. the trap's wrong-query, a value-note check).

- Confirm the tables and columns involved exist as schema_docs describes them.
- Sample actual values for any column the question will filter or group on (`SELECT DISTINCT ... LIMIT 20`, or a small aggregate to see the distribution).
- For `value-grounded`: locate the specific schema_docs value note the question will depend on, and verify the enum/code values in the note appear in the data.
- For `trap`: identify the near-miss pair (two columns, two grains, or two roles that a careless query would confuse) and confirm both members exist and differ in the data.

Present a short grounding summary: which tables/columns, what the sampled values look like, anything surprising. Questions are grounded in observed data, never assumed data. If the data doesn't support the concept (empty enum, degenerate distribution, absent field), say so and pivot before drafting.

## Step 2 - Draft

Present:

- **Question text** - phrased as a real user would ask it. It must not echo column names, table names, or read like a SQL statement translated to English. ("How much EU money went to the SMARTAQUA project?" not "What is the ecMaxContribution of the project with acronym SMARTAQUA?")
- **Gold SQL** - the query that answers it.
- **answer_columns** - the pinned list of result columns that constitute the answer. Extra columns a system might helpfully return are not compared; these are.

For `trap`, additionally present the **wrong query**: the plausible near-miss version, with one sentence on why a careless model writes it.

## Step 3 - Execute and verify

Every draft is verified by execution in the same pass. Never carry results from a previous draft - any edit to the SQL means re-execution.

1. Execute the gold SQL via `run_sql`.
   - Error: the draft is broken; fix and re-execute.
   - Empty result: reject at birth. An L1-L3 question with an empty gold answer is a zero-match ADV question wearing the wrong label; rewrite or abandon.
   - Surprising result (magnitude, cardinality, or content that doesn't match the grounding): investigate before proceeding; surprises here are usually wrong SQL, occasionally interesting data - distinguish which.
2. Compute and present **level_evidence** from the gold SQL:
   ```
   {join_count, non_trivial_where_count, has_group_by, has_order_by_limit,
    value_note_dependencies: [...], trap_documented: bool}
   ```
   Check it against the operational test for the claimed level. Mismatch: back to Step 2 with a rewrite, or relabel with user approval.
3. **Subtype obligations:**
   - `rank` - set `sql_comparison=ordered`. Run the tie check: re-execute with LIMIT n+1 (or inspect the boundary values) and confirm no tie at the cutoff. Tie found: rewrite the question or move the cutoff; a tied ranking has two correct orderings and cannot be scored.
   - `trap` - execute the wrong query too. It must run cleanly and return a different answer than the gold. Record both results. If the wrong query errors or returns the same answer, the trap doesn't exist; rewrite or re-subtype.
   - `value-grounded` - name the schema_docs value note the question depends on in the notes field. If no note is needed to write the gold SQL correctly, the question isn't value-grounded; re-subtype.
   - All non-`rank` subtypes - confirm the answer's meaning is order-independent; `sql_comparison=set`.

## Step 4 - Reference answer

Written from the executed result, nothing else.

- Prose meaningfully paraphrased - never a verbatim readback of the result table.
- Named entities, project acronyms, country/programme codes, and all numeric values stay **verbatim** - never paraphrase, round, or reword the tokens the judge must match. Only the connective prose is rephrased.
- Length: one to two sentences for `lookup`/`aggregate`; up to four for `rank`/`grouped-aggregate`/`multi-join` results with several rows. If the result has many rows, the reference states the pattern plus the pinned rows, not a full table dump.

## Step 5 - Reviewer (mandatory, every pass)

Re-read question, gold SQL, executed result, level_evidence, reference answer. Every item gets an explicit PASS / FAIL / WARN plus one sentence; items marked with a subtype apply only to that subtype, and must be answered N/A for others.

**Interactive mode:** run every item below, skip nothing. **Orchestrated mode:** an independent `question-reviewer` attacks the draft afterward. It owns NO-TELEGRAPH, NEAR-DUPLICATE, and GENERIC-FACT as MINOR flags, and NATURAL-PHRASING is dropped entirely (pure phrasing taste - no one runs it), so skip all four here - run every other item (including all subtype obligations) in full.

```
EXECUTED-GOLD        Gold SQL executed this session; result recorded; non-empty. FAIL otherwise.
PINNED-COLUMNS       answer_columns explicitly listed and all present in the executed result. FAIL otherwise.
LEVEL-EVIDENCE       level_evidence satisfies the claimed level's operational test. FAIL on mismatch.
SUBTYPE-LEGAL        Subtype is legal at this level (rank anywhere; others per the reference table). FAIL otherwise.
NATURAL-PHRASING     Question reads as a user's question; no schema echo, no SQL-in-English. WARN otherwise.
ONE-QUESTION         A single ask; no "and" joining two questions. FAIL if two-part.
NO-TELEGRAPH         Question betrays nothing about the answer's shape, count, or content from having
                     seen the result. WARN otherwise.
VALUE-NOTE           (value-grounded) The schema_docs dependency is named in notes and genuinely required
                     to write correct SQL. FAIL if missing or unnecessary.
TIE-CHECK            (rank) No tie at the cutoff; sql_comparison=ordered. FAIL otherwise.
TRAP-DOCUMENTED      (trap) Wrong query recorded, executes cleanly, returns a different answer. FAIL otherwise.
ORDER-INDEPENDENCE   (non-rank) Answer meaning doesn't depend on row order; sql_comparison=set. FAIL otherwise.
REFERENCE-FIDELITY   Reference derived only from the executed result; prose paraphrased; entities, codes,
                     and numbers verbatim. FAIL if it contains claims not in the result or paraphrased values.
GENERIC-FACT         Answer requires the database - not answerable from general knowledge
                     ("what does MSCA stand for"). WARN only.
NEAR-DUPLICATE       Not a near-duplicate of an existing bank question. WARN, naming the colliding id.
```

**Verdict:** APPROVE / REVISE / REJECT

Then wait for the user. "confirm" appends; "confirm anyway" overrides a non-APPROVE verdict (recorded as `reviewer_override: true`); anything else is treated as revision instructions.

## On confirmation - append

Append one JSONL line to `eval/bank.jsonl` with every field:

```
question_id            next free sql-NN
text                   the question
expected_route         "sql"
level                  L1 | L2 | L3
subtype                as drafted
specification          "well-specified"        (this skill never authors underspecified)
gold_sql               the executed query
sql_comparison         set | ordered            (ordered iff subtype=rank)
answer_columns         pinned list
level_evidence         the computed dict from Step 3
reference_answer       from Step 4
schema_docs_hash       from startup
notes                  grounding observations, value-note name, trap wrong-query + both results,
                       tie-check outcome, anything a verifier needs
reviewer_override      only if "confirm anyway"
```

Then run `./.venv/Scripts/python.exe -m src.cli validate-bank` and show its output. A validation failure after append is a skill bug - fix the entry before ending the pass.

## Standing rules

- **Never append without explicit confirmation.** Never rewrite an existing bank entry without explicit instruction.
- **The label is born verified.** No entry is appended whose gold SQL was not executed in this pass. Any SQL edit invalidates prior execution - re-run, never carry stale results.
- **Levels are computed, never asserted.** level_evidence decides; disagreement between requested and computed level goes to the user, not to silent relabeling.
- **Reject at birth rather than patch.** Empty results, dead traps, tied rankings, unneeded value notes - these end the draft; they are not fixed by adjusting the label.
- **Grounded, never assumed.** Every filter value, enum, and entity in a question was observed via `run_sql` in this pass.
- **One question, one fact-shape.** No compound asks.
- **The reviewer runs every time**, every item explicit, before any confirmation prompt.
