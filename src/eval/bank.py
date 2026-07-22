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

# Subtype vocabularies. For sql and vector the value is the set of levels
# the subtype is legal at; hybrid subtypes are not level-bound; ADV subtypes
# apply at level=ADV on any route.
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
HYBRID_SUBTYPES = ("filter-read", "filter-synthesize", "filter-compare",
                   "filter-survey")
ADV_SUBTYPES = ("zero-match", "false-presupposition", "data-absent",
                "unanswerable")

# Only these keys may appear in a bank record - typos never pass silently.
KNOWN_FIELDS = frozenset({
    "question_id", "text", "expected_route", "acceptable_routes",
    "level", "subtype", "specification", "term_style", "compositional",
    "gold_sql", "sql_comparison", "answer_columns", "level_evidence",
    "gold_project_ids", "reference_answer", "schema_docs_hash",
    "reviewer_override", "notes",
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
        if subtype not in HYBRID_SUBTYPES:
            bad(f"hybrid subtype must be one of {HYBRID_SUBTYPES}, "
                f"got {subtype!r}")


def _validate_record(obj: dict, where: str, errs: list[str]) -> BankQuestion | None:
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

    # SQL ladder entries are born verified: pinned columns, computed
    # evidence, and the schema_docs hash they were authored against.
    sql_ladder = route == "sql" and level in LADDER
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
        elif route == "vector" and level in LADDER and gold_ids:
            # Vector-route level is DEFINED by |gold_project_ids| (§1).
            n = len(gold_ids)
            want = {"L1": n == 1, "L2": 2 <= n <= 4, "L3": n >= 5}
            if not want[level]:
                bad(f"vector {level} requires |gold_project_ids| "
                    f"{'== 1' if level == 'L1' else 'in [2,4]' if level == 'L2' else '>= 5'},"
                    f" got {n}")

    for key in ("reference_answer", "notes"):
        v = obj.get(key)
        if v is not None and (not isinstance(v, str) or not v.strip()):
            bad(f"{key} must be a non-empty string when present")

    if errs:
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
        reference_answer=obj.get("reference_answer"),
        schema_docs_hash=schema_docs_hash,
        reviewer_override=obj.get("reviewer_override", False),
        notes=obj.get("notes"))


def load_bank(path: str | Path) -> list[BankQuestion]:
    """Parse + validate a bank JSONL. Raises BankValidationError listing EVERY
    violation in the file; returns the questions only if all lines are clean."""
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
        q = _validate_record(obj, where, line_errs)
        errors.extend(line_errs)
        if q is None:
            continue
        if q.question_id in seen_ids:
            errors.append(f"{where}: duplicate question_id {q.question_id!r}")
            continue
        seen_ids.add(q.question_id)
        questions.append(q)
    if errors:
        raise BankValidationError(errors)
    return questions


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
