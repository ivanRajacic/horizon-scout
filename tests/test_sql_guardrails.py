"""Guardrail and error-loop tests for the SQL path. No LLM server needed:
generation is faked; execution runs against the real DuckDB read-only."""

import duckdb
import pytest

from src.config import DB_PATH
from src.retrieval.sql_path import (SqlGuardrailError, SqlPath, ensure_limit,
                                    results_match, strip_fences, validate_sql)


class FakeLlm:
    """Returns queued responses; counts calls."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, messages, **kw):
        self.calls += 1
        return self.responses.pop(0)


def make_path(tmp_path, responses, timeout_s=10.0):
    return SqlPath(llm=FakeLlm(responses), log_path=tmp_path / "log.jsonl",
                   timeout_s=timeout_s)


# --- statement validation ---

@pytest.mark.parametrize("sql", [
    "INSERT INTO project VALUES (1)",
    "UPDATE project SET status = 'X'",
    "DELETE FROM project",
    "DROP TABLE project",
    "CREATE TABLE t (i INT)",
    "ATTACH 'other.duckdb'",
    "COPY project TO 'out.csv'",
    "PRAGMA database_list",
])
def test_non_select_rejected(sql):
    with pytest.raises(SqlGuardrailError):
        validate_sql(sql)


def test_multiple_statements_rejected():
    with pytest.raises(SqlGuardrailError, match="multiple"):
        validate_sql("SELECT 1; SELECT 2")


def test_select_with_embedded_write_keyword_rejected():
    with pytest.raises(SqlGuardrailError, match="DELETE"):
        validate_sql("SELECT 1 WHERE 1 = (DELETE FROM project)")


def test_plain_select_and_cte_accepted():
    assert validate_sql("SELECT 42;") == "SELECT 42"
    assert validate_sql("WITH x AS (SELECT 1) SELECT * FROM x").startswith("WITH")


def test_fences_stripped():
    assert strip_fences("```sql\nSELECT 1\n```") == "SELECT 1"
    assert strip_fences("```\nSELECT 1\n```") == "SELECT 1"
    assert strip_fences("SELECT 1") == "SELECT 1"


def test_limit_injected_only_when_missing():
    assert ensure_limit("SELECT id FROM project") == \
        "SELECT id FROM project LIMIT 1000"
    assert ensure_limit("SELECT id FROM project LIMIT 5") == \
        "SELECT id FROM project LIMIT 5"


# --- execution guardrails ---

def test_connection_is_read_only(tmp_path):
    path = make_path(tmp_path, [])
    with pytest.raises(duckdb.Error, match="read-only"):
        path._execute("CREATE TABLE hack (i INT)")


def test_timeout_fires(tmp_path):
    path = make_path(tmp_path, [], timeout_s=0.3)
    with pytest.raises(duckdb.Error):
        # ~5.4e10-row cross join: cannot finish in 0.3s, must be interrupted
        path._execute("SELECT COUNT(*) FROM web_link a, web_link b")


# --- ask() end to end with fake generation ---

def test_ask_success_no_retry(tmp_path):
    path = make_path(tmp_path, ["SELECT COUNT(*) FROM project"])
    r = path.ask("how many projects?")
    assert r.ok and not r.retried and r.rows[0][0] == 35389
    assert path.llm.calls == 1
    assert "LIMIT 1000" in r.sql


def test_error_loop_exactly_one_retry_then_give_up(tmp_path):
    path = make_path(tmp_path, ["SELECT nope FROM project",
                                "SELECT still_nope FROM project"])
    r = path.ask("q")
    assert not r.ok and r.retried and r.error
    assert r.rows == []
    assert path.llm.calls == 2  # never more than 2 generation calls


def test_retry_recovers_from_bad_first_attempt(tmp_path):
    path = make_path(tmp_path, ["SELECT nope FROM project",
                                "SELECT COUNT(*) FROM project"])
    r = path.ask("q")
    assert r.ok and r.retried and r.rows[0][0] == 35389


def test_guardrail_rejection_feeds_retry(tmp_path):
    path = make_path(tmp_path, ["DROP TABLE project",
                                "SELECT COUNT(*) FROM project"])
    r = path.ask("q")
    assert r.ok and r.retried


def test_log_written(tmp_path):
    path = make_path(tmp_path, ["SELECT 1"])
    path.ask("q")
    assert (tmp_path / "log.jsonl").read_text(encoding="utf-8").count("\n") == 1


# --- results_match ---

def test_results_match_semantics():
    assert results_match([(1, "a"), (2, "b")], [(2, "b"), (1, "a")])  # order
    assert results_match([("a", 1)], [(1, "a")])                      # col order
    assert results_match([(1.0000001,)], [(1.0,)])                    # tolerance
    assert not results_match([(1,)], [(2,)])
    assert not results_match([(1,)], [(1,), (1,)])
