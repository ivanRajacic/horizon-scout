"""Full-build smoke with a fake embedding server: the sub-batched embed loop
produces a complete index with correct counts, writes meta, and removes the
progress file. Guards the code path that fronts the hour-long full-corpus
build - no real llama-server or GGUF involved."""

import json
import math

import duckdb
import numpy as np
import pytest

from src.config import EMBED_DIM
from src.ingest import embed_index

OBJECTIVE = ("This project develops a novel approach to measuring things. "
             "It combines several methods and validates them on real data. ") * 3
SUMMARY = ("The consortium delivered the first prototype and evaluated it "
           "across three pilot sites with encouraging results. ") * 3


class FakeClient:
    """Deterministic unit vectors; records every batch size it was given."""

    def __init__(self):
        self.batch_sizes = []

    def embed_documents(self, texts):
        self.batch_sizes.append(len(texts))
        v = [1.0 / math.sqrt(EMBED_DIM)] * EMBED_DIM
        return [list(v) for _ in texts]

    def embed_query(self, text):
        return self.embed_documents([text])[0]


@pytest.fixture
def tiny_corpus(tmp_path, monkeypatch):
    db = tmp_path / "tiny.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""CREATE TABLE project (
        id BIGINT, acronym VARCHAR, title VARCHAR, objective VARCHAR)""")
    con.execute("""CREATE TABLE report_text (
        projectID BIGINT, summary VARCHAR, workPerformed VARCHAR,
        finalResults VARCHAR)""")
    for pid in (101, 102, 103):
        con.execute("INSERT INTO project VALUES (?, ?, ?, ?)",
                    [pid, f"ACR{pid}", f"Project {pid}", OBJECTIVE])
        if pid != 103:  # one project without a report (LEFT JOIN path)
            con.execute("INSERT INTO report_text VALUES (?, ?, ?, ?)",
                        [pid, SUMMARY, SUMMARY, None])
    con.close()

    client = FakeClient()
    monkeypatch.setattr(embed_index, "DB_PATH", db)
    monkeypatch.setattr(embed_index, "INDEX_DIR", tmp_path / "faiss_index")
    monkeypatch.setattr(embed_index, "TMP_INDEX_DIR",
                        tmp_path / "faiss_index.tmp")
    monkeypatch.setattr(embed_index, "INDEX_META_PATH",
                        tmp_path / "index_meta.json")
    monkeypatch.setattr(embed_index, "PROGRESS_PATH",
                        tmp_path / "progress.json")
    monkeypatch.setattr(embed_index, "check_server",
                        lambda: {"build_info": "test"})
    monkeypatch.setattr(embed_index, "LlamaServerEmbeddings", lambda: client)
    monkeypatch.setattr(embed_index, "gguf_sha256", lambda: "test-hash")
    return tmp_path, client


def test_full_build_smoke(tiny_corpus):
    tmp_path, client = tiny_corpus
    embed_index.build_index(limit=None)

    meta = json.loads((tmp_path / "index_meta.json").read_text("utf-8"))
    con = duckdb.connect(str(tmp_path / "tiny.duckdb"))
    n_chunks = con.execute("SELECT count(*) FROM chunk").fetchone()[0]
    con.close()

    assert n_chunks > 0
    assert meta["n_vectors"] == n_chunks
    assert meta["gguf_sha256"] == "test-hash"
    assert sum(meta["chunk_counts"].values()) == n_chunks
    assert sum(client.batch_sizes) == n_chunks
    assert all(b <= embed_index.EMBED_LOG_BATCH for b in client.batch_sizes)
    assert (tmp_path / "faiss_index").exists()
    assert not (tmp_path / "faiss_index.tmp").exists()
    assert not (tmp_path / "progress.json").exists()

    # The index is loadable and returns our vectors.
    from langchain_community.vectorstores import FAISS
    vs = FAISS.load_local(str(tmp_path / "faiss_index"), client,
                          allow_dangerous_deserialization=True)
    assert vs.index.ntotal == n_chunks
