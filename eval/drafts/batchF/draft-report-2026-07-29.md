# Draft batch - 2026-07-29

Draft-bank-file: eval/drafts/batchF/draft-bank-2026-07-29.jsonl
Order: One hybrid slot, hyb-10 - a redraft of the hyb-10 rejected at the human gate on
2026-07-28. The rejected version's `filter_evidence.filter_sql` mixed the question's
stated filter (`fundingScheme LIKE 'MSCA-IF%'`, 9,845 projects) with a bespoke free-text
keyword device, so its recorded 7 survivors described the drafter's keyword search rather
than the filter, and could not be reproduced from the question text. Drafted by a separate
session against eval/drafts/batchF/hyb-10-fix-brief.md.
Tally: 1 accepted / 0 failed / 0 blocked

## hyb-10 - ACCEPTED

**Question:** "Among the Marie Sklodowska-Curie individual fellowships classified under cognitive neuroscience and hosted by an organisation in the Netherlands, how do the ones that act on the brain from outside the head, rather than only measuring it, use that to test whether a particular brain area or the pace of its activity is genuinely doing the work?"  (hybrid/L2/filter-synthesize, term_style paraphrase, well-specified)

**Filter (all three predicates are stored columns, and all three are stated in the question):**

```sql
SELECT DISTINCT p.id FROM project p JOIN euroscivoc e ON e.projectID = p.id JOIN organization o ON o.projectID = p.id WHERE p.fundingScheme LIKE 'MSCA-IF%' AND e.euroSciVocTitle = 'cognitive neuroscience' AND o.role = 'coordinator' AND o.country = 'NL' ORDER BY p.id
```

Survivors: 17 (inside filter-synthesize's 5-20 window).
Gold: [702402, 794455, 101033489] - |gold| = 3, inside filter-synthesize's 2-4 bound.

**Verification re-run independently before promotion:**

- `filter_sql` re-executed live -> exactly the 17 recorded survivor ids, no text matching anywhere.
- Gold is a subset of the survivors.
- The wording risk was checked: "hosted by an organisation in the Netherlands" reads either
  as coordinator or as any participant, and both return the identical 17 projects.
- All 17 survivors were read and adjudicated (S <= 20), so there is no pooling gap;
  the two borderlines (704992 CraNOC, 843379 ResonanceCircuits) are stated in notes - both
  perturb causally but from inside the skull.
- `validate-record` passes.

Decision: [x] APPROVE  [ ] REJECT
