"""Text-to-SQL leg: question -> DuckDB SELECT -> executed rows.

Guardrails are enforced in code, not in the prompt: read-only connection,
SELECT-only single-statement validation, LIMIT injection, query timeout.
One retry on failure with the DuckDB error fed back; then structured give-up.
Every attempt is logged to a local jsonl (debugging corpus / eval fodder).
"""

import json
import re
import threading
import time
from dataclasses import dataclass, field

import duckdb

from src.config import (DB_PATH, SCHEMA_DOCS_PATH, SQL_LOG_PATH, SQL_ROW_LIMIT,
                        SQL_TIMEOUT_S)
from src.llm import fingerprint, make_llm

# Frozen after Study 0.5's single value-description intervention (d7); bump on
# ANY edit. The fingerprint hashes the FULL system prompt including
# schema_docs.md, so doc edits are visible in traces too.
# q2-pilot (2026-07-24): no prompt text changed here - schema_docs.md was
# corrected to sd2 (euroSciVocPath leading-slash bug), which changes this
# prompt's content and therefore its fingerprint. Study 0.5's baseline is
# q2-pilot/sd2; the study has not run, so nothing is contaminated.
SQL_PROMPT_VERSION = "q2-pilot"


class SqlGuardrailError(ValueError):
    pass


@dataclass
class SqlResult:
    question: str
    sql: str | None = None          # last SQL attempted (post-guardrails)
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    error: str | None = None        # set only on give-up
    retried: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None


_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_LIMIT_RE = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)
_FORBIDDEN_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|TRUNCATE|ATTACH|DETACH"
    r"|COPY|EXPORT|IMPORT|PRAGMA|INSTALL|LOAD|CALL|SET|RESET|BEGIN|COMMIT"
    r"|ROLLBACK|VACUUM|CHECKPOINT|GRANT|REVOKE)\b", re.IGNORECASE)


def strip_fences(text: str) -> str:
    """Local models love fences despite the contract - strip them defensively."""
    m = _FENCE_RE.search(text)
    return (m.group(1) if m else text).strip()


def validate_sql(sql: str) -> str:
    """Return the normalized statement or raise SqlGuardrailError."""
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise SqlGuardrailError("empty statement")
    if ";" in sql:
        raise SqlGuardrailError("multiple statements are not allowed")
    first = re.split(r"\s+", sql, maxsplit=1)[0].upper()
    if first not in ("SELECT", "WITH"):
        raise SqlGuardrailError(f"only SELECT is allowed, got '{first}'")
    m = _FORBIDDEN_RE.search(sql)
    if m:
        raise SqlGuardrailError(f"forbidden keyword '{m.group(1).upper()}'")
    return sql


def ensure_limit(sql: str, row_limit: int = SQL_ROW_LIMIT) -> str:
    return sql if _LIMIT_RE.search(sql) else f"{sql} LIMIT {row_limit}"


def build_system_prompt() -> str:
    docs = SCHEMA_DOCS_PATH.read_text(encoding="utf-8")
    return (
        "You translate natural-language questions about the Horizon 2020 "
        "CORDIS database into DuckDB SQL.\n\n"
        f"{docs}\n\n"
        "Output contract:\n"
        "- Reply with exactly ONE DuckDB SELECT statement (WITH ... SELECT is "
        "allowed) and nothing else: no commentary, no explanation, no "
        "markdown fences.\n"
        "- Read-only: never modify data.\n"
        "- Use only the tables, columns and enumerated values documented "
        "above."
    )


class SqlPath:
    def __init__(self, llm=None, db_path=DB_PATH,
                 log_path=SQL_LOG_PATH, timeout_s: float = SQL_TIMEOUT_S,
                 row_limit: int = SQL_ROW_LIMIT,
                 system_prompt: str | None = None):
        self.llm = llm or make_llm()
        self.db_path = db_path
        self.log_path = log_path
        self.timeout_s = timeout_s
        self.row_limit = row_limit
        # Injectable so M4 hybrid reuses all guardrails/retry with a different
        # instruction (id-narrowing) instead of copying this class.
        self.system_prompt = system_prompt or build_system_prompt()
        self.prompt_version = f"{SQL_PROMPT_VERSION}:{fingerprint(self.system_prompt)}"

    def build_messages(self, question: str) -> list[dict]:
        return [{"role": "system", "content": self.system_prompt},
                {"role": "user", "content": question}]

    def ask(self, question: str) -> SqlResult:
        """At most 2 generation calls: initial + one error-informed retry."""
        result = SqlResult(question=question)
        messages = self.build_messages(question)
        for attempt in (0, 1):
            raw = self.llm.chat(messages)
            error = None
            try:
                sql = ensure_limit(validate_sql(strip_fences(raw)),
                                   self.row_limit)
                result.sql = sql
            except SqlGuardrailError as e:
                sql = raw.strip()
                result.sql = sql
                error = f"guardrail: {e}"
            if error is None:
                try:
                    result.columns, result.rows = self._execute(sql)
                except duckdb.Error as e:
                    error = f"{type(e).__name__}: {e}"
            self._log(question, attempt, sql, error,
                      n_rows=len(result.rows) if error is None else None)
            if error is None:
                result.error = None
                return result
            result.error = error
            if attempt == 0:
                result.retried = True
                messages = messages + [
                    {"role": "assistant", "content": sql},
                    {"role": "user", "content":
                        f"That SQL failed with this DuckDB error:\n{error}\n"
                        "Return a corrected single DuckDB SELECT statement, "
                        "nothing else."},
                ]
        return result  # give-up: error is set, no answer invented

    def _execute(self, sql: str) -> tuple[list[str], list[tuple]]:
        """Read-only connection; interrupt after timeout_s."""
        con = duckdb.connect(str(self.db_path), read_only=True)
        try:
            timer = threading.Timer(self.timeout_s, con.interrupt)
            timer.start()
            try:
                cur = con.execute(sql)
                rows = cur.fetchall()
                columns = [d[0] for d in cur.description]
            finally:
                timer.cancel()
            return columns, rows
        finally:
            con.close()

    def execute_trusted(self, sql: str) -> tuple[list[str], list[tuple]]:
        """Run hand-written SQL (eval ground truth) with the same executor."""
        return self._execute(sql)

    def _log(self, question, attempt, sql, error, n_rows):
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "question": question, "attempt": attempt, "sql": sql,
                 "ok": error is None, "error": error, "n_rows": n_rows,
                 "model": self.llm.model, "prompt": self.prompt_version}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# --- execution-accuracy comparison (smoke eval now, real eval in M5) ---

def _norm_value(v):
    """Numbers to floats rounded to 6 decimals; everything else to str."""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)) or type(v).__name__ == "Decimal":
        return round(float(v), 6)
    return str(v)


def _canon_row(row) -> tuple:
    """One row, values normalized and sorted - column ORDER never matters, so
    `SELECT a, b` and `SELECT b, a` compare equal."""
    return tuple(sorted(repr(_norm_value(v)) for v in row))


def results_match(rows_a: list[tuple], rows_b: list[tuple]) -> bool:
    """Unordered row-set comparison, column order-insensitive, numeric
    tolerance 1e-6 (via rounding). Column NAMES are ignored - only values
    count, so aliasing differences never fail a case."""
    return sorted(_canon_row(r) for r in rows_a) == \
        sorted(_canon_row(r) for r in rows_b)


def results_match_ordered(rows_a: list[tuple], rows_b: list[tuple]) -> bool:
    """Row-by-row in the order returned, same value normalization.

    For `rank` questions the order IS the answer - "the three largest grants,
    largest first" is wrong if it comes back smallest first, and results_match
    would call it right.
    """
    if len(rows_a) != len(rows_b):
        return False
    return all(_canon_row(a) == _canon_row(b) for a, b in zip(rows_a, rows_b))


def rows_match(want: list[tuple], got: list[tuple],
               comparison: str = "set") -> bool:
    """Execution-accuracy comparison honouring the bank's `sql_comparison`.

    "set" is BIRD's default and the bank's; "ordered" is required (and enforced
    by bank.py) exactly when subtype = rank.
    """
    if comparison == "ordered":
        return results_match_ordered(want, got)
    if comparison != "set":
        raise ValueError(f"unknown sql_comparison {comparison!r}; "
                         "expected 'set' or 'ordered'")
    return results_match(want, got)


def project_to_answer_columns(columns, rows, answer_columns
                              ) -> tuple[list[tuple] | None, str]:
    """Narrow a result to the columns the bank pinned as THE answer.

    Returns (rows, how). `how` records which alignment was possible, because a
    run wants to report how many questions passed only after projection:

      "none"      nothing pinned; rows come back untouched
      "by-name"   every pinned name is present; those positions are kept, in
                  answer_columns order
      "as-is"     names do not align but the counts do - the generator aliased,
                  which it is free to do, so the values decide (same reason
                  results_match ignores column names)
      "unmatched" neither: which column holds the answer is unknowable, rows None

    Both sides go through this. The gold result is a superset too - sql-15 pins
    two columns out of a three-column gold_sql - so projecting only the
    generated side would compare different shapes.
    """
    if not answer_columns:
        return list(rows), "none"
    names = list(columns or [])
    index = {name: i for i, name in enumerate(names)}
    if all(name in index for name in answer_columns):
        keep = [index[name] for name in answer_columns]
        return [tuple(row[i] for i in keep) for row in rows], "by-name"
    if len(names) == len(answer_columns):
        return list(rows), "as-is"
    return None, "unmatched"


def columns_match(answer_columns, got_columns) -> bool | None:
    """Does the generated query return the shape the bank pinned?

    Reported BESIDE pass/fail, never folded into it. A right answer carrying an
    extra column is a different failure from a wrong answer, and collapsing the
    two would hide which one happened. Compares COUNT, not names: the bank pins
    what the answer is made of, while the generator is free to alias, and
    results_match already ignores names for the same reason.

    None when the bank pinned nothing to check against.
    """
    if not answer_columns:
        return None
    return len(answer_columns) == len(got_columns or [])
