"""Ranking metrics for the retriever bake-off.

Gold is labelled at the PROJECT level (a set of relevant project ids), so a
retriever's best-first chunk list is first deduplicated to an ordered list of
unique project ids (dedup_projects), then scored at cutoff k. Relevance is
binary (a project is relevant or not), which is why nDCG uses a 0/1 gain.

All functions take `ranked` = ordered unique project ids (best-first) and
`gold` = the set of relevant project ids.
"""

from math import log2

from src.retrieval.base import SearchResult


def dedup_projects(chunks: list[SearchResult]) -> list[int]:
    """Best-first chunk list -> ordered unique project ids (first occurrence)."""
    seen: set[int] = set()
    out: list[int] = []
    for c in chunks:
        if c.project_id not in seen:
            seen.add(c.project_id)
            out.append(c.project_id)
    return out


def hit_at_k(ranked: list[int], gold: set[int], k: int) -> float:
    """1.0 if any gold project appears in the top k, else 0.0."""
    return 1.0 if set(ranked[:k]) & gold else 0.0


def recall_at_k(ranked: list[int], gold: set[int], k: int) -> float:
    """Fraction of gold projects retrieved within the top k."""
    if not gold:
        return 0.0
    return len(set(ranked[:k]) & gold) / len(gold)


def mrr_at_k(ranked: list[int], gold: set[int], k: int) -> float:
    """Reciprocal rank of the FIRST gold project in the top k (0 if none)."""
    for i, pid in enumerate(ranked[:k], start=1):
        if pid in gold:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked: list[int], gold: set[int], k: int) -> float:
    """Binary-relevance nDCG at k: DCG / ideal DCG."""
    if not gold:
        return 0.0
    dcg = sum(1.0 / log2(i + 1)
              for i, pid in enumerate(ranked[:k], start=1) if pid in gold)
    ideal_hits = min(len(gold), k)
    idcg = sum(1.0 / log2(i + 1) for i in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


METRICS = {
    "hit": hit_at_k,
    "recall": recall_at_k,
    "mrr": mrr_at_k,
    "ndcg": ndcg_at_k,
}


def score_ranking(ranked: list[int], gold: set[int], k: int) -> dict[str, float]:
    """All metrics at cutoff k for one query's project ranking."""
    return {name: fn(ranked, gold, k) for name, fn in METRICS.items()}
