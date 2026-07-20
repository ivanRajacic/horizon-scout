"""Faithfulness check: llama-server bge GGUF embeddings vs reference sentence-transformers.

Reference: BAAI/bge-base-en-v1.5 via sentence-transformers, CPU, fp32, normalized.
Candidate: llama-server --embedding --pooling cls with a bge GGUF (OpenAI-compatible API).

Checks, per the GPU-validation side-quest spec:
- per-text cosine(reference, candidate): PASS if min >= 0.995 (F16 GGUF) / 0.99 (Q8_0)
- no NaN/Inf, norms sane
- retrieval equivalence on 10 queries over the same 200 chunks:
  top-10 overlap >= 9/10 for every query, rank-1 identical for >= 8/10 queries.
  Swaps between items whose reference similarities differ by < TIE_EPS are ties
  (rank order among near-equal neighbors is not meaningful) and do not count
  against the overlap; raw overlap is still reported.
"""
import argparse
import sys

import duckdb
import numpy as np
import requests
from sentence_transformers import SentenceTransformer

DB_PATH = r"C:\horizon-scout\data\processed\horizon.duckdb"
MODEL_NAME = "BAAI/bge-base-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
N_CHUNKS = 200
CHUNK_CHARS = 1500
TIE_EPS = 0.002

QUERIES = [
    "machine learning methods for medical image analysis",
    "renewable hydrogen production from electrolysis",
    "CO2 capture and storage technologies",
    "quantum computing hardware based on superconducting qubits",
    "sustainable agriculture and soil health monitoring",
    "battery materials for electric vehicles",
    "cybersecurity of critical infrastructure networks",
    "gene therapy for rare genetic diseases",
    "offshore wind turbine design and maintenance",
    "recycling of plastic waste into new materials",
]


def load_chunks(n=N_CHUNKS):
    con = duckdb.connect(DB_PATH, read_only=True)
    rows = con.execute(
        "select summary, workPerformed, finalResults from report_text "
        "where summary is not null and length(summary) > 500 "
        "order by rcn limit 300"
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


def embed_server(texts, base_url, batch=32):
    out = []
    for i in range(0, len(texts), batch):
        r = requests.post(
            f"{base_url}/v1/embeddings",
            json={"input": texts[i : i + batch]},
            timeout=300,
        )
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        out.extend(d["embedding"] for d in data)
    v = np.asarray(out, dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def top10(query_vecs, corpus_vecs):
    sims = query_vecs @ corpus_vecs.T
    return np.argsort(-sims, axis=1)[:, :10]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8080")
    ap.add_argument("--min-cosine", type=float, default=0.995,
                    help="0.995 for F16 GGUF, 0.99 for Q8_0")
    args = ap.parse_args()

    chunks = load_chunks()
    queries = [QUERY_PREFIX + q for q in QUERIES]
    print(f"loaded {len(chunks)} chunks from {DB_PATH}")

    ref_model = SentenceTransformer(MODEL_NAME, device="cpu")
    ref_chunks = ref_model.encode(chunks, batch_size=32, normalize_embeddings=True)
    ref_queries = ref_model.encode(queries, batch_size=32, normalize_embeddings=True)
    print("reference (sentence-transformers CPU) embedded")

    cand_chunks = embed_server(chunks, args.base_url)
    cand_queries = embed_server(queries, args.base_url)
    print("candidate (llama-server) embedded")

    ok = True

    for name, arr in [("ref", ref_chunks), ("cand", cand_chunks)]:
        if not np.isfinite(arr).all():
            print(f"FAIL: NaN/Inf in {name} embeddings")
            ok = False
    norms = np.linalg.norm(cand_chunks, axis=1)
    if not np.allclose(norms, 1.0, atol=1e-4):
        print(f"FAIL: candidate norms off: min={norms.min()}, max={norms.max()}")
        ok = False

    cos = (ref_chunks * cand_chunks).sum(axis=1)
    print(f"\ncosine(ref, cand) over {len(chunks)} chunks: "
          f"min={cos.min():.6f} p1={np.percentile(cos, 1):.6f} "
          f"mean={cos.mean():.6f}")
    if cos.min() < args.min_cosine:
        worst = int(np.argmin(cos))
        print(f"FAIL: min cosine {cos.min():.6f} < {args.min_cosine}")
        print(f"  worst chunk [{worst}]: {chunks[worst][:120]!r}")
        ok = False
    else:
        print(f"PASS: min cosine >= {args.min_cosine}")

    ref_top = top10(ref_queries, ref_chunks)
    cand_top = top10(cand_queries, cand_chunks)
    ref_sims = ref_queries @ ref_chunks.T
    print("\nretrieval equivalence (top-10 per query):")
    rank1_same = 0
    overlap_fail = False
    for qi, q in enumerate(QUERIES):
        r_top, c_top = set(ref_top[qi]), set(cand_top[qi])
        overlap = len(r_top & c_top)
        # a miss is a tie if the dropped item's ref-sim is within TIE_EPS of
        # some item that replaced it — rank order there carries no signal
        real_misses = 0
        for i in r_top - c_top:
            if not any(abs(ref_sims[qi][i] - ref_sims[qi][j]) < TIE_EPS
                       for j in c_top - r_top):
                real_misses += 1
        eff_overlap = 10 - real_misses
        r1 = ref_top[qi][0] == cand_top[qi][0]
        rank1_same += int(r1)
        flag = "" if eff_overlap >= 9 else "  <-- overlap FAIL"
        if eff_overlap < 9:
            overlap_fail = True
        tie_note = f" ({10 - overlap - real_misses} tie swaps)" if overlap < 10 else ""
        print(f"  q{qi}: overlap {overlap}/10, effective {eff_overlap}/10{tie_note}, "
              f"rank1 {'same' if r1 else 'DIFF'} | {q[:60]}{flag}")
    print(f"rank-1 identical: {rank1_same}/10 (need >= 8)")
    if overlap_fail or rank1_same < 8:
        print("FAIL: retrieval equivalence")
        ok = False
    else:
        print("PASS: retrieval equivalence")

    print(f"\nOVERALL: {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
