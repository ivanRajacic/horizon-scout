"""Rerank client unit tests. The reranker server is NOT running, so every test
runs against a mocked HTTP layer - a fake session injected into the client, or
a monkeypatched requests.get - and no live call is ever made."""

import pytest
import requests

from src.config import RERANK_SERVER_LAUNCH_CMD
from src.retrieval import rerank as rerank_mod
from src.retrieval.base import SearchResult
from src.retrieval.rerank import RerankClient, RerankServerError


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    """Records POSTs and replays a queued response per URL path."""
    def __init__(self, response):
        self._response = response
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        return self._response


def mk_result(pid, text, score=0.0):
    return SearchResult(chunk_id=f"{pid}-c", project_id=pid, acronym=f"P{pid}",
                        title=f"P{pid} title", source="report",
                        section="summary", score=score, text=text)


def client_with(response):
    c = RerankClient()
    c._session = FakeSession(response)
    return c


# --- rerank() ---

def test_rerank_sorts_by_score_descending():
    # Response is deliberately out of order; rerank must sort best-first.
    resp = FakeResponse({"results": [
        {"index": 0, "relevance_score": 0.1},
        {"index": 1, "relevance_score": 0.9},
        {"index": 2, "relevance_score": 0.5},
    ]})
    c = client_with(resp)
    out = c.rerank("q", ["a", "b", "c"])
    assert out == [(1, 0.9), (2, 0.5), (0, 0.1)]


def test_rerank_accepts_score_key():
    # Robustness: some builds emit `score` instead of `relevance_score`.
    resp = FakeResponse({"results": [
        {"index": 0, "score": 0.2},
        {"index": 1, "score": 0.8},
    ]})
    out = client_with(resp).rerank("q", ["a", "b"])
    assert out == [(1, 0.8), (0, 0.2)]


def test_rerank_empty_documents_no_http_call():
    c = client_with(FakeResponse({"results": []}))
    assert c.rerank("q", []) == []
    assert c._session.calls == []  # server never contacted


def test_rerank_posts_model_and_top_n():
    resp = FakeResponse({"results": [{"index": 0, "relevance_score": 1.0}]})
    c = client_with(resp)
    c.rerank("what is x", ["doc"], top_n=1)
    url, body = c._session.calls[0]
    assert url.endswith("/rerank")
    assert body["model"] == c.model
    assert body["query"] == "what is x"
    assert body["documents"] == ["doc"]
    assert body["top_n"] == 1


# --- rerank_results() ---

def test_rerank_results_reorders_and_sets_score():
    results = [mk_result(10, "alpha", score=-1.0),
               mk_result(20, "beta", score=-1.0),
               mk_result(30, "gamma", score=-1.0)]
    # Rank: beta best, gamma next, alpha worst.
    resp = FakeResponse({"results": [
        {"index": 0, "relevance_score": 0.1},
        {"index": 1, "relevance_score": 0.9},
        {"index": 2, "relevance_score": 0.4},
    ]})
    out = client_with(resp).rerank_results("q", results, top_k=2)
    assert len(out) == 2
    assert [r.project_id for r in out] == [20, 30]
    assert out[0].score == 0.9 and out[1].score == 0.4


def test_rerank_results_empty_short_circuits():
    c = client_with(FakeResponse({"results": []}))
    assert c.rerank_results("q", [], top_k=5) == []
    assert c._session.calls == []


# --- check_server() ---

def test_check_server_raises_with_launch_cmd(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(rerank_mod.requests, "get", boom)
    with pytest.raises(RerankServerError) as ei:
        RerankClient().check_server()
    assert RERANK_SERVER_LAUNCH_CMD in str(ei.value)
