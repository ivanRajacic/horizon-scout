---
name: review-question
description: Adversarially review one Horizon Scout bank question - a bank entry by id or an in-flight draft - by independently attempting to break it with the horizon-draft MCP tools. Reports only FATAL problems (invalid / mislabeled / non-discriminating) plus cheap MINOR flags, each with executed evidence; classifies every FATAL as RECOVERABLE or DEAD. Verdict SOUND | FATAL-RECOVERABLE | FATAL-DEAD. Purely advisory - never writes, never edits the bank; the orchestrator (or user) judges what happens next.
argument-hint: <question_id | draft>
---

# /review-question

Adversarially review one question for the Horizon Scout M5 bank.

**Arguments:** $ARGUMENTS
Format: `<question_id>` (e.g. `sql-03`, `vec-01`, `hyb-02`) to review a bank entry, or `draft` (or no argument during a drafting pass) to review the draft currently on the table in this conversation.

This skill reviews exactly one question per pass - it never authors, revises, or appends. Authoring belongs to the `draft-*-question` skills. This skill is the adversary: it attacks the question and reports; someone else (the orchestrator judge, or the user) decides what happens next. It has no write path of any kind - it must never edit `eval/bank.jsonl`, never modify a draft, never "fix" anything it finds.

## The reviewing direction

**Attack, do not re-audit.** The drafting skill already ran its compliance checklist; re-running it adds nothing. This skill earns its keep by doing what the author could not: an INDEPENDENT attempt to break the question. Independence is procedural - derive your own answer from the question text before comparing against the gold, hunt for satisfying evidence the author did not look for, and try readings the author did not intend.

**Report the real problems, let the judge weigh them.** You do not need to self-censor or calibrate "is this worth reporting". Your job is to surface what you found and back it with executed evidence; a downstream judge decides whether a FATAL warrants a redraft and whether a MINOR matters at all. The one discipline that keeps you honest: **every finding cites evidence you executed this session** - a query result, a project text, a rank matrix. A hunch with no run behind it is not a finding.

## Tooling

All data access goes through the `horizon-draft` MCP server:

- `run_sql(query, row_cap=50)` - SELECT-only, read-only, rows capped (hard ceiling 200), ~10s timeout. SQL failures come back as a `{"error": ...}` result, not a tool error.
- `get_schema_docs()` - schema_docs.md verbatim plus `{version, content_hash}`.
- `get_bank_questions(route)` - existing entries for a route: id, text, level, subtype only.
- `search_corpus(query, condition="pooled", k=10, scope_project_ids=None)` - project-level rankings per condition (`lexical`, `dense`, `hybrid`, `hybrid_rerank`, or `pooled` for all four), with per-condition ranks and best-chunk text. Requires the embedder and reranker llama-servers.
- `get_project_text(project_ids)` - full stored text (objective + report fields) for up to 10 projects. The adjudication channel.

Bank entries are read from `eval/bank.jsonl` directly with the Read tool - a review needs the full record (gold, evidence blocks, notes, reference), which `get_bank_questions` does not return.

There are no write tools, and this skill uses no file-editing tools. The report at the end is conversation output, nothing else.

## Startup (every invocation)

1. **Resolve the target.**
   - Bank mode (argument is an id): Read `eval/bank.jsonl` and locate the record. Id not found: report and end. Duplicate id lines: an immediate FATAL (the validator rejects duplicates); review the last occurrence and say so.
   - Draft mode (`draft` or no argument): collect the draft's fields from the conversation - text, route, level, subtype, and whatever gold/evidence exists (gold_sql + result, or gold_project_ids + adjudications, or filter_sql + survivors). If a field the attack needs is missing, ask for it - never infer or invent a gold label to review against.
2. **Probe the stack as the route requires.** All routes: `get_schema_docs()`; record `content_hash`. Vector, hybrid, topical ADV: `search_corpus("probe", k=1)`; a down server ends the pass. SQL-route reviews proceed without the retrieval servers.
3. **Staleness check (bank mode only).** Compare the entry's `schema_docs_hash` against the live hash, and `pooling_evidence.index_fingerprint` against the probe's `index_meta.content_hash`. A mismatch is not itself a defect - it means the recorded evidence predates the snapshot, so every attack runs against live data and the entry's recorded evidence is a claim to re-verify. Record the mismatch as a MINOR note.
4. **State the plan of attack**: the core items for this route/level, then execute them.

## The attack catalog

Run every CORE item for the route (each ends EXECUTED with its evidence, or N/A with the reason). Beyond the core, spend up to 3 discretionary probes on anything that smells wrong - no unbounded fishing.

### A. All routes - CORE

```
GOLD-ALIVE      Re-derive the gold from live data: execute gold_sql, or get_project_text every
                gold_project_id, or re-run filter_sql. Empty result, SQL error, or a gold id with
                no text = the label is dead. FATAL.
REFERENCE-TRUTH Check every entity, number, and code in reference_answer against the evidence just
                fetched. A reference claim the evidence does not support = FATAL.
LEVEL-CHECK     Recompute the level from live evidence (level_evidence tests for SQL, |gold| for
                vector, subtype gold-bounds for hybrid). A mismatch that moves the cell = FATAL.
ONE-READING     Steelman at most two alternate readings a reasonable user could take. EXECUTE each
                (a query, a search). Two defensible readings that run cleanly and yield different
                answers = the question cannot be scored = FATAL.
```

### B. SQL route - CORE

```
BLIND-SOLVE     BEFORE re-reading gold_sql, write your own SQL from the question text + schema_docs
                alone. Execute it, THEN compare to gold under the entry's sql_comparison. Match at
                L1 is EXPECTED - not a finding. Mismatch: adjudicate against the data - gold wrong =
                FATAL; both defensible = FATAL (ambiguity); blind-solve wrong for a reason the
                question fairly signals = the question works, record it as evidence FOR.
NEAR-MISS       Execute the confusion pairs that apply: ecMaxContribution vs totalCost vs
                organization ecContribution/netEcContribution; project vs participation grain;
                coordinator vs participant; H2020 vs all-programmes. A near-miss that runs cleanly,
                differs from gold, AND is a reading the text does not rule out = FATAL (ambiguity).
TRAP-CHECK      (trap) Execute the recorded wrong query: it must still run and still differ. A
                "trap" whose wrong query now matches gold is a dead trap = FATAL for the subtype.
```

### C. Vector route - CORE

```
GOLD-SATISFIES  Read every gold project's full text and adjudicate fresh: does this text satisfy
                the question AS ASKED - not "related topic", satisfies? Cite the passage. A gold
                member whose text does not support the question = FATAL.
MISSED-GOLD     Hunt satisfying projects OUTSIDE gold through channels the author may not have used:
                (a) pooled search with 1-2 of YOUR OWN reformulations - attack the wording, do not
                reuse it; (b) run_sql LIKE/keyword sweep over objectives with synonyms; (c)
                euroscivoc membership for the relevant codes. Adjudicate every new candidate by
                reading its text. One genuinely satisfying project outside gold = the level is
                wrong (level IS |gold|) = FATAL.
COLUMN-LEAK     Try to answer the question from stored columns (title, acronym, totalCost, dates)
                via run_sql. A column that fully answers it = this is a SQL question mislabeled
                vector = FATAL (route mislabel). Partial leak = MINOR.
```

### D. Hybrid route - CORE

```
FILTER-RERUN    Execute filter_evidence.filter_sql live. Survivors must match the recorded
                survivor_ids and gold must still be a subset. Gold outside the live survivor set =
                FATAL.
SURVIVOR-CHECK  S<=20: re-read every survivor, re-adjudicate IN/OUT independently, compare to gold;
                a wrong IN or wrong OUT = FATAL. S>20: scoped pooled search with your own
                reformulation + a survivor-scoped keyword sweep; adjudicate hits not in gold.
FILTER-MATTERS  Unscoped pooled search on the textual part: find projects satisfying the text but
                failing the filter. None findable and none recorded = the filter is decoration, a
                vector question mislabeled hybrid = FATAL. Also check the text is not answerable
                from a stored column (COLUMN-LEAK); a full column answer = FATAL.
```

### E. ADV level - CORE (subtypes zero-match / false-presupposition / data-absent / unanswerable)

```
ZERO-IS-ZERO    (zero-match) Attack the zero hard: pooled search with reformulations, LIKE sweeps
                with synonyms, euroscivoc codes. ONE genuine match kills it - a system that finds
                something would be right and the refusal judge would wrongly fail it. FATAL.
FALSE-IS-FALSE  (false-presupposition) Execute the query/search that would VERIFY the
                presupposition. It must come back empty or contradicting; if it is actually true =
                FATAL. Also check the presupposition is temptingly plausible.
ABSENT-IS-ABSENT (data-absent) Confirm the field/fact is absent corpus-wide, not just in the rows
                the author sampled - schema check plus a targeted sweep for the fact in free text.
NO-ROUTE-ANSWERS (unanswerable) Try to answer it via each route. A successful answer by any route =
                FATAL.
```

## Severity - two buckets

Every finding is `FATAL | MINOR - claim - evidence`.

- **FATAL** - the question cannot stand in its cell as recorded: invalid (gold dead/wrong, reference unsupported, ADV premise disproven, two defensible readings), mislabeled (level wrong, route mislabeled - filter decoration, full column answer), or non-discriminating (degenerate: no condition retrieves ANY gold member even with reasonable reformulations). Only FATAL findings can justify a redraft.
- **MINOR** - the question stands; a note for the record, never grounds to redraft. This is where taste lives:
  - **NO-TELEGRAPH** - the question text leaks the answer's shape/count/content.
  - **GENERIC-FACT** - answerable from general knowledge without this corpus.
  - **NEAR-DUPLICATE** - close to an existing bank question (name the colliding id; one `get_bank_questions` call).
  - Staleness mismatches, term_style tension, a marginal-but-honest gold member, mild near-duplicate proximity.
  Read MINOR flags off evidence already in hand - do not spend new queries hunting for them. Pure phrasing taste ("I would word it differently") is NOT a finding - do not report it.

**Recoverability - classify every FATAL** (this is the adversary's call; the judge acts on it):

- **RECOVERABLE** - a bounded edit to the text, gold, SQL, or filter fixes it without changing what the question is about. State the concrete fix direction (the revised reading, the corrected filter, the narrowed gold rule).
- **DEAD** - it cannot be salvaged without abandoning the candidate or turning it into a different question (e.g. a filter no user could express, a topic with no discriminating gold, an ADV premise that is simply true). Say why no bounded fix exists.

If any FATAL is DEAD, the whole draft is dead (one unsalvageable defect sinks it).

**Calibration - the one thing that is NOT a finding:** an L1 question being easy. L1 cells are the clean-route baseline; the study needs them easy. A blind-solve match at L1, gold at rank 1 for an L1 identify - expected, say so, move on. (Difficulty inflation only matters when the LEVEL LABEL overstates it - that is caught by LEVEL-CHECK / a simpler query returning the same answer, which is a FATAL mislabel, not a taste call.)

## Report and verdict

End every pass with exactly this report, in the conversation - no files:

```
TARGET      sql-03 (bank) | DRAFT - route/level/subtype - one-line restatement
STALENESS   schema_docs: match|MISMATCH - index: match|MISMATCH|n/a
ATTACKS     every CORE item for the route: EXECUTED (one-line evidence) or N/A (reason)
FINDINGS    F1..Fn, FATAL first, each: severity | claim | evidence (query text or project id +
            quoted passage) | (FATAL only) RECOVERABLE: <fix direction>  or  DEAD: <why>
            - or "none"
VERDICT     SOUND | FATAL-RECOVERABLE | FATAL-DEAD - one paragraph
```

- **SOUND** - no FATAL. The label survived independent attack; MINOR flags at most.
- **FATAL-RECOVERABLE** - at least one FATAL, and every FATAL is RECOVERABLE (each with a fix direction). The draft can be salvaged with a bounded edit.
- **FATAL-DEAD** - at least one FATAL is DEAD. The question cannot be salvaged as this candidate.

This vocabulary gates nothing itself - the skill has no write path. It describes the question's state and hands the judge what it needs: which findings are fatal, and for each fatal, whether a fix exists and what it is.

## Standing rules

- **Advisory only, forever.** No appends, no edits, no file writes - not to bank.jsonl, not to the draft, not even on FATAL. The fix travels through a drafting skill or the user's hands.
- **Every finding cites executed evidence.** A query or project text read this session. Recorded evidence in the entry is a claim, not proof.
- **Attack, do not re-audit.** The drafting checklist ran once; do not re-run it.
- **Independent derivation first.** Blind solves and blind filters are committed before comparison with gold. Procedural blinding, honestly labeled.
- **FATAL is for the label, MINOR is for the record.** Only a FATAL can justify a redraft; MINOR is a note. Classify every FATAL RECOVERABLE or DEAD.
- **Expected-easy is not a finding.** L1 cells are supposed to be easy.
- **Bounded attack budget.** The core items plus at most 3 discretionary probes. Do not manufacture objections.
- **One question per pass.** Never batch reviews.
- **Stale evidence re-verifies, it does not condemn.** Hash mismatches trigger live re-verification, not automatic findings.
