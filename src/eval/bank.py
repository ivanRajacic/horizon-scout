"""Question bank schema + validator (M5 §1: one bank, multiple measurements).

One JSONL file, one record per question. Labels are optional columns; each
metric script filters on which labels exist. Validation is loud: every schema
violation in the file is collected and reported, and loading fails on any.

Route vocabulary follows the plan doc (sql | vector | hybrid | ambiguous);
the runtime router calls the hybrid mode "scoped" - ROUTE_TO_MODE is the one
place that mapping lives.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

ROUTES = ("sql", "vector", "hybrid", "ambiguous")
ROUTE_TO_MODE = {"sql": "sql", "vector": "vector", "hybrid": "scoped"}
COMPLEXITIES = ("L1", "L2", "L3")
SPECIFICATIONS = ("well-specified", "underspecified")
TERM_STYLES = ("exact-term", "paraphrase")
ADVERSARIAL = ("zero-match", "false-presupposition", "unanswerable",
               "data-absent")
SQL_COMPARISONS = ("set", "ordered")

# Only these keys may appear in a bank record - typos never pass silently.
KNOWN_FIELDS = frozenset({
    "question_id", "text", "expected_route", "acceptable_routes",
    "complexity", "specification", "term_style", "compositional",
    "adversarial", "gold_sql", "sql_comparison", "gold_project_ids",
    "reference_answer", "notes",
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
    complexity: str
    specification: str = "well-specified"
    acceptable_routes: list[str] = field(default_factory=list)
    term_style: str | None = None
    compositional: bool = False
    adversarial: str | None = None
    gold_sql: str | None = None
    sql_comparison: str = "set"
    gold_project_ids: list[int] | None = None
    reference_answer: str | None = None
    notes: str | None = None

    @property
    def is_topical(self) -> bool:
        """Vector/hybrid questions (incl. ambiguous ones that may go there)."""
        routes = {self.expected_route, *self.acceptable_routes}
        return bool(routes & {"vector", "hybrid"})


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

    if obj.get("complexity") not in COMPLEXITIES:
        bad(f"complexity must be one of {COMPLEXITIES}, "
            f"got {obj.get('complexity')!r}")

    if obj.get("specification", "well-specified") not in SPECIFICATIONS:
        bad(f"specification must be one of {SPECIFICATIONS}")

    adversarial = obj.get("adversarial")
    if adversarial is not None and adversarial not in ADVERSARIAL:
        bad(f"adversarial must be one of {ADVERSARIAL}, got {adversarial!r}")

    if not isinstance(obj.get("compositional", False), bool):
        bad("compositional must be a boolean")

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

    gold_ids = obj.get("gold_project_ids")
    if gold_ids is not None:
        if (not isinstance(gold_ids, list)
                or any(not isinstance(i, int) for i in gold_ids)):
            bad("gold_project_ids must be a list of integers")
        elif len(set(gold_ids)) != len(gold_ids):
            bad("gold_project_ids contains duplicates")
        elif adversarial == "zero-match" and gold_ids:
            bad("zero-match questions must have empty gold_project_ids")
        elif (route == "vector" and adversarial is None and gold_ids):
            # Vector-route complexity is DEFINED by |gold_project_ids| (§1).
            n = len(gold_ids)
            want = {"L1": n == 1, "L2": 2 <= n <= 4, "L3": n >= 5}
            comp = obj.get("complexity")
            if comp in want and not want[comp]:
                bad(f"vector {comp} requires |gold_project_ids| "
                    f"{'== 1' if comp == 'L1' else 'in [2,4]' if comp == 'L2' else '>= 5'},"
                    f" got {n}")

    for key in ("reference_answer", "notes"):
        v = obj.get(key)
        if v is not None and (not isinstance(v, str) or not v.strip()):
            bad(f"{key} must be a non-empty string when present")

    if errs:
        return None
    return BankQuestion(
        question_id=qid, text=text, expected_route=route,
        complexity=obj["complexity"],
        specification=obj.get("specification", "well-specified"),
        acceptable_routes=list(acceptable) if route == "ambiguous" else [],
        term_style=term_style,
        compositional=obj.get("compositional", False),
        adversarial=adversarial, gold_sql=gold_sql,
        sql_comparison=obj.get("sql_comparison", "set"),
        gold_project_ids=gold_ids,
        reference_answer=obj.get("reference_answer"),
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
    """Route x complexity counts plus label coverage - the allocation view."""
    from collections import Counter

    cell = Counter((q.expected_route, q.complexity) for q in questions)
    lines = ["route x complexity:"]
    for route in ROUTES:
        row = "  ".join(f"{c}={cell.get((route, c), 0)}" for c in COMPLEXITIES)
        lines.append(f"  {route:10s} {row}  total="
                     f"{sum(cell.get((route, c), 0) for c in COMPLEXITIES)}")
    n = len(questions)
    labels = {
        "gold_sql": sum(q.gold_sql is not None for q in questions),
        "gold_project_ids": sum(q.gold_project_ids is not None for q in questions),
        "reference_answer": sum(q.reference_answer is not None for q in questions),
        "term_style": sum(q.term_style is not None for q in questions),
        "adversarial": sum(q.adversarial is not None for q in questions),
        "underspecified": sum(q.specification == "underspecified" for q in questions),
        "compositional": sum(q.compositional for q in questions),
    }
    lines.append(f"labels ({n} questions): "
                 + ", ".join(f"{k}={v}" for k, v in labels.items()))
    return "\n".join(lines)
