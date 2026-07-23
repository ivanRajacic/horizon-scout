---
name: review-question
description: Adversarially review one Horizon Scout bank question - a bank entry by id or an in-flight draft - by independently attempting to break it with the horizon-draft MCP tools - blind re-solve, alternate-reading hunt, missed-gold sweep, discriminatory-power audit. Produces an evidence-cited findings report and a SOUND/FLAWED/BROKEN verdict. Purely advisory - never writes, never edits the bank.
argument-hint: <question_id | draft>
---

# /review-question

Adversarially review one question for the Horizon Scout M5 bank.

**Arguments:** $ARGUMENTS
Format: `<question_id>` (e.g. `sql-03`, `vec-01`, `hyb-02`) to review a bank entry, or `draft` (or no argument during a drafting pass) to review the draft currently on the table in this conversation.

This skill reviews exactly one existing question per pass - it never authors, revises, or appends questions. Authoring belongs to `/draft-sql-question`, `/draft-vector-question`, `/draft-hybrid-question`. This skill is a second opinion: it attacks the question and reports; the user decides what happens next. It has no write path of any kind - it must never edit `eval/bank.jsonl`, never modify a draft, never "fix" anything it finds.

## The reviewing direction

**Attack, do not re-audit.** The drafting skill already ran its compliance checklist; re-running it adds nothing. This skill earns its keep by doing what the author could not: an INDEPENDENT attempt to break the question. Independence is procedural - the reviewer derives its own answer from the question text before comparing against the gold, hunts for satisfying evidence the author did not look for, and tries readings the author did not intend. Where the drafting checklist asks "did the author follow the rules?", this skill asks "is the label actually true, and does the question actually discriminate?"

**Balanced, not maximal.** The goal is a bank of good discriminatory questions, not a bank of zero questions. A finding exists only if it names concrete executed evidence - a query result, a project text, a rank matrix. "I would have phrased it differently" is not a finding. The severity rules below draw the line: label-contradicting evidence is fatal; everything else is at most a suggested improvement.

## Tooling

All data access goes through the `horizon-draft` MCP server:

- `run_sql(query, row_cap=50)` - SELECT-only, read-only, rows capped (hard ceiling 200), ~10s timeout. SQL failures come back as a `{"error": ...}` result, not a tool error - broken queries are data to reason about.
- `get_schema_docs()` - schema_docs.md verbatim plus `{version, content_hash}`.
- `get_bank_questions(route)` - existing entries for a route: id, text, level, subtype only.
- `search_corpus(query, condition="pooled", k=20, scope_project_ids=None)` - project-level rankings per condition (`lexical`, `dense`, `hybrid`, `hybrid_rerank`, or `pooled` for all four), with per-condition ranks and best-chunk text. Requires the embedder and reranker llama-servers.
- `get_project_text(project_ids)` - full stored text (objective + report fields) for up to 10 projects. The adjudication channel.

Bank entries are read from `eval/bank.jsonl` directly with the Read tool - `get_bank_questions` returns only id/text/level/subtype, and a review needs the full record: gold, evidence blocks, notes, reference.

There are no write tools, and this skill uses no file-editing tools either. The report at the end is conversation output, nothing else.

## Startup (every invocation)

1. **Resolve the target.**
   - Bank mode (argument is an id): Read `eval/bank.jsonl` and locate the record. Id not found: report that and end the pass. Duplicate id lines: an immediate FATAL data-integrity finding (the validator rejects duplicate ids); review the last occurrence and say so.
   - Draft mode (`draft` or no argument): collect the draft's fields from the conversation - text, route, level, subtype, and whatever gold/evidence exists at this point in the drafting pass (gold_sql + executed result, or gold_project_ids + adjudications, or filter_sql + survivors). If a field the attack catalog needs is missing, ask for it - never infer or invent a gold label to review against.
2. **Probe the stack as the route requires.** All routes: `get_schema_docs()`; record `content_hash`. Vector, hybrid, and topical ADV: `search_corpus("probe", k=1)`; a down server ends the pass, as in the drafting skills. SQL-route reviews proceed without the retrieval servers.
3. **Staleness check (bank mode only).** Compare the entry's `schema_docs_hash` against the live hash, and `pooling_evidence.index_fingerprint` against the probe's `index_meta.content_hash`. A mismatch is not itself a defect - it means the recorded evidence predates the current snapshot, so every attack below runs against live data and the entry's recorded evidence is treated as a claim to re-verify, not a fact. Record the mismatch as a NOTE.
4. **State the plan of attack**: which catalog sections apply (by route and level), then execute them.

## The attack catalog

Run every item in the applicable sections. Each item ends in EXECUTED (with its evidence summarized) or N/A (with the reason). Up to 3 additional discretionary probes are allowed when an executed item smells wrong; unbounded fishing is not.

### A. All routes

```
GOLD-ALIVE          Re-derive the gold from live data this session: execute gold_sql, or
                    get_project_text every gold_project_id, or re-run filter_sql. Empty result,
                    SQL error, or a gold id with no text = the label is dead. FATAL.
REFERENCE-TRUTH     Check every entity, number, and code in reference_answer against the live
                    evidence just fetched. A reference claim the evidence does not support is a
                    label error, not a style issue. FATAL.
LEVEL-RECOMPUTE     Recompute the level from live evidence (level_evidence tests for SQL,
                    |gold| for vector, subtype gold-bounds for hybrid) and compare to the label.
ONE-READING         Steelman at most two alternate readings a reasonable user could take of the
                    question text. For each, EXECUTE the reading (a query, a search) - an
                    alternate reading only counts as a finding if it runs cleanly and yields a
                    different answer. Two defensible readings with different answers = ambiguity.
DISCRIMINATION      The discriminatory-power audit (section below). Is the question too easy
                    for its cell, or degenerate (no condition can get it)?
```

### B. SQL route (levels L1-L3)

```
BLIND-SOLVE         BEFORE re-reading gold_sql, write your own SQL from the question text and
                    schema_docs alone. Present it, execute it, THEN compare to the gold result
                    under the entry's sql_comparison. (The gold was visible at load - the
                    blinding is procedural: commit to an independent derivation first; its value
                    is the independent path, not true blindness.)
                    - Match: corroboration. At L1 a match is EXPECTED - not a finding.
                    - Mismatch: adjudicate which query is right against the data. Gold wrong =
                      FATAL. Both defensible = ambiguity, MAJOR. Blind solve wrong for a reason
                      the question fairly signals = the question is doing its job - record it
                      as evidence FOR the question.
NEAR-MISS-SWEEP     Execute the corpus's standard confusion pairs where they apply to this
                    question: ecMaxContribution vs totalCost vs organization ecContribution /
                    netEcContribution; project grain vs participation grain; coordinator vs
                    participant; H2020 scope vs all-programmes. Each near-miss that runs
                    cleanly, differs from gold, AND is a reading the question text does not
                    rule out = ambiguity.
NULL-AND-TIE        rank: re-run with LIMIT n+1; a tie at the cutoff breaks ordered comparison.
                    Aggregates/filters: check whether NULLs in the filtered or aggregated
                    column silently change the answer (COUNT(*) vs COUNT(col), NULL codes,
                    NULL dates). Joins: check for fan-out changing an aggregate.
TRAP-TRAPS          (trap) Execute the recorded wrong query: it must still run and still
                    differ. Then the real test: did your OWN blind solve fall into the trap,
                    or is the trap so telegraphed no one would? A trap the blind solve walks
                    around effortlessly is a weak trap (MAJOR, not fatal); a "trap" whose
                    wrong query now matches gold is a dead trap (FATAL for the subtype).
SIMPLER-QUERY       Try to answer the question with a strictly simpler query than gold (fewer
                    joins, no value note). A simpler query returning the same answer on live
                    data proves the level is inflated - execute it to prove it.
```

### C. Vector route (levels L1-L3)

```
GOLD-SATISFIES      Read every gold project's full text and adjudicate it fresh: does this
                    text satisfy the question AS ASKED - not "related topic", satisfies? Cite
                    the passage. A gold member whose text does not support the question =
                    FATAL.
MISSED-GOLD-HUNT    Hunt for satisfying projects OUTSIDE gold through channels the author may
                    not have used: (a) pooled search with 1-2 of YOUR OWN reformulations of
                    the question - attack the wording, do not reuse it verbatim; (b) run_sql
                    LIKE/keyword sweep over objectives using synonyms and near-terms the
                    question avoids; (c) euroscivoc membership for the relevant codes.
                    Adjudicate every new candidate by reading its text. One genuinely
                    satisfying project outside gold = the level label is wrong (level IS
                    |gold|) = FATAL.
PRESUPPOSITION      Does the question assert anything as given (a method, a problem, an
                    outcome) that the gold texts do not actually say? Quote the gold passage
                    that grounds each presupposed claim, or flag the claim.
COLUMN-LEAK         Try to answer the question from stored columns via run_sql (title,
                    acronym, totalCost, dates...). A column that answers it means this is a
                    SQL question in disguise. MAJOR.
TERM-STYLE-AUDIT    Re-run the question verbatim through pooled search; check the declared
                    term_style against the fresh rank matrix (lexical-found gold vs dense-only
                    gold). Contradiction = NOTE, unless the cell assignment flips.
```

### D. Hybrid route (levels L1-L3)

```
FILTER-RERUN        Execute filter_evidence.filter_sql live. The survivor set must match the
                    recorded survivor_ids and gold must still be a subset. Drift = staleness
                    escalated to a label finding.
BLIND-FILTER        Write your own filter SQL from the question text alone (same procedural-
                    blinding discipline as BLIND-SOLVE). A defensible alternate filter (start
                    date vs signature date for "after 2020", coordinator country vs any
                    participant country) whose survivor set changes the gold = ambiguity.
SURVIVOR-RECHECK    S <= 20: re-read every survivor and re-adjudicate IN/OUT independently,
                    then compare to the recorded gold; disagreements are resolved by the
                    texts, and a wrong IN or a wrong OUT = FATAL. S > 20: scoped pooled
                    search with your own reformulation plus a survivor-scoped keyword sweep;
                    adjudicate hits not in gold.
FILTER-MATTERS      Unscoped pooled search on the textual part: find projects satisfying the
                    text but failing the filter. None findable and none recorded = the filter
                    is decoration; this is a vector question mislabeled hybrid. MAJOR.
TEXT-MATTERS        Can a stored column answer the textual ask? Same COLUMN-LEAK check as
                    vector. MAJOR.
```

### E. ADV level (any route; subtypes zero-match / false-presupposition / data-absent / unanswerable)

```
ZERO-IS-ZERO        (zero-match) Attack the zero hard: pooled search with reformulations,
                    LIKE sweeps with synonyms, euroscivoc codes. ONE genuine match kills the
                    question - a system that "finds" something would be right, and the
                    refusal judge would wrongly fail it. FATAL.
FALSE-IS-FALSE      (false-presupposition) Execute the query/search that would VERIFY the
                    presupposition. It must come back empty or contradicting. Also check the
                    presupposition is temptingly plausible - a presupposition nobody would
                    assert discriminates nothing.
ABSENT-IS-ABSENT    (data-absent) Confirm the field/fact is absent corpus-wide, not just in
                    the rows the author sampled - schema check plus a targeted sweep for the
                    fact hiding in free text.
NO-ROUTE-ANSWERS    (unanswerable) Try to answer it via each route. A successful answer by
                    any route = FATAL.
```

## Discriminatory-power audit

A question earns its bank slot by SEPARATING conditions - the trap/value-grounded/L3/ADV cells carry the study's discrimination. Compute, do not vibe:

**Too-easy signals** (each cited with its evidence):

- Vector/hybrid: every gold member at rank <= 3 in ALL FOUR conditions on the fresh pooled run, AND the question shares distinctive verbatim strings with the gold texts. Both together = nothing separates lexical from rerank here. (For a declared `paraphrase` question the verbatim overlap is separately a term_style finding.)
- Vector: the answer (acronym/title) appears verbatim in a stored column reachable from words in the question.
- SQL L2/L3: SIMPLER-QUERY succeeded - the claimed difficulty (join, value note, multi-hop) is not actually needed for the right answer.
- Trap: the blind solve avoided the trap without noticing it existed.

**Degenerate signals:**

- No condition places ANY gold member in its top-k (scoped top-k for hybrid) even with reasonable reformulations - no condition can get this question, so it measures nothing.
- The reference cannot be reconstructed from the gold evidence.

**Calibration - what is NOT a finding:**

- An L1 question being easy. L1 cells are the clean-route baseline; the v4 tie prediction NEEDS them easy. A blind-solve match at L1, gold at rank 1 for an L1 identify - expected, say so, move on.
- "This could be harder." Difficulty inflation is the author's call; only report when the LABEL overstates it (SIMPLER-QUERY evidence).
- Discrimination findings are at most MAJOR, never FATAL - a too-easy question is still correctly labeled; retire-or-keep is the user's allocation decision.

## Severity rules

Every finding is one block: `severity | claim | evidence | suggested action`.

- **FATAL** - the label contradicts executed evidence; the entry cannot stand as recorded: gold SQL wrong or dead, a gold project whose text does not satisfy the question, a satisfying project outside gold (level wrong), gold outside survivors, ADV premise disproven (zero-match with a match, false presupposition that is true), reference claims the evidence does not support, duplicate id in the bank file.
- **MAJOR** - the question stands but a concretely demonstrated defect weakens its evidential value: two executed readings with different answers, dead or telegraphed trap, filter not load-bearing, level inflation proven by a simpler query, degenerate retrievability, SQL/vector-in-disguise.
- **NOTE** - an observation with evidence but no needed action: staleness mismatches, term_style tension, mild telegraphing, near-duplicate proximity. **Hard cap: 3 NOTEs per pass.** If more exist, keep the three most useful; the rest were probably taste.

**Suggested actions** (vocabulary, advisory only): `revise-text`, `fix-gold`, `relabel`, `retire` (bank) / `abandon` (draft), `no-action`.

**What is never a finding:**

- Anything without executed evidence attached. No query result or project text = no finding, full stop.
- Phrasing taste, tone, question length, "a user would more likely ask...".
- Re-litigating a WARN the drafting reviewer already recorded, unless new executed evidence changes its substance.
- Hypotheticals ("if the data changed...", "a future model might...").
- Objections to the bank's design itself (level definitions, subtype vocabulary, allocation) - out of scope; those live in horizon-scout.md.

## Report and verdict

End every pass with exactly this report, in the conversation - no files:

```
TARGET      sql-03 (bank) | DRAFT - route/level/subtype - one-line restatement
STALENESS   schema_docs: match|MISMATCH - index: match|MISMATCH|n/a
ATTACKS     every applicable catalog item: EXECUTED (one-line evidence) or N/A (reason)
FINDINGS    F1..Fn, severity-ordered, each: severity | claim | evidence (query text or
            project id + quoted passage) | suggested action    - or "none"
VERDICT     SOUND | FLAWED | BROKEN - one paragraph
```

- **SOUND** - no FATAL, no MAJOR. The label survived independent attack; NOTEs at most.
- **FLAWED** - no FATAL, at least one MAJOR. The label is true but the question underperforms its cell; revision would pay.
- **BROKEN** - at least one FATAL. The entry cannot stand as recorded.

This vocabulary is deliberately not APPROVE/REVISE/REJECT: those words gate an append, and this skill gates nothing. The verdict describes the question's state; the user decides the action - revise the draft, retire the entry, fix and re-verify through a drafting skill, or overrule the reviewer. In draft mode, hand the findings back to the drafting pass in progress and stop; in bank mode, stop after the report either way.

## Standing rules

- **Advisory only, forever.** No appends, no edits, no file writes - not to bank.jsonl, not to the draft, not even on FATAL findings. The fix travels through a drafting skill or the user's hands.
- **Every finding cites executed evidence.** A query run this session or a project text read this session. Recorded evidence in the entry is a claim, not proof.
- **Attack, do not re-audit.** The drafting checklist ran once; do not run it again.
- **Independent derivation first.** Blind solves and blind filters are committed before comparison with gold. Procedural blinding, honestly labeled as such.
- **Expected-easy is not a finding.** L1 cells are supposed to be easy; calibrate per the discrimination section.
- **Bounded attack budget.** The catalog plus at most 3 discretionary probes; at most 3 NOTEs. Do not manufacture objections to justify the pass.
- **One question per pass.** Never batch reviews.
- **Stale evidence re-verifies, it does not condemn.** Hash mismatches trigger live re-verification, not automatic findings.
