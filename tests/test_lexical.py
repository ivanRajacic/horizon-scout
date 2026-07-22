"""Tests for the lexical BM25 retriever against the real DuckDB corpus.

FTS is pure SQL/DuckDB, so no llama servers are needed. The session fixture
builds the index once; all tests then query the read-only LexicalRetriever.
"""

import duckdb
import pytest

from src import config
from src.retrieval.base import SearchResult
from src.retrieval.lexical import LexicalRetriever, build_fts_index

# EBOVAC1 / EBOVAC2: "Development of a Prophylactic Ebola Vaccine Using an
# Heterologous Prime-Boost Regimen". The acronym lives in the project title,
# so matching it exercises the acronym/title-concatenated search_text.
EBOLA_QUERY = "prophylactic Ebola vaccine prime-boost regimen"
EBOVAC_PROJECTS = {115854, 115861}


@pytest.fixture(scope="session")
def retriever():
    n = build_fts_index()
    # Corpus-size agnostic (dev slice or full build): the FTS table must
    # cover the chunk table exactly, whatever its current size.
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    n_chunks = con.execute("SELECT count(*) FROM chunk").fetchone()[0]
    con.close()
    assert n == n_chunks, (
        f"FTS indexed {n} rows but chunk table has {n_chunks}")
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
