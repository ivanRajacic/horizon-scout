---
name: draft-vector-question
description: Draft one vector-route (L1-L3) benchmark question for the Horizon Scout M5 bank. Evidence first - seed projects are read before the question exists; the question is composed from their observed text; pooled retrieval across all four conditions completes and verifies the gold set; a mandatory reviewer checklist gates a confirmation-only append.
argument-hint: <level> [subtype] [term_style]
---

# /draft-vector-question

Draft one vector-route benchmark question for the Horizon Scout M5 bank.

**Arguments:** $ARGUMENTS
Format: `<level> [subtype] [term_style]` - e.g. `L1 identify paraphrase` or `L2`. Level values: `L1`, `L2`, `L3`. Subtype values: `identify`, `detail` (L1); `comparison`, `synthesis` (L2); `survey` (L3). term_style values: `exact-term`, `paraphrase`. If subtype or term_style is omitted, propose one based on what the bank currently lacks and wait for the user to pick.

This skill authors `route=vector` questions at levels L1-L3 only. SQL questions have `/draft-sql-question`; ADV, hybrid, and ambiguous questions have their own skills - if the user asks for one of those, point them there instead of stretching this one. One question per pass; never batch.

## Orchestrated mode (question-drafter subagents only)

When this skill is followed by a `question-drafter` subagent under `/draft-batch` (the prompt says so and carries a pre-assigned `question_id` plus a corpus-profile candidate block):

- **Read `src/eval/bank_brief.md` first.** It is the shared standard the drafter, the critic, and the judge all work from - what the bank is for, what "good" means, the route/level/subtype reference, and the role boundaries. The most useful thing in it for you: a defective question does not produce a wrong answer, it produces a wrong finding in a study.
- **The precheck gate.** Before you may emit a package, call `precheck_record(<your finished RECORD>)` and get `ok: true`. On this route it confirms every `gold_project_id` exists and carries stored text. A FAIL is a fact; fix the draft and call again. Include the passing result in your package as the `PRECHECK` section.
- **You author and verify; you do not grade yourself.** An independent `question-reviewer` attacks the draft afterwards and an independent `question-judge` rules on what it finds. Emit no verdict, argue no case, and record any doubt you still hold in HISTORY rather than suppressing it.
- The orchestrator runs `python -m src.cli validate-record` on your RECORD the moment you return it, so the JSON must be schema-clean (`pooling_evidence.accepted` must equal `gold_project_ids` exactly); a validator error comes straight back to you.
- The candidate block is the subject and the batch order fixes level/subtype/term_style - skip every propose-and-wait step, and skip the startup profile calls (`get_corpus_profile(section="vector")` and `frontier`): the candidate block IS your profile slice, and the frontier only matters when choosing a subject. Still call `get_bank_questions("vector")` and run the retrieval-server probe.
- **Two-tier grounding - the candidate is part proven, part advisory.** Its `evidence` (executed SQL + counts) and `sample_ids` are proven-by-execution and merge-pass spot-checked: trust and confirm them - re-run the candidate's SQL once as a drift check and `get_project_text` the `sample_ids` directly, rather than hunting seeds with a fresh `run_sql`. Everything else it asserts - route/level/subtype, term_style, and above all gold/cluster membership - is ADVISORY and re-verified in full: euroSciVoc leaf tags are noisy, so confirm the theme by READING text, the gold set by pooled verification + the completeness sweep, and recompute the level from |gold|. Reject-at-birth and born-verified-this-pass are unchanged - the confirming re-execution happens in-pass.
- Use the pre-assigned `question_id`, never "next free".
- There is no user in the loop: skip the confirmation prompt, never append, never write any file, and skip the `validate-bank` shell step (promotion validates). Instead return the complete entry - every field from the append table - plus evidence and history, in the output contract of `.claude/agents/question-drafter.md`.
- Run Step 5 as the **orchestrated-mode checklist** (see Step 5): every gate and diagnostic except the pure-judgment polish items the independent `question-reviewer` owns (nothing on this route's list is covered by `precheck_record`).
- Everything else applies unchanged, including "reject at birth": a dead candidate is reported as `DRAFT-FAILED`, never worked around by wandering to a new topic. Level disagreements that would normally go to the user (|gold| contradicting the requested level) go into the returned package instead - as a `DRAFT-FAILED` if the requested cell cannot be met honestly.

Interactive invocations are unaffected: the per-question confirm gate stands.

## The authoring direction (load-bearing)

**Evidence first.** The seed project(s) are selected and their texts READ before any question exists. The question is composed from what the texts actually say - never invented and then checked against the corpus. The pooled search afterwards does NOT find the answer (the seed evidence is the answer); it solves a different problem: in a 35,389-project corpus, which OTHER projects also satisfy the question? Those must join the gold set or the label is incomplete and the level (defined by |gold|) is wrong. Retrieval never decides truth - a candidate enters gold only because reading its text shows it satisfies the question, and seeds stay in gold regardless of where they rank.

## Tooling

All data access goes through the `horizon-draft` MCP server:

- `search_corpus(query, condition="pooled", k=10)` - project-level rankings over the chunk corpus. `pooled` runs all four conditions (lexical | dense | hybrid | hybrid_rerank) and returns the union with a per-condition rank matrix, each project carrying the full text of its best chunk, plus `index_meta` (embedding model, n_vectors, `content_hash`). Requires the embed AND reranker llama-servers; a down server comes back as an `{"error": ...}` result, and in pooled mode any dead condition fails the whole call - by design, partial pooling would bias labels.
- `get_project_text(project_ids)` - full free text for up to 10 projects: acronym, title, objective, and published report sections (summary, workPerformed, finalResults). The gold-evidence channel: grounding, candidate adjudication, reference writing.
- `run_sql(query, row_cap=50)` - SELECT-only. Used here for seed selection (euroscivoc topic clusters, metadata slices) and the completeness sweep.
- `get_schema_docs()` - schema reference when composing seed-selection SQL.
- `get_bank_questions("vector")` - existing entries: id, text, level, subtype only.
- `get_corpus_profile(section=None)` - the exploration agent's corpus_profile.md (whole, or one section by key). Query-verified candidate topics (euroscivoc clusters pre-sized to levels, term_style flags) plus the `frontier` coverage table. An `{"error": ...}` result means the profile is not built yet - proceed without it.
- `precheck_record(record)` - re-executes the finished record's mechanical claims (here: every gold project exists and carries stored text; plus the SQL/filter checks when those fields are present) and returns PASS/FAIL/N-A per check. Free to call as often as you like; a FAIL is a result, not an error.

There are no write tools. The append at the end is a confirmation-gated file edit, done by this skill directly.

## Level, subtype, and term_style reference

**Levels are computed from |gold_project_ids| after pooled verification, never asserted.** L1: |gold| = 1. L2: |gold| in [2,4]. L3: |gold| >= 5.

- **L1 `identify`** - the question describes work or a topic; the answer names the project. The question must never contain the acronym or title (that telegraphs the answer).
- **L1 `detail`** - a fact from one project's free text (what it developed, how it approached something, what it found). The fact must NOT be answerable from a stored column - "how much funding did X get" is a SQL question in disguise.
- **L2 `comparison`** - contrast the approaches of 2-4 projects; the reference draws on each of them explicitly.
- **L2 `synthesis`** - one integrated answer combining evidence across 2-4 projects (a shared theme, complementary findings).
- **L3 `survey`** - a landscape question over a topic with 5+ satisfying projects; the reference characterizes the set with named examples, not an exhaustive dump. (L3 is capped at ~7 bank questions - the most expensive tier under pooled verification.)

**term_style** (required on every entry; feeds RQ2's crossover table):
- `exact-term` - the question reuses distinctive vocabulary observed in the gold texts.
- `paraphrase` - the question deliberately avoids the gold texts' key terms and describes the same content in other words.

The pooled rank matrix is the honesty check: gold projects found by `lexical` imply real term overlap (evidence for exact-term); gold found only by `dense` implies the wording diverges (evidence for paraphrase). It is a heuristic - the declared style is a judgment call, the matrix just has to not contradict it.

## Startup (every invocation)

1. Call `get_bank_questions("vector")`. Review existing questions to avoid near-duplicates and see subtype/term_style coverage. If subtype or term_style was not given: state the current counts, propose the least-covered combination, and wait for the user's pick.
2. Call `get_corpus_profile(section="vector")` and `get_corpus_profile(section="frontier")`. If the profile is not built yet, note that and proceed without it. When the user names no topic: propose seeds from a profile candidate on a **least-covered axis** (a topic branch or entity family no bank question touches yet; the frontier's `mapped`-but-not-`mined` buckets are the first place to look), not yet used by any bank question - least-covered axis beats least-covered subtype when they conflict. Candidates are advisory seeds: their route/level/subtype/term_style and gold membership are re-verified in full (evidence-first reading + pooled verification), while their executed `evidence` and `sample_ids` are only re-confirmed cheaply - see Orchestrated mode for the two-tier rule. (Orchestrated mode skips both these profile calls - the candidate block already carries the section.)
3. Probe the retrieval stack: `search_corpus("probe", condition="pooled", k=1)`. An error result means a server is down - report it and end the pass before any drafting work. Record `index_meta.content_hash` from the probe; every appended entry carries it as `pooling_evidence.index_fingerprint`.

## Step 1 - Ground (evidence first)

Select seed project(s) sized to the requested level and READ them before drafting anything:

- Find candidate seeds via `run_sql` - euroscivoc topic clusters for topic-shaped questions (`SELECT ... FROM euroscivoc WHERE ... GROUP BY ...`), or a metadata slice, or the user names a topic/project directly. **Orchestrated mode:** take the seeds from the candidate's `sample_ids` and re-execute the candidate's `evidence` SQL once to confirm the cluster size has not drifted - do not run a fresh seed hunt.
- L1: one seed. L2: 2-4 seeds that genuinely share a theme (comparison/synthesis needs real common ground). L3: a topic whose cluster plausibly has 5+ satisfying projects.
- `get_project_text` on the seeds (batch up to 10 ids per call). Read the objectives and report sections - the question will be built from THIS text.

Present a short grounding summary: which seeds, what their texts actually say, which observed phrases or facts the question will be built on. If the texts do not support the intended question shape (thin objectives, no shared theme, degenerate cluster), say so and pivot before drafting.

## Step 2 - Draft

Present:

- **Question text** - phrased as a real user would ask it, composed from the observed seed text. No schema echo, no corpus jargon the texts do not use.
- **Declared level, subtype, term_style** - with one sentence on why the seed evidence fits that shape.

For `identify`: confirm the question does not name the acronym/title. For `exact-term`: name the distinctive terms borrowed from the seed texts. For `paraphrase`: show the seed's phrasing next to the question's phrasing.

## Step 3 - Pooled verify

Every draft is verified by pooled retrieval in the same pass. ANY edit to the question text invalidates prior verification - re-run the search, never carry stale results.

1. `search_corpus(question_text, condition="pooled", k=10)`. Record the per-condition project counts and the rank matrix.
2. **Seed check:** every seed should appear in at least one condition's top-k. A seed absent everywhere is a red flag - the question probably does not ask what the seed's text says. Rewrite before proceeding (a question no condition can connect to its own evidence is a bad benchmark item).
3. **Adjudicate every pooled candidate.** First pass from the best-chunk text `search_corpus` already returned for each candidate: a candidate whose best chunk is *clearly* off-topic is OUT, no fetch needed. Only for candidates that plausibly satisfy the question or are genuinely borderline, do a full read - collect their ids and `get_project_text` them in as few batched calls as possible (<= 10 per call). Every candidate ends IN or OUT with a one-line justification grounded in its text (best chunk for clear OUTs, full text for IN/borderline). IN means its text genuinely satisfies the question as asked - not "related topic", satisfies. `gold_project_ids` = seeds + accepted candidates.
4. **Completeness sweep (mandatory at every level):** the pool only shows what retrieval surfaced - and at k=10 it surfaces less - so this non-embedding sweep, not pool depth, is the real completeness guarantee. Via `run_sql`: keyword/LIKE queries over `project.objective` on the question's key concepts (and their obvious synonyms), plus euroscivoc topic membership for the relevant codes. Read and adjudicate any hits not already seen (batch the reads, <= 10 per call). An unswept gold set is unverified - a missed satisfying project makes the level label wrong and unfairly fails any system that finds it; at L3 the large gold set is exactly where the pool misses most, so the sweep is not optional there.
5. **Compute the level** from final |gold|. Mismatch with the request: say so, and either rewrite (tighten or broaden the question) or relabel - the user chooses. Never append a question whose gold count contradicts its level.

## Step 4 - Reference answer

Written from the gold projects' texts only - never from rejected candidates, never from retrieval snippets of projects outside gold.

- Prose meaningfully paraphrased; project acronyms, named entities, and any numbers stay **verbatim**.
- Length by subtype: 1-2 sentences for `identify`/`detail`; up to four for `comparison`/`synthesis`; `survey` states the pattern plus named example projects, not all of them.
- `comparison` references must draw on each gold project explicitly; `identify` references name the project and say what it does.

## Step 5 - Reviewer (mandatory, every pass)

Re-read question, gold set, adjudications, sweep results, reference answer. Every item gets an explicit PASS / FAIL / WARN plus one sentence.

**Interactive mode:** run every item below, skip nothing.

**Orchestrated mode:** the independent `question-reviewer` owns `NEAR-DUPLICATE`, `GENERIC-FACT`, and the general-shape check of `NO-TELEGRAPH`, reporting them as LOW findings for the judge to note; `NATURAL-PHRASING` is dropped entirely (pure phrasing taste). Skip those - but the identify acronym/title leak stays a FAIL gate you run yourself - and run every other item in full. Nothing on this route's list is covered by `precheck_record` (it checks that gold projects exist and have text, which is upstream of every item here), so the list is otherwise unchanged. Report each item as a fact with PASS/WARN/N-A - no verdict.

```
POOLED-VERIFIED      All four conditions ran this session at the recorded k; every pooled
                     candidate adjudicated with a reason. FAIL otherwise.
LEVEL-EVIDENCE       |gold_project_ids| satisfies the claimed level (L1=1, L2=2-4, L3>=5).
                     FAIL on mismatch.
SEED-RETRIEVED       Every seed appears in at least one condition's top-k. WARN otherwise,
                     stating why the question was kept anyway.
POOL-COMPLETENESS    Non-embedding sweep (objective keywords + euroscivoc) run and its hits
                     adjudicated. FAIL if skipped at any level.
TERM-STYLE-HONEST    Declared term_style is consistent with the rank-matrix heuristic
                     (lexical-found gold vs dense-only gold). WARN otherwise.
TEXT-NOT-SQL         The answer lives in free text, not in a stored column. FAIL otherwise.
NATURAL-PHRASING     Reads as a user's question; no schema echo, no unnatural corpus jargon.
                     WARN otherwise.
ONE-QUESTION         A single ask; no "and" joining two questions. FAIL if two-part.
NO-TELEGRAPH         Question betrays nothing about the answer from having seen the evidence;
                     identify questions never contain the acronym/title. FAIL for identify
                     leaks, WARN otherwise.
REFERENCE-FIDELITY   Reference derived only from gold texts; prose paraphrased; acronyms,
                     entities, numbers verbatim. FAIL if it contains claims not in the gold
                     texts or paraphrased values.
GENERIC-FACT         Answer requires this corpus - not answerable from general knowledge.
                     WARN only.
NEAR-DUPLICATE       Not a near-duplicate of an existing bank question. WARN, naming the
                     colliding id.
```

**Verdict (interactive mode only):** APPROVE / REVISE / REJECT

Then wait for the user. "confirm" appends; "confirm anyway" overrides a non-APPROVE verdict (recorded as `reviewer_override: true`); anything else is treated as revision instructions. In orchestrated mode there is no verdict and no `reviewer_override`: you report the checklist as facts, pass `precheck_record`, and return the package - a critic attacks it and a judge decides.

## On confirmation - append

Append one JSONL line to `eval/bank.jsonl` with every field:

```
question_id            next free vec-NN
text                   the question
expected_route         "vector"
level                  L1 | L2 | L3          (computed from |gold|)
subtype                as drafted
specification          "well-specified"      (this skill never authors underspecified)
term_style             exact-term | paraphrase
gold_project_ids       seeds + accepted candidates
pooling_evidence       {conditions_run, k, pooled_candidate_count, accepted,
                        rejected_count, index_fingerprint}
                       accepted = gold_project_ids exactly; index_fingerprint =
                       index_meta.content_hash from this session's searches
reference_answer       from Step 4
notes                  seed ids and why chosen, per-candidate adjudications (id: IN/OUT +
                       reason), sweep queries and outcome, term_style rationale, anything
                       a verifier needs
reviewer_override      only if "confirm anyway"
```

Then run `./.venv/Scripts/python.exe -m src.cli validate-bank` and show its output. A validation failure after append is a skill bug - fix the entry before ending the pass.

## Standing rules

- **Never append without explicit confirmation.** Never rewrite an existing bank entry without explicit instruction.
- **Evidence first.** Seeds are read before the question exists; the question is composed from observed text, never checked against it after the fact.
- **The label is born verified.** No entry is appended whose gold set was not pooled-verified in this pass. Any question edit invalidates prior verification - re-run, never carry stale results.
- **Retrieval never decides truth.** Candidates enter gold because their text satisfies the question; seeds stay in gold regardless of rank; rank only diagnoses phrasing.
- **Levels are computed, never asserted.** |gold| decides; disagreement with the request goes to the user, not to silent relabeling.
- **Reject at birth rather than patch.** Unretrievable seeds, themeless L2 clusters, L3 topics that pool thin - these end the draft; they are not fixed by adjusting the label.
- **One question, one fact-shape.** No compound asks.
- **The reviewer runs every time**, every item explicit, before any confirmation prompt.
