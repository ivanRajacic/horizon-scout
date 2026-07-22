"""Read-only MCP server for question-bank drafting (M5).

Exposes exactly three tools to the drafting agent (/draft-sql-question now,
vector/hybrid skills later): run_sql, get_schema_docs, get_bank_questions.
Deliberately minimal - the agent must be able to ground questions in real
data and verify gold labels by execution, and nothing more. No write tools:
bank appends are confirmation-gated in the skill layer, outside the MCP.

Safety is enforced in code, not prompt - twice over: the statement guard
from sql_path.validate_sql (single SELECT/WITH, no forbidden keywords, no
multi-statement) AND a read-only DuckDB connection. SQL errors come back as
structured results ({"error": ...}), never tool failures, because
trap-question authoring needs to reason about broken queries.

Paths come from env (HS_DB_PATH, HS_BANK_PATH, HS_SCHEMA_DOCS_PATH,
HS_DRAFT_MCP_LOG_PATH) with defaults from src.config. Every call is logged
as JSONL - the same trace-everything discipline as the rest of the pipeline.

Run: python -m src.eval.mcp_server   (stdio transport; wired in .mcp.json)
"""

import datetime
import decimal
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb

from src.config import (BANK_PATH, DB_PATH, DRAFT_MCP_LOG_PATH,
                        SCHEMA_DOCS_PATH, SCHEMA_DOCS_VERSION, SQL_TIMEOUT_S)
from src.eval.bank import ROUTES
from src.llm import fingerprint
from src.retrieval.sql_path import SqlGuardrailError, validate_sql

ROW_CAP_DEFAULT = 50
ROW_CAP_CEILING = 200  # hard ceiling regardless of the row_cap argument


@dataclass
class ServerConfig:
    db_path: Path
    bank_path: Path
    schema_docs_path: Path
    log_path: Path
    timeout_s: float = SQL_TIMEOUT_S

    @classmethod
    def from_env(cls) -> "ServerConfig":
        def path(env: str, default: Path) -> Path:
            return Path(os.environ.get(env, str(default)))

        return cls(
            db_path=path("HS_DB_PATH", DB_PATH),
            bank_path=path("HS_BANK_PATH", BANK_PATH),
            schema_docs_path=path("HS_SCHEMA_DOCS_PATH", SCHEMA_DOCS_PATH),
            log_path=path("HS_DRAFT_MCP_LOG_PATH", DRAFT_MCP_LOG_PATH))


cfg = ServerConfig.from_env()


def _log(tool: str, **fields) -> None:
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": tool, **fields}
    cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg.log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(cfg.db_path), read_only=True)


def _jsonable(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.hex()
    return v


def _execute(con: duckdb.DuckDBPyConnection, sql: str,
             fetch: int | None = None) -> tuple[list[str], list[tuple]]:
    """Run one statement with the project's interrupt-based timeout."""
    timer = threading.Timer(cfg.timeout_s, con.interrupt)
    timer.start()
    try:
        cur = con.execute(sql)
        rows = cur.fetchmany(fetch) if fetch is not None else cur.fetchall()
        return [d[0] for d in cur.description], rows
    finally:
        timer.cancel()


def run_sql(query: str, row_cap: int = ROW_CAP_DEFAULT) -> dict:
    """Execute a single read-only SELECT against the CORDIS DuckDB.

    Returns {columns, rows, row_count, truncated}; row_count is the true
    result size before capping (null only if counting itself timed out).
    Rejected or failing SQL returns {"error": "..."} instead - never a tool
    failure - so broken queries can be reasoned about.
    """
    try:
        sql = validate_sql(query)
    except SqlGuardrailError as e:
        error = f"guardrail: {e}"
        _log("run_sql", query=query, ok=False, error=error)
        return {"error": error}

    cap = max(1, min(int(row_cap), ROW_CAP_CEILING))
    con = _connect()
    try:
        try:
            columns, rows = _execute(con, sql, fetch=cap + 1)
        except duckdb.InterruptException:
            error = f"timeout: query exceeded {cfg.timeout_s}s"
            _log("run_sql", query=sql, ok=False, error=error)
            return {"error": error}
        except duckdb.Error as e:
            error = f"{type(e).__name__}: {e}"
            _log("run_sql", query=sql, ok=False, error=error)
            return {"error": error}

        truncated = len(rows) > cap
        rows = rows[:cap]
        if truncated:
            # True pre-cap size; a plain SELECT/WITH always nests cleanly.
            try:
                _, count_rows = _execute(
                    con, f"SELECT COUNT(*) FROM ({sql}) AS _q")
                row_count = count_rows[0][0]
            except duckdb.Error:
                row_count = None
        else:
            row_count = len(rows)
    finally:
        con.close()

    result = {"columns": columns,
              "rows": [[_jsonable(v) for v in row] for row in rows],
              "row_count": row_count, "truncated": truncated}
    _log("run_sql", query=sql, ok=True, row_count=row_count,
         rows_returned=len(rows), truncated=truncated)
    return result


def get_schema_docs() -> dict:
    """Return schema_docs.md verbatim plus its version label and content
    hash, so drafted questions can record what they were authored against."""
    text = cfg.schema_docs_path.read_text(encoding="utf-8")
    content_hash = fingerprint(text)
    _log("get_schema_docs", version=SCHEMA_DOCS_VERSION,
         content_hash=content_hash)
    return {"markdown": text, "version": SCHEMA_DOCS_VERSION,
            "content_hash": content_hash}


def get_bank_questions(route: str) -> dict:
    """List existing bank questions for one route - id, text, level, subtype
    only (enough for near-duplicate avoidance, nothing that tempts copying
    reference answers). Accepts pre-migration banks where level is still
    called complexity and subtype does not exist yet."""
    if route not in ROUTES:
        error = f"unknown route {route!r}; valid routes: {', '.join(ROUTES)}"
        _log("get_bank_questions", route=route, ok=False, error=error)
        return {"error": error}
    if not cfg.bank_path.exists():
        error = f"bank file not found: {cfg.bank_path}"
        _log("get_bank_questions", route=route, ok=False, error=error)
        return {"error": error}

    questions = []
    for line in cfg.bank_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if obj.get("expected_route") != route:
            continue
        questions.append({
            "question_id": obj.get("question_id"),
            "text": obj.get("text"),
            "level": obj.get("level", obj.get("complexity")),
            "subtype": obj.get("subtype"),
        })
    _log("get_bank_questions", route=route, ok=True, n=len(questions))
    return {"route": route, "questions": questions}


def main() -> None:
    # SDK import stays local so tests of the tool functions above never
    # depend on it.
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("horizon-scout-draft")
    for fn in (run_sql, get_schema_docs, get_bank_questions):
        server.tool()(fn)
    server.run()  # stdio


if __name__ == "__main__":
    main()
