"""Guardrail and error-loop tests for the SQL path. No LLM server needed:
generation is faked; execution runs against the real DuckDB read-only."""

import duckdb
import pytest

from src.config import DB_PATH
from src.retrieval.sql_path import (SqlGuardrailError, SqlPath, ensure_limit,
                                    columns_match, results_match,
                                    project_to_answer_columns,
                                    results_match_ordered, rows_match,
                                    strip_comments, strip_fences, validate_sql)


class FakeLlm:
    """Returns queued responses; counts calls."""

    model = "fake-llm"  # SqlPath logs llm.model per attempt (M5 traces)

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


def test_ensure_limit_replace_swaps_a_trailing_model_limit():
    """The narrowing path: hyb-13 and hyb-15 ended in LIMIT 1 and collapsed the
    filter set to one project. replace=True swaps it for the bound."""
    assert ensure_limit("SELECT id FROM project LIMIT 1", 50000, replace=True) \
        == "SELECT id FROM project LIMIT 50000"
    assert ensure_limit("SELECT id FROM project", 50000, replace=True) == \
        "SELECT id FROM project LIMIT 50000"


def test_ensure_limit_replace_never_touches_a_subquery_limit():
    sql = "SELECT id FROM (SELECT id FROM project LIMIT 5) t"
    assert ensure_limit(sql, 50000, replace=True) == sql


# --- comment stripping (the r3-fields-phaseA false-positive class: 14 of 25
# narrowing calls rejected over semicolons or keywords inside comments) ---

def test_comment_with_semicolon_is_not_two_statements():
    # Verbatim shape from the narrowing log.
    sql = ("SELECT DISTINCT p.id FROM project p JOIN organization o "
           "ON o.projectID = p.id\nWHERE o.country = 'SE'\n"
           "-- Topic constraint is handled semantically; no topic filter here")
    out = validate_sql(sql)
    assert out.rstrip().endswith("o.country = 'SE'")
    assert "Topic constraint" not in out    # the stripped text is what runs


def test_comment_with_forbidden_keyword_is_not_a_write():
    assert validate_sql("SELECT 1\n-- do not SET anything here") == "SELECT 1"


def test_block_comment_stripped_without_fusing_tokens():
    out = validate_sql("SELECT/* count; DROP */COUNT(*) FROM project")
    assert "DROP" not in out and "COUNT(*)" in out
    assert "SELECTCOUNT" not in out


def test_semicolon_inside_a_string_literal_is_data():
    sql = "SELECT id FROM project WHERE title = 'a;b'"
    assert validate_sql(sql) == sql


def test_forbidden_keyword_inside_a_string_literal_is_data():
    sql = "SELECT id FROM project WHERE title = 'DROP-IN centre'"
    assert validate_sql(sql) == sql


def test_double_dash_inside_a_string_literal_survives():
    sql = "SELECT id FROM project WHERE acronym = 'a--b'"
    assert validate_sql(sql) == sql


def test_escaped_quote_inside_a_string_literal():
    sql = "SELECT id FROM project WHERE title = 'it''s; fine'"
    assert validate_sql(sql) == sql


def test_genuine_second_statement_still_rejected_behind_a_comment():
    with pytest.raises(SqlGuardrailError, match="multiple"):
        validate_sql("SELECT 1 -- note\n; SELECT 2")


def test_strip_comments_keeps_the_newline_of_a_line_comment():
    assert strip_comments("SELECT 1 -- x\nFROM t") == "SELECT 1 \nFROM t"


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


def test_replace_limit_overrides_a_model_limit_end_to_end(tmp_path):
    path = SqlPath(llm=FakeLlm(["SELECT id FROM project LIMIT 1"]),
                   log_path=tmp_path / "log.jsonl",
                   row_limit=50000, replace_limit=True)
    r = path.ask("q")
    assert r.sql.endswith("LIMIT 50000")
    assert len(r.rows) == 35389        # the whole set, not one project


def test_prompt_label_names_the_callers_prompt(tmp_path):
    path = SqlPath(llm=FakeLlm([]), log_path=tmp_path / "log.jsonl",
                   system_prompt="narrowing instruction",
                   prompt_label="narrow-v2")
    assert path.prompt_version.startswith("narrow-v2:")


# --- results_match ---

def test_results_match_semantics():
    assert results_match([(1, "a"), (2, "b")], [(2, "b"), (1, "a")])  # order
    assert results_match([("a", 1)], [(1, "a")])                      # col order
    assert results_match([(1.0000001,)], [(1.0,)])                    # tolerance
    assert not results_match([(1,)], [(2,)])
    assert not results_match([(1,)], [(1,), (1,)])


# --- rows_match / columns_match (the bank's sql_comparison + answer_columns) ---

def test_ordered_comparison_cares_about_row_order():
    """For a rank question the order IS the answer, and results_match - which
    the smoke eval uses - would call a reversed list correct."""
    want, got = [(1,), (2,)], [(2,), (1,)]
    assert results_match(want, got)
    assert not results_match_ordered(want, got)
    assert results_match_ordered(want, [(1,), (2,)])


def test_ordered_comparison_keeps_the_other_leniencies():
    # column order and numeric tolerance still forgiven, row order is not
    assert results_match_ordered([("a", 1)], [(1, "a")])
    assert results_match_ordered([(1.0000001,)], [(1.0,)])
    assert not results_match_ordered([(1,)], [(1,), (1,)])   # length differs


def test_rows_match_dispatches_on_the_banks_comparison():
    want, got = [(1,), (2,)], [(2,), (1,)]
    assert rows_match(want, got, "set")
    assert not rows_match(want, got, "ordered")
    assert rows_match(want, got)                             # default is set


def test_rows_match_rejects_an_unknown_comparison():
    with pytest.raises(ValueError, match="unknown sql_comparison"):
        rows_match([(1,)], [(1,)], "vibes")


# --- project_to_answer_columns (what the bank pinned as THE answer) ---

def test_projection_by_name_keeps_only_the_pinned_columns():
    rows, how = project_to_answer_columns(
        ["id", "acronym", "title"], [(1, "BIG", "t")], ["acronym"])
    assert how == "by-name" and rows == [("BIG",)]


def test_projection_by_name_follows_answer_columns_order():
    rows, how = project_to_answer_columns(
        ["a", "b"], [(1, 2)], ["b", "a"])
    assert how == "by-name" and rows == [(2, 1)]


def test_projection_falls_back_to_as_is_when_the_generator_aliased():
    """A correct `SELECT SUM(ecMaxContribution)` returns a column named after
    the expression, not the bank's `total_eu_funding`. Same count, so the values
    decide - results_match ignores names for exactly this reason."""
    rows, how = project_to_answer_columns(
        ["sum(ecMaxContribution)"], [(42,)], ["total_eu_funding"])
    assert how == "as-is" and rows == [(42,)]


def test_projection_unmatched_when_neither_names_nor_counts_align():
    rows, how = project_to_answer_columns(
        ["n", "spare"], [(2127, 1)], ["count"])
    assert how == "unmatched" and rows is None


def test_projection_is_a_no_op_when_the_bank_pinned_nothing():
    for pinned in (None, []):
        rows, how = project_to_answer_columns(["n"], [(1,)], pinned)
        assert how == "none" and rows == [(1,)]


def test_columns_match_counts_not_names():
    # the generator may alias freely; the bank pins what the answer is made of
    assert columns_match(["count"], ["n"]) is True
    assert columns_match(["a", "b"], ["x", "y"]) is True
    assert columns_match(["count"], ["n", "spare"]) is False
    assert columns_match(None, ["n"]) is None                # nothing pinned
    assert columns_match([], ["n"]) is None
