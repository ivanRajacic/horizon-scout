---
name: explore-corpus
description: Cumulative corpus exploration for the Horizon Scout M5 bank. Maintains src/retrieval/corpus_profile.md as a growing MAP of the database - what each part is about and what questions it can support - plus query-verified candidate seeds for drafting. A frontier table over the 46 euroSciVoc buckets records what has been explored and what has not, so every run goes somewhere new instead of re-deriving the corpus. Everything with a right answer is a deterministic CLI node (`frontier-report`, `verify-evidence`, `explore-crosscheck`, `write-profile`); the orchestrator spawns bounded `corpus-explorer` subagents (4 in flight), relays their typed payloads into an append-only journal, and judges nothing.
argument-hint: [scope, e.g. "map=6" or "vector=10 hybrid=10"]
---

# /explore-corpus

**Arguments:** $ARGUMENTS

Maintain `src/retrieval/corpus_profile.md`: the query-verified "what is in this database" map that tells the drafting skills where to go and what each region can support. Runs autonomously - bounded parallel slices, per-slice close-out, ONE user review of the finished artifact at the end. No per-section confirmation.

**You are a message bus, not an author.** Four deterministic CLI nodes own everything with a right answer - the frontier, the partition, the orientation block, id assignment, evidence verification, the width and duplicate checks, the profile insertions, the version bump, and the telemetry. Your irreducible jobs are: parse the scope, spawn subagents on their slices, relay each returned payload into the journal, decide whether a thin section earns its one top-up wave, and present the result. You verify nothing by hand and you write no profile prose.

**The point of this skill is that knowledge ACCUMULATES.** Every run adds to the map and advances the frontier; no run re-derives what an earlier run already established. That is what keeps the bank wide - drafting draws from parts of the corpus we have deliberately been to, instead of clustering on the handful of entities and funding columns that are easy to find from a cold start.

## Scope

No arguments = full run. Otherwise the argument caps the run:

- `map=<N>` - map N `unexplored` buckets (topical work: what is in there, what it can support).
- `<section>=<N>` - candidate targets for `sql` / `vector` / `hybrid` / `adversarial` / `ambiguous` / `distributions`, as before. Topical sections (vector, hybrid, ambiguous) draw from buckets and mark the buckets they touch as explored; structural sections (sql, adversarial) do not touch the frontier.

Anything not named is SKIPPED - no subagent is spawned for it. **Restate the parsed scope as the first output line** (buckets to be mapped, per-section targets, what is skipped) so a wrong parse dies immediately.

## Hard constraints

- **Read-only exploration.** All data access through the `horizon-draft` MCP tools; every call is traced automatically. The only file YOU write is the run journal (`eval/exploration/journal-<date>.jsonl`); `write-profile` writes `src/retrieval/corpus_profile.md` and the `CORPUS_PROFILE_VERSION` bump in `src/config.py`. Never touch the bank, schema_docs, or anything frozen.
- **No unqueried claims.** Every number, distribution, trap pair, cluster size, survivor count and absence carries typed evidence - `{"sql": ..., "key_result": ...}` - and `verify-evidence` re-executes ALL of it. A claim without its query does not reach the file, and a number that does not reproduce fails its slice.
- **Append, never rewrite.** A later run fills stubs, adds map entries, appends candidates and updates frontier rows. It never renumbers, edits or drops what an earlier version recorded - drafting sessions may already have consumed it. The writer enforces this by construction: it inserts before the next H2 and re-emits nothing it did not touch.
- **Candidates are advisory.** Recommended route and subtype are the exploration agent's judgment calls to speed drafting; the drafting skills recompute everything from their own evidence and remain the authority. Nothing here pre-commits a bank entry. The **level is not a judgment call** - it is arithmetic on a count, so a deterministic node derives it from the candidate's `topic_filter` and the subagent never writes one.
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
  read: <the project ids the `about:` was written from - at least 2>
  read first: <the subset read BEFORE any topic probe - at least 2>
  good for: <which question kinds this region supports - route/level/subtype - and WHY>
  thin for: <what this region cannot support, and why>
  mapped: <cpN>
```

`read:` is what turns "written from text, not from the tag" into a check instead of an honour rule: `verify-evidence` confirms those ids exist, carry text and sit in the bucket, and flags an `about:` that is mostly the bucket label back. The region id and `mapped:` are assigned by the writer - a subagent never numbers its own entry.

`read first:` (added cp5) closes the gap that check left open. cp4 passed every `read:` check and the descriptions were still wrong: the explorers searched for a term, read the projects that matched, and described the whole bucket from them - 16 of 17 reads were members of a candidate's own result set. An `about:` written that way describes the seeds, not the region, and because the map is append-only and a mapped bucket is never revisited, it stays wrong. So the first reads happen before any topic probe, picked by something topic-blind (largest contribution, oldest/newest start, one per large third-level node), and `verify-evidence` checks both that `read_first` is populated (`MAP-FIRST`, FAIL) and that some of it landed outside the slice's candidates (`MAP-INDEPENDENT`, WARN - a pre-probe read that turns up in a candidate is a coincidence worth allowing, all of them doing so is cp4 again).

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
  counts: <N corpus-wide, M inside the bucket>
  bucket: <the frontier bucket this came from, or "-" for structural>
  evidence: <the executed SQL> -> <the key numbers/rows it returned>
  axes: <axis=value pairs, e.g. country=IT scheme=EIC dates=2019-2021>
  why: <one sentence - what makes this a good seed>
```

In the journal this is typed: `evidence` is a list of `{"sql": ..., "key_result": ...}` (add `"expect_empty": true` when the absence IS the claim), and topical candidates carry `satisfying_count`, hybrid combos `survivor_count`. Each must reproduce from that candidate's own evidence rows (`COUNT`), and `survivor_count` must sit inside the subtype's drafting window (`WINDOW`) - the hyb-02 birth-failure a drafter used to discover the expensive way. The writer renders the block above from the typed form and assigns the id.

**The level is derived, not recommended (cp5).** A subagent explores one bucket, so every count it takes is fenced by that bucket's predicate; the question the seed becomes carries no such fence. cp4 counted `loneliness` at 3 in sociology when the corpus has 8, and 7 of its 18 seeds landed in the wrong cell for exactly this reason. So a topical candidate hands back `topic_filter` - its topic condition ALONE, over `project` aliased `p`, with no euroSciVoc join and no bucket predicate - and `verify-evidence` runs it corpus-wide and derives the level. The subagent writes no `level=`; `write-batch`'s sibling `write-profile` sets it and renders both counts in `counts:` so a drafter can see which number is fenced. A candidate whose `recommend` still names a level that disagrees with the derived one FAILs (`LEVEL`), and one whose `topic_filter` will not execute is refused at write time rather than written with an underivable level.

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

**Model and effort:** run in an Opus-class session at **low** effort. Partition, merge, dedup, frontier update, spot-check and write are all deterministic nodes now; what is left is parse, spawn, relay, present. (Effort is set AT LAUNCH - `claude --effort low` - not by asking a session to change its own.) A skill cannot change the session's own model, so CHECK: if the session model is not Opus-class or better, stop before spawning anything and tell the user to relaunch under `/model` (or explicitly waive the check). Subagents are the `corpus-explorer` agent type (`.claude/agents/corpus-explorer.md`): read-only, Opus at LOW effort, with its turn budget and stop-don't-loop rule baked into the agent def so the spawn prompt stays lean. Spawn via the Agent tool with `subagent_type: corpus-explorer`. **Never use Haiku** for any part of this run - Haiku is the system under test, and letting it pick the topics it will be tested on violates the one-hat rule. Avoid Sonnet too (it wears the judge hat).

**Concurrency:** at most **4** `corpus-explorer` subagents in flight - the `horizon-draft` MCP server is one stdio process over a single read-only DuckDB connection, so more just queues. **Sliding window, not waves:** dispatch up to 4, and dispatch the next the moment ANY of them returns, rather than waiting for the slowest of a batch. Log a one-line progress note per slice so a fan-out run is never a silent black box.

**Per-slice close-out.** A returned slice is verified and journalled immediately, before you dispatch its replacement. Two things follow. Work is checkpointed - a killed run keeps every verified slice, and a re-run resumes from the journal instead of starting over. And your context never has to hold a payload twice: relay it into the journal and forget it, because `write-profile` reads the journal from disk, not from your window.

### 1. Startup - one command

1. Parse and restate the scope.
2. Run it:

   ```bash
   ./.venv/Scripts/python.exe -m src.cli frontier-report --map <N>
   ```

   That single call replaces the whole old startup. It returns, computed from the live database and the live bank:

   - the **frontier** - all 46 buckets with `status` / `map` / `seeds` / `bank` recomputed (`bank` traced through `gold_project_ids` -> `euroscivoc`), plus the counter line;
   - the **slice partition** - which unexplored buckets go to which slice, largest-first, 3 per slice;
   - the **next free ids** - `m<NN>`, `sf-NN`, and one per candidate section, counted across the whole profile;
   - the **orientation block** - path format and level idiom, the 6 branches with counts, the fundingScheme inventory, the startDate range, ecMaxContribution deciles, report_text coverage - followed by the **seed standard** (`## 7. Seeds` of `src/eval/bank_brief.md`).

   Paste the orientation block **verbatim** into every spawn prompt. It is not optional: measured runs showed subagents independently re-deriving the same branch inventory and re-probing the path format - roughly a third of each subagent's queries spent getting oriented. ~1.5-2k prompt tokens buys back ~10 turns per subagent. The seed standard travels with it so the explorer is held to the same definition of "good" the drafter, critic and judge use.

3. Open the journal: `eval/exploration/journal-<date>.jsonl`, line 0 the run header.

   ```jsonc
   {"kind": "run", "date": "2026-07-25", "scope": "map=6",
    "started": "2026-07-25T14:03:00",          // used to window the telemetry
    "subagents": 3,                             // explorers + critic, as spawned
    "targets": {"vector": 9},                   // per-section supply, if scoped
    "versions": {"corpus_profile": "cp3", "schema_docs": "sd2",
                 "bank_brief": "bb2"}}
   ```

   Update `subagents` at close-out if a slice was re-spawned. The journal counts *slices*, and one subagent may carry three buckets and journal three lines - so without this the telemetry line can only honestly say "N slices".

   `started` must be an ISO timestamp in the MCP log's format (`date +%Y-%m-%dT%H:%M:%S`); `write-profile` counts this run's `run_sql` and `get_project_text` traffic from it.

### 2. Fan out (sliding window, 4 in flight)

Slices come from `frontier-report`'s partition, never from your own re-derivation:

- **Topical work** (`map=N`, vector, hybrid, ambiguous): take `unexplored` buckets, largest-first unless the scope says otherwise, and give each subagent **2-3 buckets**. It returns a map entry per bucket plus **~5 candidates per bucket** for the requested route(s).

  Five is flat, not scaled by bucket size, and that is deliberate. A run is not trying to drain a bucket - it takes what a visit yields and the NEXT run reads the frontier, sees which buckets have the fewest seeds, and goes back into those. Breadth first: every bucket visited once beats one big bucket mined out, because a bucket nobody has entered is the only thing a later run cannot recover from cheaply. (cp4 used 3 and that was slightly thin; the bank needs ~400 seeds against 46 buckets, so 5 a visit plus return visits gets there.)
- **Structural work** (sql, adversarial): sub-batches of ~8 candidates over disjoint families - funding-money traps; role/grain; distinct-value inventories; date/status/scheme for SQL; zero-match filters; false-presuppositions; data-absent fields for Adversarial.
- **Distributions** - statistical, no candidates and no width rule; one subagent (or two by metric group).

Each spawn prompt carries: the orientation block **and the seed standard** (verbatim from `frontier-report`), its mode, its assigned buckets or family, the output formats it must fill, and its targets. The toolset, the turn/anti-loop bounds, the no-unqueried-claims rule and the `precheck_candidate` gate live in the agent def, not the prompt.

**Always spawn.** Never explore inline in this session, even for a single slice. Inline work runs the cheapest queries in the most expensive context. Transparency is covered by the per-slice progress notes and the telemetry line, not by doing the work here.

### 3. Relay each returned slice (you judge nothing)

As each subagent returns - not at the end - do exactly this, then dispatch its replacement:

1. **Append a journal line** with the payload verbatim. Envelope typed, payload relayed:

   ```jsonc
   {"kind": "slice", "slice_id": "s01", "status": "RETURNED",
    "mode": "topical",
    "buckets": ["social sciences / sociology"],
    "targets": {"map_entries": 1, "candidates": 5},
    "map_entry": {"bucket": "...", "slice": "...", "size": "...",
                  "about": "...", "texture": "...", "read": [123456, 234567],
                  "good_for": "...", "thin_for": "..."},
    "candidates": [{"id": "vector-16", "topic": "...",
                    "recommend": "route=vector level=L2 subtype=comparison",
                    "bucket": "...", "satisfying_count": 3,
                    "evidence": [{"sql": "SELECT ...", "key_result": "3 projects"}],
                    "axes": "branch=social-sciences leaf=... satisfying=3",
                    "why": "..."}],
    "findings": [],
    "short": null}
   ```

   Ids are yours to assign from `frontier-report`'s next-free list, in dispatch order. `evidence` is a LIST of `{sql, key_result}`; mark an absence claim `"expect_empty": true`. `satisfying_count` / `survivor_count` are what make the level and window checks possible - relay them if the subagent reported them.

2. **Verify it:**

   ```bash
   ./.venv/Scripts/python.exe -m src.cli verify-evidence eval/exploration/journal-<date>.jsonl
   ```

   Exhaustive, not sampled: every evidence SQL is re-executed and every recorded number must reproduce; map entries must cite >= 2 real read project ids that exist, carry text and sit in the bucket; `about:`/`texture:` must not be the taxonomy label back; a recommended level must agree with its own count and a hybrid survivor count must sit inside its subtype's window.

3. **On PASS** append a second line for that slice with `"status": "VERIFIED"` (or `"SHORT"` if the subagent returned a `SHORT:` note). **On FAIL**, re-spawn that one slice with the failing checks quoted, then journal the replacement payload. One re-spawn per slice; if it fails again, journal `"status": "FAILED"` with the reason and move on. Never hand-edit a number into passing.

Do not read the payload for quality. You have no judgement to add that `verify-evidence` has not already made mechanically, and re-reading it is the context leak this design removes.

### 4. Close out

1. **Cross-check** - what no single slice can see:

   ```bash
   ./.venv/Scripts/python.exe -m src.cli explore-crosscheck eval/exploration/journal-<date>.jsonl
   ```

   Width (no axis value on more than a third of a section), entity spread (no named entity in more than two candidates), near-duplicates against both the run and the existing profile, and supply against this run's targets. These are FLAGS, not gates. A section that fails width or falls short gets **one** top-up slice aimed at the thin axis - that decision is yours, and it is the only judgement call in the loop. A subagent's `SHORT:` note counts toward it.

2. **Spawn the completeness critic** (`subagent_type: explore-critic`) once, with the journal path and the crosscheck output. It reads the typed summaries - never the payloads - and returns the `## Coverage notes` prose plus a list of what is missing. It reports; it does not gate and it does not re-spawn.

   **Journal its output** - do not hold it to paste later:

   ```jsonc
   {"kind": "critic", "coverage_notes": "<the COVERAGE-NOTES block verbatim>",
    "gaps": ["<the GAPS lines>"]}
   ```

   `write-profile` inserts it into `## Coverage notes` under a version heading. The first live run (cp4) proved why this is a journal line and not a manual step: the critic ran, and its notes were silently dropped because nothing deterministic carried them.

3. **Write:**

   ```bash
   ./.venv/Scripts/python.exe -m src.cli write-profile eval/exploration/journal-<date>.jsonl cp<N>
   ```

   Insertion only: new map entries, structural findings and candidates go in before the next H2 with their assigned ids, stub lines are replaced, the frontier table and counter line are recomputed, `CORPUS_PROFILE_VERSION` is bumped, and the telemetry line is appended to the Header's run log with `run_sql` / `get_project_text` counts computed from `data/logs/draft_mcp.jsonl` over this run's window. Pass `--dry-run` first if you want to see the result before it lands. Append the critic's `## Coverage notes` prose afterwards, as the one piece of writing a model still does.

4. **Trace what the run cost:**

   ```bash
   ./.venv/Scripts/python.exe -m src.cli agent-trace --orchestrator
   ```

   Per agent: turns, wall clock, output tokens, input tokens with the cache share, and tool calls - read from each subagent's own transcript, so concurrent agents are never confused with each other. Paste the table into the review summary. This is how the next run's estimate stops being a guess.

5. Run `./.venv/Scripts/python.exe -m pytest tests/test_explore.py tests/test_mcp_server.py -q`.

### 5. Review gate

Present a summary to the user and wait: the scope this run used, which buckets moved and which are still `unexplored`, per-section candidate counts vs targets, the frontier counter, the crosscheck flags, what the completeness critic said is missing, any slice that failed verification or needed a re-spawn, any section that needed a top-up, the telemetry line, and the `agent-trace` table. The user reviews the artifact once; revision instructions loop back through subagents (any revised payload goes through the journal and `verify-evidence` like any other - never hand-edit the profile). Revisions within this review session do not re-bump the version.

## Standing rules

- **Autonomous until the review gate.** No mid-run confirmations; surface problems in the final summary.
- **Bounded everything - never loop.** 2-3 buckets or ~15 candidates per subagent; max 4 in flight; each `corpus-explorer` has a turn budget (~18 `run_sql` + ~5 `get_project_text`), uses `fields` on project text and reads <= 3 ids per call; one re-spawn per failed slice; one top-up per section. A subagent that hits a bound returns partial with a `SHORT:` note - it never loops and never wanders outside its slice.
- **Every claim carries its query, and every query is re-run.** A number without SQL does not enter the profile, and a number that does not reproduce under `verify-evidence` does not either.
- **The deterministic nodes are the authority on facts.** If your reading of a payload disagrees with `verify-evidence` or `explore-crosscheck`, they are right. You do not overrule them, and you do not re-derive what they computed.
- **Knowledge accumulates.** Each run advances the frontier and adds to the map. Never re-derive what the profile already records; never rewrite what an earlier run wrote.
- **The profile proposes, the drafting skills dispose.** No bank writes, ever, from this skill.
