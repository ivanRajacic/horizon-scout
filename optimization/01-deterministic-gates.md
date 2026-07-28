# Plan 01 - Deterministic gates

**Kind:** code. `src/eval/mcp_server.py`, `src/cli.py`, tests.
**Status:** IMPLEMENTED 2026-07-26 (entity guard in `43c5dd5`; `snippet_chars`, `SURVIVOR-WINDOW`/`GOLD-BOUNDS`, one-reading check in `e7c5476`). Verified by the measured re-run: pending. Verified against real data 2026-07-26: `precheck_record` over all 21 bank entries reports no `SURVIVOR-WINDOW` or `GOLD-BOUNDS` failure or warning, only the expected `sd1-pilot` schema-docs provenance flags.
**Depends on:** nothing. Do this first - plan 02's skill edits reference the checks added here.

## Context

The 2026-07-25 `/question-orchestrator` run cost 1,961,828 subagent tokens over 1 h 37 m for 4
questions. Two of its biggest cost events were things a machine could have settled:

- Candidate hyb-09 c1 cost **~519k tokens** across two drafter passes, two critic rounds
  and two judge rounds, and was then abandoned. The killing defect was that "classified
  under musicology" has two executable readings against euroSciVoc - the narrow one
  returns 23 survivors with **zero gold**. One SQL query reveals this.
- The same candidate's re-scope took its filter from 13 survivors to **46**, in a
  `filter-synthesize` cell whose survivor window is 5-20. Nobody noticed the cell no
  longer fit its own filter until two rounds later.

Separately, `search_corpus` is the largest uncontrolled data channel in the pipeline: 46
calls returned 563 project entries on 2026-07-25, each carrying `best_chunk.text` verbatim
at a corpus mean of **1,437 chars** - about **809k chars ≈ 200k tokens**, roughly 1.9x the
whole `get_project_text` channel. 17 of those calls were `probe` at k=1, pulling 68 full
chunks (~98k chars) to answer 17 booleans.

This plan follows the repo's standing principle (`CLAUDE.md`: "anything with a right
answer is code, not a model") and moves four such things into code.

---

## Item 1 - `snippet_chars` on `search_corpus`

**File:** `src/eval/mcp_server.py` - `search_corpus` (def ~:339), result assembly ~:396-429.

Add `snippet_chars: int | None = None`. At the point where `entry["best_chunk"]["text"]`
is populated, truncate to `snippet_chars` characters; `0` omits the text entirely; `None`
keeps today's behaviour byte-for-byte.

Report what was cut the way `get_project_text` already does (it returns a `truncated`
key - mirror that shape rather than inventing a new one).

**Update the tool docstring.** This is not cosmetic: the docstring is what FastMCP puts in
the schema agents see, and it is how agents discovered `fields` on `get_project_text`
without any skill mentioning it (35 of 37 calls used it). State the recommended values -
`0` for a liveness probe, ~400 for a discrimination sweep, ~600 for triage where borderline
candidates get a full `get_project_text` read anyway.

Callers are set in plan 02; this item only adds the capability.

**Existing behaviour must not change when the parameter is omitted.**

---

## Item 2 - `SURVIVOR-WINDOW` and `GOLD-BOUNDS` in `precheck_record`

**File:** `src/eval/mcp_server.py` - `precheck_record` (~:616-856).

Today it runs six checks - `GOLD-SQL` (~:675), `ANSWER-COLUMNS` (~:700), `GOLD-TEXT`
(~:718), `FILTER-SURVIVORS` (~:757), `GOLD-SUBSET` (~:806), `SCHEMA-DOCS` (~:823) - and
**never reads `subtype`**. `ok` is computed at ~:849 by filtering for `"FAIL"`.

Add two **separately named** checks so `FILTER-SURVIVORS` keeps one meaning.

### `SURVIVOR-WINDOW` (WARN-only)

Place inside the `FILTER-SURVIVORS` success branch (~:789-804) where `live_survivors` is
already computed, so it checks the **live** count, not the recorded one.

Compare against `SURVIVOR_WINDOWS` imported from `src.eval.explore` - `mcp_server.py:67-68`
already imports from that module, so this is free. Windows are filter-read 2-10,
filter-synthesize 5-20, filter-compare 2-20, filter-survey 5-60.

**Emit WARN, never FAIL.** `draft-hybrid-question/SKILL.md:61` writes the guidance with a
tilde ("~5-20"), and `SURVIVOR_WINDOWS`'s own docstring frames it as a drafting window for
candidate seeds - a FAIL would forbid a legitimate 25-survivor synthesize. This requires
adding WARN to the check-status vocabulary (currently PASS/FAIL/N-A). Because `ok` filters
on `"FAIL"` (~:849), WARN is a no-op for the gate - which is the point. The WARN travels in
the returned CHECKLIST, which both the critic and the judge see
(`question-orchestrator/SKILL.md:76-77`).

### `GOLD-BOUNDS`

`|gold_project_ids|` against the subtype bound.

**Use `HYBRID_SUBTYPE_GOLD_BOUNDS` from `src.eval.bank` for hybrid** - extend the existing
`from src.eval.bank import ROUTES` at `mcp_server.py:66`. Use `LEVEL_WINDOWS` from
`explore.py` **only when `route == "vector"`**.

> **Do not apply `LEVEL_WINDOWS` to hybrid.** It is `{"L1":(1,1),"L2":(2,4),"L3":(5,None)}`
> - the *vector* rule, where level is defined by `|gold_project_ids|`. Hybrid
> `filter-compare` is L3 with |gold| in [2,4], so live bank entry **`hyb-03` would fail**.

FAIL is correct here - `validate-record` already enforces it (`bank.py:309-317`), so the
gain is *earliness*: the failure moves inside the drafter's own loop, before it emits,
instead of at slot close where it costs a pass (`question-orchestrator/SKILL.md:183`).

### While here

Collapse the duplicated `SURVIVOR_CEILING = 200` (`mcp_server.py:588` vs `explore.py:95`).

### Existing data is safe

All 11 hybrid records across `eval/bank.jsonl` and the three staged `eval/drafts/*.jsonl`
pass both checks (survivor counts 4-18, all inside their windows). **Re-confirm this as
part of verification rather than trusting it.**

---

## Item 3 - one-reading check in `precheck_candidate`

**File:** `src/eval/mcp_server.py` - `precheck_candidate` (~:859), and its shared gate
`verify_payload` in `src/eval/explore.py:858` if the check belongs there.

This is the furthest-upstream fix and the highest-leverage one: it kills a structurally
bad seed at *seed-authoring* time, so no drafter ever sees it.

When a candidate's filter references a euroSciVoc term, check whether that term's path
shape admits exactly one executable reading:

```sql
SELECT DISTINCT euroSciVocPath, euroSciVocTitle, COUNT(DISTINCT projectID)
FROM euroscivoc WHERE euroSciVocPath LIKE '%<term>%' GROUP BY 1, 2
```

One row means a leaf with one path: the title reading (`euroSciVocTitle = '<term>'`) and
the subtree reading (`euroSciVocPath LIKE '%/<term>%'`) select the identical set, so the
scope has one reading and the seed is safe. Multiple rows means the term is a branch with
siblings - the two readings diverge, and a question worded "classified under `<term>`" is
ambiguous.

Real cases: `viticulture` returns 1 row (46 projects, leaf) - safe, and the hyb-09 c2
drafter ran exactly this query and proceeded. `musicology` returns 3 (musicology 51,
ethnomusicology 20, popular music studies 17) - ambiguous, and that ambiguity cost ~519k
tokens.

**Emit as WARN with the row set attached, not FAIL.** A branch term is not unusable - it is
usable only if the question names the branch explicitly ("classified anywhere under
musicology - ethnomusicology and popular music studies included"). The explorer should be
told what it is proposing, not blocked. `corpus-explorer.md:31` already makes non-`ok`
items fixed-or-dropped, so keep this out of the `ok` computation.

**Extracting the term is the fiddly part.** Do not over-engineer: match the euroSciVoc
literal out of a `LIKE '%.../<term>%'` or `euroSciVocTitle = '<term>'` predicate, and skip
the check (N-A) when no euroSciVoc predicate is recognisable. A check that silently does
nothing on an unrecognised shape is fine; a check that guesses wrong is not.

---

## Item 4 - HTML-entity guard at the transcription boundary

**File:** `src/cli.py` - `cmd_validate_record` (~:458-489), between the `json.loads`
(~:476) and the `validate_record` call (~:481).

Agent-returned packages on 2026-07-25 contained `&lt;`, `&gt;` and `&amp;` where the source
text had `<`, `>` and `&` (`ab-run-log-2026-07-25.md:487-501`). They were unescaped by hand.
Unnoticed, corrupted text goes into the bank permanently - the only silent-and-permanent
failure mode the run exposed.

Check the **raw string**, not the parsed dict, so entities in keys and nested values are
both caught:

```
&(?:lt|gt|amp|quot|apos|nbsp|#\d+|#x[0-9a-fA-F]+);
```

Print the offending substrings **with surrounding context** - "an entity is present" is
useless without "where".

> **Not in `src/eval/bank.py`.** `_validate_record` is a schema validator that `load_bank`
> runs over the promoted bank forever. A CORDIS title legitimately carrying `&` would make
> the bank permanently unloadable by a check that is really about a transcription hazard.
> `src/cli.py:458` is exactly the boundary the run log identifies as unguarded -
> `question-orchestrator/SKILL.md:176-183` pipes the drafter's raw returned text into this command on
> stdin - and it fires while the drafter is still warm and messageable.

Zero occurrences in `eval/bank.jsonl` or any staged draft today, so nothing breaks.

---

## Verification

```bash
./.venv/Scripts/python.exe -m pytest
./.venv/Scripts/python.exe -m src.cli validate-bank
```

Then, specifically:

1. **No regression on omitted parameters.** `search_corpus` called without `snippet_chars`
   returns output byte-identical to before the change.
2. **`snippet_chars` both directions.** `search_corpus("probe", k=1, snippet_chars=0)`
   returns no chunk text and still reports all four conditions;
   `snippet_chars=400` truncates and reports what was cut.
3. **`SURVIVOR-WINDOW` both directions.** Call `precheck_record` on hyb-10 from
   `eval/drafts/draft-bank-2026-07-25.jsonl` (S=18, filter-synthesize, window 5-20):
   expect PASS and `ok: true`. Then reconstruct hyb-09 candidate 1's 46-survivor
   `filter_sql` against a `filter-synthesize` cell: expect **WARN**, and `ok` still `true`.
4. **`GOLD-BOUNDS` does not break `hyb-03`.** Run `precheck_record` on the live `hyb-03`
   (`filter-compare`, L3, |gold|=4) and confirm PASS. This is the specific trap in item 2.
5. **Every existing record still passes.** Loop all 11 hybrid records across
   `eval/bank.jsonl` and the three staged draft files through `precheck_record`; none may
   newly FAIL.
6. **One-reading check.** `precheck_candidate` on a `viticulture` seed → PASS/no warning;
   on a `musicology` seed → WARN naming the three sibling paths.
7. **Entity guard.** Pipe a record containing `&lt;` into
   `python -m src.cli validate-record -` → rejected, offending context printed. A clean
   record still validates.

Add unit tests alongside the existing validator tests (`tests/test_bank.py`,
`tests/test_batch.py`). Per `CLAUDE.md`, nothing in the suite may require a running server -
fake the transports.

## Out of scope

The `pooling_evidence` arithmetic check. It is blocked on `vec-05` (which violates it) and
on an unsettled convention - see `optimization/README.md` under "Deferred".
