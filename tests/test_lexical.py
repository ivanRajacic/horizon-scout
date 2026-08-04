"""Tests for the lexical BM25 retriever against the real DuckDB corpus.

FTS is pure SQL/DuckDB, so no llama servers are needed. The session fixture
reuses the already-built production index when fts_index_is_fresh() says it
matches the chunk table and config - the ~27s full-corpus rebuild (and its
write lock, which conflicts with a running horizon-draft MCP server) only
happens when the index is actually missing or stale. The builder itself keeps
end-to-end coverage through the tiny-corpus test at the bottom.
"""

import duckdb
import pytest

from src import config
from src.retrieval.base import SearchResult
from src.retrieval.lexical import (LexicalRetriever, build_fts_index,
                                   fts_index_is_fresh)

# EBOVAC1 / EBOVAC2: "Development of a Prophylactic Ebola Vaccine Using an
# Heterologous Prime-Boost Regimen". The acronym lives in the project title,
# so matching it exercises the acronym/title-concatenated search_text.
EBOLA_QUERY = "prophylactic Ebola vaccine prime-boost regimen"
EBOVAC_PROJECTS = {115854, 115861}


@pytest.fixture(scope="session")
def retriever():
    # Corpus-size agnostic (dev slice or full build): fresh means the FTS
    # table covers the chunk table exactly and was built with the configured
    # stemmer/stopwords, whatever the corpus size.
    if not fts_index_is_fresh():
        build_fts_index()
        assert fts_index_is_fresh(), "rebuild did not leave a fresh index"
    return LexicalRetriever()


def test_missing_index_raises(tmp_path):
    """A DB without a chunk_fts index refuses to serve with a clear error."""
    empty = tmp_path / "empty.duckdb"
    duckdb.connect(str(empty)).close()
    with pytest.raises(RuntimeError, match="build_fts_index"):
        LexicalRetriever(empty)


def test_distinctive_query_best_first(retriever):
    """A distinctive query returns best-first results with clean chunk text."""
    results = retriever.search(EBOLA_QUERY, k=10)
    assert results, "expected at least one hit"
    assert all(isinstance(r, SearchResult) for r in results)

    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True), "results not best-first"
    assert all(r.score is not None for r in results)

    # The returned text must be the CLEAN chunk.text verbatim, not the
    # acronym/title-concatenated search_text.
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    try:
        for r in results:
            clean = con.execute(
                "SELECT text FROM chunk WHERE chunk_id = ?",
                [r.chunk_id]).fetchone()[0]
            assert r.text == clean
    finally:
        con.close()


def test_acronym_title_matching(retriever):
    """An acronym/title term surfaces the right project in the top results."""
    results = retriever.search(EBOLA_QUERY, k=10)
    top_pids = {r.project_id for r in results}
    assert top_pids & EBOVAC_PROJECTS, (
        f"expected an EBOVAC project in top 10, got {sorted(top_pids)}")


def test_project_ids_filter(retriever):
    """project_ids restricts results to exactly the requested project."""
    results = retriever.search(EBOLA_QUERY, k=10, project_ids={115854})
    assert results, "expected hits within project 115854"
    assert {r.project_id for r in results} == {115854}


def test_source_filter(retriever):
    """source restricts results to that source only."""
    results = retriever.search("vaccine", k=30, source="objective")
    assert results, "expected objective-source hits"
    assert {r.source for r in results} == {"objective"}


def test_empty_project_ids_returns_empty(retriever):
    """An empty project_ids set matches nothing."""
    assert retriever.search(EBOLA_QUERY, k=10, project_ids=set()) == []


def test_bad_source_rejected(retriever):
    with pytest.raises(ValueError):
        retriever.search("vaccine", source="bogus")


def _tiny_corpus(db_path):
    """Three chunks, two projects. The DRAGON acronym and its title words
    appear only in the project row, mirroring the EBOVAC case: matching them
    proves the build indexed the acronym/title-concatenated search_text."""
    con = duckdb.connect(str(db_path))
    try:
        con.execute(
            "CREATE TABLE project (id BIGINT, acronym VARCHAR, title VARCHAR)")
        con.execute(
            "CREATE TABLE chunk (chunk_id VARCHAR PRIMARY KEY, "
            "project_id BIGINT, source VARCHAR, section VARCHAR, "
            "n_tokens INTEGER, text VARCHAR)")
        con.execute("INSERT INTO project VALUES "
                    "(1, 'DRAGON', 'Dragonfly wing telemetry platform'), "
                    "(2, 'SOIL', 'Alpine soil chemistry')")
        con.executemany(
            "INSERT INTO chunk VALUES (?, ?, ?, ?, ?, ?)",
            [["c1", 1, "report", "summary", 8,
              "Sensors record wingbeat data in free flight."],
             ["c2", 2, "objective", None, 7,
              "Nutrient cycling in high-altitude meadows."],
             ["c3", 2, "report", "summary", 6,
              "Field measurements of nitrogen content."]])
    finally:
        con.close()


def test_build_and_freshness_on_a_tiny_corpus(tmp_path, monkeypatch):
    """build_fts_index end-to-end on a hand-made corpus: the freshness check
    flips stale -> fresh across the build, a title-only term matches, the
    clean chunk text comes back, and a config change reads as stale again."""
    db = tmp_path / "tiny.duckdb"
    _tiny_corpus(db)
    assert not fts_index_is_fresh(db)

    assert build_fts_index(db) == 3
    assert fts_index_is_fresh(db)

    results = LexicalRetriever(db).search("dragonfly telemetry", k=5)
    assert results and results[0].project_id == 1
    assert results[0].text == "Sensors record wingbeat data in free flight."

    monkeypatch.setattr(config, "FTS_STEMMER", "none")
    assert not fts_index_is_fresh(db)
