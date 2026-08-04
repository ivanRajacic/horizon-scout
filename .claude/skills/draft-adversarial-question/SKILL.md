---
name: draft-adversarial-question
description: Draft one adversarial (level ADV) benchmark question for the Horizon Scout M5 bank. Derived from a question already in the bank, which is both the answerable control and the near-miss proof. The gold is an absence, and absence is proven by execution, not asserted - per-subtype verification protocols (zero-match, false-presupposition, data-absent, unanswerable), a typed absence_evidence record the precheck re-runs, a refusal-shaped reference for the rubric judge, a mandatory reviewer checklist, and a confirmation-only append.
argument-hint: <subtype> [twin_id]
---

# /draft-adversarial-question

Draft one adversarial benchmark question for the Horizon Scout M5 bank.

**Arguments:** $ARGUMENTS
Format: `<subtype> [twin_id]` - e.g. `zero-match sql-07` or `false-presupposition`. Subtype values: `zero-match`, `false-presupposition`, `data-absent`, `unanswerable`. `twin_id` names the bank question this one is derived from (see below); omit it and the skill proposes candidates and waits for the pick. If subtype is omitted, propose one based on what the bank currently lacks and wait for the user to pick.

This skill authors `level=ADV` questions on any route. L1-L3 questions belong to `/draft-sql-question`, `/draft-vector-question`, `/draft-hybrid-question`; ambiguous questions have `/draft-ambiguous-question` - point the user there instead of stretching this one. One question per pass; never batch.

## Orchestrated mode (question-drafter subagents only)

When this skill is followed by a `question-drafter` subagent under `/question-orchestrator` (the prompt says so and carries a pre-assigned `question_id` plus a parent block):

- The parent block is the subject and the batch order fixes the subtype - skip every propose-and-wait step. All grounding, absence-proof execution, reference, and reviewer steps run unchanged and in full.
- Use the pre-assigned `question_id` and the packet's `twin_id`, never "next free" and never a parent of your own choosing.
- There is no user in the loop: skip the confirmation prompt, never append, never write any file, and skip the `validate-bank` shell step (promotion validates). Instead return the complete entry - every field from the append table - plus evidence and history, in the output contract of `.claude/agents/question-drafter.md`.
- Everything else applies unchanged, including "reject at birth": a dead parent (its gold no longer holds) or a dead candidate (the absence turns out not to hold, the premise turns out true) is reported as `DRAFT-FAILED`, never worked around by wandering to a new topic or a new parent.

Interactive invocations are unaffected: the per-question confirm gate stands.

## The gold is an absence (load-bearing)

Every other drafting skill verifies that an answer EXISTS. This one inverts: the correct answer is a refusal, and what must be verified is that nothing in the corpus contradicts the refusal. An asserted absence is worth nothing - a question is appended only when its absence claim was proven by execution IN THIS PASS, including the near-miss variants a determined answerer would try. The failure mode this guards against is a "zero-match" question that actually has three matches under a synonym, which would unfairly fail every honest system and pass every hallucinating one.

Three consequences:

- **Plausibility is the trap.** The question must sound answerable - a user could ask it in good faith. A transparently silly question ("projects on Mars agriculture") tests nothing; the pipeline refusing it proves nothing about refusal behavior on realistic inputs.
- **The proof is typed, not narrated.** Every executed claim goes into `absence_evidence` as `{sql, expect, key_result}`, and `precheck_record` re-runs all of it. A proof that lives only in `notes` is a paragraph a model wrote, which is exactly what this skill exists not to produce.
- **Route is a costume.** `expected_route` records which route a well-behaved router should send this question down (ADV questions do double duty as route-quality cells). The question is phrased to look like a normal question of that route; the router is never probed to check - routing behavior is the experiment's outcome, not authoring input.

## The twin (load-bearing)

Every `zero-match` and `false-presupposition` question is a **perturbation of a question already in the bank**, named in `twin_id`. `data-absent` may have one; `unanswerable` derives from nothing and must not.

Two reasons, and both are the reason:

- **It is the control.** A bank of refusal questions alone cannot tell a well-calibrated system from one that refuses everything - a system that says "no data" to all 60 questions scores perfectly on the adversarial cells. The twin is the near-identical question that *does* have an answer, so over-refusal is visible. Every published benchmark that measures abstention holds both halves for this reason.
- **It is the near-miss proof.** The parent's gold is a fact of the corpus that was already verified. Shift one value of it and the emptiness you get is provably adjacent to something real, rather than an unrelated empty corner nobody would ever ask about. Its gold SQL returns rows; yours returns none. `precheck_record` re-executes the parent's gold as `TWIN-LIVE` and fails the draft if the control has itself gone dead.

**Minimal edit is the goal, not a smell.** The best adversarial question differs from its parent by as little as the absence allows - one country, one year, one scheme, one inverted clause. The reviewer checks for this (`MINIMAL-EDIT`), and `NEAR-DUPLICATE` is read differently here: near the parent is required; near anything *else* in the bank is the warning.

## Tooling

All data access goes through the `horizon-draft` MCP server:

- `get_bank_record(question_id)` - one COMPLETE bank entry, gold and reference included. The parent's raw material: what is true, which filter to shift, which fact to invert. This is the one place a full bank record is legitimately in a drafter's hands, and only because the parent IS the subject.
- `run_sql(query, row_cap=50)` - SELECT-only, read-only, ~10s timeout. The absence-proof workhorse: zero queries, near-miss variants, premise-refuting queries. SQL failures come back as an `{"error": ...}` result, not a tool error - for `data-absent`, a "column does not exist" error IS evidence and is recorded as such.
- `precheck_record(record)` - the deterministic gate. For ADV it runs `GOLD-EMPTY`, `ABSENCE-QUERIES` (every claim re-executed), `TWIN-EXISTS` and `TWIN-LIVE`. Run it before the reviewer, every pass.
- `search_corpus(query, condition="pooled", k=20)` - project-level pooled retrieval across all four conditions. The semantic-absence check: what the pipeline will actually surface for this question. **Always pass `condition="pooled"` in this skill, never the tool's default.** Since 2026-08-03 that default is `hybrid_rerank`, and an absence proven under one condition is a weaker claim than an absence proven under four - which is the claim this skill exists to make. Requires the embed AND reranker llama-servers; a down server comes back as an `{"error": ...}` result. Only needed when the subtype protocol below calls for a sweep - a SQL-only pass (e.g. false-presupposition on stored columns) runs fine with the servers down.
- `get_project_text(project_ids)` - full free text for up to 10 projects. Adjudicates sweep hits and, for false-presupposition, verifies what the entity's text does and does not claim.
- `get_schema_docs()` - schema + value notes. For `data-absent`, the primary evidence that no column carries the asked-for facet.
- `get_bank_questions(route)` - existing entries per route: id, text, level, subtype only. Used to pick parent candidates, to check the costume route, and to scan for ADV near-duplicates. Full records come from `get_bank_record`, one at a time, for the parent only.
- `get_corpus_profile(section=None)` - the exploration agent's corpus_profile.md. The `adversarial` section holds query-verified absence seeds. An `{"error": ...}` result means the profile (or section) is not built yet - proceed without it.

There are no write tools. The append at the end is a confirmation-gated file edit, done by this skill directly.

## Subtype reference

ADV is off-ladder: no level_evidence, no gold-count arithmetic. The subtype defines what is being tested, what must be proven, and how it is derived from the parent:

- **`zero-match`** - the question names a plausible-sounding thing with zero corpus instances (a country/scheme/topic/year combination that never happened). The pipeline should say "nothing matches".
  *Derivation:* take the parent's filter and shift ONE value to a plausible-but-empty neighbour. Emptiness caused by the data ending (a start year past the corpus cutoff) belongs here too, with the reason written into notes - it is a zero like any other, just for a different cause.
  *Gold:* `gold_project_ids: []` (validator-enforced). *Proof:* structured zeros PLUS a semantic sweep (protocol below). *Twin:* required.
- **`false-presupposition`** - the question embeds a premise the data contradicts ("Why was project X's funding cut?" when it never was). The pipeline should correct the premise, not answer around it.
  *Derivation:* invert or distort a fact the parent's gold answer establishes. The parent's own gold SQL is usually the refutation query, already executed.
  *Proof:* the entity EXISTS (else this is zero-match in disguise) AND the presupposed fact is FALSE - two executions. *Twin:* required.
- **`data-absent`** - the question asks for a kind of information the corpus does not carry at all (no column, not recoverable from report text). The pipeline should say the data is not available. Distinct from zero-match: the FACET is missing, not the instance - "which projects failed their ethics review" is data-absent (no such field); "Liechtenstein-coordinated MSCA projects" is zero-match (the fields exist, the count is 0).
  *Derivation:* keep the parent's subject and ask for a facet the schema does not carry. **Prefer a facet whose name resembles a column that does exist** - "termination reason" next to a real `status`, "evaluation score" next to a real `totalCost`. A facet that plainly belongs to another universe tests schema linking not at all; one that almost lands is the hard cell.
  *Proof:* schema proof plus a free-text spot-check. *Twin:* optional - record it when the question really is the parent's subject, omit it when the facet stands alone.
- **`unanswerable`** - a question no route can satisfy even in principle: underspecified beyond repair, category error, or requiring information outside any corpus. The pipeline should decline rather than guess. The weakest proof by nature, so the protocol demands the failure argument be made per interpretation, each backed by a probe. *Twin:* forbidden - there is no answerable question this is a perturbation of.

Subtype boundaries are checked by the reviewer (SUBTYPE-FIT): a question provable as zero-match must not be labeled data-absent, and vice versa - the refusal-overlay cells (H5c) depend on the labels meaning what they say.

## Startup (every invocation)

1. Call `get_bank_questions` for all four routes. Review existing ADV entries (level=ADV rows) to avoid near-duplicates and see subtype coverage; review the costume route's entries so the ADV question blends in with, rather than clones, its L1-L3 siblings. If subtype was not given: state current per-subtype counts, propose the least-covered, wait for the pick.
2. **Pick the parent.** If `twin_id` was given, take it. Otherwise propose 2-3 candidates from step 1 that suit the chosen subtype - prefer a parent whose gold has a shiftable value (a country, a year, a scheme, a named fact), spread across routes and topics away from existing ADV entries, and never a question already named by another ADV entry's `twin_id`. State them and wait for the pick. `unanswerable` skips this step entirely.
3. Call `get_bank_record(twin_id)` and read the parent whole: its text, gold, reference answer, notes. Re-execute its gold now (`run_sql` on `gold_sql`, or read its `gold_project_ids`). A parent whose gold no longer holds ends the pass - say so and stop rather than quietly picking another.
4. Call `get_corpus_profile(section="adversarial")` and `get_corpus_profile(section="structural-findings")`. If the profile or section is not built yet, note that and proceed without it. Candidates there are advisory - every absence is re-proven in this pass regardless.
5. Call `get_schema_docs()`. Record the returned `content_hash` - entries whose proof relies on schema structure (`data-absent`, and any subtype whose proof queries lean on value notes) record it as `schema_docs_hash`.
6. If the protocol for the chosen subtype requires a `search_corpus` sweep, probe the retrieval stack: `search_corpus("probe", condition="pooled", k=1)`. An error result means a server is down - report it and end the pass before any drafting work.

## Step 1 - Ground

Establish the absence (or false premise) in the data BEFORE composing the question - evidence first, exactly like the other skills, just with inverted polarity and starting from the parent:

- `zero-match`: from the parent's filter, shortlist the values that could be shifted, and find which shift lands on a true zero. Start from something real (a country that funds heavily, a scheme that exists) and locate the empty cell next to it - plausible-but-empty beats exotic-and-empty.
- `false-presupposition`: from the parent's gold answer, list the facts it establishes; the premise to falsify is the plausible-sounding negation or distortion of one of them.
- `data-absent`: read schema_docs for what is NOT there; shortlist facets users would plausibly ask about (evaluation scores, rejection reasons, gender breakdowns, follow-on funding), preferring ones that sit next to a real column name, and confirm no column or report section covers them.
- `unanswerable`: identify the structural defect (missing referent, category error, out-of-corpus scope) and enumerate the 2-3 interpretations a charitable reader would try.

Present a short grounding summary: the parent and what it establishes, the claimed absence/false premise, the observations that suggest it, and why a real user might plausibly ask about it. If grounding already shows the absence is shaky, pivot before drafting.

## Step 2 - Draft

Present:

- **Question text** - phrased as a real user of the costume route would ask it. It must not telegraph its own trap: no "are there any...", no hedging that invites a refusal. A good ADV question is indistinguishable, on its face, from an honest question.
- **The parent, and the edit** - the parent's text beside the new text, and one sentence naming exactly what changed. If more than one thing changed, say why the absence needed it.
- **Declared subtype and costume route** - with one sentence each on why the trap fits the subtype and why a well-behaved router sends the question down that route.
- **The absence claim, stated precisely** - exactly what must be true of the corpus for the refusal to be the correct answer. This claim is what Step 3 proves.

## Step 3 - Prove the absence

Every draft is verified by execution in the same pass. Any edit to the question that shifts the absence claim invalidates prior proof - re-run, never carry stale results. The protocol is per-subtype; run every listed item.

**`zero-match`:**
1. Execute the direct query - must return nothing (or a `COUNT` of 0). A nonzero result kills the draft: reject at birth or pick a different empty cell.
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

**Then, for every subtype: record the proof as `absence_evidence` and run the gate.**

Each executed claim becomes one entry: `{"sql": ..., "expect": "zero"|"rows", "key_result": "<what this proves, in words>"}`. `expect: "zero"` means the query must come back empty - either no rows, or a single `COUNT` of 0. `expect: "rows"` means it must come back full, which is what a refutation query does. Include the near-miss variants, not just the headline zero: the variants are the part a later reader cannot reconstruct. Anything that cannot be expressed as SQL (a `search_corpus` sweep, a read of report text, a "column does not exist" error) stays in `notes` as prose, with the typed entries carrying whatever the sweep can be reduced to.

Then call `precheck_record` on the assembled entry and read every check. It re-runs all of `absence_evidence`, resolves `twin_id`, and re-executes the parent's gold. Do not proceed to the reviewer until it returns `ok: true`; a FAIL there is the draft being wrong, not the gate being fussy.

## Step 4 - Reference answer

The reference is the correct refusal, written from the executed proofs - it is what the rubric judge (`src/judge/judge.py`) checks answers against, since ADV questions bypass RAGAS.

- It states the refusal precisely: nothing matches / the premise is false (and what is actually true) / that data is not in the corpus / the question cannot be answered as posed - matching the subtype.
- It invents nothing: no speculation about why the absence holds, no facts beyond the executed evidence. For false-presupposition, the correction states the true fact with entities and numbers **verbatim** from the refuting result.
- Length: one to two sentences. A refusal is short.

## Step 5 - Reviewer (mandatory, every pass)

Re-read question, parent, absence claim, every executed proof, the precheck result, reference answer. Every item gets an explicit PASS / FAIL / WARN plus one sentence. Skip nothing; items marked with a subtype apply only to that subtype, and must be answered N/A for others.

```
ABSENCE-PROVEN       Every clause of the absence claim has an executed query (or quoted text
                     read) from THIS session backing it, and precheck_record returned ok:true.
                     FAIL otherwise.
ABSENCE-TYPED        Every SQL-expressible proof appears in absence_evidence with the right
                     expect, near-miss variants included - not only in notes. FAIL otherwise.
TWIN-VALID           (zero-match, false-presupposition) twin_id names a real, non-ADV bank
                     question whose gold was re-executed this pass and still holds. FAIL
                     otherwise. N/A for unanswerable; PASS or N/A for data-absent.
MINIMAL-EDIT         (twinned) The question differs from its parent by as little as the absence
                     allows, and every difference is needed. WARN if it has drifted into a
                     different question that merely happens to be empty.
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
NEAR-DUPLICATE       Resemblance to the PARENT is expected and not a finding. Not a near-duplicate
                     of any OTHER bank question, and not of an existing ADV entry sharing this
                     parent. WARN, naming the colliding id.
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
twin_id                the parent's question_id  (required for zero-match and
                                                  false-presupposition; optional for
                                                  data-absent; omitted for unanswerable)
absence_evidence       [{sql, expect, key_result}, ...] - every SQL-expressible proof,
                       near-miss variants included. Required, non-empty.
gold_project_ids       [] for zero-match (mandatory); omitted otherwise
schema_docs_hash       from startup, when the proof relies on schema structure or value notes
                       (always for data-absent)
reference_answer       from Step 4
notes                  the absence claim verbatim, the parent and what was shifted, the proofs
                       that are not SQL (sweep candidates and their adjudication, quoted report
                       text, error results), the subtype-fit rationale, anything a verifier
                       needs that absence_evidence cannot hold
reviewer_override      only if "confirm anyway"
```

ADV is off-ladder: no level_evidence, no pooling_evidence, no term_style, no answer_columns. Do not invent them.

Then run `./.venv/Scripts/python.exe -m src.cli validate-bank` and show its output. A validation failure after append is a skill bug - fix the entry before ending the pass.

## Standing rules

- **Never append without explicit confirmation.** Never rewrite an existing bank entry without explicit instruction - the parent is read, never edited.
- **Absence is proven, never asserted.** No entry is appended whose absence claim was not executed in this pass, near-misses included, and typed into `absence_evidence` where SQL can hold it. Any question edit that shifts the claim invalidates prior proof - re-run, never carry stale results.
- **The twin is a live control, not a citation.** Its gold is re-executed this pass. A parent that has gone dead ends the draft; it is not swapped out quietly.
- **Minimal edit.** The further the question drifts from its parent, the less it controls for. Change one thing.
- **Plausible or pointless.** A question whose emptiness is visible on its face tests nothing; reject it.
- **The router is never probed.** Routing behavior is the experiment's outcome; the costume route is argued from the question's shape, not measured.
- **Subtypes mean what they say.** A mislabeled trap corrupts the refusal-overlay cells - re-subtype honestly or reject; never bend the label to fit the draft.
- **Reject at birth rather than patch.** A nonzero count, a true premise, a facet hiding in report text, an interpretation that works, a dead parent - these end the draft; they are not fixed by adjusting the label.
- **One question, one trap.** No compound asks, no stacked traps.
- **The reviewer runs every time**, every item explicit, after `precheck_record` returns ok and before any confirmation prompt.
