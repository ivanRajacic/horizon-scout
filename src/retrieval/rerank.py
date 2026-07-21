"""Cross-encoder reranker client for llama-server's /rerank endpoint.

Third llama-server process (see config: RERANK_BASE_URL, port 8082) running
bge-reranker-v2-m3 with --reranking --pooling rank. It scores (query, passage)
pairs and returns a relevance score per document; a higher score means a better
match. This client POSTs to /rerank (Jina/Cohere-style), always sorts the
results best-first itself, and reorders SearchResult objects for the M6 hybrid
rerank stage.

HTTP style mirrors embed_client.py: plain `requests`, /props health check, and
a fail-fast error carrying the server relaunch command.
"""

from dataclasses import replace

import requests

from src.config import (RERANK_BASE_URL, RERANK_SERVER_LAUNCH_CMD,
                        RERANKER_MODEL)
from src.retrieval.base import SearchResult


class RerankServerError(RuntimeError):
    pass


def check_server(base_url: str = RERANK_BASE_URL) -> dict:
    """Return llama-server /props, or fail fast with the relaunch command."""
    try:
        r = requests.get(f"{base_url}/props", timeout=5)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise RerankServerError(
            f"reranker server not reachable at {base_url} ({e}).\n"
            f"Launch it with:\n  {RERANK_SERVER_LAUNCH_CMD}"
        ) from None


def _score_of(item: dict) -> float:
    """Read the relevance score, accepting either llama.cpp/Jina key."""
    if "relevance_score" in item:
        return float(item["relevance_score"])
    if "score" in item:
        return float(item["score"])
    raise RerankServerError(f"rerank result missing a score key: {item!r}")


class RerankClient:
    def __init__(self, base_url: str = RERANK_BASE_URL,
                 model: str = RERANKER_MODEL):
        self.base_url = base_url
        self.model = model
        self._session = requests.Session()

    def check_server(self) -> dict:
        """Ping the server, failing fast with the relaunch command if down."""
        return check_server(self.base_url)

    def _post_rerank(self, payload: dict) -> dict:
        """POST to /rerank, falling back to /v1/rerank on a 404."""
        r = self._session.post(f"{self.base_url}/rerank", json=payload,
                               timeout=600)
        if r.status_code == 404:
            r = self._session.post(f"{self.base_url}/v1/rerank", json=payload,
                                   timeout=600)
        r.raise_for_status()
        return r.json()

    def rerank(self, query: str, documents: list[str],
               top_n: int | None = None) -> list[tuple[int, float]]:
        """Score documents against query, returning (original_index, score)
        pairs sorted by score descending. Empty documents short-circuits."""
        if not documents:
            return []
        payload = {"model": self.model, "query": query, "documents": documents}
        if top_n is not None:
            payload["top_n"] = top_n
        data = self._post_rerank(payload)
        scored = [(int(item["index"]), _score_of(item))
                  for item in data["results"]]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored

    def rerank_results(self, query: str, results: list[SearchResult],
                       top_k: int) -> list[SearchResult]:
        """Rerank SearchResults by cross-encoder score, returning the top_k
        reordered with each result's score replaced by its rerank score
        (higher = better; best-first, per base.py's score contract)."""
        if not results:
            return []
        order = self.rerank(query, [r.text for r in results])
        reranked = [replace(results[i], score=score) for i, score in order]
        return reranked[:top_k]
