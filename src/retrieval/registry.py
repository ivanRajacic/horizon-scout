"""Retriever registry: build any of the four bake-off contestants by name.

    lexical        - BM25 only (LexicalRetriever)
    dense          - vector only (VectorSearcher)
    hybrid         - RRF fusion of lexical + dense (no rerank)
    hybrid_rerank  - RRF fusion + cross-encoder rerank

All four satisfy src.retrieval.base.Retriever, so the bench runner (and, later,
the ask pipeline) can swap them purely by name. build_retriever accepts already
constructed components so the runner builds the FAISS-backed dense searcher, the
lexical connection, and the rerank client ONCE and shares them across the hybrid
and hybrid_rerank contestants instead of reloading the index per contestant.
"""

from src.retrieval.hybrid import HybridRetriever
from src.retrieval.lexical import LexicalRetriever
from src.retrieval.rerank import RerankClient
from src.retrieval.vector_search import VectorSearcher

RETRIEVERS = ("lexical", "dense", "hybrid", "hybrid_rerank")


def build_retriever(name: str, *,
                    lexical: LexicalRetriever | None = None,
                    dense: VectorSearcher | None = None,
                    reranker: RerankClient | None = None):
    """Construct one contestant by name. Prebuilt components are reused when
    given; otherwise each retriever constructs what it needs on demand."""
    if name == "lexical":
        return lexical if lexical is not None else LexicalRetriever()
    if name == "dense":
        return dense if dense is not None else VectorSearcher()
    if name == "hybrid":
        return HybridRetriever(lexical=lexical, dense=dense, rerank=False)
    if name == "hybrid_rerank":
        return HybridRetriever(lexical=lexical, dense=dense,
                               reranker=reranker, rerank=True)
    raise ValueError(f"unknown retriever {name!r}; choose from {RETRIEVERS}")
