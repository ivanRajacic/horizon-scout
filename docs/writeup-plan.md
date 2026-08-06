# Write-up plan

*What the write-up says, in what order, and which fact carries each claim.
Opened 2026-08-06. Every number below was recomputed from disk on that date;
where a number is not yet pinned it says so.*

*Structure set 2026-08-06 by the user. Post one is the piece. Everything held
back is in "Post two - held material" at the bottom; it is kept, not cut, and
may be used later.*

## The frame

**The QA system is the instrument. The authoring pipeline is the contribution.**
Everyone has built a RAG demo over a public corpus. Almost nobody shows how they
know their own benchmark is right. That asymmetry is the whole piece.

Tone: a side project written up honestly. Relaxed voice, hard receipts. Do not
dress it as a paper. The two-week build is not a thing to apologise for or to
hide - it is what the story explains.

**Title direction.** Name the factory and the bet, not the corpus. The bet is
that the parts that are code hold no matter which model runs them. Candidates:

- *A Question Factory, and the Bet That Code Holds the Line*
- *I Didn't Trust My Own Benchmark, So I Built a Factory to Attack It*
- *The Factory and the Bet: authoring a benchmark that argues with itself*

Nothing with CORDIS, Horizon or "EU research" in the title. The corpus is
setup.

---

# Post one

## 0. Cold open - the thesis, one paragraph

I built a hybrid retrieval and text-to-SQL system over 35,389 EU research
projects, then got more interested in whether I could trust the benchmark I was
measuring it with. So I built a multi-agent pipeline that authors its own
questions and adversarially attacks them before they are allowed in.

Then the line that sets up the whole piece:

> A cheaper model ran the same factory. The gates that are code held. The parts
> that depend on the model keeping a contract did not.

That sentence is the promise. Sections 3 and 4 pay it.

## 1. What this is (a box, few paragraphs)

Setup only. Compressed from the old sections 1 and 2. Facts, no argument.

**The corpus and the system.** 35,389 CORDIS projects in DuckDB; a
190,248-vector FAISS index. Four retrieval conditions behind one interface,
guardrailed text-to-SQL, a router, and a scoped path that filters then searches.
One retrieval stack runs everywhere (`hybrid_rerank`), chosen on the 2026-07-29
ladder. **Retrieval is not a measured variable** - say so here so nobody reads
the piece waiting for a retrieval comparison.

**The bank, and what "verified" means.** 58 questions. Every gold answer is
proven by execution, not asserted.

- Levels are mechanical on the vector route: L1 = 1 gold project, L2 = 2-4,
  L3 = 5+. Never tuned against results.
- SQL entries are born verified - `answer_columns`, `level_evidence`, and the
  hash of the schema docs they were authored against.
- Adversarial entries are born verified too: `absence_evidence` is a typed
  `{sql, expect, key_result}` proof that gets re-executed.

**The twin control.** Every adversarial question names `twin_id` - the
answerable bank question it perturbs. 49 answerable questions control
over-refusal; 9 twins control over-assertion. A refusal-only set cannot supply
that control, and this is the one design point in the box worth a full sentence
of argument.

Keep the box short. If it runs past a screen, cut from the corpus half, not the
verified half.

## 2. The factory - the centre of the piece

Three roles per question, split authority:

- the **drafter** authors and self-verifies facts by executing SQL and retrieval
- the **critic** attacks and reports typed findings, with no verdict and no kill
  power
- the **judge** rules UPHELD or DISMISSED on every HIGH and MID finding, and only
  then decides ACCEPT / FIX / ABANDON

Plus the rule that makes it work: **anything with a right answer is code, not a
model.** The gap report, id assignment, evidence re-execution, the width rule -
all deterministic nodes. Model nodes author and judge; they never do arithmetic.

**The numbers** (all from `docs/factory-telemetry.md`, computed by
`src/eval/telemetry.py`):

| | |
|---|---|
| batches / slots / candidates tried | 14 / 43 / 58 |
| accepted / failed | 42 / 1 |
| FIX rounds / candidate abandons | 22 / 6 |
| adjudication rounds per slot | 22 slots closed in 1; worst took 5 |
| terminal findings | 158 - 9 HIGH, 58 MID, 91 LOW |
| HIGH+MID rulings | 41 UPHELD, 5 DISMISSED |
| top defect classes | REFERENCE-UNSUPPORTED 17, AMBIGUOUS-READING 16, MISSED-GOLD 14, GOLD-WRONG 6 |
| **accepted questions carrying an upheld finding** | **26 of 42** |
| accepted questions that went through a FIX round | 20 of 42 |
| `precheck_record` / `precheck_candidate` | 209 runs, 30 with failures / 125 runs, 23 with failures |
| MCP calls over 10 days | 3,898 - of which 1,763 SQL executions |
| questions through the pipeline | 76 (58 in the bank, 18 trimmed to the v5 allocation) |

**The headline sentence: 26 of 42 accepted questions entered the bank in a
different state than the drafter first submitted.** That is the measurement of
what review bought.

**The 5 dismissals matter as much as the 41 upholds** - they show the critic was
filtered, not rubber-stamped. In post one the dismissal count carries that
point on its own; the vec-31 episode that illustrates it is held for post two.

**Two episodes** (`docs/factory-exemplars.md`, all quotes verbatim, bank
membership verified):

- **hyb-13** - a wrong gold, caught and repaired. The critic proved by executed
  SQL that a project was excluded only because the filter reads the objective
  field while the project describes the work in its report text. One clause
  changed. It shipped. **This is diagram 2.**
- **hyb-14** - the one abandoned slot. Round 1 upheld an ambiguity, the fix
  targeted it, round 2 showed the ambiguity had moved rather than closed. The
  judge killed it on the stop rule: "the fix moved the ambiguity instead of
  closing it". The question was not badly worded, it was undecidable.

Closing line for the section: the critic has no verdict, which is why it can
attack hard; the judge has no tools, which forces every ruling back onto evidence
someone else executed.

## 3. What it cost

Two numbers next to each other, which is the whole argument:

- **A full 58-question judged run costs $0.08.**
- **The pipeline that authored the bank cost about $1,507 of API-equivalent
  compute - roughly $20 per question through it.**

Then the driver: 934M of the tokens are cache reads from warm agents re-reading
held context across FIX rounds, and the orchestrator sessions alone outspent the
drafter, critic and judge combined. **The cost driver is the architecture, not
the model.** That sentence is what makes section 4 land - if the architecture
drives the bill, a cheaper model should not save much, and the probe tests that.

**Disclose both floor assumptions where the figure first appears:** it is
API-equivalent compute rather than money paid, and cache writes are priced at
the 1.25x five-minute rate. Both push the same way, so $1,507 is a floor.

Almost no published evaluation reports its own cost. This one reports both sides.

## 4. The Sonnet probe - what a cheaper factory produced

Nine cells were re-drafted with every factory role on Sonnet 5 instead of
Opus 5, from the same recorded seeds, same cells, same gates, same effort. The
plan and its disclosed limits are in `docs/sonnet-replication-plan.md`. **Call
it a matched-workload run, never a replication** - the seed does not bind the
question, so the two arms produce different questions from the same starting
material. Say that before the table, not after it.

**The authoring side.**

| | Opus | Sonnet |
|---|---|---|
| cells attempted | 9 | 9 |
| slots accepted | 9 | 7 |
| schema-valid records | 9 | **5** |
| candidates consumed | 12 of 27 | 17 of 27 |
| adjudication rounds | 18 | 16 |
| `precheck_record` failures | 0 of 17 | **0 of 17** |
| cost | ~$180 | **$65.72** |
| **cost per accepted slot** | **~$20** | **$9.39** |

**The execution gate held perfectly.** Every Sonnet draft re-executed to the
numbers it claimed. That is the design's own claim surviving contact: the
deterministic re-execution does not care which model wrote the record.

**Two contract breaches, both at the orchestrator, not the drafter.** The
Sonnet orchestrators reset `judge_decisions` per candidate instead of
accumulating across the slot, so the last journal line understates the work -
`telemetry.py` reads that line and reports 0 FIX rounds where the true count
across all lines is 4. And two accepted vector records shipped without
`pooling_evidence` while the journal recorded `validate-record OK`; calling the
validator directly on those records fails. Traced: `vec-s38`'s first candidate
had the field, the second candidate onward never did.

Both breaches are proxy-validation failures, so name them here and hand them
forward - section 5 is where they are explained.

**The judged side** (`data/runs/mixed-2026-08-06`, router, 16 questions, $0.02).
Both arms' questions were put in one bank, author readable off the id - an `-s`
infix means Sonnet.

| cell | Opus | Sonnet |
|---|---|---|
| sql-14 / sql-s14 | fail | PASS |
| sql-15 / sql-s15 | fail | fail |
| sql-16 / sql-s16 | PASS | PASS |
| vec-38 / vec-s38 | 0.80 | 0.80 |
| vec-41 / vec-s41 | 0.75 | 1.00 |
| vec-42 / (none) | 0.19 | - |
| hyb-12 / hyb-s12 | 0.00 | 0.00 |
| hyb-13 / hyb-s13 | 0.26 | 0.45 |
| hyb-15 / (none) | 0.33 | - |

**The discriminativeness point.** On the seven matched pairs the system scored
higher on three, equal on four, lower on none. A question the system scores
higher on is a question that discriminates less. The two Opus cells with no
counterpart are the two hardest in the set - 0.19 and 0.33 - and they are
exactly the two where Sonnet burned all three candidates and failed the slot.

**The line to write.** A cheaper drafter finished the easy cells for half the
money per accepted slot, wrote questions the system found easier, and could not
close the two hardest ones at all. The gates that are code held; the parts that
depend on the model keeping a contract did not. That is a sharper claim than
either "Sonnet works" or "Sonnet doesn't".

> **Footnote required wherever the judged table appears.** `vec-s38` (0.80) and
> `vec-s41` (1.00) were scored off gold sets with no `pooling_evidence` behind
> them - the record of the pooled search that proves a vector gold set was
> derived rather than asserted. The judge never reads that field, so the run
> scored them normally, but those two numbers rest on an unproven gold set and
> are not the same kind of number as the other five. The run itself was executed
> with `--unsafe-skip-bank-validation`, and both the run's meta and the first
> line of its report record the bypass and name the two violations.

## 5. What the checks kept missing

One paragraph does the unifying work, then three instances. The paragraph:

> Every check that failed in this project failed the same way. It validated a
> proxy for the thing instead of the thing. Not one of them was absent, wrong at
> the time it was written, or unreasonable. Each one looked at something that
> stood in for what it was supposed to guard, and the substitution was invisible
> until the two came apart.

**Instance one, the sharpest - the taxonomy gate.** `hyb-08` answered "No
projects match the structured criteria in this question." The narrowing SQL was:

```sql
... AND e.euroSciVocPath LIKE '%/ textiles%'
```

There is a stray space after the slash. Remove it and the query returns 7
projects. `scoped.py:188` normalises a term before looking it up in the taxonomy
- it strips `%`, `_`, slashes and whitespace. So it looked up `textiles`, found
it live, and passed the filter. The gate validated the normalised term. Nothing
validated the pattern as written. **The normalisation that makes the lookup
robust is exactly what hid the typo.**

This one gets the literal SQL on the page and gets read out line by line. It is
the most concrete moment in the piece.

**Instance two - the journal's `validate-record OK`.** The orchestrator records
that the validator ran and passed. That record is a proxy for the record being
valid. Two Sonnet vector records carry a passing line and fail the validator
when it is called on them directly.

**Instance three - telemetry's last line.** `telemetry.py` reads the last
journal line per slot, because the envelope is restated on every event and the
last one is supposed to be cumulative. That is a proxy for the slot's whole
history. When the Sonnet orchestrators reset `judge_decisions` per candidate,
the proxy silently reported 0 FIX rounds against a true 4.

Closing line: two of the three were only visible because a different model ran
the same pipeline. That is the argument for the probe, made after the fact.

## 6. What was cut, what's next

Short and unapologetic. Cutting scope on purpose is a better ending than a list
of things left undone.

**Cut.**

- **The agentic round.** Never built; the only remaining item needing new
  architecture, a pilot and a freeze. The QA system is the instrument, not the
  contribution.
- **Ambiguous and compositional cells**, dropped 2026-08-04.

**Next**, two pointers, one paragraph each.

- **The absence fix.** The system cannot yet tell "I did not find it" apart from
  "it is not there" - `hyb-08` above is that failure in one direction. Two
  changes would make round two: teach synthesis to separate the three cases, and
  make the scoped route prove a zero-row filter by executing each predicate
  alone. The bank was built to measure exactly that.
- **Cut the orchestrator's token bill.** It is the largest single line in
  section 3 and it is pure relay.

## Diagrams - two, no more

1. **The slot lifecycle** - drafter to critic to judge, with the FIX loop back
   to the drafter and the ABANDON exit. Goes in section 2.
2. **hyb-13 before and after** - the one changed clause and the project it let
   back in. Goes in section 2, next to the episode.

---

## Open choices - decide before drafting

1. **Venue and length.** A long blog post is the natural fit. The structure
   above already leads with the factory, so a portfolio site needs no
   re-ordering.
2. **Where post two goes.** The held material is enough for a second piece on
   measurement alone - judge noise floor and metric-mode sensitivity are the
   strongest parts of it. Decide after post one is drafted, not before.
3. **How much SQL to show.** The `hyb-08` stray space needs the literal line on
   the page. Elsewhere prefer prose.

## Numbers that still need pinning

- **The $20-per-question denominator.** $1,507 over the 76 questions that went
  through the pipeline is $19.83. Over the 42 accepted it is $35.88. The plan
  previously said "$20 per accepted question", which is the wrong pairing. Post
  one currently says "per question through it" - **decide which denominator the
  piece uses and use only that one**, including in the section 4 comparison
  against Sonnet's $9.39 per accepted slot, which is an accepted-slot number.
  These two must not be compared across different denominators.
- **Sonnet FIX-round count.** Section 4 says 4, counted by hand across all
  journal lines. `telemetry.py` still reports 0 because it reads the last line
  per slot, which the Sonnet orchestrators did not keep cumulative. Fix the
  script to recount across lines before quoting either number - it is the
  correct fix regardless of this run.
- Everything else in post one was recomputed from disk on 2026-08-06 and can be
  quoted as written.
- **Not needed for post one:** the hedging count and the judge-noise-floor
  provenance both belong to held material below, so neither blocks drafting.

## Traps

- **Do not quote `data/runs/full-2026-08-06/report.md`.** It shows f1 aggregates.
  The pinned judge is precision, and those verdicts are in `precision-judge.log`.
  This only matters if held material is used, but the trap is permanent.
- **`records.jsonl` holds two conditions.** The file is append-only and the run
  covered `router` and `always-hybrid`, so the last line per question is the
  always-hybrid record. Filter on `condition == "router"` or every SQL question
  looks like it produced no SQL and 13 of 58 look misrouted. This error was made
  and caught while writing this plan.
- **Journals restate the whole slot envelope on every event.** Count from the
  last line per slot or every finding is multiplied by its slot's round count.
  (And see section 5 - that rule is itself what broke on the Sonnet arm.)
- **`expected_route == "sql"` returns 19, not 16** - three adversarial questions
  wear a SQL costume. Filter `level != "ADV"` for the SQL cell.

---

# Post two - held material

*Kept, not cut. All of it was recomputed from disk on 2026-08-06 and stands on
its own. None of it is needed for post one, and putting any of it back in would
turn the piece from an argument about authoring into a results report.*

### The scores, and the gradient

Round one, `full-2026-08-06`, all 58 questions, router condition. Read the
numbers off `precision-judge.log`, not off `report.md` - the run was judged when
`factual_correctness` ran in f1 mode, the pinned judge is `mode="precision"`, and
the 33 judged answers were re-scored in precision from the same stored contexts.
A footnote must say this rather than quietly using one file and linking the
other.

Factual correctness, precision mode:

| cell | n | mean | median | max |
|---|---|---|---|---|
| vector L1 | 7 | 0.63 | 0.75 | 1.00 |
| vector L2 | 9 | 0.44 | 0.41 | 0.61 |
| vector L3 | 6 | 0.37 | 0.27 | 0.81 |
| hybrid | 11 | 0.35 | 0.44 | 0.67 |

Alongside: SQL 12/16 exact, adversarial 2/9, misroutes 2/58, retrieval hit 0.968
and recall 0.788 over the 31 questions with a gold set.

**The gradient is the point.** 0.63 / 0.44 / 0.37 across L1, L2, L3 is monotonic,
and the level definition is purely mechanical - gold-set size, never tuned
against results. An untuned definition produced an ordered difficulty ladder.
That is evidence the bank measures difficulty rather than noise.

Do not oversell the absolute scores. 0.35 mean on hybrid is not a headline.

### The other direction of the absence failure

Post one uses `hyb-08` - the system states an absence it has not proven. The
opposite direction is `vec-07`: it retrieved the wrong project, ADEMU, then
wrote that there is no answer available. The right project, INFL, is in the
corpus. Score 0.00.

Both directions are one missing capability: the system cannot tell "I did not
find it" apart from "it is not there". And that is the same distinction the
benchmark is built on - adversarial gold is an absence proven by execution. So
the instrument caught the system failing to meet the standard the instrument
itself holds. This is a strong paragraph and it is the main reason to write post
two.

**Blocked on a definition.** This section needs "N of 58 answers report an empty
search instead of stating a fact". A loose regex over the router answers matches
22 (17 answerable, 5 adversarial); a stricter one matches 12. Fix the
definition first, state it in the piece, then count once. Do not quote a number
until that is done.

### The judge's noise floor

The same 33 answers were re-judged under three metric modes. Faithfulness should
have been identical, because the mode does not touch it. It moved 0.081 mean
absolute, 0.35 max, and 10 of 31 questions moved more than 0.1 - at temperature
0. Almost nobody publishes a noise floor for their own LLM judge.

Provenance to confirm before quoting: which two logs these figures were computed
across, cited by filename.

### Metric-mode sensitivity

The same 33 answers scored 0.374 under f1, 0.341 under recall, 0.438 under
precision. And ragas' names are inverted relative to what they measure:
`precision` = tp/(tp+fp) over the *reference's* claims, so it is coverage of the
reference and extra content cannot cost anything, while `recall` is the
extra-content penalty. A benchmark number is a choice about the metric, not a
property of the system.

### The router rebuild

Misroutes fell from 12/58 to 2/58 by changing the contract, not the model: the
router stopped returning a conclusion and now reports two facts -
`needs_project_text` and `structured_constraints` - with the mode derived in
code, the same split the judge uses. This is the deterministic-first rule applied
to the runtime rather than the pipeline, and it is the cleanest single
before/after in the project.

### The vec-31 episode

The dismissal, dropped from post one because the dismissal count carries the
point. The critic argued for a ninth gold member and quoted a real line from the
project's final report. The judge threw it out using the critic's own evidence:
every experiment ran in blood cancers, so the carcinoma line is a prospective
claim, not work performed.
