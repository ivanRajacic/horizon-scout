# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Horizon Scout: a hybrid retrieval + SQL question-answering system over the EU CORDIS/Horizon research-project corpus (35,389 projects in DuckDB, 190,248-vector dense index), plus an evaluation study (M5) measuring routing strategies against a hand-authored question bank. The research design lives in `horizon-scout.md` (plan doc, v4) and the execution state in `working-plan.md` - read those before any M5/eval work; they are the source of truth for decisions and their rationale, and decisions recorded there are locked unless the user changes them.

## Commands

Always use the venv interpreter: `./.venv/Scripts/python.exe` (Git Bash syntax).

- Run all tests: `./.venv/Scripts/python.exe -m pytest`
- Single test file: `./.venv/Scripts/python.exe -m pytest tests/test_bank.py`
- Single test: `./.venv/Scripts/python.exe -m pytest tests/test_bank.py -k <name>`
- Validate the question bank: `./.venv/Scripts/python.exe -m src.cli validate-bank`
- End-to-end ask pipeline: `./.venv/Scripts/python.exe -m src.cli ask "<question>"`
- Other CLI subcommands (`python -m src.cli --help`): `build-index`, `build-fts`, `search`, `smoke`, `ask-sql`, `smoke-sql`, `smoke-router`, `bench-retrievers`, `explore` (verbose REPL), `judge-file`

Local model servers (llama-server processes; launch commands are pinned in `src/config.py` and their flags are LOAD-BEARING - do not tune them):

- Embedder (bge-base, port 8080): required for dense/hybrid retrieval and index builds
- Reranker (bge-reranker-v2-m3, port 8082): required for the rerank condition
- Qwen3-8B (port 8081): legacy generation backend only (`GEN_BACKEND="local"`); not needed in the default configuration

Generation and judging run through `claude -p` (Claude CLI on the Max subscription), not the local servers.

## Architecture

Pipeline (M1-M4, `src/`):

- `ingest/` - CORDIS CSVs into `data/processed/horizon.duckdb`
- Chunker + FAISS index build - structure-first paragraph packing (policy in `src/config.py`), bge embeddings via the local llama-server
- `retrieval/` - four conditions behind one interface (`base.py`, `registry.py`): `lexical.py` (DuckDB FTS/BM25), `vector_search.py` (FAISS), `hybrid.py` (RRF fusion), `rerank.py` (cross-encoder), plus `scoped.py` (SQL-filtered vector search) and `sql_path.py` (guardrailed text-to-SQL; `validate_sql` enforces single read-only SELECT)
- `router/` - routes a question to sql / vector / scoped
- `synthesis/` - answer generation from retrieved evidence
- `ask.py` - end-to-end: route, retrieve/execute, synthesize; logs every run to `data/logs/ask.jsonl`

Generation/judging transport (v4 decision): `src/claude_cli.py` is the ONE `claude -p` transport function, gated by ONE process-wide semaphore (cap 16) shared by generation AND judging - never add a second transport or semaphore. `src/llm.py:make_llm()` selects the generation backend by `GEN_BACKEND` ("claude" = Haiku, default). Role separation is fixed: Haiku generates, Sonnet judges (`src/judge/`, RAGAS + rubric refusal-overlay), Opus authors questions/references - no model wears two hats.

Evaluation (M5, `src/eval/`, `eval/`):

- `bank.py` - question-bank schema v2 + loud validator (every violation reported). Levels L1/L2/L3/ADV; route-scoped subtypes; SQL entries born verified (`answer_columns`, `level_evidence`, `schema_docs_hash`); vector levels are DEFINED by |gold_project_ids| (L1=1, L2=2-4, L3=5+)
- `eval/bank.jsonl` - the bank. Authored ONLY through drafting skills (e.g. `/draft-sql-question`), one question per pass, execution-verified. Two sanctioned append paths, both human-gated: the interactive per-question confirm inside a drafting skill, or `/draft-batch` staging to `eval/drafts/` + the user's ticked report reviewed via `python -m src.cli promote-drafts` (`src/eval/promote.py`). Never hand-edit or bulk-import; `eval/archive/` holds the retired pre-skill smoke set
- `mcp_server.py` - read-only MCP server (`horizon-draft` in `.mcp.json`) exposing `run_sql` / `get_schema_docs` / `get_bank_questions` to drafting skills. Deliberately has no write tools; safety enforced in code (SQL guard + read-only connection), SQL errors returned as results, every call logged

## Conventions that matter here

- Trace everything: every prompt asset carries a version label AND a content hash (`src/llm.py:fingerprint`); bump the version on any meaningful edit. Runs log model + prompt versions to `data/logs/*.jsonl`. New prompts or judged paths must follow this discipline.
- `src/config.py` is the single source of truth for paths, models, thresholds, and server launch commands. Comments there record WHY values are load-bearing - read them before changing anything.
- `src/retrieval/schema_docs.md` is a versioned prompt asset (`SCHEMA_DOCS_VERSION` in config): any meaningful edit needs a version bump, and bank questions record the hash they were authored against.
- Tests fake external transports (llama servers, `claude -p`, RAGAS internals); nothing in the suite requires a running server. Keep it that way.
- `ragas==0.4.3` and `mcp` are version-pinned for documented reasons (see `requirements.txt` comments) - do not upgrade casually.
- The plan docs use route vocabulary `sql|vector|hybrid|ambiguous`; the runtime calls the hybrid mode "scoped". `ROUTE_TO_MODE` in `src/eval/bank.py` is the one place that mapping lives.
- Frozen artifacts (router prompt, bank, retrieval stack, judge thresholds) must never be edited after their freeze point in `working-plan.md`; anything frozen needs the user's explicit say-so to touch.
