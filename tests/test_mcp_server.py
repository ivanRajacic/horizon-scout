"""Drafting MCP server smoke tests (M5): SELECT works, every write path is
rejected, caps and true counts behave, SQL errors are results not
exceptions, the connection is genuinely read-only, calls are logged."""

import json

import duckdb
import pytest

from src.config import SCHEMA_DOCS_VERSION
from src.eval import mcp_server
from src.eval.mcp_server import (ServerConfig, get_bank_questions,
                                 get_schema_docs, run_sql)
from src.llm import fingerprint

DOCS_TEXT = "# schema docs\ntable t: i INTEGER, name VARCHAR\n"

BANK_RECORDS = [
    # Pre-migration record: level still called complexity, no subtype.
    {"question_id": "b-sql-01", "text": "How many rows?",
     "expected_route": "sql", "complexity": "L1",
     "gold_sql": "SELECT COUNT(*) FROM t"},
    # Post-migration shape.
    {"question_id": "b-sql-02", "text": "Top row by i?",
     "expected_route": "sql", "level": "L3", "subtype": "rank",
     "gold_sql": "SELECT name FROM t ORDER BY i DESC LIMIT 1"},
    {"question_id": "b-vec-01", "text": "Find rows about widgets.",
     "expected_route": "vector", "complexity": "L1"},
]


@pytest.fixture
def server(tmp_path, monkeypatch):
    db = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE t (i INTEGER, name VARCHAR)")
    con.execute("INSERT INTO t SELECT range, 'row' || range FROM range(10)")
    con.close()
    docs = tmp_path / "schema_docs.md"
    docs.write_text(DOCS_TEXT, encoding="utf-8")
    bank = tmp_path / "bank.jsonl"
    bank.write_text("\n".join(json.dumps(r) for r in BANK_RECORDS),
                    encoding="utf-8")
    cfg = ServerConfig(db_path=db, bank_path=bank, schema_docs_path=docs,
                       log_path=tmp_path / "draft_mcp.jsonl")
    monkeypatch.setattr(mcp_server, "cfg", cfg)
    return cfg


def test_select_returns_rows(server):
    result = run_sql("SELECT i, name FROM t ORDER BY i")
    assert "error" not in result
    assert result["columns"] == ["i", "name"]
    assert result["row_count"] == 10 and not result["truncated"]
    assert result["rows"][0] == [0, "row0"]


def test_row_cap_keeps_true_count(server):
    result = run_sql("SELECT i FROM t ORDER BY i", row_cap=3)
    assert len(result["rows"]) == 3
    assert result["row_count"] == 10
    assert result["truncated"] is True


def test_row_cap_hard_ceiling(server):
    result = run_sql("SELECT * FROM range(1000)", row_cap=500)
    assert len(result["rows"]) == mcp_server.ROW_CAP_CEILING
    assert result["row_count"] == 1000
    assert result["truncated"] is True


@pytest.mark.parametrize("bad", [
    "INSERT INTO t VALUES (99, 'x')",
    "SELECT 1; SELECT 2",
    "PRAGMA database_list",
    "WITH x AS (SELECT 1) INSERT INTO t SELECT 99, 'x'",
    "DROP TABLE t",
])
def test_non_select_rejected(server, bad):
    result = run_sql(bad)
    assert set(result) == {"error"}
    assert result["error"].startswith("guardrail:")


def test_bad_sql_is_error_result_not_exception(server):
    result = run_sql("SELECT * FROM no_such_table")
    assert set(result) == {"error"}
    assert "no_such_table" in result["error"]


def test_connection_is_read_only(server):
    # Even if a write slipped past the statement guard, the connection
    # itself refuses it.
    con = mcp_server._connect()
    try:
        with pytest.raises(duckdb.Error):
            con.execute("INSERT INTO t VALUES (99, 'x')")
    finally:
        con.close()


def test_get_schema_docs(server):
    result = get_schema_docs()
    assert result["markdown"] == DOCS_TEXT
    assert result["version"] == SCHEMA_DOCS_VERSION
    assert result["content_hash"] == fingerprint(DOCS_TEXT)


def test_get_bank_questions_filters_and_maps_levels(server):
    result = get_bank_questions("sql")
    assert [q["question_id"] for q in result["questions"]] == [
        "b-sql-01", "b-sql-02"]
    legacy, migrated = result["questions"]
    assert legacy["level"] == "L1" and legacy["subtype"] is None
    assert migrated["level"] == "L3" and migrated["subtype"] == "rank"
    assert all(set(q) == {"question_id", "text", "level", "subtype"}
               for q in result["questions"])


def test_get_bank_questions_unknown_route(server):
    result = get_bank_questions("sparql")
    assert "error" in result and "sparql" in result["error"]


def test_every_call_is_logged(server):
    run_sql("SELECT 1")
    run_sql("DROP TABLE t")
    get_schema_docs()
    get_bank_questions("sql")
    lines = [json.loads(l) for l in
             server.log_path.read_text(encoding="utf-8").splitlines()]
    assert [e["tool"] for e in lines] == [
        "run_sql", "run_sql", "get_schema_docs", "get_bank_questions"]
    assert lines[1]["ok"] is False
