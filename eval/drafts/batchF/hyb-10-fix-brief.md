# Handoff: redraft hyb-10 (hybrid / L2 / filter-synthesize / paraphrase)

You are picking up one rejected draft. Read this whole file first, then start.

## What happened

On 2026-07-28 a `/question-orchestrator` batch (group `batchF`) drafted three questions.
Two were promoted to the bank. The third, `hyb-10`, was **rejected at the human gate**
- not by the judge, which accepted it - because of a defect in its evidence record that
the judge's own checks could not see.

The rejected record is still on disk, untouched:

- record: `eval/drafts/batchF/draft-bank-2026-07-28.jsonl` (the `hyb-10` line)
- full report section with all its evidence: `eval/drafts/batchF/draft-report-2026-07-28.md`,
  the `## hyb-10` heading onward
- the journal of how it was drafted: `eval/drafts/batchF/draft-batch-journal-2026-07-28.jsonl`

The id `hyb-10` is free again - `next-ids` hands it out, because the bank never
received it. Reuse it.

The question as drafted was:

> "Across the Marie Sklodowska-Curie individual fellowships that set out to explain,
> in mice, how circuits at the front of the brain keep information alive across a
> short gap before it is used, what cellular mechanisms do they propose to test?"

## The defect, exactly

A hybrid question is two-sided by construction: **a structured SQL filter the user
actually states**, and a textual requirement that retrieval must satisfy inside that
filter's survivors. `filter_evidence.filter_sql` is supposed to record the first of
those, so that anyone can reproduce the survivor set from the question text alone.

The rejected record's `filter_sql` does not do that:

```sql
WITH t AS (
  SELECT DISTINCT p.id, p.acronym, p.fundingScheme,
         COALESCE(p.title,'') || ' ' || COALESCE(p.objective,'') || ' ' ||
         COALESCE(r.teaser,'') || ' ' || COALESCE(r.summary,'') || ' ' ||
         COALESCE(r.workPerformed,'') AS txt
  FROM project p LEFT JOIN report_text r ON r.projectID = p.id
  WHERE p.fundingScheme LIKE 'MSCA-IF%')
SELECT DISTINCT id, acronym, fundingScheme FROM t
WHERE (txt ILIKE '%working memory%' OR txt ILIKE '%short-term memory%'
    OR txt ILIKE '%short term memory%' OR txt ILIKE '%delay period%'
    OR txt ILIKE '%memory maintenance%' OR txt ILIKE '%holding in memory%')
  AND (txt ILIKE '%mice%' OR txt ILIKE '% mouse %'
    OR txt ILIKE '%rodent%' OR txt ILIKE '% rats %')
ORDER BY id
```

Only the first predicate - `fundingScheme LIKE 'MSCA-IF%'` - is stated by the question,
and **it matches 9,845 projects**. Everything after it is a free-text keyword device the
drafter built over concatenated title + objective + report fields. The record claims
`survivor_count: 7`; that 7 is the output of the keyword device, not of the question's
filter.

Three consequences:

1. **The survivor set is not reproducible from the question.** The question is a
   `paraphrase` question, so it deliberately never says "working memory" - it says
   "keep information alive across a short gap". A system answering it can build
   `fundingScheme LIKE 'MSCA-IF%'` and nothing more. It faces 9,845 projects, not 7.
2. **`SURVIVOR-WINDOW PASS (7 inside filter-synthesize's 5-20)` is vacuous.** It measures
   the drafter's keyword search, not the filter's pruning power. `precheck_record` cannot
   catch this: it re-executes the drafter's own `filter_sql`, so a topical device passes
   its own test by construction.
3. **The device shows signs of being fitted to the answer.** `'holding in memory'`
   matches exactly one project - 659719 AG-GF, the project a round-1 HIGH finding said
   was missing from gold - and two of the six memory arms match nothing at all. That arm
   exists to rescue that project.

For contrast, here is `hyb-09`'s filter, which is the shape to aim for - two structured
predicates, both stated in the question, nothing else:

```sql
SELECT DISTINCT p.id FROM project p JOIN euroscivoc e ON e.projectID = p.id
WHERE e.euroSciVocTitle = 'viticulture' AND p.fundingScheme = 'SME-1' ORDER BY p.id
```

## What is NOT wrong

Do not assume the whole draft is rotten. Specifically:

- **The gold set of 4 is probably correct.** The critic ran an independent completeness
  test - 18 further phrasings crossed with a widened species disjunction over all MSCA-IF
  projects - and found no satisfying project the device misses. The one project that
  escapes through the rodent conjunct (794273 M-INHIB) fails on substance.
- The topic is genuinely good: mouse frontal-cortex delay-activity work, where three
  survivors sit in the pool on vocabulary alone and must be read out.
- The reference answer's substance was verified verbatim against the four projects.

So this is a redraft of the *instrument*, not a rediscovery of the *subject*. You may
reuse the reading already done - but re-execute anything you rely on rather than trusting
the old record's numbers.

## The fix

Rebuild the question so that its filter is a predicate over **stored columns**, stated in
the question, and doing the narrowing by itself. The topical requirement stays textual and
stays the retrieval system's job.

Concretely: replace "MSCA-IF fellowships" + hidden keyword search with something like
"Marie Sklodowska-Curie fellowships classified under <euroSciVoc leaf>", so the filter
becomes `fundingScheme LIKE 'MSCA-IF%' AND euroSciVocTitle = '<leaf>'` and nothing more.

Measured starting points (`fundingScheme LIKE 'MSCA-IF%'` crossed with euroSciVoc leaves
under a neuro path, re-executed 2026-07-29 - verify them yourself, do not trust this
table):

| leaf | MSCA-IF projects |
|---|---|
| molecular neuroscience | 11 |
| computational neuroscience | 22 |
| cognitive neuroscience | 108 |
| neurobiology | 351 |

`filter-synthesize` wants **5-20 survivors**, so `molecular neuroscience` is in the window
and `computational neuroscience` is just outside it. These are starting points, not an
instruction - a different leaf, a different scheme, or a country or date filter is fine.
What is not fine is a text `ILIKE` standing in for the filter.

Changing the filter changes which projects are in scope, so **the gold set may change**.
That is expected. Re-derive it; do not carry the old four across without re-adjudicating
them against the new scope.

## Constraints - these are not negotiable

- **Cell:** route `hybrid`, level `L2`, subtype `filter-synthesize`, term_style
  `paraphrase`. Keep the paraphrase style: the bank's hybrid route is currently 5
  exact-term to 1 paraphrase, which is the worst imbalance in it. `|gold|` must land in
  2-4 (`HYBRID_SUBTYPE_GOLD_BOUNDS`) and survivors in 5-20.
- **A warning about the corpus profile.** `src/retrieval/corpus_profile.md` at cp7 prints
  `level=L3` on **every** hybrid seed. That label is wrong and you must ignore it.
  `explore.py:level_for` derives it from the topic's corpus-wide count against the VECTOR
  windows (L1=1, L2=2-4, L3=5+), but `bank.py` defines a hybrid question's level by its
  SUBTYPE - `HYBRID_SUBTYPE_LEVELS` maps filter-read to L1, filter-synthesize to L2,
  filter-compare and filter-survey to L3. Every hybrid topic is large before filtering, so
  that derivation returns L3 every time.
- **A second warning about the profile.** All twenty of cp7's hybrid seeds (`hybrid-11`
  through `hybrid-30`) define their topic with a text `ILIKE` and report a survivor count
  computed that way - including `hybrid-13`, the seed this draft came from, which handed
  the drafter "18 survivors". If you take a cp7 seed as a starting point, **rebuild its
  filter on stored columns before using its numbers.** The cp2 seeds (`hybrid-01` to
  `hybrid-10`) use euroSciVoc paths and do not have this problem.
- **Never hand-edit `eval/bank.jsonl`.** Ever. There are exactly two sanctioned append
  paths and both are human-gated. Use the drafting skill; it stages, and the user ticks.
- Read-only data access through the `horizon-draft` MCP tools. Do not add write tools.
- Both llama servers are up as of this handoff (embedder :8080, reranker :8082) and all
  four retrieval conditions return. If a `search_corpus` call errors, say so and stop
  rather than working around it.

## How to do it

Run `/draft-hybrid-question L2 filter-synthesize paraphrase` and follow that skill. It is
interactive and one question per pass; it owns grounding, pooled verification, the
`precheck_record` gate and the reviewer checklist. This brief tells you *what went wrong
and what the fix has to satisfy* - the skill tells you *how to draft*. Where they
disagree, the skill wins on procedure and this brief wins on the defect.

Do not batch, do not draft a second question, and do not promote anything. Stop at the
staged record and report back:

1. the new question text and its filter SQL, with the survivor count it really yields;
2. the gold set, and whether it changed from the old four;
3. one plain sentence on why the new filter is reproducible from the question where the
   old one was not.
