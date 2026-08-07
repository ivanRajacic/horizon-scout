# Sonnet replication run - what will be done

*Proposal, 2026-08-06. Approved and RUN the same day. This file stays as the
record of what was planned and why. The results - authoring numbers, the judged
mixed run (`data/runs/mixed-2026-08-06`), the two orchestrator contract
breaches - live in `docs/writeup-plan.md` §10. Outputs are in
`eval/drafts/sonnet-probe/` and `eval/banks/mixed-opus-sonnet-2026-08-06.jsonl`.*

Re-draft nine already-authored cells with every factory role on Sonnet instead
of Opus, and compare how hard the pipeline had to work. The bank is not touched.

---

## 1. The nine slots

The most recent batch-authored non-adversarial slot in each route. Batch-authored
matters: only those have a recorded `packet.json`, so the identical candidate
list can be handed to Sonnet. The 30 interactively-authored bank questions have
no seed to supply and are not eligible.

| slot | batch | date | cell | Opus rounds | dispositions | findings | cands used |
|---|---|---|---|---|---|---|---|
| `sql-14` | H | 07-29 | sql / L2 / value-grounded | 2 | FIX, ACCEPT | 1 MID | 1 of 3 |
| `sql-15` | H | 07-29 | sql / L2 / grouped-aggregate | 2 | FIX, ACCEPT | 3 MID | 1 of 3 |
| `sql-16` | H | 07-29 | sql / L3 / trap | 1 | ACCEPT | 0 | 1 of 3 |
| `vec-38` | E | 07-28 | vector / L1 / identify, exact-term | 1 | ACCEPT | 0 | 1 of 3 |
| `vec-41` | F | 07-28 | vector / L2 / synthesis, paraphrase | 2 | FIX, ACCEPT | 1 MID 2 LOW | 1 of 3 |
| `vec-42` | F | 07-28 | vector / L3 / survey, exact-term | 2 | FIX, ACCEPT | 1 HIGH 5 MID 3 LOW | 1 of 3 |
| `hyb-12` | I | 08-01 | hybrid / L1 / filter-read, paraphrase | 5 | FIX, ABANDON, ABANDON, FIX, ACCEPT | 2 MID 4 LOW | 3 of 3 |
| `hyb-13` | I | 08-01 | hybrid / L2 / filter-synthesize, exact-term | 2 | FIX, ACCEPT | 1 HIGH 1 MID 1 LOW | 1 of 3 |
| `hyb-15` | J | 08-01 | hybrid / L3 / filter-compare, paraphrase | 1 | ACCEPT | 1 MID | 2 of 3 |

**The Opus baseline these nine set, which is what Sonnet is measured against:**

- 9 of 9 accepted, 0 failed
- 18 adjudication rounds, 7 FIX dispositions, 2 ABANDONs (both inside `hyb-12`)
- 26 findings: 2 HIGH, 14 MID, 10 LOW
- 12 of 27 available candidates consumed

The level spread is L1 x2, L2 x4, L3 x3. The route spread is 3/3/3 as asked.

### Why `vec-42`, and the one constraint it breaks

The vector column needed an L3. **`vec-42` is the only vector L3 in the whole
project with a usable seed.** The evidence, checked rather than assumed:

- Six vector L3 questions are in the bank. Four were authored interactively and
  have no seed at all. The other two, `vec-24` and `vec-31`, are from batches
  that predate `packet.json`, and their journals recorded a one-line summary per
  candidate - 131 to 220 characters, no bucket map - not the block the drafter
  actually received. Feeding that to Sonnet would starve it relative to Opus and
  confound the whole run.
- The packet era for vector is batchD onward. Those batches produced exactly one
  L3: `vec-42`, with three full candidates (719 to 1549 characters plus bucket
  maps), identical in shape to every other slot here.

**`vec-42` is archived, not in the bank.** It was removed on 2026-08-03 for
allocation, not quality - the recorded reason is *"L3 over target;
survey/exact-term, biomedical engineering - the three exact-term L3 slots went
to transport, metrology and oncology"*. It passed drafter, critic and judge,
shipped into the bank, and was cut weeks later because the v5 allocation had one
L3 too many. Its record survives intact in
`eval/archive/bank-trimmed-2026-08-03.jsonl` and is byte-identical to what
batchF produced, so the Opus side of the comparison is fully available.

**It was therefore never judged in round one**, which was the other half of the
original request. That costs nothing here: the round-one factual score plays no
part in this comparison. What is measured is precheck failures, rounds,
findings and candidates consumed - all of which `vec-42` has, and richly. Its
1 HIGH / 5 MID / 3 LOW is the strongest single baseline in the set.

The alternative, if bank membership matters more than seed fidelity, is to drop
back to `vec-35` and accept L1 / L1 / L2 in the vector column. The set would
still cover L3 through `sql-16` and `hyb-15`.

Note `hyb-12` is the worst slot in the whole project - it burned all three
candidates and took five rounds. It is included because it is the most recent
hybrid L1, and because a slot Opus barely finished is the most informative one
to hand a smaller model.

**Seeds.** Every slot's candidate list is recoverable verbatim from its batch
`packet.json`. Sonnet gets the same list in the same order, not just the winning
candidate, so it has the same choices and the candidate-consumption count stays
comparable.

## 2. What has to change to make it all-Sonnet

The role model is not a launch flag. It is pinned in each agent definition:

| file | line | from | to |
|---|---|---|---|
| `.claude/agents/question-drafter.md` | frontmatter | `model: opus` | `model: sonnet` |
| `.claude/agents/question-reviewer.md` | frontmatter | `model: opus` | `model: sonnet` |
| `.claude/agents/question-judge.md` | frontmatter | `model: opus` | `model: sonnet` |
| the run script | launch | `--model claude-opus-5` | `--model claude-sonnet-5` |

`reasoningEffort: low` stays as it is on all three roles, and the orchestrator
keeps `--effort medium`. Only the model moves. The orchestrator session itself is
Sonnet too, so "all Sonnet" is literal.

**These are tracked files.** The three agent edits are made on a scratch branch,
the run happens there, and the branch is deleted afterwards. `master` never
carries a Sonnet agent definition. This is the part most likely to go wrong if
done casually, so it is done on a branch on purpose.

## 3. Ids

Scratch ids `sql-s14`, `sql-s15`, `sql-s16`, `vec-s38`, `vec-s41`, `vec-s42`,
`hyb-s12`, `hyb-s13`, `hyb-s15`. Verified by running the real code paths on
2026-08-06:

- `validate_record` has no id-format constraint and accepts them
- `journal_append` and `load_journal` round-trip them
- `write_batch` completes and stages the draft with the scratch id
- `next_ids` is unaffected: a staged scratch draft does **not** burn a real
  number, because `ID_RE` is `^(sql|vec|hyb|adv)-(\d+)$` and a letter suffix
  does not match
- `promote.py`'s `HEADING_ID_RE` also does not match, so `promote-drafts`
  physically cannot see these headings - the Sonnet drafts cannot reach the bank
  even by mistake

The letter suffix is load-bearing. A numeric scratch id like `vec-901` **would**
match `ID_RE` and would jump the id counter to 902, permanently burning 900 ids.

Output goes to `eval/drafts/sonnet-probe/`, a new directory, so no existing batch
directory is touched.

## 4. What is matched, and what is not

Matched: the cell (route, level, subtype, term_style), the candidate list and its
order, the corpus, the MCP tool surface, the deterministic gates, and the
`--effort` setting.

Not matched, and disclosed rather than papered over:

1. **The seed does not bind the question.** The drafter reads projects and
   composes from what it observes. It may walk a long way from the candidate or
   discard it. Two runs from the same candidate can produce different subjects,
   different gold sets and different difficulty. **This is not a replication and
   the write-up must not call it one.** It is a matched-workload run: same nine
   kinds of work, same starting material, same gates.
2. **Asset versions have moved since these slots ran.** Schema docs were `sd2`
   for all nine and are `sd3` today. The corpus profile was `cp7` for `vec-38`, `vec-41` and `vec-42`
   and `cp8` for the rest,
   and is `cp8` today. So
   the Sonnet arm runs on slightly newer assets than the Opus arm did. Supplying
   the candidate directly reduces how much the profile version matters, but it
   does not eliminate it.
3. **Every role is Sonnet, so the measuring apparatus moved with the thing being
   measured.** A weaker drafter and a weaker critic can agree with each other.
   Accept rate under this design therefore cannot separate "the questions are
   good" from "the critic did not notice". That limit is stated up front in
   whatever gets written, and it is why the gate numbers below carry the weight.

## 5. What gets measured

**Load-bearing, because these are code and do not care which model ran:**

- `precheck_record` failures per draft - Opus baseline across the whole project
  is 30 failures in 209 runs
- `precheck_candidate` failures - Opus baseline 23 in 125
- whether the SQL and retrieval a drafter claims actually re-executes to the
  numbers it recorded

**Process, comparable but model-influenced:**

- slots accepted, failed, abandoned
- adjudication rounds per slot, against the Opus 17
- candidates consumed, against the Opus 12 of 27
- findings by severity, against 1 HIGH / 8 MID / 10 LOW

**Free extra signal:** seed drift. The journals record `candidate_index` and the
drafter writes down when it discards a candidate, so how often Sonnet kept the
supplied seed against how often Opus did comes out at no extra cost.

**Cost:** `src/eval/telemetry.py` already computes dollars per accepted question
by model. It runs unchanged over the new journal and gives the Sonnet figure
directly against the Opus $20.

## 6. Cost and time

Roughly $85 to $110 of API-equivalent compute if Sonnet needs about as many
rounds as Opus did, against roughly $180 for these nine on Opus. If Sonnet needs
more rounds the gap narrows, which is itself part of the answer.

Three tabs of three slots. One to three hours of wall clock, half a day with
babysitting and the write-up of results.

## 7. Safety

- `eval/bank.jsonl` is never opened for writing. The orchestrator never touches
  it; promotion is a separate human-gated command that will not be run.
- Scratch ids cannot be promoted even deliberately, per section 3.
- Output confined to `eval/drafts/sonnet-probe/`.
- Agent-definition edits live on a scratch branch and are deleted after.
- The MCP server is read-only by construction and its DuckDB handle is read-only.
  One known wrinkle from `CLAUDE.md`: if another session's `horizon-draft` server
  holds the database open, an FTS rebuild would fail. Nothing here rebuilds FTS,
  so this run does not trigger it.

## 8. What I need from you

1. **Approve the nine slots**, or swap any. The most likely swap is `hyb-12` -
   it is the hardest slot in the project and may just fail, which is a blunt
   result. `hyb-16` is the same date and a cleaner L3.
2. **Confirm all-Sonnet** knowing the limit in 4.3. You have already said all
   Sonnet; this is recorded here so the constraint is on paper, not so it is
   asked again.
3. **Confirm the branch discipline** for the three agent edits.

On approval I build `eval/drafts/sonnet-probe/packet.json` from the nine recorded
candidate lists, write the run script, show you both, and only then launch.
