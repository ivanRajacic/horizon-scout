"""Drafting MCP server smoke tests (M5): SELECT works, every write path is
rejected, caps and true counts behave, SQL errors are results not
exceptions, the connection is genuinely read-only, calls are logged.
search_corpus and get_project_text run against FAKED retrievers - nothing
here needs a llama server or a real index."""

import json

import duckdb
import pytest

from src.config import (CORPUS_PROFILE_VERSION, RUNTIME_RETRIEVER,
                        SCHEMA_DOCS_VERSION)
from src.eval import mcp_server
from src.eval.mcp_server import (ServerConfig, get_bank_questions,
                                 get_corpus_profile, get_project_text,
                                 get_schema_docs, precheck_candidate,
                                 precheck_record, run_sql, search_corpus)
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
        "(3, 'GAMMA', 'Gamma title', 'Gamma objective'), "
        # No objective and no report row: the textless-gold case.
        "(4, 'DELTA', 'Delta title', NULL)")
    con.execute(
        "CREATE TABLE report_text (projectID BIGINT, title VARCHAR, "
        "teaser VARCHAR, summary VARCHAR, workPerformed VARCHAR, "
        "finalResults VARCHAR)")
    con.execute(
        "INSERT INTO report_text VALUES "
        "(1, 'Alpha report', 'Alpha teaser', 'Alpha summary', "
        "'Alpha work', 'Alpha results')")
    # precheck_candidate places a map entry's read projects in their bucket.
    con.execute(
        "CREATE TABLE euroscivoc (projectID BIGINT, euroSciVocPath VARCHAR, "
        "euroSciVocTitle VARCHAR)")
    con.execute(
        "INSERT INTO euroscivoc VALUES "
        "(1, 'natural sciences/biological sciences/ecology', 'ecology'), "
        "(2, 'natural sciences/biological sciences/genetics', 'genetics'), "
        "(3, 'humanities/arts/music', 'music')")
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
    result = search_corpus("widgets", condition="pooled", k=5)
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
    result = search_corpus("widgets", condition="pooled")
    assert set(result) == {"error"}
    assert "dense" in result["error"] and "embed server down" in result["error"]
    # Single condition: same contract.
    result = search_corpus("widgets", condition="dense")
    assert set(result) == {"error"}


def test_search_defaults_to_the_runtime_retriever_not_pooled(fakes):
    # 2026-08-03: the default went pooled -> config.RUNTIME_RETRIEVER, so an
    # ordinary check asks about the stack the system answers with. Pooling is
    # now opt-in, and the two jobs that still need it (gold labelling, ADV
    # absence proofs) pass condition="pooled" explicitly.
    fakes["lexical"].results = [sr("L1", 1)]
    fakes["dense"].results = [sr("D2", 2)]
    fakes["hybrid_rerank"].results = [sr("R3", 3)]
    result = search_corpus("widgets")
    assert result["condition"] == RUNTIME_RETRIEVER == "hybrid_rerank"
    # Only the one condition ran: the other three were never called.
    assert [p["project_id"] for p in result["projects"]] == [3]
    assert not fakes["lexical"].calls and not fakes["dense"].calls


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


def test_search_snippet_chars_omitted_keeps_the_historical_shape(fakes):
    # No `truncated` key and full chunk text when the parameter is absent -
    # existing callers see byte-identical output.
    fakes["lexical"].results = [sr("c1", 1, text="x" * 500)]
    result = search_corpus("widgets", condition="lexical")
    assert "truncated" not in result
    assert result["projects"][0]["best_chunk"]["text"] == "x" * 500


def test_search_snippet_chars_zero_omits_text_keeps_ranks(fakes):
    # A liveness probe needs ranks, not payload.
    fakes["lexical"].results = [sr("c1", 1, text="x" * 500), sr("c2", 2)]
    result = search_corpus("widgets", condition="lexical", snippet_chars=0)
    for project in result["projects"]:
        assert "text" not in project["best_chunk"]
        assert project["best_chunk"]["chunk_id"]    # the rest survives
    assert [p["project_id"] for p in result["projects"]] == [1, 2]
    assert result["truncated"]["chunks_truncated"] == 2
    assert result["truncated"]["chars_dropped"] == 500 + len("text of c2")


def test_search_snippet_chars_truncates_and_reports(fakes):
    fakes["lexical"].results = [sr("c1", 1, text="x" * 500),
                                sr("c2", 2, text="short")]
    result = search_corpus("widgets", condition="lexical", snippet_chars=400)
    long_chunk, short_chunk = [p["best_chunk"] for p in result["projects"]]
    assert long_chunk["text"] == "x" * 400
    assert short_chunk["text"] == "short"       # under the cap: untouched
    assert result["truncated"] == {"snippet_chars": 400, "chars_dropped": 100,
                                   "chunks_truncated": 1}


def test_search_snippet_chars_under_cap_reports_no_truncation(fakes):
    fakes["lexical"].results = [sr("c1", 1, text="short")]
    result = search_corpus("widgets", condition="lexical", snippet_chars=400)
    assert result["truncated"] is None
    assert result["projects"][0]["best_chunk"]["text"] == "short"


def test_search_snippet_chars_validation(fakes):
    assert "error" in search_corpus("widgets", snippet_chars=-1)
    assert "error" in search_corpus("widgets", snippet_chars=True)


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


# --- precheck_record: the drafter's deterministic self-gate ---------------

def status_of(result, name):
    return next(c["status"] for c in result["checks"] if c["name"] == name)


def status_detail(result, name):
    return next(c["detail"] for c in result["checks"] if c["name"] == name)


def sql_record(**overrides):
    record = {
        "question_id": "sql-90", "text": "How many rows are there?",
        "expected_route": "sql", "level": "L1", "subtype": "aggregate",
        "gold_sql": "SELECT COUNT(*) AS n FROM t",
        "answer_columns": ["n"],
        "schema_docs_hash": fingerprint(DOCS_TEXT)}
    record.update(overrides)
    return record


def hybrid_record(**overrides):
    record = {
        "question_id": "hyb-90", "text": "What do the early projects do?",
        "expected_route": "hybrid", "level": "L1", "subtype": "filter-read",
        "term_style": "exact-term", "gold_project_ids": [1],
        "filter_evidence": {
            "filter_sql": "SELECT id FROM project WHERE id < 3",
            "survivor_count": 2, "survivor_ids": [1, 2],
            "schema_docs_hash": fingerprint(DOCS_TEXT)}}
    record.update(overrides)
    return record


def test_precheck_passes_a_clean_sql_record(server):
    result = precheck_record(sql_record())
    assert result["ok"] and result["failures"] == []
    assert status_of(result, "GOLD-SQL") == "PASS"
    assert status_of(result, "ANSWER-COLUMNS") == "PASS"
    assert status_of(result, "SCHEMA-DOCS") == "PASS"
    # Nothing topical to check on a SQL record.
    assert status_of(result, "GOLD-TEXT") == "N/A"
    assert status_of(result, "FILTER-SURVIVORS") == "N/A"


def test_precheck_accepts_a_json_string(server):
    # Agents sometimes hand a tool the JSON line rather than the object.
    assert precheck_record(json.dumps(sql_record()))["ok"]
    assert "error" in precheck_record("{not json")
    assert "error" in precheck_record(["nope"])
    assert "error" in precheck_record({"text": "no id"})


def test_precheck_fails_an_empty_gold_result(server):
    result = precheck_record(sql_record(
        gold_sql="SELECT i AS n FROM t WHERE i > 1000"))
    assert not result["ok"] and result["failures"] == ["GOLD-SQL"]
    assert "0 rows" in status_detail(result, "GOLD-SQL")


def test_precheck_fails_broken_and_guarded_gold_sql(server):
    broken = precheck_record(sql_record(gold_sql="SELECT * FROM nope"))
    assert broken["failures"] == ["GOLD-SQL"]
    guarded = precheck_record(sql_record(gold_sql="DROP TABLE t"))
    assert "guardrail" in status_detail(guarded, "GOLD-SQL")


def test_precheck_requires_gold_sql_on_the_sql_ladder(server):
    result = precheck_record(sql_record(gold_sql=None))
    assert status_of(result, "GOLD-SQL") == "FAIL"
    assert "SQL ladder entry requires one" in status_detail(result, "GOLD-SQL")


def test_precheck_catches_answer_columns_absent_from_the_result(server):
    result = precheck_record(sql_record(answer_columns=["n", "total_cost"]))
    assert result["failures"] == ["ANSWER-COLUMNS"]
    assert "total_cost" in status_detail(result, "ANSWER-COLUMNS")


def test_precheck_checks_gold_projects_exist_and_carry_text(server):
    assert precheck_record(hybrid_record())["ok"]
    absent = precheck_record(hybrid_record(
        gold_project_ids=[1, 999],
        filter_evidence={**hybrid_record()["filter_evidence"],
                         "filter_sql": "SELECT id FROM project WHERE id < 3 "
                                       "OR id = 999"}))
    assert "GOLD-TEXT" in absent["failures"]
    assert "999" in status_detail(absent, "GOLD-TEXT")
    textless = precheck_record(hybrid_record(
        gold_project_ids=[4],
        filter_evidence={**hybrid_record()["filter_evidence"],
                         "filter_sql": "SELECT id FROM project WHERE id = 4",
                         "survivor_count": 1, "survivor_ids": [4]}))
    assert "GOLD-TEXT" in textless["failures"]
    assert "no stored text: [4]" in status_detail(textless, "GOLD-TEXT")


def test_precheck_zero_match_gold_is_not_a_text_failure(server):
    result = precheck_record({
        "question_id": "adv-90", "text": "Any projects on warp drives?",
        "expected_route": "vector", "level": "ADV", "subtype": "zero-match",
        "gold_project_ids": []})
    assert status_of(result, "GOLD-TEXT") == "N/A"
    assert result["ok"]


def test_precheck_catches_drifted_filter_survivors(server):
    result = precheck_record(hybrid_record(filter_evidence={
        "filter_sql": "SELECT id FROM project WHERE id < 3",
        "survivor_count": 3, "survivor_ids": [1, 2, 3],
        "schema_docs_hash": fingerprint(DOCS_TEXT)}))
    assert result["failures"] == ["FILTER-SURVIVORS"]
    assert "recorded-but-gone [3]" in status_detail(result,
                                                    "FILTER-SURVIVORS")


def test_precheck_catches_gold_outside_the_live_survivors(server):
    result = precheck_record(hybrid_record(gold_project_ids=[3]))
    assert result["failures"] == ["GOLD-SUBSET"]
    assert "[3]" in status_detail(result, "GOLD-SUBSET")


def test_precheck_requires_filter_evidence_on_the_hybrid_ladder(server):
    result = precheck_record(hybrid_record(filter_evidence=None))
    assert status_of(result, "FILTER-SURVIVORS") == "FAIL"
    assert status_of(result, "GOLD-SUBSET") == "N/A"


def test_precheck_survivor_window_pass_and_warn_never_gate(server):
    # filter-read window is 2-10; the fixture filter has 2 live survivors.
    inside = precheck_record(hybrid_record())
    assert status_of(inside, "SURVIVOR-WINDOW") == "PASS"
    # filter-synthesize wants 5-20 - 2 survivors is outside, but a WARN,
    # never a FAIL: the windows are drafting guidance, not law.
    outside = precheck_record(hybrid_record(
        level="L2", subtype="filter-synthesize", gold_project_ids=[1, 2]))
    assert status_of(outside, "SURVIVOR-WINDOW") == "WARN"
    assert "5-20" in status_detail(outside, "SURVIVOR-WINDOW")
    assert outside["ok"] and "SURVIVOR-WINDOW" not in outside["failures"]


def test_precheck_survivor_window_na_without_a_live_set(server):
    result = precheck_record(hybrid_record(filter_evidence=None))
    assert status_of(result, "SURVIVOR-WINDOW") == "N/A"
    sql = precheck_record(sql_record())
    assert status_of(sql, "SURVIVOR-WINDOW") == "N/A"


def test_precheck_gold_bounds_hybrid_hangs_off_the_subtype(server):
    # filter-read wants exactly 1 gold: the fixture passes.
    assert status_of(precheck_record(hybrid_record()), "GOLD-BOUNDS") == "PASS"
    # filter-synthesize wants 2-4 gold; 1 is a FAIL - and it fires inside
    # the drafter's loop instead of at slot close.
    result = precheck_record(hybrid_record(
        level="L2", subtype="filter-synthesize"))
    assert "GOLD-BOUNDS" in result["failures"]
    assert "[2,4]" in status_detail(result, "GOLD-BOUNDS")
    # filter-compare is L3 with |gold| in [2,4] - the hyb-03 shape. The
    # VECTOR level windows (L3 >= 5) must NOT apply to hybrid.
    compare = precheck_record(hybrid_record(
        level="L3", subtype="filter-compare", gold_project_ids=[1, 2]))
    assert status_of(compare, "GOLD-BOUNDS") == "PASS"


def test_precheck_gold_bounds_vector_uses_the_level_windows(server):
    def vector_record(level, gold):
        return {"question_id": "vec-90", "text": "Find the fungi projects.",
                "expected_route": "vector", "level": level,
                "subtype": "single-project", "gold_project_ids": gold}
    assert status_of(precheck_record(vector_record("L1", [1])),
                     "GOLD-BOUNDS") == "PASS"
    result = precheck_record(vector_record("L2", [1]))
    assert "GOLD-BOUNDS" in result["failures"]
    assert "DEFINED by the count" in status_detail(result, "GOLD-BOUNDS")


def test_precheck_gold_bounds_na_without_gold(server):
    assert status_of(precheck_record(sql_record()), "GOLD-BOUNDS") == "N/A"


def test_precheck_catches_a_stale_schema_docs_hash(server):
    result = precheck_record(sql_record(schema_docs_hash="deadbeef0000"))
    assert result["failures"] == ["SCHEMA-DOCS"]
    detail = status_detail(result, "SCHEMA-DOCS")
    assert "deadbeef0000" in detail and fingerprint(DOCS_TEXT) in detail


def test_precheck_is_logged(server):
    precheck_record(sql_record())
    precheck_record(sql_record(schema_docs_hash="deadbeef0000"))
    precheck_record("{not json")
    lines = [json.loads(l) for l in
             server.log_path.read_text(encoding="utf-8").splitlines()]
    assert [(e["tool"], e["ok"]) for e in lines
            if e["tool"] == "precheck_record"] == [
        ("precheck_record", True), ("precheck_record", False),
        ("precheck_record", False)]


# --- precheck_candidate: the explorer's deterministic self-gate -----------

BIO = "natural sciences / biological sciences"


def candidate(**overrides):
    block = {
        "id": "vector-07",
        "topic": "Two projects reading DNA out of field samples.",
        "recommend": "route=vector level=L2 subtype=comparison",
        "bucket": BIO,
        "satisfying_count": 2,
        "topic_filter": "p.objective ILIKE '%Alpha%' "
                        "OR p.objective ILIKE '%Beta%'",
        "evidence": [{"sql": "SELECT COUNT(*) AS n FROM project WHERE id < 3",
                      "key_result": "2 projects"}],
        "axes": "branch=biological-sciences satisfying=2",
        "why": "Two members, distinct methods."}
    block.update(overrides)
    return block


def map_entry(**overrides):
    entry = {
        "bucket": BIO,
        "about": "Two fellowships on lake fungi and on genome assembly, both "
                 "field-collection work rather than theory.",
        "texture": "Report text present for one of the two.",
        "read": [1, 2],
        "read_first": [1, 2],
        "evidence": {"sql": "SELECT COUNT(*) AS n FROM project WHERE id < 3",
                     "key_result": "2 projects"}}
    entry.update(overrides)
    return entry


def test_precheck_candidate_passes_a_clean_block(server):
    result = precheck_candidate(candidate(), bucket=BIO)
    assert result["ok"] and result["failures"] == []
    assert status_of(result, "LEVEL vector-07") == "PASS"


def test_precheck_candidate_accepts_a_json_string(server):
    assert precheck_candidate(json.dumps(candidate()))["ok"]
    assert "error" in precheck_candidate("{not json")
    assert "error" in precheck_candidate(["nope"])


def test_precheck_candidate_catches_a_number_that_does_not_reproduce(server):
    result = precheck_candidate(candidate(evidence=[{
        "sql": "SELECT COUNT(*) AS n FROM project WHERE id < 3",
        "key_result": "11 projects"}]))
    assert not result["ok"]
    assert "11" in status_detail(result, "EVIDENCE vector-07")


def test_precheck_candidate_derives_the_level_without_the_bucket_fence(server):
    """The explorer counts inside its bucket; the question will not. So the
    level comes from the topic_filter run over the whole corpus, and a
    recommended level that disagrees with it is refused in the agent's own
    loop rather than three nodes later."""
    result = precheck_candidate(candidate(
        topic_filter="p.acronym = 'ALPHA'"))       # 1 project -> L1, not L2
    assert result["failures"] == ["LEVEL vector-07"]
    detail = status_detail(result, "LEVEL vector-07")
    assert "L2" in detail and "L1" in detail and "corpus-wide" in detail


def test_precheck_candidate_catches_an_unqueried_satisfying_count(server):
    result = precheck_candidate(candidate(satisfying_count=7))
    assert result["failures"] == ["COUNT vector-07"]
    assert "satisfying_count=7" in status_detail(result, "COUNT vector-07")


def test_precheck_candidate_catches_a_survivor_count_outside_its_window(server):
    """The hyb-02 birth-failure, refused before the candidate is emitted."""
    result = precheck_candidate(candidate(
        id="hybrid-11",
        recommend="route=hybrid level=L1 subtype=filter-read",
        survivor_count=40))
    assert "WINDOW hybrid-11" in result["failures"]
    assert "2-10" in status_detail(result, "WINDOW hybrid-11")


def test_precheck_candidate_catches_a_stray_bucket(server):
    result = precheck_candidate(candidate(), bucket="humanities / arts")
    assert "SLICE vector-07" in result["failures"]


def test_precheck_candidate_checks_a_map_entry(server):
    assert precheck_candidate(map_entry(), bucket=BIO)["ok"]

    strayed = precheck_candidate(map_entry(read=[1, 3]), bucket=BIO)
    assert "MAP-MEMBER" in strayed["failures"]

    echo = precheck_candidate(
        map_entry(about="Natural sciences: biological sciences.",
                  texture="Biological sciences, naturally."), bucket=BIO)
    assert "MAP-ORIGINAL" in echo["failures"]

    thin = precheck_candidate(map_entry(read=[1]), bucket=BIO)
    assert "MAP-READ" in thin["failures"]


def test_precheck_candidate_one_reading_leaf_passes(server):
    # 'ecology' has exactly one (path, title) row: the title reading and the
    # subtree reading select the identical set, so the scope is unambiguous.
    result = precheck_candidate(candidate(
        satisfying_count=1, evidence=[{
            "sql": "SELECT COUNT(DISTINCT projectID) AS n FROM euroscivoc "
                   "WHERE euroSciVocTitle = 'ecology'",
            "key_result": "1 project"}]))
    assert result["ok"]
    assert status_of(result, "ONE-READING vector-07") == "PASS"
    assert "leaf" in status_detail(result, "ONE-READING vector-07")


def test_precheck_candidate_one_reading_branch_warns_never_gates(server):
    # 'biological sciences' sits above ecology and genetics: the narrow and
    # wide readings diverge - the musicology defect class. WARN with the
    # sibling rows attached, and `ok` stays true (the explorer is told what
    # it is proposing, not blocked).
    result = precheck_candidate(candidate(evidence=[{
        "sql": "SELECT COUNT(DISTINCT projectID) AS n FROM euroscivoc "
               "WHERE euroSciVocPath LIKE '%/biological sciences%'",
        "key_result": "2 projects"}]))
    assert result["ok"] and result["failures"] == []
    assert status_of(result, "ONE-READING vector-07") == "WARN"
    detail = status_detail(result, "ONE-READING vector-07")
    assert "ecology" in detail and "genetics" in detail


def test_precheck_candidate_one_reading_unrecognised_shape_is_na(server):
    # Touches euroscivoc but with no recognisable title/path predicate: the
    # check must say so rather than guess a term.
    result = precheck_candidate(candidate(evidence=[{
        "sql": "SELECT COUNT(DISTINCT projectID) AS n FROM euroscivoc",
        "key_result": "3 projects"}]))
    assert status_of(result, "ONE-READING vector-07") == "N/A"


def test_precheck_candidate_one_reading_absent_without_euroscivoc(server):
    result = precheck_candidate(candidate())
    assert not any(c["name"].startswith("ONE-READING")
                   for c in result["checks"])


def test_precheck_candidate_is_logged(server):
    precheck_candidate(candidate())
    precheck_candidate(candidate(satisfying_count=7))
    lines = [json.loads(l) for l in
             server.log_path.read_text(encoding="utf-8").splitlines()]
    assert [(e["tool"], e["ok"]) for e in lines] == [
        ("precheck_candidate", True), ("precheck_candidate", False)]


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
