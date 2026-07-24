---
name: explore-corpus
description: Cumulative corpus exploration for the Horizon Scout M5 bank. Maintains src/retrieval/corpus_profile.md as a growing MAP of the database - what each part is about and what questions it can support - plus query-verified candidate seeds for drafting. A frontier table over the 46 euroSciVoc buckets records what has been explored and what has not, so every run goes somewhere new instead of re-deriving the corpus. Fans out bounded parallel `corpus-explorer` subagents (max 4 in flight) over the horizon-draft MCP tools, then merges by APPENDING - never rewriting - and presents the result for one user review.
argument-hint: [scope, e.g. "map=6" or "vector=10 hybrid=10"]
---

# /explore-corpus

**Arguments:** $ARGUMENTS

Maintain `src/retrieval/corpus_profile.md`: the query-verified "what is in this database" map that tells the drafting skills where to go and what each region can support. Runs autonomously - bounded parallel sub-batches, one merge pass, ONE user review of the finished artifact at the end. No per-section confirmation.

**The point of this skill is that knowledge ACCUMULATES.** Every run adds to the map and advances the frontier; no run re-derives what an earlier run already established. That is what keeps the bank wide - drafting draws from parts of the corpus we have deliberately been to, instead of clustering on the handful of entities and funding columns that are easy to find from a cold start.

## Scope

No arguments = full run. Otherwise the argument caps the run:

- `map=<N>` - map N `unexplored` buckets (topical work: what is in there, what it can support).
- `<section>=<N>` - candidate targets for `sql` / `vector` / `hybrid` / `adversarial` / `ambiguous` / `distributions`, as before. Topical sections (vector, hybrid, ambiguous) draw from buckets and mark the buckets they touch as explored; structural sections (sql, adversarial) do not touch the frontier.

Anything not named is SKIPPED - no subagent is spawned for it. **Restate the parsed scope as the first output line** (buckets to be mapped, per-section targets, what is skipped) so a wrong parse dies immediately.

## Hard constraints

- **Read-only exploration.** All data access through the `horizon-draft` MCP tools; every call is traced automatically. The ONLY files this skill writes are `src/retrieval/corpus_profile.md` and the `CORPUS_PROFILE_VERSION` bump in `src/config.py`. Never touch the bank, schema_docs, or anything frozen.
- **No unqueried claims.** Every number, distribution, trap pair, cluster size, survivor count and absence shows the executed SQL that produced it and its key result. A claim without its query does not go in the file.
- **Append, never rewrite.** A later run fills stubs, adds map entries, appends candidates and updates frontier rows. It never renumbers, edits or drops what an earlier version recorded - drafting sessions may already have consumed it.
- **Candidates are advisory.** Recommended route/level/subtype are the exploration agent's judgment calls to speed drafting; the drafting skills recompute levels from evidence and remain the authority. Nothing here pre-commits a bank entry.
- **No retrieval-stack dependency.** Exploration uses SQL and project text only. `search_corpus` verification belongs to the drafting skills; this skill must run fine with the llama servers down.

## The frontier - how coverage is counted

`euroscivoc` already partitions the corpus, so we do not invent a taxonomy. **46 buckets:** 40 named second-level categories (`split_part(euroSciVocPath,'/',2)`, from biological sciences at 8,057 projects down to veterinary sciences at 15), 5 top-level-only paths (one per branch that has depth-1 rows), and 1 `(unclassified)` bucket for the 3,153 projects with no euroSciVoc row. A project can carry up to 5 euroSciVoc rows, so buckets overlap slightly - it is a cover, not a strict partition. Say so in the file; it is honest and it costs nothing.

Three statuses, in order:

- `unexplored` - nobody has been there.
- `mapped` - a `## Corpus map` entry exists: we know what is in there and what it can support.
- `mined` - at least one bank question has been drawn from it.

`status` and the `bank` column are RECOMPUTED every run from `get_bank_questions` (all routes); everything else is carried. The frontier is **the only section you need to read to plan a run** - "where have we not been" is a filter on one column.

## Output contract

`src/retrieval/corpus_profile.md`, H2 sections in this order (section keys are the kebab-cased headings, served by `get_corpus_profile(section=...)`):

`## Header`, `## Frontier`, `## Corpus map`, `## Structural findings`, `## Distributions`, `## SQL`, `## Vector`, `## Hybrid`, `## Adversarial`, `## Ambiguous`, `## Coverage notes`

**Frontier row:**

```
| bucket | projects | status | map | bank |
|--------|----------|--------|-----|------|
| natural sciences / biological sciences | 8057 | mined | m03 | vec-01 |
```

Ends with the counter line: `mapped <n>/46 · mined <n>/46`.

**Map entry** (`## Corpus map`, one per `mapped` bucket, append-only):

```
- region: m<NN>
  bucket: <top-level> / <second-level>
  slice: <the SQL predicate that DEFINES this bucket>
  size: <N> projects  (<count query> -> <N>)
  about: <2-3 sentences on what work actually lives here, written from text you READ>
  texture: <report_text coverage; whether taxonomy labels are echoed verbatim in member text or paraphrased; how noisy the tags are; anything that changes how a question must be written>
  good for: <which question kinds this region supports - route/level/subtype - and WHY>
  thin for: <what this region cannot support, and why>
  mapped: <cpN>
```

`about` / `good for` / `thin for` are the payload the drafting skills consume. `texture` must come from read text, never from the tag alone: cp1 established that euroSciVoc leaf labels lie on interdisciplinary and MSCA projects (`ethnomycology` tagged an aquatic-fungi ecology project; `sustainable architecture` tagged a district-heating project). A map entry that just paraphrases the taxonomy label is worthless.

**Structural finding** (`## Structural findings`, append-only, no denominator - structural space has no finite bucket list):

```
- id: sf-<NN>
  kind: trap-pair | absence | dual-encoded | value-inventory
  claim: <the precise fact, stated so it can be checked>
  evidence: <the executed SQL> -> <the key numbers it returned>
  serves: <which routes/subtypes this feeds>
```

**Candidate format** (uniform across the SQL/Vector/Hybrid/Adversarial/Ambiguous sections):

```
- id: <section>-NN
  topic: <one line - what the question would be about>
  recommend: route=<sql|vector|hybrid|ambiguous> level=<L1|L2|L3|ADV> subtype=<drafting-skill vocabulary>
  bucket: <the frontier bucket this came from, or "-" for structural>
  evidence: <the executed SQL> -> <the key numbers/rows it returned>
  axes: <axis=value pairs, e.g. country=IT scheme=EIC dates=2019-2021>
  why: <one sentence - what makes this a good seed>
```

Adversarial candidates add `claim:` (the precise absence or false premise) and `near-miss:` (the synonym / adjacent-column / loosened-range variants checked, each with its count - a zero-match candidate without checked near-misses is not query-verified). Ambiguous candidates carry no subtype; they use `routes=<two-or-three of sql|vector|hybrid, joined with +>` and add a `readings:` line, one clause per route stating how that route would parse the question.

**Supply targets, FULL run** (2-3x the allocation so drafting always has slack): SQL >= 45 candidates (allocation 22), Vector >= 50 (25), Hybrid >= 50 (24), Adversarial >= 25 (12), Ambiguous >= 20 (10). **Scoped run:** the argument's targets replace these outright - they are caps as much as floors; exploration stops at the target.

**Skipped sections still get their H2 heading**, in contract order, with a one-line stub: `Not yet explored (scoped run "<scope>", <date>).` Section keys stay stable for `get_corpus_profile`, and incompleteness is explicit rather than silent. Header, Frontier and Coverage notes are produced on EVERY run; Distributions only when targeted - it serves Study 0.5, not the pilot.

**Width inside every section:** candidates must spread across axes - no more than a third of a section's candidates may share one axis value, and no named entity (project, org, scheme instance) may appear in more than two candidates. The frontier does most of this work now (you cannot cluster on one region if you are sent to unexplored ones), but check it anyway in the merge.

## Section specs

- **Header** - version label, generation date, corpus fingerprint (project count, vector count from `data/index/full` meta if readable), the `content_hash` of the schema_docs the run was grounded against, and the per-run telemetry log (below).
- **Distributions** - per-country project counts and EU-funding totals; fundingScheme counts; per-year startDate histogram; status, activityType and sme splits; ecMaxContribution percentiles (deciles + notable outliers); report_text coverage rate. Doubles as the Study 0.5 value-description source, so enum meanings and code semantics (from schema_docs value notes, verified against the data) belong here.
- **SQL** - distinct-value inventories for every filterable column; verified near-miss trap pairs with BOTH divergent numbers computed and shown (ecContribution vs ecMaxContribution-across-join, totalCost vs EU funding, partner-role NULLs, coordinator-vs-participant grain, and any new ones found). Structural mode. Candidates use /draft-sql-question's subtype vocabulary.
- **Vector** - within a bucket: leaf-level clusters, satisfying-project count, 2-3 sample acronyms + ids, report_text coverage; bucketed by the bank's level definition (1 project -> L1 seed, 2-4 -> L2, 5+ -> L3). Read one or two members' text to confirm the theme is really there. Flag clusters whose taxonomy label does NOT appear lexically in members' text (paraphrase `term_style` material) vs clusters whose vocabulary is echoed verbatim (exact-term material). Candidates use /draft-vector-question's subtype vocabulary.
- **Hybrid** - topic x filter survivor-count matrix: the bucket crossed with country / fundingScheme / date-range / funding-percentile filters, keeping combos whose TRUE survivor count lands in the drafting windows (2-10 filter-read, ~5-20 filter-synthesize, tight-but-rich for compare/survey; hard ceiling 200). Show the count query per kept combo. Candidates use /draft-hybrid-question's subtype vocabulary.
- **Adversarial** - query-verified genuine absences: zero-match filter values (plausible countries/schemes/topics/years with 0 rows - show the `COUNT(*)=0` query), false-presupposition seeds (a premise that sounds true, with the refuting query and its result), and data-absent fields (things users would ask that no column or text carries). Structural mode. level=ADV; subtype from /draft-adversarial-question's vocabulary.
- **Ambiguous** - facts present in BOTH a structured column and free text (funding amounts restated in report summaries, topics as euroSciVoc codes AND objective prose, dates narrated in workPerformed), each verified on a concrete example project. Feeds `## Structural findings` as `dual-encoded` entries too.

## Orchestration

**Model and effort:** run in an Opus-class session at **medium** effort - the orchestrator partitions, merges, dedups, updates the frontier, spot-checks and writes, which is bookkeeping, not authorship. A skill cannot change the session's own model, so CHECK: if the session model is not Opus-class or better, stop before spawning anything and tell the user to relaunch under `/model` (or explicitly waive the check). Subagents are the `corpus-explorer` agent type (`.claude/agents/corpus-explorer.md`): read-only, Opus at LOW effort, with its turn budget and stop-don't-loop rule baked into the agent def so the spawn prompt stays lean. Spawn via the Agent tool with `subagent_type: corpus-explorer`. **Never use Haiku** for any part of this run - Haiku is the system under test, and letting it pick the topics it will be tested on violates the one-hat rule. Avoid Sonnet too (it wears the judge hat).

**Concurrency:** at most **4** `corpus-explorer` subagents in flight - the `horizon-draft` MCP server is one stdio process over a single read-only DuckDB connection, so more just queues. Run in waves: dispatch up to 4, wait, dispatch the next; log a one-line progress note per wave so a fan-out run is never a silent black box.

### 1. Startup

1. Parse and restate the scope.
2. `get_corpus_profile(section="frontier")` - the plan comes from here. An `{"error": ...}` means the profile does not exist yet: this is a bootstrap run, so build the frontier from scratch with one `GROUP BY` query and mark all 46 buckets `unexplored`.
3. `get_bank_questions(route)` for every route - recompute the `bank` column and promote any `mapped` bucket that now has a bank question to `mined`.
4. `get_schema_docs()` - record the hash.
5. **Build the orientation block** (once, ~6 queries, pasted verbatim into EVERY spawn prompt): the euroSciVocPath format and level idiom, the 6 top-level branches with counts, the 40 second-level categories with counts, the fundingScheme inventory, the startDate range, ecMaxContribution deciles, and report_text coverage. If `## Distributions` is already populated, lift these from it instead of re-querying.

   This block is not optional. It exists because measured runs showed subagents independently re-deriving the same branch inventory and re-probing the path format - roughly a third of each subagent's queries spent getting oriented. ~1.5-2k prompt tokens buys back ~10 turns per subagent.

### 2. Fan out (max 4 in flight, in waves)

Assign slices from the frontier, not from re-derivation:

- **Topical work** (`map=N`, vector, hybrid, ambiguous): take `unexplored` buckets, largest-first unless the scope says otherwise, and give each subagent **2-3 buckets**. It returns a map entry per bucket plus ~3 candidates per bucket for the requested route(s).
- **Structural work** (sql, adversarial): sub-batches of ~8 candidates over disjoint families - funding-money traps; role/grain; distinct-value inventories; date/status/scheme for SQL; zero-match filters; false-presuppositions; data-absent fields for Adversarial.
- **Distributions** - statistical, no candidates and no width rule; one subagent (or two by metric group).

Each spawn prompt carries: the orientation block, its mode, its assigned buckets or family, the output formats it must fill, the no-unqueried-claims rule, and its targets. The toolset and the turn/anti-loop bounds live in the agent def, not the prompt.

**Always spawn.** Never explore inline in this session, even for a single sub-batch. Inline work runs the cheapest queries in the most expensive context - it lands in the orchestrator's window and then has to survive the whole merge, spot-check and write phase. Transparency is covered by the wave progress notes and the telemetry line, not by doing the work here.

### 3. Merge pass (this session)

- Assemble the NEW material only: map entries, structural findings, and candidates, concatenated across sub-batches. Number new map entries `m<NN>` and new candidates `<section>-NN` continuing from the highest existing id - never restart numbering, never renumber what exists.
- Drop near-duplicates across a wave's sub-batches (boundary overlaps) and against what the profile already holds (same topic + same axes).
- **Spot-check:** re-execute at least two embedded queries per produced section via `run_sql`. Any mismatch between a claim and the re-run fails that item - send its slice back to a fix-up sub-batch, never hand-edit the numbers.
- Update the frontier: newly mapped buckets `unexplored` -> `mapped` with their `m<NN>` id; recompute the counter line.
- Check the width rule across each merged section and the supply targets. A section that fails width or falls short gets **one** top-up wave - a single additional sub-batch aimed at the thin axis. A subagent's `SHORT:` note counts toward deciding a top-up.

### 4. Write - by insertion, never by rewrite

The profile grows monotonically; re-emitting it every run costs output tokens for sections that did not change, and invites transcription drift in anything "carried over verbatim". So:

1. `Grep` `corpus_profile.md` for `^## ` to get the heading line numbers.
2. `Read` ONLY the narrow ranges you are about to change (the frontier rows, the end of a section you are appending to). This also satisfies the read-before-edit requirement without pulling the whole file.
3. `Edit` in place: insert new blocks immediately before the next H2, replace stub lines with real content, update the changed frontier rows and the counter line.

Never `Write` the whole file (bootstrap run excepted, when there is nothing to preserve).

Then bump `CORPUS_PROFILE_VERSION` in `src/config.py` to the next `cpN`, and append the **telemetry line** to the Header's run log:

```
- cp<N> (<date>) scope "<scope>": <n> subagents, <n> run_sql, <n> projects read, <duration>; frontier mapped <before>/46 -> <after>/46, mined <n>/46
```

Counts come from `data/logs/draft_mcp.jsonl` (filter to this run's time window). This is how the next optimization pass gets measured instead of guessed.

Finally run `./.venv/Scripts/python.exe -m pytest tests/test_mcp_server.py -q`.

### 5. Review gate

Present a summary to the user and wait: the scope this run used, which buckets moved and which are still `unexplored`, per-section candidate counts vs targets, the frontier counter, the least-covered areas, anything surprising found, any section that needed a top-up, and the telemetry line. The user reviews the artifact once; revision instructions loop back through subagents or targeted queries (spot-check discipline applies to any edit). Revisions within this review session do not re-bump the version.

## Standing rules

- **Autonomous until the review gate.** No mid-run confirmations; surface problems in the final summary.
- **Bounded everything - never loop.** 2-3 buckets or ~8 candidates per subagent; max 4 in flight; each `corpus-explorer` has a turn budget (~12 `run_sql` + ~4 `get_project_text`), uses `fields` on project text and reads <= 3 ids per call; one top-up wave per section. A subagent that hits a bound returns partial with a `SHORT:` note - it never loops and never wanders outside its slice.
- **Every claim carries its query.** A number without SQL does not enter the profile.
- **Knowledge accumulates.** Each run advances the frontier and adds to the map. Never re-derive what the profile already records; never rewrite what an earlier run wrote.
- **The profile proposes, the drafting skills dispose.** No bank writes, ever, from this skill.
