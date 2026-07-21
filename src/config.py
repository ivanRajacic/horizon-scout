"""Project-wide configuration: paths, embedding server, chunking policy.

Single source of truth for milestone 2's chunk/embed/index/search stack.
The llama-server launch flags are LOAD-BEARING (see
analysis/gpu_validation/REPORT.md): --pooling cls and --cache-ram 0 are
correctness/throughput requirements, not tuning niceties.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "horizon.duckdb"
GGUF_PATH = ROOT / "data" / "models" / "bge-base-en-v1.5-f16.gguf"
TOKENIZER_PATH = ROOT / "data" / "models" / "bge-base-en-v1.5.tokenizer.json"
INDEX_DIR = ROOT / "data" / "processed" / "faiss_index"
INDEX_META_PATH = ROOT / "data" / "processed" / "index_meta.json"
PROGRESS_PATH = ROOT / "data" / "processed" / "index_build_progress.json"

EMBED_MODEL = "bge-base-en-v1.5-f16.gguf"
EMBED_DIM = 768
EMBED_BASE_URL = "http://127.0.0.1:8080"
EMBED_BATCH = 32
EMBED_WORKERS = 4

SERVER_LAUNCH_CMD = (
    r"C:\llama\llama-server.exe -m " + str(GGUF_PATH)
    + " --embedding --pooling cls -ngl 99 --port 8080 --cache-ram 0"
)

# --- LLM server (SQL generation, M3b; router/synthesis M4; judge M5) ---
# Second llama-server process; bge stays on 8080. Unlike the embedding server,
# the LLM server KEEPS the default prompt cache: every SQL call starts with the
# same system prompt (schema_docs.md), so prefix reuse skips most of prefill.
# Qwen3-8B, NOT Qwen3.5: the whole Qwen3.5 family is a hybrid SSM/linear-
# attention architecture whose decode ops run CPU-only on this ROCm build
# (3 t/s measured). See analysis/llm_gate/REPORT.md. The launch flags are
# LOAD-BEARING: -fit off -ngl 99 (explicit full offload), -np 1 (one slot),
# --reasoning-budget 0 (thinking disabled server-side), prompt cache left ON
# (shared schema_docs system prefix makes prefill a cache hit).
LLM_GGUF_PATH = ROOT / "data" / "models" / "Qwen3-8B-Q5_K_M.gguf"
LLM_MODEL = "Qwen3-8B-Q5_K_M"
LLM_BASE_URL = "http://127.0.0.1:8081"
LLM_CTX = 8192

LLM_SERVER_LAUNCH_CMD = (
    r"C:\llama\llama-server.exe -m " + str(LLM_GGUF_PATH)
    + f" -ngl 99 -fit off -np 1 -c {LLM_CTX} --reasoning-budget 0 --port 8081"
)

SCHEMA_DOCS_PATH = ROOT / "src" / "retrieval" / "schema_docs.md"
SQL_LOG_PATH = ROOT / "data" / "logs" / "sql_path.jsonl"
SQL_TIMEOUT_S = 10.0
SQL_ROW_LIMIT = 1000

# bge query prefix for short queries at search time (report_text chunks and
# objectives are embedded WITHOUT it).
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

CHUNK_TARGET = 400   # packing target, tokens of clean chunk text
CHUNK_CAP = 512      # hard cap on the FULL embedded string (header + specials)
SPLIT_OVERLAP = 50   # sentence-split overlap (tokens) inside oversized paragraphs

REPORT_SECTIONS = ("summary", "workPerformed", "finalResults")

# --- Reranker server (M6 hybrid retrieval; cross-encoder rerank) ---
# Third llama-server process; bge embedder stays on 8080, Qwen on 8081. The
# reranker is a cross-encoder (bge-reranker-v2-m3) scoring (query, passage)
# pairs via llama.cpp's /rerank endpoint. Launch flags are load-bearing:
# --reranking enables the endpoint, --pooling rank selects the rank head, and
# -b/-ub 2048 raise the physical batch above the default 512 - a (query+chunk)
# pair can exceed 512 tokens and llama.cpp returns HTTP 500 when a single
# sequence overflows n_ubatch (the same 512-cap failure the embedding server
# hit; see analysis/gpu_validation/REPORT.md). Q8_0 (~606 MB) fits alongside
# the other two models on the 12 GB card.
RERANKER_GGUF_PATH = ROOT / "data" / "models" / "bge-reranker-v2-m3-Q8_0.gguf"
RERANKER_MODEL = "bge-reranker-v2-m3-Q8_0"
RERANK_BASE_URL = "http://127.0.0.1:8082"

RERANK_SERVER_LAUNCH_CMD = (
    r"C:\llama\llama-server.exe -m " + str(RERANKER_GGUF_PATH)
    + " --reranking --pooling rank -ngl 99 --port 8082 -c 2048 -b 2048 -ub 2048"
)

# --- Judge (M5, RQ5): Claude via `claude -p` on the Max subscription ---
# Transport = ONE function (src/judge/judge.py: call_claude); a billing change
# means swapping that function for an API call, nothing else. Model strings
# are pinned in full and logged per verdict; judge selection (Haiku vs Sonnet)
# is empirical over the hand-graded set - both are wired.
JUDGE_MODELS = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-5",
}
JUDGE_DEFAULT = "haiku"
JUDGE_LOG_PATH = ROOT / "data" / "logs" / "judge.jsonl"
JUDGE_TIMEOUT_S = 240.0

# Parallel `claude -p` judge processes. One shared semaphore gates every
# judging path, so this cap is global. Max's constraint is the usage window
# (total tokens), not request rate, so concurrency is effectively free;
# JUDGE_MAX_CONCURRENCY bounds local process sprawl.
JUDGE_CONCURRENCY = 8
JUDGE_MAX_CONCURRENCY = 16

# RAGAS pass thresholds - PILOT DRAFT values, calibrated against hand grades
# and frozen with the judged-metric rubric before Study 2 (d10 freeze table).
# Pass = factual_correctness >= threshold AND (faithfulness >= threshold when
# measurable). Adversarial questions bypass RAGAS (rubric-judge overlay).
JUDGE_PASS_FACTUAL = 0.75
JUDGE_PASS_FAITHFULNESS = 0.80

# --- Lexical (BM25) retrieval: DuckDB FTS over the chunk corpus ---
FTS_STEMMER = "porter"
FTS_STOPWORDS = "english"

# --- Hybrid fusion (lexical + dense) ---
RRF_K = 60              # reciprocal-rank-fusion constant (Cormack et al. 2009)
FUSE_CANDIDATES = 100   # candidates pulled from EACH retriever before fusion
RERANK_DEPTH = 50       # top fused candidates re-scored by the cross-encoder


def chunk_policy(chunk_target: int, split_overlap: int) -> str:
    """One-line policy string recorded in index_meta.json."""
    return (
        "structure-first paragraph packing per section, "
        f"target={chunk_target}, cap={CHUNK_CAP} tokens on the embedded "
        "string (bge tokenizer, incl. header and special tokens), "
        f"oversized-paragraph sentence split with {split_overlap}-token "
        "overlap, header 'ACRONYM - title | section', objectives whole"
    )


def load_tokenizer():
    from tokenizers import Tokenizer

    return Tokenizer.from_file(str(TOKENIZER_PATH))
