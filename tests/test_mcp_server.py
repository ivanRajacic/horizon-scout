"""Drafting MCP server smoke tests (M5): SELECT works, every write path is
rejected, caps and true counts behave, SQL errors are results not
exceptions, the connection is genuinely read-only, calls are logged.
search_corpus and get_project_text run against FAKED retrievers - nothing
here needs a llama server or a real index."""

import json

import duckdb
import pytest

from src.config import CORPUS_PROFILE_VERSION, SCHEMA_DOCS_VERSION
from src.eval import mcp_server
from src.eval.mcp_server import (ServerConfig, get_bank_questions,
                                 get_corpus_profile, get_project_text,
                                 get_schema_docs, run_sql, search_corpus)
from src.llm import fingerprint
from src.retrieval.base import SearchResult

DOCS_TEXT = "# schema docs\ntable t: i INTEGER, name VARCHAR\n"

PROFILE_TEXT = (
    "# Corpus profile\npreamble before any section\n"
    "## Header\nversion cp1\n"
    "## SQL\ntrap pair: a vs b\n"
    "## Coverage ledger\naxes: country, scheme\n")

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


INDEX_META = {"embedding_model": "test-embed.gguf", "n_vectors": 5,
              "built_at": "2026-07-22T00:00:00+00:00"}


@pytest.fixture
def server(tmp_path, monkeypatch):
    db = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE t (i INTEGER, name VARCHAR)")
    con.execute("INSERT INTO t SELECT range, 'row' || range FROM range(10)")
    con.execute(
        "CREATE TABLE project (id BIGINT, acronym VARCHAR, title VARCHAR, "
        "objective VARCHAR)")
    con.execute(
        "INSERT INTO project VALUES "
        "(1, 'ALPHA', 'Alpha title', 'Alpha objective'), "
        "(2, 'BETA', 'Beta title', 'Beta objective'), "
        "(3, 'GAMMA', 'Gamma title', 'Gamma objective')")
    con.execute(
        "CREATE TABLE report_text (projectID BIGINT, title VARCHAR, "
        "teaser VARCHAR, summary VARCHAR, workPerformed VARCHAR, "
        "finalResults VARCHAR)")
    con.execute(
        "INSERT INTO report_text VALUES "
        "(1, 'Alpha report', 'Alpha teaser', 'Alpha summary', "
        "'Alpha work', 'Alpha results')")
    con.close()
    docs = tmp_path / "schema_docs.md"
    docs.write_text(DOCS_TEXT, encoding="utf-8")
    bank = tmp_path / "bank.jsonl"
    bank.write_text("\n".join(json.dumps(r) for r in BANK_RECORDS),
                    encoding="utf-8")
    index_meta = tmp_path / "index_meta.json"
    index_meta.write_text(json.dumps(INDEX_META), encoding="utf-8")
    profile = tmp_path / "corpus_profile.md"
    profile.write_text(PROFILE_TEXT, encoding="utf-8")
    cfg = ServerConfig(db_path=db, bank_path=bank, schema_docs_path=docs,
                       log_path=tmp_path / "draft_mcp.jsonl",
                       index_meta_path=index_meta,
                       corpus_profile_path=profile)
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


def test_get_corpus_profile_full(server):
    result = get_corpus_profile()
    assert result["markdown"] == PROFILE_TEXT
    assert result["section"] is None
    assert result["sections"] == ["header", "sql", "coverage-ledger"]
    assert result["version"] == CORPUS_PROFILE_VERSION
    assert result["content_hash"] == fingerprint(PROFILE_TEXT)


def test_get_corpus_profile_section(server):
    result = get_corpus_profile(section="coverage-ledger")
    assert result["markdown"] == "## Coverage ledger\naxes: country, scheme\n"
    assert result["section"] == "coverage-ledger"
    # Provenance is of the FULL file even for a partial read.
    assert result["content_hash"] == fingerprint(PROFILE_TEXT)


def test_get_corpus_profile_unknown_section(server):
    result = get_corpus_profile(section="vector")
    assert set(result) == {"error"}
    assert "vector" in result["error"]
    assert "header, sql, coverage-ledger" in result["error"]


def test_get_corpus_profile_missing_file(server):
    server.corpus_profile_path.unlink()
    result = get_corpus_profile()
    assert set(result) == {"error"}
    assert "not built yet" in result["error"]


def test_get_corpus_profile_is_logged(server):
    get_corpus_profile(section="sql")
    get_corpus_profile(section="nope")
    lines = [json.loads(l) for l in
             server.log_path.read_text(encoding="utf-8").splitlines()]
    assert [(e["tool"], e["ok"]) for e in lines] == [
        ("get_corpus_profile", True), ("get_corpus_profile", False)]


# --- search_corpus + get_project_text (faked retrievers, no servers) ---

def sr(chunk_id, project_id, score=1.0, text=None):
    acronyms = {1: "ALPHA", 2: "BETA", 3: "GAMMA"}
    acronym = acronyms.get(project_id, f"P{project_id}")
    return SearchResult(
        chunk_id=chunk_id, project_id=project_id, acronym=acronym,
        title=f"{acronym} title", source="objective", section="objective",
        score=score, text=text or f"text of {chunk_id}")


class FakeRetriever:
    """Best-first canned results; honours the project_ids post-filter and
    records every call so tests can assert what the tool passed down."""

    def __init__(self, results=(), error=None):
        self.results = list(results)
        self.error = error
        self.calls = []

    def search(self, query, k=10, project_ids=None, source=None):
        self.calls.append({"query": query, "k": k,
                           "project_ids": project_ids})
        if self.error is not None:
            raise self.error
        hits = [r for r in self.results
                if project_ids is None or r.project_id in project_ids]
        return hits[:k]


@pytest.fixture
def fakes(server, monkeypatch):
    built = {name: FakeRetriever() for name in mcp_server.SEARCH_CONDITIONS}
    monkeypatch.setattr(mcp_server, "_get_retriever", lambda name: built[name])
    return built


def test_search_single_condition_collapses_to_projects(fakes):
    # Two chunks of project 1 must occupy ONE project slot, best chunk kept.
    fakes["lexical"].results = [
        sr("c1a", 1, text="best alpha chunk"), sr("c1b", 1), sr("c2", 2),
        sr("c3", 3)]
    result = search_corpus("widgets", condition="lexical", k=2)
    assert "error" not in result
    assert [p["project_id"] for p in result["projects"]] == [1, 2]
    p1, p2 = result["projects"]
    assert p1["ranks"] == {"lexical": 1}
    assert p1["best_chunk"]["chunk_id"] == "c1a"
    assert p1["best_chunk"]["text"] == "best alpha chunk"
    assert p2["ranks"] == {"lexical": 2}
    assert result["per_condition_project_counts"] == {"lexical": 2}
    # Chunk over-fetch: the retriever saw k * overfetch, the result reports k.
    assert fakes["lexical"].calls[0]["k"] == 2 * mcp_server.SEARCH_CHUNK_OVERFETCH
    assert result["k"] == 2


def test_search_pooled_union_rank_matrix_and_order(fakes):
    fakes["lexical"].results = [sr("L1", 1), sr("L2", 2)]
    fakes["dense"].results = [sr("D2", 2, text="dense beta"), sr("D3", 3)]
    fakes["hybrid"].results = [sr("H2", 2)]
    result = search_corpus("widgets", k=5)
    assert "error" not in result
    # Project 2: best rank 1 in three conditions -> first. Then project 1
    # (rank 1, one condition), then project 3 (best rank 2).
    assert [p["project_id"] for p in result["projects"]] == [2, 1, 3]
    p2 = result["projects"][0]
    assert p2["ranks"] == {"lexical": 2, "dense": 1, "hybrid": 1,
                           "hybrid_rerank": None}
    # Rank tie between dense and hybrid: earliest condition order wins, but
    # dense comes after lexical whose rank is 2 - best_chunk is dense's.
    assert p2["best_chunk"]["condition"] == "dense"
    assert p2["best_chunk"]["text"] == "dense beta"
    assert result["index_meta"]["embedding_model"] == "test-embed.gguf"
    assert result["index_meta"]["content_hash"] == fingerprint(
        json.dumps(INDEX_META))


def test_search_condition_failure_is_error_result(fakes):
    fakes["dense"].error = ConnectionError("embed server down")
    # Pooled: one dead condition fails the whole call (no partial pooling).
    result = search_corpus("widgets")
    assert set(result) == {"error"}
    assert "dense" in result["error"] and "embed server down" in result["error"]
    # Single condition: same contract.
    result = search_corpus("widgets", condition="dense")
    assert set(result) == {"error"}


def test_search_input_validation(fakes):
    assert "error" in search_corpus("  ")
    assert "sparse" in search_corpus("widgets", condition="sparse")["error"]
    assert "error" in search_corpus("widgets", scope_project_ids=[])
    assert "error" in search_corpus("widgets", scope_project_ids=[1, "2"])


def test_search_k_clamped_to_ceiling(fakes):
    fakes["lexical"].results = [sr("c1", 1)]
    result = search_corpus("widgets", condition="lexical", k=500)
    assert result["k"] == mcp_server.SEARCH_K_CEILING
    assert fakes["lexical"].calls[0]["k"] == (
        mcp_server.SEARCH_K_CEILING * mcp_server.SEARCH_CHUNK_OVERFETCH)


def test_search_scope_passed_through_and_echoed(fakes):
    fakes["lexical"].results = [sr("c1", 1), sr("c2", 2), sr("c3", 3)]
    result = search_corpus("widgets", condition="lexical",
                           scope_project_ids=[1, 3])
    assert fakes["lexical"].calls[0]["project_ids"] == {1, 3}
    assert [p["project_id"] for p in result["projects"]] == [1, 3]
    assert result["scope_size"] == 2
    assert search_corpus("widgets", condition="lexical")["scope_size"] is None


def test_search_scope_ceiling(fakes):
    too_big = list(range(mcp_server.SCOPE_CEILING + 1))
    result = search_corpus("widgets", condition="lexical",
                           scope_project_ids=too_big)
    assert set(result) == {"error"}
    assert "ceiling" in result["error"]


def test_search_missing_index_meta_is_error(fakes, server):
    server.index_meta_path.unlink()
    result = search_corpus("widgets", condition="lexical")
    assert set(result) == {"error"}
    assert "index_meta" in result["error"]


def test_get_project_text_found_report_and_missing(server):
    result = get_project_text([2, 1, 999])
    assert "error" not in result
    assert result["missing"] == [999]
    beta, alpha = result["projects"]  # requested order preserved
    assert beta["project_id"] == 2 and beta["objective"] == "Beta objective"
    assert beta["report"] is None
    assert alpha["acronym"] == "ALPHA"
    assert alpha["report"] == {
        "title": "Alpha report", "teaser": "Alpha teaser",
        "summary": "Alpha summary", "workPerformed": "Alpha work",
        "finalResults": "Alpha results"}


def test_get_project_text_fields_selects_a_subset(server):
    result = get_project_text([1], fields=["objective", "teaser"])
    assert result["projects"] == [
        {"project_id": 1, "objective": "Alpha objective",
         "report": {"teaser": "Alpha teaser"}}]
    assert result["truncated"] is None


def test_get_project_text_fields_project_only_drops_the_report(server):
    # No report field asked for -> no `report` key at all, so a caller that
    # only wants the objective never pays for the report join's text.
    result = get_project_text([1], fields=["acronym", "objective"])
    assert result["projects"] == [
        {"project_id": 1, "acronym": "ALPHA", "objective": "Alpha objective"}]


def test_get_project_text_report_title_is_distinct_from_project_title(server):
    result = get_project_text([1], fields=["title", "report_title"])
    assert result["projects"] == [
        {"project_id": 1, "title": "Alpha title",
         "report": {"title": "Alpha report"}}]


def test_get_project_text_report_fields_survive_a_missing_report(server):
    # Project 2 has no report row: the key stays, the value is None.
    result = get_project_text([2], fields=["summary"])
    assert result["projects"] == [{"project_id": 2, "report": None}]


def test_get_project_text_rejects_unknown_fields(server):
    assert "error" in get_project_text([1], fields=["objectve"])
    assert "error" in get_project_text([1], fields=[])
    assert "error" in get_project_text([1], fields="objective")


def test_get_project_text_max_chars_truncates_longest_first(server):
    long_db = server.db_path.parent / "long.duckdb"
    con = duckdb.connect(str(long_db))
    con.execute("CREATE TABLE project (id BIGINT, acronym VARCHAR, "
                "title VARCHAR, objective VARCHAR)")
    con.execute("INSERT INTO project VALUES (1, 'A', 'ab', ?)",
                ["x" * 1000])
    con.execute("CREATE TABLE report_text (projectID BIGINT, title VARCHAR, "
                "teaser VARCHAR, summary VARCHAR, workPerformed VARCHAR, "
                "finalResults VARCHAR)")
    con.close()
    server.db_path = long_db

    result = get_project_text([1], fields=["title", "objective"],
                              max_chars=100)
    project = result["projects"][0]
    # Water-filling spends the budget on breadth: the 2-char title survives
    # whole, the 1000-char objective absorbs the whole cut.
    assert project["title"] == "ab"
    assert project["objective"] == "x" * 98
    assert result["truncated"] == {
        "max_chars": 100, "field_char_cap": 98, "chars_dropped": 902,
        "fields_truncated": 1}


def test_get_project_text_max_chars_under_budget_is_a_no_op(server):
    result = get_project_text([1], max_chars=100_000)
    assert result["truncated"] is None
    assert result["projects"][0]["objective"] == "Alpha objective"


def test_get_project_text_rejects_bad_max_chars(server):
    assert "error" in get_project_text([1], max_chars=0)
    assert "error" in get_project_text([1], max_chars=-5)
    assert "error" in get_project_text([1], max_chars=True)


def test_get_project_text_validation_and_cap(server):
    assert "error" in get_project_text([])
    assert "error" in get_project_text(["1"])
    assert "error" in get_project_text(list(range(mcp_server.PROJECT_TEXT_CAP + 1)))
    # Duplicates collapse before the cap check.
    result = get_project_text([1] * (mcp_server.PROJECT_TEXT_CAP + 5))
    assert "error" not in result
    assert [p["project_id"] for p in result["projects"]] == [1]


def test_new_tools_are_logged(fakes, server):
    fakes["lexical"].results = [sr("c1", 1)]
    search_corpus("widgets", condition="lexical")
    search_corpus("widgets", condition="nope")
    get_project_text([1])
    lines = [json.loads(l) for l in
             server.log_path.read_text(encoding="utf-8").splitlines()]
    assert [(e["tool"], e["ok"]) for e in lines] == [
        ("search_corpus", True), ("search_corpus", False),
        ("get_project_text", True)]
