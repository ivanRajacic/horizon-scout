"""Unit tests for RRF fusion, the hybrid retriever, and ranking metrics.
No servers or DB: lexical/dense/reranker are faked, metrics are pure functions."""

from math import isclose

from src.eval.metrics import (dedup_projects, hit_at_k, mrr_at_k, ndcg_at_k,
                              recall_at_k, score_ranking)
from src.retrieval.base import SearchResult
from src.retrieval.hybrid import HybridRetriever, rrf_fuse


def mk(cid, pid, score=0.0, text="text"):
    return SearchResult(chunk_id=cid, project_id=pid, acronym=f"A{pid}",
                        title=f"T{pid}", source="report", section="summary",
                        score=score, text=text)


class FakeRetriever:
    def __init__(self, results):
        self._results = results

    def search(self, query, k=10, project_ids=None, source=None):
        return self._results[:k]


class FakeReranker:
    """Reverses the candidate order so a test can prove rerank reordered."""
    def rerank_results(self, query, results, top_k):
        return list(reversed(results))[:top_k]


# --- RRF fusion ---

def test_rrf_fuse_orders_by_reciprocal_rank():
    # list A: a,b,c   list B: b,d  -> b is in both (rank 2 in A, rank 1 in B)
    a = [mk("a", 1), mk("b", 2), mk("c", 3)]
    b = [mk("b", 2), mk("d", 4)]
    fused = rrf_fuse([a, b], k_const=60)
    order = [r.chunk_id for r in fused]
    # b highest (appears in both); then a (1/61) > d (1/62) > c (1/63)
    assert order == ["b", "a", "d", "c"]
    b_score = 1 / 62 + 1 / 61
    assert isclose(fused[0].score, b_score)


def test_rrf_fuse_dedups_chunk_ids_keeps_representative():
    a = [mk("x", 7, text="clean")]
    b = [mk("x", 7, text="clean")]
    fused = rrf_fuse([a, b], k_const=60)
    assert len(fused) == 1 and fused[0].chunk_id == "x"
    assert isclose(fused[0].score, 1 / 61 + 1 / 61)


# --- HybridRetriever ---

def test_hybrid_fusion_only_returns_top_k():
    lex = FakeRetriever([mk("a", 1), mk("b", 2)])
    den = FakeRetriever([mk("b", 2), mk("c", 3)])
    h = HybridRetriever(lexical=lex, dense=den, rerank=False)
    out = h.search("q", k=2)
    assert len(out) == 2 and out[0].chunk_id == "b"  # b in both -> top


def test_hybrid_rerank_reorders_top_candidates():
    lex = FakeRetriever([mk("a", 1), mk("b", 2)])
    den = FakeRetriever([mk("b", 2), mk("c", 3)])
    h = HybridRetriever(lexical=lex, dense=den, reranker=FakeReranker(),
                        rerank=True, rerank_depth=10)
    fused = rrf_fuse([lex.search("q", k=100), den.search("q", k=100)])
    out = h.search("q", k=3)
    # FakeReranker reverses the fused order
    assert [r.chunk_id for r in out] == [r.chunk_id for r in reversed(fused)][:3]


# --- metrics ---

def test_dedup_projects_preserves_first_occurrence():
    chunks = [mk("1", 5), mk("2", 5), mk("3", 7), mk("4", 5), mk("5", 9)]
    assert dedup_projects(chunks) == [5, 7, 9]


def test_hit_recall_mrr():
    ranked, gold = [10, 20, 30, 40], {20, 40}
    assert hit_at_k(ranked, gold, 3) == 1.0
    assert hit_at_k([10, 30], gold, 3) == 0.0
    assert isclose(recall_at_k(ranked, gold, 3), 0.5)   # only 20 in top 3
    assert isclose(recall_at_k(ranked, gold, 4), 1.0)
    assert isclose(mrr_at_k(ranked, gold, 3), 0.5)      # first gold at rank 2
    assert mrr_at_k([10, 30], gold, 3) == 0.0


def test_ndcg_binary():
    from math import log2
    ranked, gold = [10, 20, 30], {20, 40}   # one gold (20) at position 2
    dcg = 1 / log2(3)                        # gain at position 2
    idcg = 1 / log2(2) + 1 / log2(3)         # 2 gold, ideal positions 1 and 2
    assert isclose(ndcg_at_k(ranked, gold, 3), dcg / idcg)


def test_score_ranking_returns_all_metrics():
    s = score_ranking([10, 20, 30], {20}, 3)
    assert set(s) == {"hit", "recall", "mrr", "ndcg"}
    assert s["hit"] == 1.0 and isclose(s["mrr"], 0.5)
