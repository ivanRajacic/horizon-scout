# Three episodes from the authoring factory

Narrative companions to `factory-telemetry.md`. Every quote is verbatim from the
batch journals. Bank membership was checked for all 42 accepted records before
these three were picked.

*Scope note 2026-08-07: the denominators in this file (42 accepted, 43 slots,
167 HIGH+MID findings) count the OPUS factory only - the three
`sonnet-probe/*` runs are excluded. They are the recounted values from the
2026-08-07 telemetry fix, which merges every journal line per slot instead of
reading the last line; the finding denominator rose from the 46 the last-line
method reported. The three episodes themselves are unaffected.*

---

## 1. A caught defect - hyb-13, working-memory fellowships

**Slot.** `hyb-13`, batchI, hybrid / L2 / filter-synthesize. Round 1 FIX, round 2
ACCEPT. In the bank today, byte-identical to the fixed draft.

**The question.** Marie Sklodowska-Curie individual fellowships that study working
memory: how do the ones concerned with ageing investigate it in older adults?
Gold is three projects - NeWMaBIL, Motivageing, COMEDM - out of 18 survivors.

**The finding.** MISSED-GOLD, HIGH. The critic said the gold set was incomplete
because the question's words and the gold's filter did not select the same
projects:

> "655423 NIBSAD (MSCA-IF-EF-ST) is an MSCA individual fellowship concerned with
> ageing whose fellowship work was a working memory task run on healthy elderly.
> It is excluded from gold only because the topic clause looks at objective and
> title, where the project calls its task 'visuospatial attention and cognitive
> control'."

The evidence was executed, not asserted. A SQL sweep for MSCA-IF projects that do
not name working memory in objective or title but do in their report text,
crossed with ageing terms, returned 9 rows. The critic then quoted NIBSAD's own
report: "eighteen healthy young, eighteen healthy elderly and twelve AD patients
performed a working memory task". Its reformulated question ranked NIBSAD first
under hybrid_rerank. A second finding, AMBIGUOUS-READING MID, showed the same
crack from the other side: under the report reading a fifth project joins, and
five is L3, not the L2 cell the slot was drawn for.

**The ruling.** UPHELD on both. The judge:

> "what fails is the scope clause, which lets 'on working memory' be read as what
> the fellowship reported doing rather than what it proposed to study. That
> reading pulls in 655423 and 844246 and would carry gold to 5, out of
> filter-synthesize entirely. A bounded one-clause change - a fix, not an
> abandon."

**The fix.** One clause. "the Marie Sklodowska-Curie individual fellowships on
working memory" became "the Marie Sklodowska-Curie individual fellowships whose
stated aim is to study working memory". Gold stayed at 3, survivors stayed at 18,
the precheck stayed green. Round 2 accepted: "the question's words and its filter
now select the same thing."

**Why it matters.** Without the critic, the bank would contain a question that
scores a system wrong for naming a project whose own report says it ran a working
memory task on healthy elderly people.

---

## 2. A justified kill - hyb-14, manuscript instruments

**Slot.** `hyb-14`, batchJ, hybrid / L2 / filter-synthesize. Candidate 0
ABANDONED on the within-candidate stop rule. Candidates 1 and 2 then died at
their own drafters. This is the one FAILED slot of 43. The id is absent from the
bank.

**The question.** ERC Starting Grants working on early handwritten sources - Dead
Sea Scrolls, Elephantine papyri, medieval nautical charts. How do they apply
physical-science methods to the objects themselves, imaging or dating the
material, rather than only reading what is written on them?

**Round 1.** Three findings, all upheld. The sharpest was AMBIGUOUS-READING:

> "The question's discriminating clause is 'to recover evidence that reading their
> written content cannot supply', but ELEPHANTINE's headline result is precisely
> the recovery of written content - a defensible reader excludes it, producing a
> different answer set from gold."

GOLD-WRONG and REFERENCE-UNSUPPORTED both landed on the same soft member,
MEDEA-CHART, whose special-lighting examination exists only as intent in its
objective while the reference stated it as work performed. The drafter had
already recorded the doubt in its own history: "MEDEA-CHART is the weakest gold
member ... Kept IN because the objective is gold-eligible text." The critic
executed against that doubt and the judge priced it.

**Round 2.** The fix made the final clause a method test and dropped MEDEA-CHART
from gold. The critic came straight back on the same two classes:

> "Under the new method wording, MEDEA-CHART (714033) reads as a satisfier from
> the corpus text a retrieval system actually sees, so a defensible answer names
> three projects while gold names two."

It also formally withdrew its round-1 ELEPHANTINE objection, because the new
clause did fix that half.

**The ruling.** ABANDON:

> "Both classes upheld this round are the same classes upheld in round 1, and the
> single fix round was aimed at exactly those two, so the within-candidate stop
> rule fires and ABANDON is mandatory. The substance backs the rule: the fix
> moved the ambiguity instead of closing it. ... A cell that cannot decide whether
> declared instrumentation counts cannot produce one scoreable answer set, and
> that decision is a redesign, not a bounded edit."

The orchestrator relayed the lesson to a fresh drafter as a trap, with no verdict
and none of the dead question's content: "the textual criterion must be decidable
from ONE register of the corpus text, because abstracts state intentions and
reports state outcomes and the two often disagree."

**Why it matters.** The question was not badly worded, it was undecidable. Both
wordings were defensible and they gave different answer sets, so any score on it
would have been noise dressed as a measurement.

---

## 3. A dismissal - vec-31, CAR T cells against solid tumours

**Slot.** `vec-31`, batchB, vector / L3 / survey. One round, ACCEPT. In the bank
today, text and gold unchanged.

**The question.** "Which projects are engineering CAR T cells to attack solid
tumours?" Gold is 8 H2020 projects.

**The finding.** MISSED-GOLD, MID. The critic argued for a ninth member:

> "EURE-CART (733297) was adjudicated out as a first-in-man CAR T trial without a
> solid-tumour target, but its published final report asserts that the CAR T cell
> product it engineered controls the growth of carcinomas, a defensible ninth gold
> member."

The quote it produced is real. But the critic also reported its own
counter-evidence, and that is what sank the finding: workPerformed shows every
experiment and the whole early-terminated trial in AML and multiple myeloma only,
8 patients recruited, 2 treated.

**The ruling.** DISMISSED:

> "The critic's own executed evidence shows every EURE-CART experiment and its
> whole early-terminated trial sat in AML and multiple myeloma with no
> solid-tumour work performed, so the carcinoma line in finalResults is a
> prospective claim about CD44v6 antigen expression rather than a project
> engineering CAR T cells to attack solid tumours; the drafter's recorded
> exclusion stands, and forcing 733297 into gold would plant a wrong member, which
> is the more expensive error here."

A second MID finding on the same slot was ruled RECORDED rather than upheld - "a
strained-but-losing alternate reading". ACCEPT in round 1, no fix round spent.

**Why it matters.** If the judge rubber-stamped the critic, a project that never
ran a single solid-tumour experiment would now be a required member of the gold
answer, and every correct system would be marked incomplete.

---

## What the three show together

The same defect class means different things depending on who says it and what
the record supports. In hyb-13 a MISSED-GOLD claim was upheld and repaired by one
clause. In vec-31 a MISSED-GOLD claim was thrown out, using the critic's own
evidence against it. In hyb-14 no individual claim was wrong - the question itself
could not be made decidable, and the judge killed it on a rule rather than on
taste.

The critic could not have produced any of the three outcomes: it has no verdict,
which is exactly why it is free to attack hard, and 5 of the 167 HIGH and MID
findings were ruled wrong without costing anything. The judge could not have
produced them either: it has no tools, so every ruling is forced back onto
evidence someone else executed. The drafter's own doubt about MEDEA-CHART sat in
the record for two rounds before it cost the slot. It took an adversary to test
it and a third party to price it.
