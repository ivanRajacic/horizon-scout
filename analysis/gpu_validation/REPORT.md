# GPU Embedding Validation - RX 6700 XT via llama-server (ROCm/HIP)

Date: 2026-07-20
Side-quest: go/no-go on GPU embedding before milestone 2's full index build.

## DECISION: GPU (llama-server)

11x speedup over CPU sentence-transformers with faithfulness and retrieval
equivalence proven on real corpus chunks - the full ~188k-chunk index builds in
~28 minutes on GPU vs ~5 hours on CPU.

## Why llama-server and not PyTorch ROCm

PyTorch has no GPU path for this card on this machine, verified empirically:

- `pip install torch --index-url https://download.pytorch.org/whl/rocm6.2` fails
  with "No matching distribution found" - the PyTorch ROCm index carries no
  Windows wheels at all.
- AMD's native-Windows PyTorch preview (ROCm 7.2.1 + PyTorch 2.9) supports only
  RDNA3/RDNA4 (gfx1100/1101/1200/1201) per the official Windows compatibility
  matrix. The RX 6700 XT is gfx1031 (RDNA2) - not supported, and RX 6000 is in
  maintenance mode.
- No WSL distro installed, and WSL2 ROCm also targets RDNA3+.

The existing llama.cpp HIP build already runs on this card, so embeddings are
served from `llama-server --embedding` and consumed as an OpenAI-compatible
endpoint.

## Setup

| Item | Value |
|---|---|
| GPU | AMD Radeon RX 6700 XT, 12 GB (gfx1031, RDNA2) |
| CPU (reference) | AMD Ryzen 5 5600X, 6C/12T |
| llama.cpp | `C:\llama`, build `1ec44d1`, HIP/ROCm 7.x (amdhip64_7.dll, rocblas) |
| Model | `bge-base-en-v1.5-f16.gguf` (F16, 209 MB, CompendiumLabs conversion) → `data\models\` |
| Reference | sentence-transformers `BAAI/bge-base-en-v1.5`, CPU, fp32, normalized |
| Launch | `llama-server -m bge-base-en-v1.5-f16.gguf --embedding --pooling cls -ngl 99 --port 8080 --cache-ram 0` |

Two launch flags are load-bearing:

- `--pooling cls` - bge models use CLS pooling; mean-pooled vectors are silently
  wrong.
- `--cache-ram 0` - the server's prompt cache (on by default) is designed for
  chat prefix reuse. Embedding inputs never share prefixes, so the cache gives
  zero hits while every request pays a linear scan over all previously cached
  prompts plus per-entry logging. Measured effect: GPU compute utilization stuck
  at 7-8% and the first benchmark attempt was ~10x slower end-to-end. With the
  cache disabled, GPU compute sits at a steady ~89%. This flag is mandatory for
  the embedding server, not a tuning nicety.

## Faithfulness (llama-server F16 GGUF vs sentence-transformers fp32 CPU)

200 real chunks from `report_text` in `data\processed\horizon.duckdb`.
Script: `scripts/gpu_embed_check.py`. Threshold for F16: min cosine >= 0.995.

| Metric | Value |
|---|---|
| min cosine | 0.999939 |
| p1 cosine | 0.999940 |
| mean cosine | 0.999963 |
| NaN/Inf | none |
| norms | 1.0 within 1e-4 (server returns normalized vectors) |

**PASS** - drift is ~6e-5, two orders of magnitude inside the threshold.

## Retrieval equivalence (decisive test)

10 domain queries (with the bge query prefix) against the 200 chunks, top-10
lists compared between stacks:

| Query | raw overlap | effective overlap | rank-1 |
|---|---|---|---|
| q0 medical imaging ML | 10/10 | 10/10 | same |
| q1 renewable hydrogen | 10/10 | 10/10 | same |
| q2 CO2 capture | 10/10 | 10/10 | same |
| q3 quantum computing | 10/10 | 10/10 | same |
| q4 sustainable agriculture | 9/10 | 10/10 (tie swap) | same |
| q5 EV battery materials | 10/10 | 10/10 | same |
| q6 cybersecurity | 9/10 | 10/10 (tie swap) | same |
| q7 gene therapy | 10/10 | 10/10 | same |
| q8 offshore wind | 10/10 | 10/10 | same |
| q9 plastic recycling | 8/10 | 10/10 (2 tie swaps) | same |

Every top-10 mismatch was diagnosed individually: in all cases the swapped items
sit within 0.0009 of each other in reference similarity (on scores ~0.49-0.52),
i.e. effectively tied neighbors trading places at the top-10 boundary - not
vector-space drift. Rank-1 identical for 10/10 queries (needed >= 8).
**PASS** (tie tolerance 0.002, implemented and reported in the check script).

## Throughput (5,000 real chunks, avg 867 chars)

Script: `scripts/gpu_embed_bench.py`. Server timing is end-to-end over HTTP.

| Stack | chunks/s | 188k projection |
|---|---|---|
| llama-server batch=16, 4 workers | 113.3 | 28 min |
| llama-server batch=32, 4 workers | 114.9 | 27 min |
| llama-server batch=64, 6 workers | 113.8 | 28 min |
| CPU sentence-transformers batch=64 | 10.5 | 5.0 h |

**Speedup: 11.0x** (needed >= 3x). Throughput is flat across request batch
sizes - the server's 4 parallel slots with n_ubatch=512 are the bottleneck, so
any reasonable client batching works; use batch=32 with 4 workers.

## Stability

- ~15,600 chunks embedded across the sustained runs (3 x 5,000 + checks) with
  no hangs, resets, or driver errors.
- GPU compute utilization steady at ~89% during the runs; system-wide VRAM
  ~2.7 GB total (bge-base F16 is tiny - no memory pressure).

## Constraint discovered for milestone 2: hard 512-token cap

The first benchmark attempt failed on a real chunk that tokenized to 616
tokens: llama-server rejects inputs longer than the physical batch (512) with
HTTP 500, whereas sentence-transformers silently truncates to bge's 512-token
limit. The milestone-2 chunker must enforce <= 512 tokens per chunk explicitly
(the benchmark uses 1,000-char chunks, ~250 tokens, well under the cap). This
is a correctness requirement for both stacks - silent truncation on CPU was
losing tail text too.

## Milestone 2 integration

Embedder becomes a config switch behind one interface:

```yaml
embedder:
  type: openai_compatible          # or: local_st (device: cpu) as fallback
  base_url: http://localhost:8080/v1
  model: bge-base-en-v1.5-f16.gguf
```

- `index_meta.json` records which stack built the index; the loader must refuse
  to serve queries embedded by the other stack. Equivalence passing means the
  choice is free - it does not mean stacks may be mixed at runtime.
- Query embedding uses the same server, with the bge query prefix
  "Represent this sentence for searching relevant passages: ".
- Server launch command (exact flags above) belongs in the project docs/config;
  if the server is down, fall back to `local_st` on CPU (overnight run is
  acceptable).

## Artifacts

- `scripts/gpu_embed_check.py` - faithfulness + retrieval equivalence gate
  (rerun after any llama.cpp upgrade or GGUF change)
- `scripts/gpu_embed_bench.py` - throughput benchmark
- `data\models\bge-base-en-v1.5-f16.gguf` - the served model
- `.venv-rocm` - scratch venv used for validation (CPU torch +
  sentence-transformers + duckdb); safe to delete or keep for reruns
