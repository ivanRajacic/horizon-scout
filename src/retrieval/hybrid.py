"""Hybrid retrieval: lexical (BM25) and dense (vector) fused with Reciprocal
Rank Fusion, with an optional cross-encoder rerank over the fused candidates.

This is "hybrid" in the IR sense (lexical + dense), distinct from scoped.py
(a structured SQL pre-filter + semantic search). It satisfies the same
base.Retriever protocol as its components, so it drops in wherever a retriever
is expected and honours the same project_ids / source filters.

Flow (search):
  1. pull FUSE_CANDIDATES from each of lexical and dense (filters applied inside)
  2. RRF-fuse: score(chunk) = sum over lists of 1 / (RRF_K + rank_in_list)
  3. rerank=False -> return the top k fused chunks
     rerank=True  -> rerank the top RERANK_DEPTH fused chunks, return top k

The four bake-off contestants are: lexical alone, dense alone, this with
rerank=False (hybrid), and this with rerank=True (hybrid_rerank).
"""

from dataclasses import replace

from src.config import FUSE_CANDIDATES, RERANK_DEPTH, RRF_K
from src.retrieval.base import SearchResult
from src.retrieval.lexical import LexicalRetriever
from src.retrieval.rerank import RerankClient
from src.retrieval.vector_search import VectorSearcher


def rrf_fuse(result_lists: list[list[SearchResult]],
             k_const: int = RRF_K) -> list[SearchResult]:
    """Reciprocal Rank Fusion over several best-first result lists.

    A chunk's fused score is the sum of 1/(k_const + rank) across every list it
    appears in (rank is 1-based). Chunks are identified by chunk_id; the first
    SearchResult seen for a chunk_id is the representative (text/metadata are
    identical across retrievers), with its score replaced by the RRF score.
    Returns fused chunks best-first (highest RRF score first).
    """
    scores: dict[str, float] = {}
    rep: dict[str, SearchResult] = {}
    for results in result_lists:
        for rank, r in enumerate(results, start=1):
            scores[r.chunk_id] = scores.get(r.chunk_id, 0.0) + 1.0 / (k_const + rank)
            rep.setdefault(r.chunk_id, r)
    fused = [replace(rep[cid], score=sc) for cid, sc in scores.items()]
    fused.sort(key=lambda r: r.score, reverse=True)
    return fused


class HybridRetriever:
    """Lexical + dense RRF fusion, optional cross-encoder rerank.

    Components are injectable (the bake-off builds lexical/dense/reranker once
    and shares them across the hybrid and hybrid_rerank contestants); when left
    as None they are constructed on demand. The reranker is only built when
    rerank=True, so fusion-only never touches the rerank server.
    """

    def __init__(self, lexical: LexicalRetriever | None = None,
                 dense: VectorSearcher | None = None,
                 reranker: RerankClient | None = None,
                 rerank: bool = False,
                 rrf_k: int = RRF_K,
                 fuse_candidates: int = FUSE_CANDIDATES,
                 rerank_depth: int = RERANK_DEPTH):
        self.lexical = lexical if lexical is not None else LexicalRetriever()
        self.dense = dense if dense is not None else VectorSearcher()
        self.rerank = rerank
        self.reranker = reranker
        if self.rerank and self.reranker is None:
            self.reranker = RerankClient()
        self.rrf_k = rrf_k
        self.fuse_candidates = fuse_candidates
        self.rerank_depth = rerank_depth

    def search(self, query: str, k: int = 10,
               project_ids: set[int] | None = None,
               source: str | None = None) -> list[SearchResult]:
        lex = self.lexical.search(query, k=self.fuse_candidates,
                                  project_ids=project_ids, source=source)
        den = self.dense.search(query, k=self.fuse_candidates,
                                project_ids=project_ids, source=source)
        fused = rrf_fuse([lex, den], self.rrf_k)
        if not self.rerank:
            return fused[:k]
        return self.reranker.rerank_results(query, fused[:self.rerank_depth],
                                            top_k=k)
