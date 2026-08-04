"""Lexical BM25 retrieval over the chunk corpus via DuckDB's FTS extension.

build_fts_index() (re)builds a `chunk_fts` table whose indexed `search_text`
concatenates the project acronym, project title, and the clean chunk text, so a
distinctive acronym like "EBOVAC" matches lexically even when it appears only in
the project title. The index is created with the configured Porter stemmer and
English stopword list, and a sidecar meta file next to the database (same
pattern as the FAISS index_meta.json) records what the build used - stemmer,
stopwords, row count - so fts_index_is_fresh() can tell a current index from a
stale one without opening the database for writing.

LexicalRetriever serves searches over a READ-ONLY connection and returns the
SAME SearchResult shape as the dense VectorSearcher, carrying the CLEAN chunk
text (chunk.text verbatim, never the acronym/title-concatenated search text) so
the two retrievers are directly comparable. Filters (`project_ids`, `source`)
are applied in SQL, so no over-fetch is needed.

Score is the BM25 relevance returned by DuckDB's match_bm25 macro (higher =
better); per the base.py contract only list order is meaningful.
"""

import json
from pathlib import Path

import duckdb

from src import config
from src.retrieval.base import SearchResult


def fts_meta_path(db_path=config.DB_PATH) -> Path:
    """The sidecar meta file for db_path's FTS index (horizon.fts-meta.json)."""
    return Path(db_path).with_suffix(".fts-meta.json")


def build_fts_index(db_path=config.DB_PATH):
    """(Re)build the `chunk_fts` table and its BM25 index, idempotently.

    Opens a WRITE connection (create_fts_index cannot run read-only). The
    indexed text is `acronym || ' ' || title || ' ' || chunk.text`; the clean
    `text` column is kept alongside so searches return it verbatim. Writes the
    sidecar meta file last, so a build that dies midway leaves the old meta
    behind and the index still reads as stale.
    """
    con = duckdb.connect(str(db_path))
    try:
        con.execute("INSTALL fts; LOAD fts;")
        con.execute(
            "CREATE OR REPLACE TABLE chunk_fts AS "
            "SELECT c.chunk_id, c.project_id, c.source, c.section, c.text, "
            "       (coalesce(p.acronym, '') || ' ' || coalesce(p.title, '') "
            "        || ' ' || c.text) AS search_text "
            "FROM chunk c JOIN project p ON p.id = c.project_id")
        n = con.execute("SELECT count(*) FROM chunk_fts").fetchone()[0]
        # PRAGMA statements cannot be prepared, so the stemmer/stopwords config
        # values (trusted, not user input) are inlined as quoted literals.
        con.execute(
            "PRAGMA create_fts_index('chunk_fts', 'chunk_id', 'search_text', "
            f"stemmer='{config.FTS_STEMMER}', "
            f"stopwords='{config.FTS_STOPWORDS}', overwrite=1)")
    finally:
        con.close()
    fts_meta_path(db_path).write_text(json.dumps(
        {"stemmer": config.FTS_STEMMER, "stopwords": config.FTS_STOPWORDS,
         "n_rows": n}), encoding="utf-8")
    return n


def fts_index_is_fresh(db_path=config.DB_PATH) -> bool:
    """True when chunk_fts covers the chunk table exactly and the sidecar meta
    says it was built with the configured stemmer and stopwords.

    Opens the database read-only, so it is safe while other processes (e.g. a
    horizon-draft MCP server) hold the file. Anything missing or mismatched -
    no meta file, a config change, a count drift - reads as stale, and the
    caller rebuilds. The stemmer/stopwords comparison is what makes the check
    honest: a bare row-count match cannot see a config change.
    """
    try:
        meta = json.loads(fts_meta_path(db_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if (meta.get("stemmer") != config.FTS_STEMMER
            or meta.get("stopwords") != config.FTS_STOPWORDS):
        return False
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        tables = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables").fetchall()}
        if not {"chunk", "chunk_fts"} <= tables:
            return False
        has_index = con.execute(
            "SELECT count(*) FROM information_schema.schemata "
            "WHERE schema_name = 'fts_main_chunk_fts'").fetchone()[0]
        if not has_index:
            return False
        n_chunks = con.execute("SELECT count(*) FROM chunk").fetchone()[0]
        n_fts = con.execute("SELECT count(*) FROM chunk_fts").fetchone()[0]
        return meta.get("n_rows") == n_fts == n_chunks
    finally:
        con.close()


class LexicalRetriever:
    """BM25 retriever satisfying the src.retrieval.base.Retriever protocol."""

    def __init__(self, db_path=config.DB_PATH):
        self.con = duckdb.connect(str(db_path), read_only=True)
        self.con.execute("LOAD fts;")
        has_table = self.con.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name = 'chunk_fts'").fetchone()[0]
        has_index = self.con.execute(
            "SELECT count(*) FROM information_schema.schemata "
            "WHERE schema_name = 'fts_main_chunk_fts'").fetchone()[0]
        if not has_table or not has_index:
            raise RuntimeError(
                "FTS index not found - run "
                "src.retrieval.lexical.build_fts_index() first.")

    def search(self, query: str, k: int = 10,
               project_ids: set[int] | None = None,
               source: str | None = None) -> list[SearchResult]:
        if source is not None and source not in ("report", "objective"):
            raise ValueError("source must be 'report' or 'objective'")

        params: list = [query]
        where = ["score IS NOT NULL"]
        if project_ids is not None:
            if not project_ids:
                return []
            placeholders = ", ".join("?" for _ in project_ids)
            where.append(f"f.project_id IN ({placeholders})")
            params.extend(int(pid) for pid in project_ids)
        if source is not None:
            where.append("f.source = ?")
            params.append(source)
        params.append(int(k))

        rows = self.con.execute(
            "SELECT f.chunk_id, f.project_id, f.source, f.section, c.text, "
            "       p.acronym, p.title, "
            "       fts_main_chunk_fts.match_bm25(f.chunk_id, ?) AS score "
            "FROM chunk_fts f "
            "JOIN chunk c ON c.chunk_id = f.chunk_id "
            "JOIN project p ON p.id = f.project_id "
            "WHERE " + " AND ".join(where) + " "
            "ORDER BY score DESC "
            "LIMIT ?",
            params).fetchall()

        return [
            SearchResult(
                chunk_id=chunk_id,
                project_id=project_id,
                acronym=acronym,
                title=title,
                source=src,
                section=section,
                score=float(score),
                text=text,
            )
            for (chunk_id, project_id, src, section, text,
                 acronym, title, score) in rows
        ]
