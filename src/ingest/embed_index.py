"""Chunk -> DuckDB chunk table -> embed via llama-server -> FAISS index.

Usage:  python -m src.cli build-index [--limit N] [--chunk-target 400]

Order of operations (crash-safe by construction):
  1. Fail fast if llama-server is unreachable (relaunch command in the error).
  2. Chunk everything and CREATE OR REPLACE the DuckDB `chunk` table - the
     durable source of truth holding CLEAN text only.
  3. Embed header-prefixed strings in batches, add to a LangChain FAISS
     index (flat / inner product), checkpoint every CHECKPOINT_EVERY chunks.

Resume model: a killed run leaves index_build_progress.json plus a partial
index in faiss_index.tmp/. Rerunning with the same parameters skips
re-chunking (the chunk table already matches the recorded fingerprint),
reloads the partial index and continues after the last checkpointed chunk -
no duplicates because chunks are processed in a deterministic ORDER BY
chunk_id and only the count survives a checkpoint. Any parameter change
invalidates the fingerprint and restarts cleanly from step 2.
"""

import hashlib
import json
import shutil
import time
from datetime import datetime, timezone

import duckdb
from langchain_community.vectorstores import FAISS
from langchain_community.vectorstores.utils import DistanceStrategy

from src.config import (CHUNK_TARGET, DB_PATH, EMBED_DIM, EMBED_MODEL,
                        GGUF_PATH, INDEX_DIR, INDEX_META_PATH, PROGRESS_PATH,
                        REPORT_SECTIONS, SPLIT_OVERLAP, chunk_policy,
                        load_tokenizer)
from src.embed_client import LlamaServerEmbeddings, check_server
from src.ingest.chunker import Chunker, embedded_text, make_header

CHECKPOINT_EVERY = 5000
TMP_INDEX_DIR = INDEX_DIR.with_suffix(".tmp")


def gguf_sha256() -> str:
    h = hashlib.sha256()
    with open(GGUF_PATH, "rb") as f:
        while block := f.read(1 << 22):
            h.update(block)
    return h.hexdigest()


def _fingerprint(limit, chunk_target, split_overlap, n_chunks) -> dict:
    return {"limit": limit, "chunk_target": chunk_target,
            "split_overlap": split_overlap, "n_chunks": n_chunks,
            "model": EMBED_MODEL}


def build_chunk_table(con, limit: int | None, chunk_target: int,
                      split_overlap: int) -> int:
    """Chunk reports + objectives into the `chunk` table. Returns row count."""
    chunker = Chunker(load_tokenizer(), chunk_target=chunk_target,
                      split_overlap=split_overlap)
    lim = f"LIMIT {limit}" if limit else ""
    projects = con.execute(f"""
        SELECT p.id, p.acronym, p.title, p.objective,
               r.summary, r.workPerformed, r.finalResults
        FROM project p LEFT JOIN report_text r ON r.projectID = p.id
        ORDER BY p.id {lim}
    """).fetchall()

    rows, t0 = [], time.perf_counter()
    for i, (pid, acr, title, obj, summ, work, final) in enumerate(projects):
        sections = dict(zip(REPORT_SECTIONS, (summ, work, final)))
        docs = (chunker.chunk_report(pid, acr, title, sections)
                + chunker.chunk_objective(pid, acr, title, obj))
        rows += [(d.metadata["chunk_id"], d.metadata["project_id"],
                  d.metadata["source"], d.metadata["section"],
                  d.metadata["n_tokens"], d.page_content) for d in docs]
        if (i + 1) % 5000 == 0:
            print(f"  chunked {i + 1}/{len(projects)} projects "
                  f"({len(rows)} chunks, {time.perf_counter() - t0:.0f}s)")

    con.execute("""
        CREATE OR REPLACE TABLE chunk (
            chunk_id VARCHAR PRIMARY KEY, project_id BIGINT, source VARCHAR,
            section VARCHAR, n_tokens INTEGER, text VARCHAR)
    """)
    con.executemany("INSERT INTO chunk VALUES (?, ?, ?, ?, ?, ?)", rows)
    print(f"chunk table: {len(rows)} chunks from {len(projects)} projects "
          f"({time.perf_counter() - t0:.0f}s)")
    return len(rows)


def build_index(limit: int | None = None, chunk_target: int = CHUNK_TARGET,
                split_overlap: int = SPLIT_OVERLAP):
    props = check_server()  # fail fast before any work
    build_id = props.get("build_info", "unknown")

    con = duckdb.connect(str(DB_PATH))
    have_chunks = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'chunk'"
    ).fetchone()[0]

    progress = None
    if PROGRESS_PATH.exists():
        progress = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))

    # -------------------------------------------------------------- chunking
    resume = False
    if progress and have_chunks and TMP_INDEX_DIR.exists():
        n_chunks = con.execute("SELECT count(*) FROM chunk").fetchone()[0]
        if progress["fingerprint"] == _fingerprint(
                limit, chunk_target, split_overlap, n_chunks):
            resume = True
        else:
            print("stale progress (parameters changed) - restarting build")
    if not resume:
        n_chunks = build_chunk_table(con, limit, chunk_target, split_overlap)
        progress = {"fingerprint": _fingerprint(
            limit, chunk_target, split_overlap, n_chunks)}
        shutil.rmtree(TMP_INDEX_DIR, ignore_errors=True)

    # ------------------------------------------------------------- embedding
    rows = con.execute("""
        SELECT c.chunk_id, c.project_id, c.source, c.section, c.n_tokens,
               c.text, p.acronym, p.title
        FROM chunk c JOIN project p ON p.id = c.project_id
        ORDER BY c.chunk_id
    """).fetchall()
    con.close()
    assert len(rows) == n_chunks

    client = LlamaServerEmbeddings()
    vs = (FAISS.load_local(str(TMP_INDEX_DIR), client,
                           allow_dangerous_deserialization=True)
          if resume else None)
    # The reloaded index's own vector count is the authoritative resume
    # point - never the progress counter. save_local persists a whole,
    # consistent index; a crash between save and the progress write can only
    # leave the counter behind, so trusting ntotal avoids re-adding (and thus
    # duplicating ids for) an already-checkpointed batch.
    done0 = vs.index.ntotal if vs is not None else 0
    if resume:
        print(f"resuming: chunk table reused ({n_chunks} chunks), "
              f"{done0} vectors already indexed")
    t0 = time.perf_counter()
    done = done0
    while done < len(rows):
        batch = rows[done:done + CHECKPOINT_EVERY]
        texts = [embedded_text(make_header(acr, title, sec), text)
                 for _, _, _, sec, _, text, acr, title in batch]
        vecs = client.embed_documents(texts)
        pairs = [(r[5], v) for r, v in zip(batch, vecs)]  # CLEAN text stored
        metas = [{"chunk_id": r[0], "project_id": r[1], "source": r[2],
                  "section": r[3], "n_tokens": r[4]} for r in batch]
        ids = [r[0] for r in batch]
        if vs is None:
            vs = FAISS.from_embeddings(
                pairs, client, metadatas=metas, ids=ids,
                distance_strategy=DistanceStrategy.MAX_INNER_PRODUCT)
        else:
            vs.add_embeddings(pairs, metadatas=metas, ids=ids)
        done += len(batch)
        vs.save_local(str(TMP_INDEX_DIR))
        PROGRESS_PATH.write_text(json.dumps({**progress, "n_done": done}),
                                 encoding="utf-8")
        rate = (done - done0) / (time.perf_counter() - t0)
        print(f"  embedded {done}/{len(rows)} ({rate:.0f} chunks/s)")

    # -------------------------------------------------------------- finalize
    wall = time.perf_counter() - t0
    by_source = {}
    for r in rows:
        by_source[r[2]] = by_source.get(r[2], 0) + 1
    meta = {
        "embedding_model": EMBED_MODEL,
        "dim": EMBED_DIM,
        "gguf_sha256": gguf_sha256(),
        "llama_build": build_id,
        "chunk_counts": by_source,
        "n_vectors": len(rows),
        "chunk_policy": chunk_policy(chunk_target, split_overlap),
        "chunk_target": chunk_target,
        "split_overlap": split_overlap,
        "limit": limit,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "wall_clock_s": round(wall, 1),
        "chunks_per_s": round((done - done0) / wall, 1),
    }
    shutil.rmtree(INDEX_DIR, ignore_errors=True)
    TMP_INDEX_DIR.rename(INDEX_DIR)
    INDEX_META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    PROGRESS_PATH.unlink(missing_ok=True)
    print(f"index built: {len(rows)} vectors in {wall / 60:.1f} min -> "
          f"{INDEX_DIR}")
    print(f"meta -> {INDEX_META_PATH}")
