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
from src.llm import LlmClient


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
    def __init__(self, llm: LlmClient | None = None, db_path=DB_PATH,
                 log_path=SQL_LOG_PATH, timeout_s: float = SQL_TIMEOUT_S,
                 row_limit: int = SQL_ROW_LIMIT,
                 system_prompt: str | None = None):
        self.llm = llm or LlmClient()
        self.db_path = db_path
        self.log_path = log_path
        self.timeout_s = timeout_s
        self.row_limit = row_limit
        # Injectable so M4 hybrid reuses all guardrails/retry with a different
        # instruction (id-narrowing) instead of copying this class.
        self.system_prompt = system_prompt or build_system_prompt()

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
                 "ok": error is None, "error": error, "n_rows": n_rows}
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


def results_match(rows_a: list[tuple], rows_b: list[tuple]) -> bool:
    """Unordered row-set comparison, column order-insensitive, numeric
    tolerance 1e-6 (via rounding). Column NAMES are ignored - only values
    count, so aliasing differences never fail a case."""
    def canon(rows):
        return sorted(tuple(sorted((repr(_norm_value(v)) for v in row)))
                      for row in rows)
    return canon(rows_a) == canon(rows_b)
