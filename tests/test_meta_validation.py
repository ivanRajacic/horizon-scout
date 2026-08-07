"""Unit tests for index_meta validation and VectorSearcher.search's fetch
behaviour (no server, no index, no DB: the FAISS store, the embedder and the
DuckDB connection are faked)."""

import pytest

from src.config import EMBED_DIM, EMBED_MODEL
from src.retrieval.vector_search import (IndexMetaError, VectorSearcher,
                                         validate_meta)

GOOD_HASH = "a" * 64


def good_meta():
    return {"embedding_model": EMBED_MODEL, "dim": EMBED_DIM,
            "gguf_sha256": GOOD_HASH}


def test_matching_meta_passes():
    validate_meta(good_meta(), GOOD_HASH)


@pytest.mark.parametrize("field,bad", [
    ("embedding_model", "some-other-model.gguf"),
    ("gguf_sha256", "b" * 64),
    ("dim", 384),
])
def test_any_mismatch_refuses(field, bad):
    meta = good_meta()
    meta[field] = bad
    with pytest.raises(IndexMetaError, match="mismatch"):
        validate_meta(meta, GOOD_HASH)


def test_missing_field_refuses():
    meta = good_meta()
    del meta["gguf_sha256"]
    with pytest.raises(IndexMetaError):
        validate_meta(meta, GOOD_HASH)


# --- search(): the over-fetch, and its widening under an id filter ---

class FakeDoc:
    def __init__(self, rank, project_id, source="report"):
        self.page_content = f"text {rank}"
        self.metadata = {"chunk_id": f"c{rank}", "project_id": project_id,
                         "source": source, "section": "summary"}


class FakeIndex:
    def __init__(self, ntotal):
        self.ntotal = ntotal


class FakeVS:
    """A ranked corpus. similarity_search_with_score_by_vector returns the top
    k of it and records every k it was asked for."""

    def __init__(self, docs):
        self.docs = docs
        self.index = FakeIndex(len(docs))
        self.fetches = []

    def similarity_search_with_score_by_vector(self, vector, k):
        self.fetches.append(k)
        return [(d, 1.0 - i / 10000) for i, d in enumerate(self.docs[:k])]


class FakeEmbedClient:
    def __init__(self):
        self.calls = 0

    def embed_query(self, text):
        self.calls += 1
        return [0.1, 0.2, 0.3]


class FakeCon:
    """The project acronym/title lookup. Returns nothing - search() tolerates a
    missing row, and these tests are about fetch sizes, not join output."""

    def execute(self, sql):
        self.sql = sql
        return self

    def fetchall(self):
        return []


def make_searcher(docs):
    """A VectorSearcher with its three collaborators faked, built without
    __init__ so no server, index file or database is touched."""
    s = object.__new__(VectorSearcher)
    s.vs = FakeVS(docs)
    s.client = FakeEmbedClient()
    s.con = FakeCon()
    return s


def corpus(n, allowed_ranks, allowed_pid=777):
    """n ranked chunks, each its own project, except the ranks named in
    allowed_ranks which all belong to project allowed_pid."""
    allowed = set(allowed_ranks)
    return [FakeDoc(i, allowed_pid if i in allowed else 10000 + i)
            for i in range(n)]


def test_narrow_filter_widens_fetch_until_k_survivors():
    # 12 allowed chunks, all ranked below the first over-fetch of 200.
    s = make_searcher(corpus(5000, range(250, 262)))
    out = s.search("q", k=10, project_ids={777})
    assert len(out) == 10                    # k survivors, not 0
    assert s.vs.fetches == [200, 400]        # doubled once, then found them
    assert s.client.calls == 1               # embedded ONCE across retries
    assert s.last_stats["widenings"] == 1
    assert s.last_stats["fetch_k"] == 400


def test_widening_stops_at_index_size_and_accepts_what_survives():
    # Only 3 allowed chunks, and they sit at the very bottom of the ranking:
    # the loop must end on one full-index fetch, not spin.
    s = make_searcher(corpus(1000, [996, 997, 998]))
    out = s.search("q", k=10, project_ids={777})
    assert len(out) == 3
    assert s.vs.fetches[0] == 200 and s.vs.fetches[-1] == 1000
    assert s.vs.fetches == sorted(s.vs.fetches)          # monotone, bounded
    assert s.last_stats["fetch_k"] == 1000               # the whole index
    assert s.last_stats["short"] is True
    assert s.last_stats["survivors"] == 3


def test_filter_satisfied_by_first_fetch_does_not_widen():
    s = make_searcher(corpus(5000, range(0, 40)))
    out = s.search("q", k=10, project_ids={777})
    assert len(out) == 10
    assert s.vs.fetches == [200]
    assert s.last_stats["widenings"] == 0


def test_unfiltered_search_fetch_is_unchanged():
    s = make_searcher(corpus(5000, []))
    out = s.search("q", k=10)
    assert len(out) == 10
    assert s.vs.fetches == [10]              # k exactly - no over-fetch
    assert s.client.calls == 1
    assert s.last_stats["filtered"] is False
    assert s.last_stats["widenings"] == 0
    assert s.last_stats["fetch_k"] == 10


def test_last_stats_exposes_survivor_count_and_filter_size():
    s = make_searcher(corpus(5000, range(250, 262)))
    s.search("q", k=10, project_ids={777, 888})
    st = s.last_stats
    assert st["survivors"] == 10 and st["k"] == 10
    assert st["n_filter_ids"] == 2
    assert st["index_size"] == 5000
    assert st["filtered"] is True and st["short"] is False


def test_source_only_shortfall_still_warns():
    # No id filter to widen for: the old warning is the only signal there.
    docs = [FakeDoc(i, 10000 + i, source="objective") for i in range(500)]
    docs[0] = FakeDoc(0, 1, source="report")
    s = make_searcher(docs)
    with pytest.warns(UserWarning, match="survived filtering"):
        out = s.search("q", k=10, source="report")
    assert len(out) == 1
    assert s.vs.fetches == [200]             # unchanged: no widening here
