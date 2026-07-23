---
name: explore-corpus
description: Autonomous corpus exploration for the Horizon Scout M5 bank. Fans out bounded parallel `corpus-explorer` subagents (sub-batches of ~8 candidates over disjoint slices, capped concurrency) over the horizon-draft MCP tools to find query-verified candidate topics for bank questions - each with a recommended route/level/subtype - then merges them into the versioned src/retrieval/corpus_profile.md for the user's review. The width mechanism for the bank: candidates and the coverage-axes ledger keep drafting spread across the corpus instead of clustered on a few entities and funding columns. Supports scoped runs (per-section candidate targets, sections skipped) to bound token spend; skipped sections are filled in by later runs.
argument-hint: [scope, e.g. "pilot sql=10 vector=10 hybrid=10"]
---

# /explore-corpus

**Arguments:** $ARGUMENTS
No arguments = full run (all six sections at the full supply targets below). A scope argument caps the run instead: per-section candidate targets (e.g. `sql=10 vector=10 hybrid=10`); any section without a target is SKIPPED entirely - no subagent is spawned for it. Restate the parsed scope (sections, targets, sections skipped) as the first output line so a wrong parse dies immediately.

Build (or rebuild) `src/retrieval/corpus_profile.md`: the query-verified "what's in this database" doc that gives every bank category grounded question-topic candidates and gives the drafting skills a coverage ledger to stay wide. Runs autonomously - bounded parallel sub-batches, one merge pass, ONE user review of the finished artifact at the end. No per-section confirmation.

## Hard constraints

- **Read-only exploration.** All data access through the `horizon-draft` MCP tools (`run_sql`, `get_project_text`, `get_schema_docs`, `get_bank_questions`); every call is traced automatically. The ONLY files this skill writes are `src/retrieval/corpus_profile.md` and the `CORPUS_PROFILE_VERSION` bump in `src/config.py`. Never touch the bank, schema_docs, or anything frozen.
- **No unqueried claims.** Every number, distribution, trap pair, cluster size, survivor count, and absence in the profile shows the executed SQL that produced it and its key result. A claim without its query does not go in the file.
- **Candidates are advisory.** Recommended route/level/subtype are the exploration agent's judgment calls to speed drafting; the drafting skills recompute levels from evidence and remain the authority. Nothing here pre-commits a bank entry.
- **No retrieval-stack dependency.** Exploration uses SQL and project text only. `search_corpus` verification belongs to the drafting skills; this skill must run fine with the llama servers down.

## Output contract

`src/retrieval/corpus_profile.md`, H2 sections in this order (section keys are the kebab-cased headings, served by `get_corpus_profile(section=...)`):

`## Header`, `## Distributions`, `## SQL`, `## Vector`, `## Hybrid`, `## Adversarial`, `## Ambiguous`, `## Coverage ledger`

**Candidate format** (uniform across the SQL/Vector/Hybrid/Adversarial/Ambiguous sections):

```
- id: <section>-NN
  topic: <one line - what the question would be about>
  recommend: route=<sql|vector|hybrid|ambiguous> level=<L1|L2|L3|ADV> subtype=<drafting-skill vocabulary>
  evidence: <the executed SQL> -> <the key numbers/rows it returned>
  axes: <axis=value pairs, e.g. country=IT scheme=EIC topic=/natural sciences/... dates=2019-2021>
  why: <one sentence - what makes this a good seed>
```

**Supply targets, FULL run** (2-3x the allocation so drafting always has slack): SQL >= 45 candidates (allocation 22), Vector >= 50 (25), Hybrid >= 50 (24), Adversarial >= 25 (12), Ambiguous >= 20 (10). **Scoped run:** the argument's per-section targets replace these outright - they are caps as much as floors; exploration stops at the target, it does not keep exploring.

**Sub-batching:** a section's supply target is never one subagent's job - each targeted section is explored in sub-batches of `SUBBATCH_TARGET = 8` candidates (see Orchestration). The target sets how many sub-batches run (`ceil(target / 8)`, each on a disjoint slice), not how much any single subagent does. A target of 8 or less runs as a single sub-batch (explored inline, no subagent).

**Skipped sections still get their H2 heading**, in contract order, with a one-line stub: `Not yet explored (scoped run "<scope>", <date>).` Section keys stay stable for `get_corpus_profile`, and incompleteness is explicit rather than silent. Header and Coverage ledger are produced on EVERY run (the ledger built from whatever sections ran plus bank usage); Distributions only when targeted - it serves Study 0.5, not the pilot.

**Width inside every section, at any scale:** candidates must spread across axes - no more than a third of a section's candidates may share one axis value (e.g. funding-money questions, or one country), and no named entity (project, org, scheme instance) may appear in more than two candidates. The current bank's failure mode (7/10 funding questions, AQUA*/EUROfusion/ERC clustering) is exactly what this rule exists to prevent.

## Section specs

- **Header** - version label, generation date, corpus fingerprint (project count, vector count from `data/index/full` meta if readable), and the `content_hash` of the schema_docs the run was grounded against (`get_schema_docs()`).
- **Distributions** - per-country project counts and EU-funding totals; fundingScheme counts; per-year startDate histogram; status, activityType, and sme splits; ecMaxContribution percentiles (deciles + notable outliers); report_text coverage rate. This section doubles as the Study 0.5 value-description source, so enum meanings and code semantics (from schema_docs value notes, verified against the data) belong here.
- **SQL** - distinct-value inventories for every filterable column (counts per value, verified against the data); verified near-miss trap pairs with BOTH divergent numbers computed and shown (ecContribution vs ecMaxContribution-across-join, totalCost vs EU funding, partner-role NULLs, coordinator-vs-participant grain, and any new ones found). Candidates use /draft-sql-question's subtype vocabulary.
- **Vector** - euroscivoc cluster inventory: subtree or leaf, satisfying-project count, 2-3 sample acronyms + ids, report_text coverage of the cluster; bucketed by the bank's level definition (1 project -> L1 seed, 2-4 -> L2, 5+ -> L3). Sample one or two cluster members' text via `get_project_text` to confirm the texts actually carry the theme. Flag clusters whose taxonomy label does NOT appear lexically in members' text (paraphrase term_style material) vs clusters whose vocabulary is echoed verbatim (exact-term material). Candidates use /draft-vector-question's subtype vocabulary.
- **Hybrid** - topic x filter survivor-count matrix: euroscivoc subtree crossed with country / fundingScheme / date-range / funding-percentile filters, keeping combos whose TRUE survivor count lands in the drafting windows (2-10 filter-read, ~5-20 filter-synthesize, tight-but-rich for compare/survey; hard ceiling 200). Show the count query per kept combo. Candidates use /draft-hybrid-question's subtype vocabulary.
- **Adversarial** - query-verified genuine absences: zero-match filter values (plausible countries/schemes/topics/years with 0 rows - show the COUNT(*)=0 query), false-presupposition seeds (a premise that sounds true, with the refuting query and its result), and data-absent fields (things users would ask that no column or text carries). level=ADV; subtype from /draft-adversarial-question's vocabulary (zero-match | false-presupposition | data-absent | unanswerable). Candidates add two lines to the uniform format: `claim:` (the precise absence or false premise - exactly what must hold for a refusal to be correct) and `near-miss:` (the synonym/adjacent-column/loosened-range variants checked, each with its count - a zero-match candidate without checked near-misses is not query-verified).
- **Ambiguous** - facts present in BOTH a structured column and free text (funding amounts restated in report summaries, topics as euroscivoc codes AND objective prose, dates narrated in workPerformed), each verified on a concrete example project - the material where routing is genuinely hard. The ambiguous route has no subtype: candidates use `recommend: route=ambiguous level=<L1|L2|L3> routes=<two-or-three of sql|vector|hybrid, joined with +>` instead of a subtype, and add a `readings:` line - one clause per recommended route stating how that route would parse the question.
- **Coverage ledger** - derived in the merge pass, not explored separately. Enumerates the axes (country, fundingScheme/programme, dates, funding-money, status, sme/activityType, euroscivoc top-level branches, report-text themes, entity families) and for each: which candidate ids touch it and which existing bank questions (from `get_bank_questions`, all routes) already use it. Ends with a short "least-covered axes" list - the drafting skills' first stop when proposing what to ask next.

## Orchestration

**Model and effort (default, user-overridable per invocation):** this skill is meant to run in an Opus-class session at HIGH effort - the orchestrator owns the partitioning, merge, dedup, ledger, spot-check, and the final artifact. A skill cannot change the session's own model or effort, so CHECK instead: if the session model is not Opus-class or better, stop before spawning anything and tell the user to relaunch under `/model` (or explicitly waive the check). Subagents are the `corpus-explorer` agent type (`.claude/agents/corpus-explorer.md`): read-only, Opus at MEDIUM effort, with its query budget and stop-don't-loop rule baked into the agent def so the spawn prompt stays lean. Spawn it via the Agent tool with `subagent_type: corpus-explorer`. Never use Haiku for any part of this run - Haiku is the system under test, and letting it pick the topics it will be tested on violates the one-hat rule. Avoid Sonnet too (it wears the judge hat).

**Concurrency:** launch at most **4** `corpus-explorer` subagents in flight at a time - the `horizon-draft` MCP server is one stdio process over a single read-only DuckDB connection, so more just queues (the sibling `draft-batch`/`review-bank` skills cap at 3; explore earns one more because it never touches the embed/rerank llama-servers, only cheap row-capped `run_sql`). Run in waves: dispatch up to 4, wait for the wave, dispatch the next; log a one-line progress note per wave so a fan-out run is never a silent black box.

1. **Startup:** parse and restate the scope (or "full run"). `get_schema_docs()` (record the hash), `get_bank_questions(route)` for every route (current usage feeds the ledger). If a profile already exists, `get_corpus_profile()` it - this run EXPANDS it (see versioning below).
2. **Fan out bounded `corpus-explorer` sub-batches (max 4 in flight, in waves).** For each TARGETED section, size the work into sub-batches of `SUBBATCH_TARGET = 8` candidates (`ceil(target / 8)` subagents), each assigned a **disjoint slice** of the section's exploration space so width emerges by construction and merge-dedup stays light:
   - **Vector** - disjoint euroscivoc top-level branch groups (each subagent owns a set of branches, spreads across its own).
   - **SQL** - disjoint trap/column families (funding-money traps; role/grain; distinct-value inventories; date/status/scheme).
   - **Hybrid** - disjoint filter dimensions (country x topic; scheme x topic; date-range x topic; percentile x topic).
   - **Adversarial** - disjoint absence types (zero-match filters; false-presuppositions; data-absent fields).
   - **Ambiguous** - disjoint field families (funding restated in text; topics as codes vs prose; dates narrated in workPerformed).
   - **Distributions** - EXCEPTION: statistical, no candidates and no width rule; run as a single subagent (or two by metric group), not sub-batched.

   Each subagent prompt carries: its section spec verbatim, the candidate format, the no-unqueried-claims rule, **its slice definition**, a **within-slice width sub-rule** (spread across the slice's values; no named entity twice), and its `SUBBATCH_TARGET`. The read-only toolset and the token/anti-loop bounds live in the `corpus-explorer` agent def, not the prompt. Each returns its candidates as raw markdown (a data payload, not a message).

   **Inline exception:** if a section's whole target is `<= SUBBATCH_TARGET` (a single sub-batch), explore it INLINE in this session instead of spawning a lone subagent - one subagent adds opacity with zero parallelism, which is exactly the failure mode this design removes. Apply the same bounds inline (get_project_text <= 3 ids/call, query budget, stop-don't-loop).
3. **Merge pass (this session):**
   - Assemble ALL EIGHT sections in contract order - produced sections in full (concatenating each section's sub-batches), untargeted ones as stubs (or carried over verbatim from the existing profile on an expansion run). Renumber candidate ids across all sub-batches of each newly produced section (`section-01..NN`). Drop near-duplicate candidates both **across a section's sub-batches** (boundary overlaps between adjacent slices) and across sections (same topic + same axes).
   - Derive the Coverage ledger from the produced candidates' `axes` lines, any pre-existing candidates, and the bank usage collected at startup.
   - **Spot-check:** re-execute at least two embedded queries per produced section via `run_sql`; any mismatch between the profile's claim and the re-run fails that candidate - send its slice back to a fix-up sub-batch, never hand-edit the numbers.
   - Check the width rule **globally across each merged section** (the per-slice sub-rule was only a local heuristic; the real bar is over the whole section) and the supply targets; a section that fails width or falls short gets **one** top-up wave - a single additional sub-batch aimed at the thin axis (respecting the max-4-in-flight cap). A subagent's `SHORT:` note counts toward deciding a top-up.
4. **Write** `src/retrieval/corpus_profile.md` and bump `CORPUS_PROFILE_VERSION` in `src/config.py` to the next `cpN` (first run = cp1 whatever its scope; every later expansion run bumps again). **Expansion is append-only:** a later run fills stubs and appends new candidates; it never renumbers, edits, or drops candidates from an earlier version - drafting sessions may already have consumed them. Run the test suite (`./.venv/Scripts/python.exe -m pytest tests/test_mcp_server.py -q`).
5. **Review gate:** present a summary to the user - the scope this run used, which sections are still stubs, per-section candidate counts vs targets, the least-covered-axes list, anything surprising found, any section that needed a top-up - and wait. The user reviews the artifact once; revision instructions loop back through subagents or targeted queries (spot-check discipline applies to any edit). Revisions within this review session do not re-bump the version.

## Standing rules

- **Autonomous until the review gate.** No mid-run confirmations; surface problems in the final summary.
- **Bounded everything - never loop.** Sub-batches of ~8 candidates; max 4 subagents in flight; each `corpus-explorer` has a query budget (~20 `run_sql` + ~6 `get_project_text` per slice) and reads `<= 3` project ids per `get_project_text` call (10 overflows to a file and thrashes); one top-up wave per section. A subagent that hits a bound returns partial with a `SHORT:` note - it never loops, never re-reads an overflowed result repeatedly, and never wanders outside its slice. Single-sub-batch targets run inline, not as a lone opaque subagent.
- **Every claim carries its query.** If a subagent returns a number without SQL, the number does not enter the profile.
- **Wide by rule, not by hope.** The per-section width rule and the ledger are checked in the merge pass, every run.
- **The profile proposes, the drafting skills dispose.** No bank writes, ever, from this skill.
