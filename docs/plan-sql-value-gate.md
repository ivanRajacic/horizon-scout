# Plan: the SQL value gate (approved direction, not yet built)

Written 2026-08-05, after the narrow-v3 constraint-plumbing change. Self-contained:
a fresh session can implement from this file alone.

## State as of writing

Today's changes are implemented and tested (604 passing) but NOT committed:

- Guardrail fixes in `src/retrieval/sql_path.py`: comment stripping before
  validation, string-literal-aware checks, `replace_limit` for the narrowing
  path, `prompt_label`.
- `narrow-v3` in `src/retrieval/scoped.py`: the narrowing model translates a
  constraint list extracted by the router (`Router.extract`, split out of
  `route()`); empty list skips the model entirely; euroscivoc allowed with the
  path-prefix idiom; a euroSciVoc term-existence gate; `uses_subject_filter`
  blanks euroscivoc columns/aliases; new trace fields `constraints`,
  `constraints_source`, `dead_terms`.
- Wiring in `src/ask.py` (router condition reuses its decision's list,
  always-hybrid extracts inside the scoped path) and record fields in
  `src/eval/run.py`.

Two measurement runs exist, both always-hybrid `--no-judge` over the 11 hybrid
questions plus 3 hybrid-route adversarials:

- `data/runs/hyb-filterfix-20260805` (after guardrail fixes, before plumbing):
  filter containment 8/11, hit@10 7/11, one false zero_match refusal (hyb-09).
- `data/runs/hyb-plumbing-20260805` (after plumbing): hit@10 9/11, five filters
  exactly or nearly the bank's own sets (hyb-01: 7, hyb-03: 4, hyb-07: 15,
  hyb-09-corrected would be 14, hyb-10: 19 vs 17). TWO false zero_match
  refusals remain: hyb-06 and hyb-09.

## The problem this plan fixes

Both remaining false refusals are the same defect: the narrowing model wrote a
VALUE that does not exist in an enumerated column, the filter matched zero
rows, and the zero-match policy turned the misspelling into a confident
refusal.

- hyb-09: `p.fundingScheme = 'SME Instrument phase 1'`. The stored code is
  `SME-1`. Corrected filter = 14 projects, the bank's exact set.
- hyb-06: `o.activityType = 'ANTIBIOTIC-RESISTANT BACTERIAL INFECTIONS'`.
  Legal values are HES, PRC, REC, PUB, OTH. Dropping the dead clause leaves
  graphene x Sweden = 18 projects, gold inside.

The cheat sheet does not solve this. `schema_docs.md` is pasted into the
narrowing prompt and already lists `SME-1` (top schemes) and ALL FIVE
activityType values. The model wrote the dead values anyway - the gen seat runs
at temperature 1.0 and prompt instructions lower error rates without zeroing
them. The fix is deterministic code that checks what the model wrote, the same
deterministic-first rule the repo already follows.

The euroSciVoc term gate built today is exactly this pattern for one dimension.
This plan generalizes it.

## Design

One value gate over every column whose legal values are a closed set. The
model does not call a tool - the narrowing transport is a plain completion
API. The "tool" is the existing write -> check -> feedback -> rewrite loop:
code checks the SQL after the model writes it and feeds a corrective re-ask
when a value is dead.

Guarded dimensions (all verified 2026-08-05):

| column                    | distinct values | lookup                       |
|---------------------------|-----------------|------------------------------|
| project.fundingScheme     | 56              | equality / pattern count     |
| project.status            | 3               | equality / pattern count     |
| organization.activityType | 5               | equality / pattern count     |
| organization.role         | 5               | equality / pattern count     |
| organization.country      | 178             | equality / pattern count     |
| euroscivoc Title/Path     | 1,053 titles    | already gated (term gate)    |

Unguarded and out of scope: dates and money (continuous - no "nonexistent
value" exists), and any column the narrowing prompt bans anyway.

Mechanics, all in `src/retrieval/scoped.py`:

1. **Collect.** `filter_literals(sql) -> [(column, op, literal)]` - a regex
   per guarded column pulls string literals compared with `=`, `LIKE`,
   `ILIKE`. Reuse the alias-blanking approach already in `_blank_euroscivoc`
   where qualification matters. Fold the existing `euroscivoc_terms` into this
   collection so there is ONE gate, not two siblings.
2. **Check.** One read-only lookup per literal via `narrow.execute_trusted`:
   `=` checks `count(*) WHERE col = value`; a pattern checks
   `count(*) WHERE col LIKE pattern` (so `LIKE 'MSCA%'` passes). Dead = 0.
   Escape single quotes by doubling.
3. **Correct, then drop.** On any dead value, ONE re-ask. The hint names each
   dead value and shows real candidates: up to 10 values from
   `col ILIKE '%<fragment>%'` (for hyb-09 this returns SME-1, SME-2, SME,
   SME-2b); when nothing matches, the 10 most common values of that column.
   Constraint-list mode rebuilds the user message with the hint appended;
   raw mode appends to the question. If the re-ask is clean, use it. If it
   still contains a dead value (or fails, or trips the subject filter), drop
   the constraints owning the dead values; if constraints remain, one final
   narrowing on the reduced list is NOT taken (call budget) - instead go
   unfiltered.
4. **Degrade label.** `degraded="value_not_found"`, replacing
   `term_not_found` (unify - do not keep two labels for one behavior). Only
   run `hyb-plumbing-20260805` ever recorded the old label; disclose the
   rename in the write-up. `ask.py`'s degrade note changes wording to "a
   filter value does not exist in the database".
5. **Trace.** `dead_values: [[column, value], ...]`, `value_reasked: bool`
   replace `dead_terms` / `term_reasked`. Same placement as today: the gate
   runs after `sql_result.ok` and the subject-filter check, BEFORE the
   zero-ids branch, so a dead value can never become a zero_match refusal. A
   genuine empty intersection of all-valid values still refuses - the gate
   checks each value against its own column, never the combined result.

Call budget per question stays bounded: initial narrowing + at most one
subject re-ask + at most one value re-ask = 3 narrowing calls worst case.

Companion change (small, separate commit): list all 56 fundingScheme values in
`src/retrieval/schema_docs.md` instead of the top 11. Bump
`SCHEMA_DOCS_VERSION` (sd2 -> sd3). Bank entries keep their authored-against
hash - that is provenance, not staleness; the validator never re-checks it.
This improves the odds; the gate enforces the floor.

## Ceiling - what the gate cannot catch

- A value that exists but is wrong (ERC-COG written where ERC-STG was meant).
- An extraction hallucination that happens to name a valid value.
- Anything on dates and amounts.

## Tests (fake transports, like everything else)

- Dead equality value -> re-ask hint contains the candidates -> corrected SQL
  used (hyb-09 shape).
- Dead value with no candidates -> constraint dropped -> remaining filter used
  or unfiltered degrade (hyb-06 shape).
- `LIKE 'MSCA%'` on a live pattern passes without a re-ask.
- Only constraint dead and re-ask dirty -> `status/degraded = value_not_found`,
  unfiltered search ran, note prefixed by ask.py.
- All-valid values with empty intersection -> still `zero_match`.
- Quote escaping (`value with 'quote'`) in the lookup.
- Trace fields present; `term_not_found` no longer emitted anywhere.
- Extend the `GateSql` fake in `tests/test_m4_pipeline.py` to answer per-column
  lookups.

## Verification run

One always-hybrid `--no-judge` re-run of the same 14 questions (routes
hybrid), new run id. Read as a group (temperature 1.0 - single questions
flip between runs; hyb-06 was clean in filterfix and broken in plumbing):

- zero_match on answerable questions: 2 -> 0. Any remaining refusal must be a
  genuine empty intersection.
- hit@10: 9/11 -> target 10/11 (hyb-13/hyb-16 gold filters need the banned
  `objective ILIKE`; they pass via wide-or-no filter or not at all).
- hyb-09's filter should be 14; hyb-06's about 18.
- adv-06 / adv-09 must still refuse; adv-03's behavior needs a judged run
  eventually - flagged open since the plumbing run.

## Bookkeeping

Pre-baseline wiring, same class as the router rebuild and the plumbing -
disclosed in the write-up, no frozen asset touched (chunking, retrieval stack,
judge all untouched). `NARROW_PROMPT_VERSION` is unchanged by the gate itself
(the gate is code); it bumps only if hint wording is added INTO the system
prompt, which this plan does not do - hints ride in the user message.
