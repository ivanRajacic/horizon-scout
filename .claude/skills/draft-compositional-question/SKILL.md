---
name: draft-compositional-question
description: Draft one compositional benchmark question for the Horizon Scout M5 bank (3 total, hand-written, interactive only). Answerable only by composing capabilities iteratively - e.g. SQL top-k, then per-item reading, then re-rank; the structured stage is executed, the per-item stage is read, and the reference encodes the expected set for partial-credit scoring. RQ4a's diagnostic cells.
argument-hint: [route]
---

# /draft-compositional-question

Draft one compositional benchmark question for the Horizon Scout M5 bank.

**Arguments:** $ARGUMENTS
Format: `[route]` - the base route the entry wears (`sql`, `vector`, or `hybrid`); propose one if omitted. Compositional is a FLAG, not a route: the entry carries a normal `expected_route` and level plus `compositional: true`.

Only 3 compositional questions exist in the allocation, all hand-written - this skill is deliberately minimal and **interactive only**: no orchestrated mode, no batch, never invoked by `question-drafter` subagents. One question per pass. L1-L3 single-capability questions belong to the route skills; ADV and ambiguous have their own.

## What compositional means (the gate)

A compositional question is answerable ONLY by composing capabilities iteratively - the canonical shape: a structured stage narrows the corpus (SQL top-k), a per-item stage judges each survivor's free text (LLM scoring/reading), and a final stage combines them (sort, pick, contrast). Every static route must fail STRUCTURALLY, not just perform badly:

- Not sql-answerable: the deciding property lives in free text, not a column.
- Not vector-answerable: the answer depends on a structured computation (ranking, arithmetic, top-k membership) retrieval cannot do.
- Not hybrid-answerable: one filter-then-read pass is insufficient - the composition needs per-item judgment feeding back into a ranking/selection step.

If any single static route could answer it, reject at birth - it belongs to that route's skill. This gate is the whole point: these are RQ4a's diagnostic cells, where the agentic condition should succeed and all static conditions fail by construction.

**The failure argument is written, not run.** Record in notes WHY each static route fails structurally; do not run the ask pipeline to demonstrate it - the executable proof is the pilot's job (one compositional question is traced through the static routes there).

## Tooling

The usual `horizon-draft` MCP tools; the ones this skill needs:

- `run_sql` - executes the structured stage (the candidate set / top-k) and grounds every filter value.
- `get_project_text` - reads the per-item stage's texts (batches of <= 10).
- `get_schema_docs()` - schema + value notes; record `content_hash` when the entry carries a gold_sql.
- `get_bank_questions(route)` - near-duplicate check against the base route's entries.

There are no write tools. The append at the end is a confirmation-gated file edit, done by this skill directly.

## Procedure

1. **Ground the structured stage.** Design and execute the stage-1 query via `run_sql` (the top-k, the filtered candidate set). The result must be non-empty, enumerable, and small enough that the per-item stage is tractable (aim <= 20 items).
2. **Ground the per-item stage.** Read every stage-1 item's text via `get_project_text`. The per-item property (what gets scored, compared, or selected on) must genuinely live in the free text and genuinely vary across items - if all items score alike, the composition collapses.
3. **Draft.** Present the question text (one natural ask whose answer requires the full composition), the base route + level (computed per that route's rules from the fields the entry carries), the stage decomposition (stage 1 query, stage 2 judgment, stage 3 combination), and the per-route structural-failure argument.
4. **Compute the expected result.** Perform the composition yourself from the executed stage-1 result and the read texts: the expected top-k set (or selection), with each item's per-item justification quoted from its text.
5. **Reference answer.** Encodes the expected SET for partial-credit scoring (TAG's ranking lesson - top-k set overlap, never exact match): name the expected items with acronyms/ids verbatim, state the basis for each in one clause, and phrase the reference so a partially-overlapping answer is gradable. Prose paraphrased; entities and numbers verbatim.
6. **Mini-checklist (mandatory, every pass).** PASS / FAIL / WARN plus one sentence each:

```
COMPOSITION-REQUIRED  Each static route's structural-failure argument written and convincing;
                      no single route answers it. FAIL otherwise.
STAGE-1-EXECUTED      Structured stage executed this session, non-empty, <= 20 items, every
                      item's text read. FAIL otherwise.
PER-ITEM-VARIES       The per-item property genuinely varies across items (quoted evidence).
                      FAIL if the composition collapses.
PARTIAL-CREDIT-REF    Reference encodes the expected set, gradable under set overlap, items
                      verbatim. FAIL otherwise.
NATURAL-PHRASING      Reads as a real user's question, not a pipeline description. WARN otherwise.
ONE-QUESTION          A single ask (a composition is one ask). FAIL if two-part.
NEAR-DUPLICATE        Not a near-duplicate of an existing bank question. WARN, naming the id.
```

**Verdict:** APPROVE / REVISE / REJECT - then wait for the user. "confirm" appends; "confirm anyway" records `reviewer_override: true`; anything else is revision instructions.

## On confirmation - append

Append one JSONL line to `eval/bank.jsonl`: `question_id` (next free `cmp-NN`), `text`, `expected_route` (the base route), `level`, `subtype` and every field that base route's ladder requires (a sql-based entry carries executed `gold_sql`/`answer_columns`/`level_evidence`/`schema_docs_hash` per `/draft-sql-question`'s rules; the validator has no compositional-specific requirements but enforces the base route's in full), `compositional: true`, `specification`, `reference_answer`, and `notes` (stage decomposition, stage-1 query + result, per-item judgments with quotes, per-route structural-failure arguments, partial-credit basis).

Then run `./.venv/Scripts/python.exe -m src.cli validate-bank` and show its output. A validation failure after append is a skill bug - fix the entry before ending the pass.

## Standing rules

- **Never append without explicit confirmation.**
- **The gate is structural.** "Hard for static routes" is not enough; each must fail by construction, argument recorded.
- **Executed and read, never assumed.** Stage 1 executed this pass; every item's text read this pass; the expected set computed from that evidence only.
- **Never run the ask pipeline.** The failure proof belongs to the pilot.
- **Partial credit by design.** The reference is a gradable set, never a brittle exact answer.
- **Interactive only.** No orchestrated mode; three questions do not need a factory.
