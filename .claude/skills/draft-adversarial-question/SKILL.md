---
name: draft-adversarial-question
description: Draft one adversarial (level ADV) benchmark question for the Horizon Scout M5 bank. The gold is an absence, and absence is proven by execution, not asserted - per-subtype verification protocols (zero-match, false-presupposition, data-absent, unanswerable), a refusal-shaped reference for the rubric judge, a mandatory reviewer checklist, and a confirmation-only append.
argument-hint: <subtype> [route]
---

# /draft-adversarial-question

Draft one adversarial benchmark question for the Horizon Scout M5 bank.

**Arguments:** $ARGUMENTS
Format: `<subtype> [route]` - e.g. `zero-match sql` or `false-presupposition`. Subtype values: `zero-match`, `false-presupposition`, `data-absent`, `unanswerable`. Route values: `sql`, `vector`, `hybrid` - the route the question *pretends* to be (see below). If subtype is omitted, propose one based on what the bank currently lacks and wait for the user to pick; if route is omitted, propose the one the drafted question most naturally wears.

This skill authors `level=ADV` questions on any route. L1-L3 questions belong to `/draft-sql-question`, `/draft-vector-question`, `/draft-hybrid-question`; ambiguous questions have `/draft-ambiguous-question` - point the user there instead of stretching this one. One question per pass; never batch.

## Orchestrated mode (question-drafter subagents only)

When this skill is followed by a `question-drafter` subagent under `/question-orchestrator` (the prompt says so and carries a pre-assigned `question_id` plus a corpus-profile candidate block):

- The candidate block is the subject and the batch order fixes subtype/route - skip every propose-and-wait step. All grounding, absence-proof execution, reference, and reviewer steps run unchanged and in full.
- Use the pre-assigned `question_id`, never "next free".
- There is no user in the loop: skip the confirmation prompt, never append, never write any file, and skip the `validate-bank` shell step (promotion validates). Instead return the complete entry - every field from the append table - plus evidence and history, in the output contract of `.claude/agents/question-drafter.md`.
- Everything else applies unchanged, including "reject at birth": a dead candidate (the absence turns out not to hold, the premise turns out true) is reported as `DRAFT-FAILED`, never worked around by wandering to a new topic.

Interactive invocations are unaffected: the per-question confirm gate stands.

## The gold is an absence (load-bearing)

Every other drafting skill verifies that an answer EXISTS. This one inverts: the correct answer is a refusal, and what must be verified is that nothing in the corpus contradicts the refusal. An asserted absence is worth nothing - a question is appended only when its absence claim was proven by execution IN THIS PASS, including the near-miss variants a determined answerer would try. The failure mode this guards against is a "zero-match" question that actually has three matches under a synonym, which would unfairly fail every honest system and pass every hallucinating one.

Two consequences:

- **Plausibility is the trap.** The question must sound answerable - a user could ask it in good faith. A transparently silly question ("projects on Mars agriculture") tests nothing; the pipeline refusing it proves nothing about refusal behavior on realistic inputs.
- **Route is a costume.** `expected_route` records which route a well-behaved router should send this question down (ADV questions do double duty as route-quality cells). The question is phrased to look like a normal question of that route; the router is never probed to check - routing behavior is the experiment's outcome, not authoring input.

## Tooling

All data access goes through the `horizon-draft` MCP server:

- `run_sql(query, row_cap=50)` - SELECT-only, read-only, ~10s timeout. The absence-proof workhorse: zero-count queries, near-miss variants, premise-refuting queries. SQL failures come back as an `{"error": ...}` result, not a tool error - for `data-absent`, a "column does not exist" error IS evidence and is recorded as such.
- `search_corpus(query, condition="pooled", k=20)` - project-level pooled retrieval across all four conditions. The semantic-absence check: what the pipeline will actually surface for this question. **Always pass `condition="pooled"` in this skill, never the tool's default.** Since 2026-08-03 that default is `hybrid_rerank`, and an absence proven under one condition is a weaker claim than an absence proven under four - which is the claim this skill exists to make. Requires the embed AND reranker llama-servers; a down server comes back as an `{"error": ...}` result. Only needed when the subtype protocol below calls for a sweep - a SQL-only pass (e.g. false-presupposition on stored columns) runs fine with the servers down.
- `get_project_text(project_ids)` - full free text for up to 10 projects. Adjudicates sweep hits and, for false-presupposition, verifies what the entity's text does and does not claim.
- `get_schema_docs()` - schema + value notes. For `data-absent`, the primary evidence that no column carries the asked-for facet.
- `get_bank_questions(route)` - existing entries per route: id, text, level, subtype only. Check the question's costume route AND scan for ADV near-duplicates.
- `get_corpus_profile(section=None)` - the exploration agent's corpus_profile.md. The `adversarial` section holds query-verified absence seeds. An `{"error": ...}` result means the profile (or section) is not built yet - proceed without it.

There are no write tools. The append at the end is a confirmation-gated file edit, done by this skill directly.

## Subtype reference

ADV is off-ladder: no level_evidence, no gold-count arithmetic. The subtype defines what is being tested and what must be proven:

- **`zero-match`** - the question names a plausible-sounding thing with zero corpus instances (a country/scheme/topic/year combination that never happened). The pipeline should say "nothing matches". Gold: `gold_project_ids: []` (validator-enforced). Proof: structured zeros PLUS a semantic sweep (protocol below).
- **`false-presupposition`** - the question embeds a premise the data contradicts ("Why was project X's funding cut?" when it never was). The pipeline should correct the premise, not answer around it. Proof: the entity EXISTS (else this is zero-match in disguise) AND the presupposed fact is FALSE - two executions.
- **`data-absent`** - the question asks for a kind of information the corpus does not carry at all (no column, not recoverable from report text). The pipeline should say the data is not available. Distinct from zero-match: the FACET is missing, not the instance - "which projects failed their ethics review" is data-absent (no such field); "Liechtenstein-coordinated MSCA projects" is zero-match (the fields exist, the count is 0).
- **`unanswerable`** - a question no route can satisfy even in principle: underspecified beyond repair, category error, or requiring information outside any corpus. The pipeline should decline rather than guess. The weakest proof by nature, so the protocol demands the failure argument be made per interpretation, each backed by a probe.

Subtype boundaries are checked by the reviewer (SUBTYPE-FIT): a question provable as zero-match must not be labeled data-absent, and vice versa - the refusal-overlay cells (H5c) depend on the labels meaning what they say.

## Startup (every invocation)

1. Call `get_bank_questions` for all four routes. Review existing ADV entries (level=ADV rows) to avoid near-duplicates and see subtype coverage; review the costume route's entries so the ADV question blends in with, rather than clones, its L1-L3 siblings. If subtype was not given: state current per-subtype counts, propose the least-covered, wait for the pick.
2. Call `get_corpus_profile(section="adversarial")` and `get_corpus_profile(section="structural-findings")`. If the profile or section is not built yet, note that and proceed without it. When the user names no subject: propose one from a profile candidate on a least-covered axis; candidates are advisory - every absence is re-proven in this pass regardless.
3. Call `get_schema_docs()`. Record the returned `content_hash` - entries whose proof relies on schema structure (`data-absent`, and any subtype whose proof queries lean on value notes) record it as `schema_docs_hash`.
4. If the protocol for the chosen subtype requires a `search_corpus` sweep, probe the retrieval stack: `search_corpus("probe", condition="pooled", k=1)`. An error result means a server is down - report it and end the pass before any drafting work.

## Step 1 - Ground

Establish the absence (or false premise) in the data BEFORE composing the question - evidence first, exactly like the other skills, just with inverted polarity:

- `zero-match`: find a filter combination with a true zero. Start from something real (a country that funds heavily, a scheme that exists) and locate the empty cell next to it - plausible-but-empty beats exotic-and-empty.
- `false-presupposition`: pick a real entity and observe what is actually true of it; the premise to falsify is the plausible-sounding negation or distortion of an observed fact.
- `data-absent`: read schema_docs for what is NOT there; shortlist facets users would plausibly ask about (evaluation scores, rejection reasons, gender breakdowns, follow-on funding) and confirm no column or report section covers them.
- `unanswerable`: identify the structural defect (missing referent, category error, out-of-corpus scope) and enumerate the 2-3 interpretations a charitable reader would try.

Present a short grounding summary: the claimed absence/false premise, the observations that suggest it, and why a real user might plausibly ask about it. If grounding already shows the absence is shaky, pivot before drafting.

## Step 2 - Draft

Present:

- **Question text** - phrased as a real user of the costume route would ask it. It must not telegraph its own trap: no "are there any...", no hedging that invites a refusal. A good ADV question is indistinguishable, on its face, from an honest question.
- **Declared subtype and costume route** - with one sentence each on why the trap fits the subtype and why a well-behaved router sends the question down that route.
- **The absence claim, stated precisely** - exactly what must be true of the corpus for the refusal to be the correct answer. This claim is what Step 3 proves.

## Step 3 - Prove the absence

Every draft is verified by execution in the same pass. Any edit to the question that shifts the absence claim invalidates prior proof - re-run, never carry stale results. The protocol is per-subtype; run every listed item.

**`zero-match`:**
1. Execute the direct count query - must return 0 (or an empty set). A nonzero result kills the draft: reject at birth or pick a different empty cell.
2. Near-miss sweep: re-execute under the variants a determined answerer would try - synonym/alternate spelling of the filter value, the adjacent column (name vs code, coordinator vs participant grain), a loosened range. Every variant's query and count is recorded. A variant that returns matches means the zero is a phrasing artifact - rewrite the question so the honest reading is genuinely empty, or abandon.
3. Semantic sweep: `search_corpus(question_text, condition="pooled", k=20)`; adjudicate the top pooled candidates by reading (`get_project_text`, batches of <= 10) - each gets OFF-TOPIC or MATCH with a one-line justification. Any MATCH kills the draft. This is the same adjudication discipline as the vector skill's completeness sweep, run to prove emptiness instead of completeness.
4. Set `gold_project_ids: []`.

**`false-presupposition`:**
1. Existence query: prove the entity/context is real (the project exists, the programme exists). If it does not, this is zero-match wearing the wrong label - re-subtype or redraft.
2. Refutation query: prove the presupposed fact is false - the query whose result directly contradicts the premise, result recorded verbatim. Where the premise concerns free text (claimed achievements, claimed topics), `get_project_text` on the entity and quote what the text actually says.
3. Check the refutation is airtight, not merely unsupported: "the data shows X, the premise says not-X" is a proof; "the data does not mention X" is data-absent territory - re-subtype if that is all there is.

**`data-absent`:**
1. Schema proof: from schema_docs, state that no table/column carries the facet; where a near-name column exists (e.g. `status` vs "why it was terminated"), execute a probe showing it does not answer the question. A "column does not exist" error result from a natural-attempt query is evidence - record it.
2. Free-text spot-check: `search_corpus` with the facet's natural phrasing, read the top hits, and confirm the report sections do not recover the facet either. The corpus has narrative report text - a facet absent from columns can still live there, and then the question is answerable and dead.
3. Distinguish from zero-match explicitly: one sentence in notes on why the FACET (not the instance count) is what is missing.

**`unanswerable`:**
1. Enumerate the 2-3 charitable interpretations from Step 1. For each: name the route a naive router would try, and execute at least one probe (`run_sql` or `search_corpus`) demonstrating that interpretation still fails - returns nothing usable, or returns something that does not address the question.
2. If any interpretation yields a defensible answer, the question is not unanswerable - it is ambiguous or merely hard; reject at birth or hand it to `/draft-ambiguous-question`.

## Step 4 - Reference answer

The reference is the correct refusal, written from the executed proofs - it is what the rubric judge (`src/judge/judge.py`) checks answers against, since ADV questions bypass RAGAS.

- It states the refusal precisely: nothing matches / the premise is false (and what is actually true) / that data is not in the corpus / the question cannot be answered as posed - matching the subtype.
- It invents nothing: no speculation about why the absence holds, no facts beyond the executed evidence. For false-presupposition, the correction states the true fact with entities and numbers **verbatim** from the refuting result.
- Length: one to two sentences. A refusal is short.

## Step 5 - Reviewer (mandatory, every pass)

Re-read question, absence claim, every executed proof, reference answer. Every item gets an explicit PASS / FAIL / WARN plus one sentence. Skip nothing; items marked with a subtype apply only to that subtype, and must be answered N/A for others.

```
ABSENCE-PROVEN       Every clause of the absence claim has an executed query (or quoted text
                     read) from THIS session backing it. FAIL otherwise.
NEAR-MISS-SWEPT      (zero-match) Synonym/adjacent-column/loosened-range variants executed, all
                     empty; semantic sweep run, every candidate adjudicated OFF-TOPIC. FAIL otherwise.
PREMISE-REFUTED      (false-presupposition) Entity existence proven AND the premise directly
                     contradicted by an executed result - not merely unsupported. FAIL otherwise.
FACET-ABSENT         (data-absent) No column carries the facet (schema proof recorded) AND the
                     free-text spot-check found nothing recovering it. FAIL otherwise.
NO-ESCAPE-READING    (unanswerable) Every charitable interpretation probed and shown to fail;
                     none yields a defensible answer. FAIL otherwise.
SUBTYPE-FIT          The trap matches its label - zero-match vs data-absent vs false-presupposition
                     boundaries respected. FAIL on a mislabel.
PLAUSIBILITY         A real user could ask this in good faith; the emptiness is not obvious from
                     the question alone. WARN otherwise.
NO-TELEGRAPH         The question does not hint at its own trap - no "are there any", no hedging,
                     no phrasing that invites a refusal. FAIL if the trap is visible.
ROUTE-COSTUME        The question reads as a natural question of its expected_route; one sentence
                     on why a well-behaved router sends it there. WARN otherwise.
REFUSAL-REFERENCE    Reference is a refusal/correction matching the subtype, derived only from
                     executed proofs, inventing nothing; entities and numbers verbatim. FAIL otherwise.
ONE-QUESTION         A single ask; no "and" joining two questions. FAIL if two-part.
GENERIC-FACT         The refusal requires this corpus - not decidable from general knowledge
                     alone. WARN only.
NEAR-DUPLICATE       Not a near-duplicate of an existing bank question (any route, any level).
                     WARN, naming the colliding id.
```

**Verdict:** APPROVE / REVISE / REJECT

Then wait for the user. "confirm" appends; "confirm anyway" overrides a non-APPROVE verdict (recorded as `reviewer_override: true`); anything else is treated as revision instructions.

## On confirmation - append

Append one JSONL line to `eval/bank.jsonl` with every field:

```
question_id            next free adv-NN
text                   the question
expected_route         sql | vector | hybrid     (the costume route)
level                  "ADV"
subtype                zero-match | false-presupposition | data-absent | unanswerable
specification          "well-specified"          ("underspecified" only for unanswerable
                                                  questions whose defect IS underspecification)
gold_project_ids       [] for zero-match (mandatory); omitted otherwise
schema_docs_hash       from startup, when the proof relies on schema structure or value notes
                       (always for data-absent)
reference_answer       from Step 4
notes                  the absence claim verbatim, every proof query + its result (including
                       near-miss variants and adjudicated sweep candidates), the subtype-fit
                       rationale, anything a verifier needs to re-prove the absence
reviewer_override      only if "confirm anyway"
```

ADV is off-ladder: no level_evidence, no pooling_evidence, no term_style, no answer_columns. Do not invent them.

Then run `./.venv/Scripts/python.exe -m src.cli validate-bank` and show its output. A validation failure after append is a skill bug - fix the entry before ending the pass.

## Standing rules

- **Never append without explicit confirmation.** Never rewrite an existing bank entry without explicit instruction.
- **Absence is proven, never asserted.** No entry is appended whose absence claim was not executed in this pass, near-misses included. Any question edit that shifts the claim invalidates prior proof - re-run, never carry stale results.
- **Plausible or pointless.** A question whose emptiness is visible on its face tests nothing; reject it.
- **The router is never probed.** Routing behavior is the experiment's outcome; the costume route is argued from the question's shape, not measured.
- **Subtypes mean what they say.** A mislabeled trap corrupts the refusal-overlay cells - re-subtype honestly or reject; never bend the label to fit the draft.
- **Reject at birth rather than patch.** A nonzero count, a true premise, a facet hiding in report text, an interpretation that works - these end the draft; they are not fixed by adjusting the label.
- **One question, one trap.** No compound asks, no stacked traps.
- **The reviewer runs every time**, every item explicit, before any confirmation prompt.
