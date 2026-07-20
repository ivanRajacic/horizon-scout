"""Throughput benchmark: llama-server GPU embeddings vs CPU sentence-transformers.

Embeds the same 5,000 real chunks both ways and projects wall-time for the full
~188k-chunk corpus. Server timing is end-to-end including HTTP, using batched
requests with a small thread pool (the server runs 4 parallel slots).
"""
import argparse
import time
from concurrent.futures import ThreadPoolExecutor

import duckdb
import numpy as np
import requests
from sentence_transformers import SentenceTransformer

DB_PATH = r"C:\horizon-scout\data\processed\horizon.duckdb"
MODEL_NAME = "BAAI/bge-base-en-v1.5"
# bge-base has a 512-token limit; llama-server rejects longer inputs outright
# (sentence-transformers silently truncates). 1000 chars stays safely under it —
# milestone 2's chunker must enforce the 512-token cap explicitly.
CHUNK_CHARS = 1000
PROJECT_TOTAL = 188_000


def load_chunks(n):
    con = duckdb.connect(DB_PATH, read_only=True)
    rows = con.execute(
        "select summary, workPerformed, finalResults from report_text "
        "where summary is not null and length(summary) > 500 "
        "order by rcn limit 3000"
    ).fetchall()
    chunks = []
    for row in rows:
        for field in row:
            if not field:
                continue
            for i in range(0, len(field), CHUNK_CHARS):
                piece = field[i : i + CHUNK_CHARS].strip()
                if len(piece) > 200:
                    chunks.append(piece)
        if len(chunks) >= n:
            break
    return chunks[:n]


def bench_server(chunks, base_url, batch, workers):
    session_local = [requests.Session() for _ in range(workers)]

    def one(args):
        wi, texts = args
        r = session_local[wi % workers].post(
            f"{base_url}/v1/embeddings", json={"input": texts}, timeout=600)
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]

    batches = [(i, chunks[i * batch:(i + 1) * batch])
               for i in range((len(chunks) + batch - 1) // batch)]
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(one, batches))
    dt = time.perf_counter() - t0
    vecs = np.asarray([v for b in results for v in b], dtype=np.float32)
    assert vecs.shape == (len(chunks), 768), vecs.shape
    assert np.isfinite(vecs).all(), "NaN/Inf in server output"
    return dt


def bench_cpu(chunks, batch):
    model = SentenceTransformer(MODEL_NAME, device="cpu")
    model.encode(chunks[:32], normalize_embeddings=True)  # warm-up
    t0 = time.perf_counter()
    model.encode(chunks, batch_size=batch, normalize_embeddings=True,
                 show_progress_bar=False)
    return time.perf_counter() - t0


def report(label, n, dt):
    rate = n / dt
    proj_min = PROJECT_TOTAL / rate / 60
    print(f"{label}: {n} chunks in {dt:.1f}s = {rate:.1f} chunks/s "
          f"-> {PROJECT_TOTAL} chunks in {proj_min:.0f} min ({proj_min / 60:.1f} h)")
    return rate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8080")
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--skip-cpu", action="store_true")
    args = ap.parse_args()

    chunks = load_chunks(args.n)
    print(f"loaded {len(chunks)} chunks, avg {sum(map(len, chunks)) / len(chunks):.0f} chars")

    rates = {}
    for batch, workers in [(16, 4), (32, 4), (64, 6)]:
        dt = bench_server(chunks, args.base_url, batch, workers)
        rates[f"server b{batch}w{workers}"] = report(
            f"llama-server batch={batch} workers={workers}", len(chunks), dt)

    if not args.skip_cpu:
        dt = bench_cpu(chunks, 64)
        cpu_rate = report("CPU sentence-transformers batch=64", len(chunks), dt)
        best = max(rates.values())
        print(f"\nspeedup (best server / CPU): {best / cpu_rate:.1f}x")


if __name__ == "__main__":
    main()
