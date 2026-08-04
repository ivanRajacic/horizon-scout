# Bank brief - the shared standard

*Versioned prompt asset (`BANK_BRIEF_VERSION` in `src/config.py`); bump the
version on any meaningful edit. Read by the three authoring nodes -
`question-drafter`, `question-reviewer` (the critic), and `question-judge` -
so that what "a good bank question" means cannot drift between them. Section 7
is read by a fourth, upstream node: `corpus-explorer` decides which seeds the
other three ever see, so it is held to the same standard.*

## 1. What the bank is

`eval/bank.jsonl` is **M5's measuring instrument**, not a quiz. Every entry is
one cell of an experiment that compares retrieval and routing strategies. The
questions are never answered by us; they are answered by the systems under
test, and their answers are scored against the entry's recorded gold label.

The consequence is the single most important sentence in this file:

> **A defective question does not produce a wrong answer. It produces a wrong
> finding in a study.**

A question whose gold set is incomplete silently fails a system that found the
missing project - and that failure is then reported as a retrieval result. A
question labelled L3 that is really L2 moves a data point into the wrong cell
of the results table. Nobody notices at scoring time. The defect is only
catchable here, at authoring time, which is why every node in this pipeline is
expensive and why the standard is high.

## 2. What "good" means here

Four properties, all of them checkable:

1. **It discriminates.** The question can distinguish the conditions being
   compared. A question every condition answers identically carries no
   information (the exception is deliberate: L1 cells are the clean-route
   baseline and are *supposed* to be easy - easiness at L1 is not a defect).
2. **It is honestly labelled in its cell.** Route, level, subtype, and
   `term_style` describe what the question actually is, computed from
   evidence, never asserted. Levels are derived (from the gold SQL, or from
   `|gold_project_ids|`, or from the subtype's bound), never chosen.
3. **Its gold survives independent re-derivation.** Someone who has not seen
   the author's work can re-execute the gold SQL, re-read the gold projects,
   or re-run the filter, and land on the same label.
4. **It has exactly one scoreable reading.** Two defensible readings that
   yield different answers make the question unscoreable, whichever one the
   system picks.

## 3. The two failure modes that matter most

Everything worth reporting reduces to one of these:

- **It measures nothing.** Dead gold, an empty result, a trap whose wrong
  query now matches the right one, an adversarial premise that turns out to be
  true, a filter that changes nothing, a question no condition can connect to
  its own evidence. The cell produces a number, and the number means nothing.
- **It measures the wrong thing.** A vector question answerable from a stored
  column (that is a SQL question wearing a vector label), a level that
  overstates or understates the work, an incomplete gold set that punishes the
  systems that did best, a reference asserting facts the evidence does not
  support.

Anything else - phrasing, elegance, how you personally would have worded it -
is not one of these and is not worth a round of work.

## 4. Route, level, and subtype reference

Route vocabulary in the bank is `sql | vector | hybrid | ambiguous`; the
runtime calls the hybrid mode "scoped" (`ROUTE_TO_MODE` in `src/eval/bank.py`
is the one place that mapping lives).

**SQL** - level is computed from the gold SQL's `level_evidence`:

| Level | Operational test | Subtypes |
|---|---|---|
| L1 | no JOIN, at most 1 non-trivial WHERE | `lookup`, `aggregate`, `rank` |
| L2 | >=1 JOIN, or a schema_docs value-note dependency, or GROUP BY without ranking | `join-lookup`, `value-grounded`, `grouped-aggregate`, `rank` |
| L3 | >=2 JOINs, or GROUP BY combined with ranking, or a documented near-miss trap | `multi-join`, `trap`, `rank` |

`rank` is legal at every level; every other subtype is level-bound.
`sql_comparison` is `ordered` iff the subtype is `rank`. SQL ladder entries
carry `answer_columns`, `level_evidence`, and `schema_docs_hash`.

**Vector** - level is DEFINED by `|gold_project_ids|`:

| Level | Test | Subtypes |
|---|---|---|
| L1 | `|gold|` = 1 | `identify`, `detail` |
| L2 | `|gold|` in [2,4] | `comparison`, `synthesis` |
| L3 | `|gold|` >= 5 | `survey` |

Ladder entries carry `gold_project_ids`, `term_style`, and `pooling_evidence`,
whose `accepted` list must equal `gold_project_ids` exactly.

**Hybrid** - subtypes are level-bound and carry gold-count bounds:

| Subtype | Level | `|gold|` |
|---|---|---|
| `filter-read` | L1 | 1 |
| `filter-synthesize` | L2 | 2-4 |
| `filter-compare` | L3 | 2-4 |
| `filter-survey` | L3 | >=5 |

Both sides must be load-bearing: drop the filter and the text alone must not
identify the gold set; drop the text and the filter alone must not answer the
question. Gold is always a subset of the enumerated survivors, and the
survivor set must be enumerable (true count <= 200).

**ADV** (off-ladder, `level: "ADV"`) - subtypes `zero-match`,
`false-presupposition`, `data-absent`, `unanswerable`. The gold is an absence,
proven by execution. `zero-match` carries an empty `gold_project_ids`.

Two things make that proof real rather than stated. `absence_evidence` is the
typed record of it - `{sql, expect, key_result}` per claim, `expect: "zero"`
for a query that must come back empty and `"rows"` for a refutation that must
come back full, near-miss variants included. `precheck_record` re-runs all of
it, so an absence that has stopped holding fails inside the drafter's own loop.

`twin_id` names the answerable bank question the adversarial one was derived
from - required for `zero-match` and `false-presupposition`, optional for
`data-absent`, forbidden for `unanswerable`. It is the control (a bank of
refusals alone cannot tell a calibrated system from one that refuses
everything) and it is the near-miss proof (the parent's gold returns rows, the
one-value perturbation returns none). The best ADV question differs from its
parent by as little as the absence allows.

**Ambiguous** - `expected_route: "ambiguous"` with >=2 `acceptable_routes`; no
subtype vocabulary exists yet.

**term_style** (required on every vector and hybrid ladder entry) -
`exact-term` when the question reuses distinctive vocabulary observed in the
gold texts, `paraphrase` when it deliberately avoids their key terms. The
per-condition rank matrix is an honesty heuristic, not proof: it just must not
contradict the declared style.

## 5. Severity - HIGH | MID | LOW

One vocabulary, used by the critic to report and by the judge to rule.

- **HIGH** - the question as recorded would produce a wrong finding. It
  measures nothing, or it measures the wrong thing (section 3). Dead or wrong
  gold, a missed satisfying project, an unsupported reference claim, a level
  or route that does not match the evidence, two defensible readings, a
  disproven adversarial premise, a decorative filter.
- **MID** - a real defect that degrades the measurement without invalidating
  the cell. A marginal-but-defensible gold member, a partial column leak, a
  strained-but-losing alternate reading, evidence that re-verified with one
  detail off, a `term_style` the rank matrix argues against.
- **LOW** - a note for the record. Telegraphing, generic-fact proximity,
  near-duplicate proximity, staleness that re-verified clean, phrasing
  observations. A LOW finding is never, on its own, a reason to change
  anything.

Calibration: **an L1 question being easy is not a finding.** L1 cells are the
clean-route baseline and the study needs them easy. Difficulty only matters
when the *label* overstates it, which is a level mislabel and therefore HIGH.

## 6. Role boundaries

Three nodes, three jobs, no overlap. The separation is the point: authority is
split from execution so that no node both finds a problem and decides what it
costs.

- **The drafter authors, and never self-adjudicates quality.** It grounds the
  question in observed data, verifies every fact by execution, and passes the
  deterministic `precheck_record` gate before it may emit a package. It does
  not argue that its question is good; a separate critic and a separate judge
  settle that.
- **The critic reports, and never rules.** It attacks the draft independently,
  tags every finding with a class and a severity, and cites evidence it
  executed this session. It has no verdict and no kill power. Fixability, when
  it can see one, is advice (`fix_direction`), not an authorization.
- **The judge rules, and never investigates.** It has no MCP tools by design.
  Both sides' claims already carry executed evidence, so its job is a logic
  check over the record, not a third investigation. It rules `UPHELD` or
  `DISMISSED` on every HIGH and MID finding *before* it emits a disposition.

The pipeline pays for two independent looks at every question. It only gets
what it paid for if each node stays inside its own job.

## 7. Seeds - the exploration standard

*Read by `corpus-explorer` (pasted into its spawn prompt by
`python -m src.cli frontier-report`). Everything above still applies: a seed is
judged by whether the question it becomes could survive sections 2 and 3.*

A **seed** is not a question. It is a place in the corpus plus the executed
evidence that a good question can be built there. The drafter recomputes route,
level, subtype and `term_style` from its own grounding pass - your
recommendation is advice that saves it time, never a label it inherits.

**What makes a seed worth a drafter's pass:**

1. **The count is taken without the fence, and you do not turn it into a
   level.** Level is DEFINED by the count (section 4), which makes it
   arithmetic - so a deterministic node does it and a seed carries no
   `level=` at all. What a topical seed carries instead is `topic_filter`:
   its topic condition ALONE, over `project` aliased `p`, with no euroSciVoc
   join and no bucket predicate.

   You explore one bucket at a time, so every count you take is fenced by
   "...and the project is tagged sociology". The question your seed becomes
   carries no such fence - nobody asks "which sociology-tagged project studies
   loneliness". cp4 counted `loneliness` at 3 inside its bucket when the corpus
   has 8, the other 5 being the same kind of project filed under health or
   computing; 7 of that run's 18 seeds named the wrong cell for this one
   reason. Record the fenced number too - `satisfying_count` for topical seeds,
   `survivor_count` for hybrid combos, each reproducible from your own evidence
   - as context for a drafter, not as a level.

   | | window |
   |---|---|
   | vector L1 / L2 / L3 | `\|satisfying\|` = 1 / 2-4 / >=5 |
   | hybrid `filter-read` | 2-10 survivors |
   | hybrid `filter-synthesize` | 5-20 survivors |
   | hybrid `filter-compare` | 2-20 survivors |
   | hybrid `filter-survey` | 5-60 survivors |
   | any hybrid | hard ceiling 200 - a survivor set that cannot be enumerated cannot be gold |

2. **The theme is in the text, not just in the tag - and the map is written
   before the search.** euroSciVoc leaf labels lie on interdisciplinary and
   MSCA projects (`ethnomycology` on an aquatic-fungi ecology project;
   `sustainable architecture` on district heating). Read two or more members
   before proposing a seed that depends on what they are about, and record
   which ids you read.

   For a map entry the reading order matters as much as the reading. Your
   first reads happen BEFORE any topic probe and are picked by something
   topic-blind (largest contribution, oldest and newest start, one per large
   third-level node); they go in `read_first:`. cp4 skipped this and passed
   every check anyway: its explorers searched for a term, read what matched,
   and described the whole bucket from those - 16 of 17 reads were members of
   a candidate's own result set, so `about:` described the seeds and not the
   region. A seed is disposable and a drafter recomputes it; the map is
   append-only and a mapped bucket is never revisited, so a description
   written backwards stays wrong.

3. **A user could actually ask it.** The filter has to be expressible in a
   natural question. This is the `hyb-02` lesson: a musicology x MSCA-IF combo
   burned a full drafter pass because its filter was one no user would state,
   and nothing upstream had checked.

4. **It is somewhere new.** The frontier exists so the bank stops clustering.
   Stay inside your assigned slice, spread candidates across values within it,
   and do not reuse a named entity that already carries two candidates.

**Every claim carries its query.** Evidence is typed - `{sql, key_result}` -
because it is re-executed in full by `verify-evidence`, not sampled. A number
that does not reproduce fails the slice. If the claim IS an absence, say so
with `expect_empty` and show the near-misses you checked; an unchecked
zero-match is not evidence of absence.

**Return fewer rather than weaker.** A thin slice that returns three sound
seeds and a `SHORT:` note costs the pipeline nothing. A padded slice costs a
full drafter pass per bad seed, and that is the most expensive thing exploration
can do.
