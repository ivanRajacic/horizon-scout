"""Standalone vector search over the FAISS index built by embed_index.

VectorSearcher loads the index once, refuses to serve on any
index_meta.json mismatch with the configured model/GGUF, and embeds
queries through the SAME llama-server stack that built the index
(query prefix applied in the embed client). Results carry CLEAN chunk
text joined with acronym/title from DuckDB.

Filters (`project_ids`, `source`) are post-filters over an over-fetched
candidate list - milestone 4's hybrid hook and milestone 5's source
routing evaluate on top of this without touching the index.
"""

import warnings

import duckdb
from langchain_community.vectorstores import FAISS

from src.config import (DB_PATH, EMBED_DIM, EMBED_MODEL, INDEX_DIR,
                        INDEX_META_PATH)
from src.embed_client import LlamaServerEmbeddings, check_server
# SearchResult now lives in base.py (shared across retrievers); re-exported here
# so existing `from src.retrieval.vector_search import SearchResult` imports and
# the dense-retriever call sites keep working unchanged.
from src.retrieval.base import SearchResult


class IndexMetaError(RuntimeError):
    pass


def validate_meta(meta: dict, gguf_hash: str) -> None:
    """Refuse to serve unless the index was built by the configured stack."""
    checks = [
        ("embedding_model", meta.get("embedding_model"), EMBED_MODEL),
        ("gguf_sha256", meta.get("gguf_sha256"), gguf_hash),
        ("dim", meta.get("dim"), EMBED_DIM),
    ]
    for name, got, want in checks:
        if got != want:
            raise IndexMetaError(
                f"index_meta mismatch on {name}: index has {got!r}, "
                f"configured stack is {want!r}. Rebuild the index or fix "
                "the config - refusing to serve.")


class VectorSearcher:
    def __init__(self, index_dir=INDEX_DIR, meta_path=INDEX_META_PATH,
                 db_path=DB_PATH):
        import json

        from src.ingest.embed_index import gguf_sha256

        check_server()
        if not meta_path.exists():
            raise IndexMetaError(f"missing {meta_path} - build the index first")
        self.meta = json.loads(meta_path.read_text(encoding="utf-8"))
        validate_meta(self.meta, gguf_sha256())

        self.client = LlamaServerEmbeddings()
        self.vs = FAISS.load_local(str(index_dir), self.client,
                                   allow_dangerous_deserialization=True)
        if self.vs.index.ntotal != self.meta["n_vectors"]:
            raise IndexMetaError(
                f"index has {self.vs.index.ntotal} vectors, meta says "
                f"{self.meta['n_vectors']} - refusing to serve.")
        self.con = duckdb.connect(str(db_path), read_only=True)

    def search(self, query: str, k: int = 10,
               project_ids: set[int] | None = None,
               source: str | None = None,
               dedup_projects: bool = False) -> list[SearchResult]:
        if source is not None and source not in ("report", "objective"):
            raise ValueError("source must be 'report' or 'objective'")
        filtered = project_ids is not None or source is not None
        fetch_k = max(k * 20, 200) if filtered or dedup_projects else k
        qv = self.client.embed_query(query)
        hits = self.vs.similarity_search_with_score_by_vector(
            qv, k=min(fetch_k, self.vs.index.ntotal))

        results, seen_projects = [], set()
        for doc, score in hits:
            m = doc.metadata
            if project_ids is not None and m["project_id"] not in project_ids:
                continue
            if source is not None and m["source"] != source:
                continue
            if dedup_projects and m["project_id"] in seen_projects:
                continue
            seen_projects.add(m["project_id"])
            results.append((doc, score))
            if len(results) == k:
                break
        if filtered and len(results) < k:
            warnings.warn(
                f"only {len(results)} of k={k} results survived filtering "
                f"(over-fetched {fetch_k})")

        proj = {pid: (acr, title) for pid, acr, title in self.con.execute(
            "SELECT id, acronym, title FROM project WHERE id IN "
            f"({', '.join(str(int(d.metadata['project_id'])) for d, _ in results) or 'NULL'})"
        ).fetchall()}
        return [
            SearchResult(
                chunk_id=d.metadata["chunk_id"],
                project_id=d.metadata["project_id"],
                acronym=proj.get(d.metadata["project_id"], (None, None))[0],
                title=proj.get(d.metadata["project_id"], (None, None))[1],
                source=d.metadata["source"],
                section=d.metadata["section"],
                score=float(s),
                text=d.page_content,
            )
            for d, s in results
        ]
