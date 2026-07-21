"""Shared retrieval types: the SearchResult record and the Retriever protocol.

Every retrieval strategy - lexical BM25 (lexical.py), dense vector
(vector_search.py), and their fusion (hybrid.py) - returns the SAME
SearchResult shape and honours the SAME search() signature, so they are
interchangeable behind one interface. The retriever bake-off (bench-retrievers)
swaps them by name and scores them on identical output.

Score convention: results are returned BEST-FIRST. The numeric `score` field's
meaning is implementation-defined and NOT comparable across retrievers - dense
returns FAISS L2 distance (lower = closer), lexical returns BM25 relevance
(higher = better), fusion returns an RRF score, rerank returns a cross-encoder
logit (both higher = better). Consumers MUST rely on list order, never on the
sign or scale of `score`.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class SearchResult:
    chunk_id: str
    project_id: int
    acronym: str | None
    title: str | None
    source: str
    section: str
    score: float
    text: str


@runtime_checkable
class Retriever(Protocol):
    """A retrieval strategy over the chunk corpus. Returns best-first results.

    project_ids / source are optional post-filters, honoured identically by
    every implementation so a retriever can be dropped into the scoped path or
    a source-restricted search without special-casing.
    """

    def search(self, query: str, k: int = 10,
               project_ids: set[int] | None = None,
               source: str | None = None) -> list[SearchResult]:
        ...
