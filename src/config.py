"""Project-wide configuration: paths, embedding server, chunking policy.

Single source of truth for milestone 2's chunk/embed/index/search stack.
The llama-server launch flags are LOAD-BEARING (see
analysis/gpu_validation/REPORT.md): --pooling cls and --cache-ram 0 are
correctness/throughput requirements, not tuning niceties.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from .env into os.environ - stdlib only, since
    requirements are deliberately pinned. Never overrides variables already
    set, so a real environment always wins over the file. Blank lines and
    `#` comments are skipped; values may be single- or double-quoted."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(ROOT / ".env")
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

# --- Generation backend switch ---
# GEN_BACKEND selects the generation client behind src.llm.make_llm():
#   "api" (v5 default) - gpt-5-nano over OpenAI's chat-completions endpoint
#                        (src/openai_compat.py); the run-time seat re-decided
#                        2026-08-05 (was Gemini 2.5 Flash-Lite, dropped before
#                        any run). Known tradeoff: the GPT-5 nano/mini tier is
#                        being retired (gpt-5-mini shutdown 2026-12-11), but
#                        the seat only has to outlive the study.
#   "claude"           - the retired v4 seat: Haiku via `claude -p`, gated by
#                        the shared semaphore below.
#   "local"            - the legacy llama-server Qwen3-8B path (kept for a
#                        possible RQ3 revival with a weak generator).
# Nothing downstream knows which client is behind .chat().
GEN_BACKEND = "api"
GEN_MODEL = "claude-haiku-4-5-20251001"

# --- External API seats (v5, decided 2026-08-04) ---
# Nothing at run time stays on the subscription: generation AND judging move
# to cheap external APIs, different vendors so no vendor holds two seats
# (horizon-scout.md §5). Both seats are FROZEN for the whole study once
# smoked - a mid-study seat change would make every recorded number
# incomparable. The judge seat gets the most capable cheap model because a
# weak judge corrupts every number; the generator seat gets the boring
# reliable JSON emitter.
#
# The *_EXTRA dicts are the thinking/reasoning OFF pin, merged verbatim into
# every request body: GPT-5.6 takes reasoning_effort "none" to switch
# reasoning off; DeepSeek V4 Flash turns thinking ON by default and takes
# {"thinking": {"type": "disabled"}} to turn it off (thinking mode also
# ignores temperature, so the judge's temperature 0 only means anything with
# thinking disabled).
#
# Two GPT-5-family quirks the gen seat pins around: temperature is locked to
# 1 (any other value is rejected), and the token cap parameter is
# max_completion_tokens, not max_tokens - *_MAX_TOKENS_PARAM names the right
# one per seat.
#
# Prices are pinned per MILLION tokens because these APIs return token
# counts, not dollars - src/openai_compat.py computes each call's cost from
# these and records it through src/eval/usage.py exactly like `claude -p`
# envelopes were. Verified 2026-08-04 against the providers' price pages.
# Unlike the Max-subscription figures, these dollars are BILLED, not priced.
API_TIMEOUT_S = 240.0

GEN_API_BASE_URL = "https://api.openai.com/v1"
GEN_API_MODEL = "gpt-5-nano"
GEN_API_KEY_ENV = "OPENAI_API_KEY"
GEN_API_TEMPERATURE = 1.0     # GPT-5 family: locked, other values rejected
# GPT-5 (unlike 5.6) has no "none" level - "minimal" is its floor.
GEN_API_EXTRA = {"reasoning_effort": "minimal"}
GEN_API_MAX_TOKENS_PARAM = "max_completion_tokens"
GEN_API_CONCURRENCY = 8
GEN_API_PRICES_PER_MTOK = {"input": 0.05, "cache_read": 0.005,
                           "output": 0.40}

JUDGE_API_BASE_URL = "https://api.deepseek.com"
JUDGE_API_MODEL = "deepseek-v4-flash"
JUDGE_API_KEY_ENV = "DEEPSEEK_API_KEY"
JUDGE_API_TEMPERATURE = 0.0
JUDGE_API_EXTRA = {"thinking": {"type": "disabled"}}
JUDGE_API_MAX_TOKENS_PARAM = "max_tokens"
JUDGE_API_CONCURRENCY = 8
JUDGE_API_PRICES_PER_MTOK = {"input": 0.14, "cache_read": 0.0028,
                             "output": 0.28}

# --- Shared `claude -p` transport (generation + rubric judge + RAGAS) ---
# ONE process-wide semaphore (src/claude_cli.py) gates every `claude -p`
# subprocess across ALL paths, so the cap is global. Max's constraint is the
# usage window (total tokens), not request rate, so concurrency is
# effectively free; the cap bounds local process sprawl. Hard ceiling 16.
CLAUDE_TIMEOUT_S = 240.0
CLAUDE_CONCURRENCY = 16
CLAUDE_MAX_CONCURRENCY = 16

# --- LLM server (legacy local generation; GEN_BACKEND = "local") ---
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

# --- Judge (M5, v5): DeepSeek V4 Flash over its OpenAI-compatible API ---
# v5 (2026-08-04): the judge seat leaves the subscription for the external
# API seat pinned above (JUDGE_API_*); the claude keys stay only so old runs
# can be reproduced and tests can exercise the legacy path. JUDGE_BACKENDS
# maps each key to its transport - "api" = src/openai_compat.py with its own
# per-seat concurrency, "claude" = the shared `claude -p` semaphore. Model
# strings are pinned in full and logged per verdict. The judge is unvalidated
# against human labels - results are judge-scored, never accuracy. Role
# separation: Opus authored references (done), Gemini generates, DeepSeek
# judges - no model wears two hats, no vendor holds two seats.
JUDGE_MODELS = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-5",
    "deepseek": JUDGE_API_MODEL,
}
JUDGE_BACKENDS = {
    "haiku": "claude",
    "sonnet": "claude",
    "deepseek": "api",
}
JUDGE_DEFAULT = "deepseek"
JUDGE_LOG_PATH = ROOT / "data" / "logs" / "judge.jsonl"

# RAGAS pass thresholds - PILOT DRAFT values, frozen with the judged-metric
# rubric before Study 2 (d10 freeze table). v4: no hand-grade calibration
# (RQ5 scratched) - thresholds are frozen as-is and disclosed as such.
# Pass = factual_correctness >= threshold AND (faithfulness >= threshold when
# measurable). Adversarial questions bypass RAGAS (rubric-judge overlay).
JUDGE_PASS_FACTUAL = 0.75
JUDGE_PASS_FAITHFULNESS = 0.80

# --- Drafting MCP server (M5 question authoring) ---
# schema_docs.md gets a version label like every other prompt asset: bump on
# any meaningful edit; the content hash (src.llm.fingerprint) catches silent
# ones. Appended bank questions record both.
# sd2 (2026-07-24): euroSciVocPath was documented WITH a leading slash, which
# no row has (0 of 111,614) - so every generated `LIKE '/natural sciences/%'`
# returned zero rows. Corrected, plus the 6 top-level branches and the
# split_part level idiom. A ground-truth bug fix, NOT Study 0.5's
# pre-registered value-description intervention; see working-plan.md Step 4.
# Bank entries authored before this carry the sd1-pilot hash as provenance -
# that is honest history, not staleness (bank.py never re-checks the hash).
# sd3 (2026-08-05): all 56 fundingScheme values listed instead of the top 11,
# after hyb-09's narrowing wrote a scheme's descriptive name where the column
# stores the code. Better odds only - the value gate in scoped.py is the
# floor (hyb-06 proved a complete list in the prompt still leaks).
SCHEMA_DOCS_VERSION = "sd3"
# The skill-authored bank (schema v2). The pre-skill smoke set is archived
# under eval/archive/ in the old schema and is not validated anymore.
BANK_PATH = ROOT / "eval" / "bank.jsonl"
DRAFT_MCP_LOG_PATH = ROOT / "data" / "logs" / "draft_mcp.jsonl"

# bank_brief.md is the SHARED standard for the three authoring nodes -
# drafter, critic, and judge all read it, so what "a good bank question" means
# cannot drift between them. Same versioning discipline as schema_docs: bump
# on any meaningful edit; the content hash (src.llm.fingerprint) catches
# silent ones. bb1 (2026-07-25) is the first version, written with the
# four-node re-architecture of /question-orchestrator. bb2 (2026-07-25) added section 7
# (Seeds), the standard for the upstream corpus-explorer: it decides which
# seeds the other three nodes ever see, so it is held to the same definition of
# good. `frontier-report` pastes that section into every explorer spawn prompt.
# bb4 (2026-08-04) records what makes an ADV proof real: absence_evidence as a
# typed, re-executed record, and twin_id naming the answerable question the
# adversarial one perturbs - the control a refusal-only set cannot supply.
BANK_BRIEF_PATH = ROOT / "src" / "eval" / "bank_brief.md"
BANK_BRIEF_VERSION = "bb4"

# corpus_profile.md is the exploration agent's output (working-plan d3):
# a CUMULATIVE map of the database - what each explored region is about and
# what questions it can support - plus query-verified candidate topics
# sectioned per bank category. Its `## Frontier` table over the 46 euroSciVoc
# buckets is what makes exploration cumulative: it records where we have and
# have not been, so each run goes somewhere new and drafting stays wide.
# Same versioning discipline as schema_docs: bump on any meaningful edit; the
# content hash catches silent ones. "cp0-unbuilt" until the first
# /explore-corpus run writes the file (then cp1). cp3 (2026-07-24) added the
# frontier, the corpus map and the structural-findings list.
CORPUS_PROFILE_PATH = ROOT / "src" / "retrieval" / "corpus_profile.md"
CORPUS_PROFILE_VERSION = "cp8"

# --- Lexical (BM25) retrieval: DuckDB FTS over the chunk corpus ---
FTS_STEMMER = "porter"
FTS_STOPWORDS = "english"

# --- Hybrid fusion (lexical + dense) ---
RRF_K = 60              # reciprocal-rank-fusion constant (Cormack et al. 2009)
FUSE_CANDIDATES = 100   # candidates pulled from EACH retriever before fusion
RERANK_DEPTH = 50       # top fused candidates re-scored by the cross-encoder

# The ONE retrieval stack the system runs: ask.py's vector route, the scoped
# route's semantic step, and every eval run. A name from
# src.retrieval.registry.RETRIEVERS, built through build_retriever.
#
# Why this value: RQ2 (the four-condition ladder as a study) was dropped
# 2026-08-03 to put the whole study on routing. The choice is not an assumption
# - the 2026-07-29 ladder (data/runs/ladder-2026-07-29/report.md, 10 vector
# questions x 4 conditions) measured hybrid_rerank best of four at recall@20
# 0.875, against hybrid 0.842, dense 0.839, lexical 0.706. That was a
# 10-question pilot, not a bank-scale measurement; the write-up carries the
# limitation. Changing the stack the study runs on is this one line.
#
# Note for anyone reading old logs: until 2026-08-03 ask.py ran DENSE-ONLY (a
# bare VectorSearcher), so every data/logs/ask.jsonl row without a
# versions.retriever field predates this and is not comparable.
RUNTIME_RETRIEVER = "hybrid_rerank"


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
