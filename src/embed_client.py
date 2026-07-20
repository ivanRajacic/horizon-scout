"""Embeddings client for llama-server's OpenAI-compatible /v1/embeddings.

LangChain Embeddings implementation (component use only). Batched requests
over a small thread pool - the validated pattern from the GPU report
(batch=32, 4 workers saturates the server's 4 parallel slots).

All embedding - index build AND queries - must go through this client so
both sides of every similarity comparison come from the same stack.
"""

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests
from langchain_core.embeddings import Embeddings

from src.config import (EMBED_BASE_URL, EMBED_BATCH, EMBED_DIM, EMBED_WORKERS,
                        QUERY_PREFIX, SERVER_LAUNCH_CMD)


class EmbedServerError(RuntimeError):
    pass


def check_server(base_url: str = EMBED_BASE_URL) -> dict:
    """Return llama-server /props, or fail fast with the relaunch command."""
    try:
        r = requests.get(f"{base_url}/props", timeout=5)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise EmbedServerError(
            f"llama-server not reachable at {base_url} ({e}).\n"
            f"Launch it with:\n  {SERVER_LAUNCH_CMD}"
        ) from None


class LlamaServerEmbeddings(Embeddings):
    def __init__(self, base_url: str = EMBED_BASE_URL,
                 batch_size: int = EMBED_BATCH, workers: int = EMBED_WORKERS):
        self.base_url = base_url
        self.batch_size = batch_size
        self.workers = workers
        self._sessions = [requests.Session() for _ in range(workers)]

    def _post_batch(self, args: tuple[int, list[str]]) -> list[list[float]]:
        i, texts = args
        r = self._sessions[i % self.workers].post(
            f"{self.base_url}/v1/embeddings", json={"input": texts},
            timeout=600)
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed texts exactly as given (header prefixing is the caller's job)."""
        if not texts:
            return []
        b = self.batch_size
        batches = [(i, texts[i * b:(i + 1) * b])
                   for i in range((len(texts) + b - 1) // b)]
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            results = list(ex.map(self._post_batch, batches))
        vecs = np.asarray([v for batch in results for v in batch],
                          dtype=np.float32)
        if vecs.shape != (len(texts), EMBED_DIM):
            raise EmbedServerError(f"bad embedding shape {vecs.shape}")
        if not np.isfinite(vecs).all():
            raise EmbedServerError("NaN/Inf in server embeddings")
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs.tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([QUERY_PREFIX + text])[0]
