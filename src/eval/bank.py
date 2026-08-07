"""Question bank schema + validator (M5, skill-authored bank v2).

One JSONL file, one record per question, authored exclusively through the
drafting skills (execution-verified, reviewer checklist, confirm-to-append).
The pre-skill smoke set lives in eval/archive/ and uses the old schema.
Validation is loud: every schema violation in the file is collected and
reported, and loading fails on any.

Schema decisions (locked 2026-07-22):
- `level` (L1|L2|L3|ADV) replaces `complexity`; ADV is off-ladder, not
  ordinal - adversarial questions are a level, not a flag.
- `subtype` is required and route-scoped; `rank` is legal at every SQL
  level, all other SQL subtypes are level-bound. The ambiguous route has no
  subtype vocabulary yet, so it carries none.
- `sql_comparison` is `ordered` iff subtype is `rank` - both directions.
- SQL ladder entries must carry `answer_columns`, `level_evidence`, and the
  `schema_docs_hash` they were authored against: the label is born verified.
- v2.1 (2026-07-23): vector ladder entries are born verified too - they must
  carry `gold_project_ids`, `term_style`, and `pooling_evidence` (the record
  of the pooled retrieval verification: all conditions run, every candidate
  adjudicated, index fingerprint). `pooling_evidence.accepted` must equal
  `gold_project_ids` - the label IS the accepted set.
- v2.2 (2026-07-23, reverses the v2 "not level-bound" note by decision):
  hybrid subtypes are level-bound - filter-read=L1, filter-synthesize=L2,
  filter-compare=L3, filter-survey=L3 - with per-subtype gold-count bounds
  (read=1, synthesize/compare 2-4, survey >=5). Hybrid ladder entries carry
  `gold_project_ids`, `term_style`, `pooling_evidence` (the SCOPED pooled
  record), and `filter_evidence` (executed filter SQL, enumerated survivor
  ids + true count, schema_docs hash); gold must be a subset of survivors.
- v2.3 (2026-08-04): ADV entries are born verified too, on the same terms.
  `absence_evidence` is the typed, re-executable proof - a list of
  {sql, expect, key_result} in the shape `explore.py` already uses, so
  `precheck_record` can re-run every claim instead of reading a paragraph
  of `notes`. `twin_id` names the answerable bank question the adversarial
  one was derived from: it is the control a refusal-only set cannot supply,
  and its gold is the near-miss proof (it returns rows, the perturbation
  returns none). Required for zero-match and false-presupposition, optional
  for data-absent, forbidden for unanswerable, which derives from nothing.

Route vocabulary follows the plan doc (sql | vector | hybrid | ambiguous);
the runtime router calls the hybrid mode "scoped" - ROUTE_TO_MODE is the one
place that mapping lives.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

ROUTES = ("sql", "vector", "hybrid", "ambiguous")
ROUTE_TO_MODE = {"sql": "sql", "vector": "vector", "hybrid": "scoped"}
LEVELS = ("L1", "L2", "L3", "ADV")
LADDER = ("L1", "L2", "L3")
SPECIFICATIONS = ("well-specified", "underspecified")
TERM_STYLES = ("exact-term", "paraphrase")
SQL_COMPARISONS = ("set", "ordered")

# Subtype vocabularies. For sql, vector, and hybrid the value is the set of
# levels the subtype is legal at; ADV subtypes apply at level=ADV on any
# route.
SQL_SUBTYPE_LEVELS = {
    "lookup": ("L1",), "aggregate": ("L1",),
    "join-lookup": ("L2",), "value-grounded": ("L2",),
    "grouped-aggregate": ("L2",),
    "multi-join": ("L3",), "trap": ("L3",),
    "rank": ("L1", "L2", "L3"),
}
VECTOR_SUBTYPE_LEVELS = {
    "identify": ("L1",), "detail": ("L1",),
    "comparison": ("L2",), "synthesis": ("L2",),
    "survey": ("L3",),
}
HYBRID_SUBTYPE_LEVELS = {
    "filter-read": ("L1",), "filter-synthesize": ("L2",),
    "filter-compare": ("L3",), "filter-survey": ("L3",),
}
ADV_SUBTYPES = ("zero-match", "false-presupposition", "data-absent",
                "unanswerable")

# ADV subtypes that must name the answerable question they were derived from.
# data-absent may carry one (same subject, missing facet) but need not;
# unanswerable derives from nothing and must not.
TWINNED_ADV_SUBTYPES = ("zero-match", "false-presupposition")

# Gold-count bounds per hybrid subtype (hybrid level is defined by what the
# filter does to the evidence problem, so the bound hangs off the subtype,
# not the level). Value = (min, max); None = unbounded.
HYBRID_SUBTYPE_GOLD_BOUNDS = {
    "filter-read": (1, 1), "filter-synthesize": (2, 4),
    "filter-compare": (2, 4), "filter-survey": (5, None),
}

# Required keys of the vector/hybrid ladder's pooled-verification record.
POOLING_EVIDENCE_KEYS = ("conditions_run", "k", "pooled_candidate_count",
                         "accepted", "rejected_count", "index_fingerprint")

# Required keys of the hybrid ladder's filter-side record.
FILTER_EVIDENCE_KEYS = ("filter_sql", "survivor_count", "survivor_ids",
                        "schema_docs_hash")

# Required keys of one ADV absence-proof entry, and what an entry may claim:
# "zero" = this query must come back empty, "rows" = it must come back full.
# Same {sql, key_result} shape as explore.py's evidence, for the same reason:
# a claim nothing can re-execute is a claim nobody checked. A "zero" query may
# be written either way - no rows, or a single-cell COUNT returning 0; both
# read as empty to precheck_record, and nothing else does.
ABSENCE_EVIDENCE_KEYS = ("sql", "expect", "key_result")
ABSENCE_EXPECTATIONS = ("zero", "rows")

# The minimum each ADV subtype's proof must contain. zero-match and
# data-absent turn on something being empty; false-presupposition turns on the
# refuting result being FULL (the data says X, the premise says not-X - "the
# data is silent" is data-absent wearing the wrong label). unanswerable has no
# single shape: its probes are per-interpretation, so only non-emptiness holds.
ADV_ABSENCE_REQUIRED = {
    "zero-match": ("zero",),
    "data-absent": ("zero",),
    "false-presupposition": ("rows",),
    "unanswerable": (),
}

# Only these keys may appear in a bank record - typos never pass silently.
KNOWN_FIELDS = frozenset({
    "question_id", "text", "expected_route", "acceptable_routes",
    "level", "subtype", "specification", "term_style", "compositional",
    "gold_sql", "sql_comparison", "answer_columns", "level_evidence",
    "gold_project_ids", "pooling_evidence", "filter_evidence",
    "twin_id", "absence_evidence",
    "reference_answer", "schema_docs_hash", "reviewer_override", "notes",
})


class BankValidationError(ValueError):
    """Raised with every violation found in the file, not just the first."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(
            f"{len(errors)} bank validation error(s):\n  " + "\n  ".join(errors))


@dataclass
class BankQuestion:
    question_id: str
    text: str
    expected_route: str
    level: str
    subtype: str | None = None
    specification: str = "well-specified"
    acceptable_routes: list[str] = field(default_factory=list)
    term_style: str | None = None
    compositional: bool = False
    gold_sql: str | None = None
    sql_comparison: str = "set"
    answer_columns: list[str] | None = None
    level_evidence: dict | None = None
    gold_project_ids: list[int] | None = None
    pooling_evidence: dict | None = None
    filter_evidence: dict | None = None
    twin_id: str | None = None
    absence_evidence: list | None = None
    reference_answer: str | None = None
    schema_docs_hash: str | None = None
    reviewer_override: bool = False
    notes: str | None = None

    @property
    def is_topical(self) -> bool:
        """Vector/hybrid questions (incl. ambiguous ones that may go there)."""
        routes = {self.expected_route, *self.acceptable_routes}
        return bool(routes & {"vector", "hybrid"})

    @property
    def is_adversarial(self) -> bool:
        """ADV questions bypass RAGAS and go to the rubric-judge overlay."""
        return self.level == "ADV"


def _validate_subtype(route, level, subtype, bad):
    """Route-scoped vocabulary + level binding. Assumes level is valid."""
    if level == "ADV":
        if subtype not in ADV_SUBTYPES:
            bad(f"level=ADV requires subtype in {ADV_SUBTYPES}, "
                f"got {subtype!r}")
        return
    if route == "ambiguous":
        if subtype is not None:
            bad("ambiguous route carries no subtype "
                "(no vocabulary defined yet)")
        return
    if subtype is None:
        bad(f"subtype is required for route={route}")
        return
    if route == "sql":
        allowed = SQL_SUBTYPE_LEVELS.get(subtype)
        if allowed is None:
            bad(f"sql subtype must be one of "
                f"{tuple(SQL_SUBTYPE_LEVELS)}, got {subtype!r}")
        elif level not in allowed:
            bad(f"sql subtype {subtype!r} is only legal at "
                f"{'/'.join(allowed)}, got {level}")
    elif route == "vector":
        allowed = VECTOR_SUBTYPE_LEVELS.get(subtype)
        if allowed is None:
            bad(f"vector subtype must be one of "
                f"{tuple(VECTOR_SUBTYPE_LEVELS)}, got {subtype!r}")
        elif level not in allowed:
            bad(f"vector subtype {subtype!r} is only legal at "
                f"{'/'.join(allowed)}, got {level}")
    elif route == "hybrid":
        allowed = HYBRID_SUBTYPE_LEVELS.get(subtype)
        if allowed is None:
            bad(f"hybrid subtype must be one of "
                f"{tuple(HYBRID_SUBTYPE_LEVELS)}, got {subtype!r}")
        elif level not in allowed:
            bad(f"hybrid subtype {subtype!r} is only legal at "
                f"{'/'.join(allowed)}, got {level}")


def _validate_adversarial(obj, qid, level, subtype, bad):
    """The two ADV-only fields, both level-scoped so the ladder is untouched.

    Whether `twin_id` actually resolves to a question is a cross-record check
    and lives in `load_bank` - one record cannot see the rest of the bank.
    """
    adv = level == "ADV"

    twin_id = obj.get("twin_id")
    if twin_id is not None:
        if not adv:
            bad("twin_id is only legal on level=ADV questions")
        if not isinstance(twin_id, str) or not twin_id.strip():
            bad("twin_id must be a non-empty string when present")
        elif twin_id == qid:
            bad("twin_id must not point at the question itself")
        if adv and subtype == "unanswerable":
            bad("unanswerable questions carry no twin_id - there is no "
                "answerable question they are a perturbation of")
    elif adv and subtype in TWINNED_ADV_SUBTYPES:
        bad(f"ADV subtype {subtype!r} requires twin_id - the answerable bank "
            "question it was derived from, which is both the control and the "
            "near-miss proof")

    absence = obj.get("absence_evidence")
    if absence is None:
        if adv:
            bad("ADV questions require absence_evidence - the typed, "
                "re-executable proof; an absence in prose is an assertion")
        return
    if not adv:
        bad("absence_evidence is only legal on level=ADV questions")
    if not isinstance(absence, list) or not absence:
        bad("absence_evidence must be a non-empty list of "
            "{sql, expect, key_result} objects")
        return

    seen_expects = set()
    for i, item in enumerate(absence):
        at = f"absence_evidence[{i}]"
        if not isinstance(item, dict):
            bad(f"{at} must be an object")
            continue
        missing = [key for key in ABSENCE_EVIDENCE_KEYS if key not in item]
        if missing:
            bad(f"{at} missing keys: {', '.join(missing)}")
        sql = item.get("sql")
        if (not isinstance(sql, str) or not sql.strip()
                or sql.strip().split()[0].upper() not in ("SELECT", "WITH")):
            bad(f"{at}.sql must be a single SELECT (or WITH...SELECT)")
        expect = item.get("expect")
        if expect not in ABSENCE_EXPECTATIONS:
            bad(f"{at}.expect must be one of {ABSENCE_EXPECTATIONS}, "
                f"got {expect!r}")
        else:
            seen_expects.add(expect)
        key_result = item.get("key_result")
        if not isinstance(key_result, str) or not key_result.strip():
            bad(f"{at}.key_result must be a non-empty string - what the query "
                "is proving, in words, so a failure is readable cold")

    for required in ADV_ABSENCE_REQUIRED.get(subtype, ()) if adv else ():
        if required not in seen_expects:
            bad(f"ADV subtype {subtype!r} requires at least one "
                f"absence_evidence entry with expect={required!r}")


def _validate_record(obj: dict, where: str, errs: list[str],
                     *, strict: bool = True) -> BankQuestion | None:
    for key in obj:
        if key not in KNOWN_FIELDS:
            errs.append(f"{where}: unknown field {key!r}")

    def bad(msg):
        errs.append(f"{where}: {msg}")

    qid = obj.get("question_id")
    if not isinstance(qid, str) or not qid.strip():
        bad("question_id must be a non-empty string")
        return None
    where = f"{where} [{qid}]"

    text = obj.get("text")
    if not isinstance(text, str) or not text.strip():
        bad("text must be a non-empty string")

    route = obj.get("expected_route")
    if route not in ROUTES:
        bad(f"expected_route must be one of {ROUTES}, got {route!r}")

    acceptable = obj.get("acceptable_routes", [])
    if route == "ambiguous":
        if (not isinstance(acceptable, list) or len(acceptable) < 2
                or not set(acceptable) <= {"sql", "vector", "hybrid"}):
            bad("ambiguous route requires acceptable_routes: >=2 of "
                "sql/vector/hybrid")
    elif acceptable:
        bad("acceptable_routes is only allowed with expected_route=ambiguous")

    level = obj.get("level")
    if level not in LEVELS:
        bad(f"level must be one of {LEVELS}, got {level!r}")

    subtype = obj.get("subtype")
    if subtype is not None and not isinstance(subtype, str):
        bad("subtype must be a string when present")
        subtype = None
    if route in ROUTES and level in LEVELS:
        _validate_subtype(route, level, subtype, bad)

    if obj.get("specification", "well-specified") not in SPECIFICATIONS:
        bad(f"specification must be one of {SPECIFICATIONS}")

    if not isinstance(obj.get("compositional", False), bool):
        bad("compositional must be a boolean")

    if not isinstance(obj.get("reviewer_override", False), bool):
        bad("reviewer_override must be a boolean")

    term_style = obj.get("term_style")
    if term_style is not None:
        if term_style not in TERM_STYLES:
            bad(f"term_style must be one of {TERM_STYLES}")
        routes_here = {route, *(acceptable if isinstance(acceptable, list) else [])}
        if not routes_here & {"vector", "hybrid"}:
            bad("term_style only applies to topical (vector/hybrid) questions")

    gold_sql = obj.get("gold_sql")
    if gold_sql is not None:
        if not isinstance(gold_sql, str) or not gold_sql.strip():
            bad("gold_sql must be a non-empty string when present")
        elif gold_sql.strip().split()[0].upper() not in ("SELECT", "WITH"):
            bad("gold_sql must be a single SELECT (or WITH...SELECT)")
    if obj.get("sql_comparison", "set") not in SQL_COMPARISONS:
        bad(f"sql_comparison must be one of {SQL_COMPARISONS}")
    if "sql_comparison" in obj and gold_sql is None:
        bad("sql_comparison requires gold_sql")
    if gold_sql is not None and subtype is not None:
        ordered = obj.get("sql_comparison", "set") == "ordered"
        if (subtype == "rank") != ordered:
            bad("sql_comparison must be 'ordered' iff subtype is 'rank' "
                f"(got subtype={subtype!r}, "
                f"sql_comparison={obj.get('sql_comparison', 'set')!r})")

    # Ladder entries are born verified: SQL carries pinned columns, computed
    # evidence, and the schema_docs hash; vector carries gold ids, term_style,
    # and the pooled-verification record; hybrid additionally carries the
    # filter-side record (executed filter SQL + enumerated survivors).
    sql_ladder = route == "sql" and level in LADDER
    vector_ladder = route == "vector" and level in LADDER
    hybrid_ladder = route == "hybrid" and level in LADDER
    answer_columns = obj.get("answer_columns")
    if answer_columns is not None:
        if gold_sql is None:
            bad("answer_columns requires gold_sql")
        if (not isinstance(answer_columns, list) or not answer_columns
                or any(not isinstance(c, str) or not c.strip()
                       for c in answer_columns)):
            bad("answer_columns must be a non-empty list of non-empty strings")
    elif sql_ladder:
        bad("sql questions require answer_columns")

    level_evidence = obj.get("level_evidence")
    if level_evidence is not None:
        if gold_sql is None:
            bad("level_evidence requires gold_sql")
        if not isinstance(level_evidence, dict):
            bad("level_evidence must be an object")
    elif sql_ladder:
        bad("sql questions require level_evidence")

    schema_docs_hash = obj.get("schema_docs_hash")
    if schema_docs_hash is not None and (
            not isinstance(schema_docs_hash, str)
            or not schema_docs_hash.strip()):
        bad("schema_docs_hash must be a non-empty string when present")
    elif schema_docs_hash is None and sql_ladder:
        bad("sql questions require schema_docs_hash")

    gold_ids = obj.get("gold_project_ids")
    if gold_ids is not None:
        if (not isinstance(gold_ids, list)
                or any(not isinstance(i, int) for i in gold_ids)):
            bad("gold_project_ids must be a list of integers")
        elif len(set(gold_ids)) != len(gold_ids):
            bad("gold_project_ids contains duplicates")
        elif subtype == "zero-match" and gold_ids:
            bad("zero-match questions must have empty gold_project_ids")
        elif route == "vector" and level in LADDER:
            # Vector-route level is DEFINED by |gold_project_ids| (§1).
            # No emptiness guard here on purpose: [] must FAIL the L1 bound
            # (n=0 != 1), not skip the check - only zero-match, caught
            # above, may be empty, and that subtype is ADV-level anyway.
            n = len(gold_ids)
            want = {"L1": n == 1, "L2": 2 <= n <= 4, "L3": n >= 5}
            if not want[level]:
                bad(f"vector {level} requires |gold_project_ids| "
                    f"{'== 1' if level == 'L1' else 'in [2,4]' if level == 'L2' else '>= 5'},"
                    f" got {n}")
        elif hybrid_ladder and subtype in HYBRID_SUBTYPE_GOLD_BOUNDS:
            # Hybrid gold bounds hang off the subtype, not the level.
            lo, hi = HYBRID_SUBTYPE_GOLD_BOUNDS[subtype]
            n = len(gold_ids)
            if n < lo or (hi is not None and n > hi):
                want = f"== {lo}" if lo == hi else (
                    f">= {lo}" if hi is None else f"in [{lo},{hi}]")
                bad(f"hybrid subtype {subtype!r} requires "
                    f"|gold_project_ids| {want}, got {n}")
    elif vector_ladder or hybrid_ladder:
        bad(f"{route} questions require gold_project_ids")

    if (vector_ladder or hybrid_ladder) and term_style is None:
        bad(f"{route} questions require term_style")

    pooling_evidence = obj.get("pooling_evidence")
    if pooling_evidence is not None:
        if gold_ids is None:
            bad("pooling_evidence requires gold_project_ids")
        if not isinstance(pooling_evidence, dict):
            bad("pooling_evidence must be an object")
        else:
            missing = [key for key in POOLING_EVIDENCE_KEYS
                       if key not in pooling_evidence]
            if missing:
                bad(f"pooling_evidence missing keys: {', '.join(missing)}")
            accepted = pooling_evidence.get("accepted")
            if (not isinstance(accepted, list)
                    or any(not isinstance(i, int) for i in accepted)):
                bad("pooling_evidence.accepted must be a list of integers")
            elif (isinstance(gold_ids, list)
                    and set(accepted) != set(gold_ids)):
                bad("pooling_evidence.accepted must equal gold_project_ids "
                    "(the label IS the accepted set)")
    elif vector_ladder or hybrid_ladder:
        bad(f"{route} questions require pooling_evidence")

    filter_evidence = obj.get("filter_evidence")
    if filter_evidence is not None:
        if route != "hybrid":
            bad("filter_evidence is only legal on hybrid questions")
        if not isinstance(filter_evidence, dict):
            bad("filter_evidence must be an object")
        else:
            missing = [key for key in FILTER_EVIDENCE_KEYS
                       if key not in filter_evidence]
            if missing:
                bad(f"filter_evidence missing keys: {', '.join(missing)}")
            fsql = filter_evidence.get("filter_sql")
            if (not isinstance(fsql, str) or not fsql.strip()
                    or fsql.strip().split()[0].upper()
                    not in ("SELECT", "WITH")):
                bad("filter_evidence.filter_sql must be a single SELECT "
                    "(or WITH...SELECT)")
            survivors = filter_evidence.get("survivor_ids")
            if (not isinstance(survivors, list)
                    or any(not isinstance(i, int) for i in survivors)):
                bad("filter_evidence.survivor_ids must be a list of integers")
            else:
                if len(set(survivors)) != len(survivors):
                    bad("filter_evidence.survivor_ids contains duplicates")
                if filter_evidence.get("survivor_count") != len(survivors):
                    bad("filter_evidence.survivor_count must equal "
                        "len(survivor_ids)")
                if (isinstance(gold_ids, list)
                        and not set(gold_ids) <= set(survivors)):
                    bad("gold_project_ids must be a subset of "
                        "filter_evidence.survivor_ids (gold outside the "
                        "filter is a contradiction)")
    elif hybrid_ladder:
        bad("hybrid questions require filter_evidence")

    _validate_adversarial(obj, qid, level, subtype, bad)

    for key in ("reference_answer", "notes"):
        v = obj.get(key)
        if v is not None and (not isinstance(v, str) or not v.strip()):
            bad(f"{key} must be a non-empty string when present")

    # strict=False builds the question anyway and leaves `errs` populated for
    # the caller to record. It exists for ONE job: judging a bank that carries
    # a known, deliberate schema gap - the 2026-08-06 mixed Opus/Sonnet file,
    # whose two Sonnet vector records have no pooling_evidence. Never use it on
    # eval/bank.jsonl; a study number off an unvalidated bank is not a number.
    if errs and strict:
        return None
    return BankQuestion(
        question_id=qid, text=text, expected_route=route,
        level=level, subtype=subtype,
        specification=obj.get("specification", "well-specified"),
        acceptable_routes=list(acceptable) if route == "ambiguous" else [],
        term_style=term_style,
        compositional=obj.get("compositional", False),
        gold_sql=gold_sql,
        sql_comparison=obj.get("sql_comparison", "set"),
        answer_columns=answer_columns,
        level_evidence=level_evidence,
        gold_project_ids=gold_ids,
        pooling_evidence=pooling_evidence,
        filter_evidence=filter_evidence,
        twin_id=obj.get("twin_id"),
        absence_evidence=obj.get("absence_evidence"),
        reference_answer=obj.get("reference_answer"),
        schema_docs_hash=schema_docs_hash,
        reviewer_override=obj.get("reviewer_override", False),
        notes=obj.get("notes"))


def validate_record(obj: dict, where: str = "record") -> list[str]:
    """Schema-validate ONE record; return every violation (empty = valid).

    The single-record entry point behind `python -m src.cli validate-record`,
    used to gate one drafted slot at close time. Same rules as `load_bank`
    applies per line - only the cross-record checks are out of scope, since one
    record cannot collide with itself (duplicate ids) or see the question it
    was derived from (whether `twin_id` resolves to a real, answerable entry).
    Both are caught at promotion, where the whole bank is in hand.
    """
    if not isinstance(obj, dict):
        return [f"{where}: record must be a JSON object"]
    errors: list[str] = []
    _validate_record(obj, where, errors)
    return errors


def load_bank(path: str | Path, *, strict: bool = True) -> list[BankQuestion]:
    """Parse + validate a bank JSONL. Raises BankValidationError listing EVERY
    violation in the file; returns the questions only if all lines are clean.

    `strict=False` is an escape hatch, not an option: it loads records that
    fail validation and returns them anyway. Use `load_bank_with_errors` when
    you need the violations back to record them - a run that bypasses the
    validator MUST say so in its own metadata.
    """
    questions, errors = load_bank_with_errors(path, strict=strict)
    if errors and strict:
        raise BankValidationError(errors)
    return questions


def load_bank_with_errors(path: str | Path, *, strict: bool = True
                          ) -> tuple[list[BankQuestion], list[str]]:
    """(questions, violations). Never raises. With `strict=True` a violating
    record is dropped; with `strict=False` it is kept and still reported."""
    errors: list[str] = []
    questions: list[BankQuestion] = []
    seen_ids: set[str] = set()
    for lineno, line in enumerate(
            Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        where = f"line {lineno}"
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"{where}: invalid JSON ({e})")
            continue
        if not isinstance(obj, dict):
            errors.append(f"{where}: record must be a JSON object")
            continue
        line_errs: list[str] = []
        q = _validate_record(obj, where, line_errs, strict=strict)
        errors.extend(line_errs)
        if q is None:
            continue
        if q.question_id in seen_ids:
            errors.append(f"{where}: duplicate question_id {q.question_id!r}")
            continue
        seen_ids.add(q.question_id)
        questions.append(q)

    # Cross-record: an adversarial question's twin must be a real question in
    # this bank, and an answerable one. A twin that is itself ADV is two
    # refusals pointing at each other, which controls for nothing.
    by_id = {q.question_id: q for q in questions}
    for q in questions:
        if q.twin_id is None:
            continue
        parent = by_id.get(q.twin_id)
        if parent is None:
            errors.append(f"[{q.question_id}]: twin_id {q.twin_id!r} is not a "
                          "question in this bank")
        elif parent.level == "ADV":
            errors.append(f"[{q.question_id}]: twin_id {q.twin_id!r} is itself "
                          "an ADV question - the twin is the answerable "
                          "control, so it must be on the ladder")

    return questions, errors


def bank_summary(questions: list[BankQuestion]) -> str:
    """Route x level counts plus label coverage - the allocation view."""
    from collections import Counter

    cell = Counter((q.expected_route, q.level) for q in questions)
    lines = ["route x level:"]
    for route in ROUTES:
        row = "  ".join(f"{lv}={cell.get((route, lv), 0)}" for lv in LEVELS)
        lines.append(f"  {route:10s} {row}  total="
                     f"{sum(cell.get((route, lv), 0) for lv in LEVELS)}")
    n = len(questions)
    subtypes = Counter(q.subtype for q in questions if q.subtype)
    labels = {
        "gold_sql": sum(q.gold_sql is not None for q in questions),
        "answer_columns": sum(q.answer_columns is not None for q in questions),
        "gold_project_ids": sum(q.gold_project_ids is not None for q in questions),
        "pooling_evidence": sum(q.pooling_evidence is not None for q in questions),
        "filter_evidence": sum(q.filter_evidence is not None for q in questions),
        "absence_evidence": sum(q.absence_evidence is not None for q in questions),
        "twinned": sum(q.twin_id is not None for q in questions),
        "reference_answer": sum(q.reference_answer is not None for q in questions),
        "term_style": sum(q.term_style is not None for q in questions),
        "underspecified": sum(q.specification == "underspecified" for q in questions),
        "compositional": sum(q.compositional for q in questions),
        "reviewer_override": sum(q.reviewer_override for q in questions),
    }
    lines.append(f"labels ({n} questions): "
                 + ", ".join(f"{k}={v}" for k, v in labels.items()))
    if subtypes:
        lines.append("subtypes: " + ", ".join(
            f"{k}={v}" for k, v in sorted(subtypes.items())))
    return "\n".join(lines)
