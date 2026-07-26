# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Horizon Scout: a hybrid retrieval + SQL question-answering system over the EU CORDIS/Horizon research-project corpus (35,389 projects in DuckDB, 190,248-vector FAISS index), plus an evaluation study (M5) measuring routing strategies against a hand-authored question bank (`eval/bank.jsonl`, 21 of a planned ~97).

Where the decisions live, in reading order:

- `horizon-scout.md` - the research design (plan doc, v4). Why the study is shaped the way it is.
- `working-plan.md` - execution state. What is done, what is next, what was rejected and why.
- `optimization/` - numbered, self-contained plans for the drafting/exploration pipelines, each measured against the 2026-07-25 batch baseline in `optimization/README.md`. Plans 01-04 implemented; 05 is an unapproved proposal.
- `docs/archive/` - the two pipeline audits (drafting, explorer) and the M2 milestone doc. Rationale, superseded by the plans above but still the record of *why*.

Read the first two before any M5/eval work. Decisions recorded there are locked unless the user changes them.

## Commands

Always use the venv interpreter: `./.venv/Scripts/python.exe` (Git Bash syntax).

- Tests: `./.venv/Scripts/python.exe -m pytest` (369 passing) - single file `-m pytest tests/test_bank.py`, single test `-k <name>`
- Bank: `validate-bank`, `validate-record <record.json|->`, `promote-drafts <report.md>`, `judge-file <cases.jsonl>`
- Runtime: `ask "<q>"`, `ask-sql`, `search`, `explore` (verbose REPL), `build-index`, `build-fts`, `smoke`, `smoke-sql`, `smoke-router`, `bench-retrievers`
- `/draft-batch` nodes: `gap-report`, `next-ids`, `journal-append`, `batch-crosscheck`, `write-batch`
- `/explore-corpus` nodes: `frontier-report`, `verify-evidence`, `explore-crosscheck`, `write-profile`
- Cost: `agent-trace` - per-agent time and tokens for a run, read from the subagent transcripts (`src/eval/trace.py`)

Local model servers (llama-server; launch commands pinned in `src/config.py`, flags LOAD-BEARING - do not tune them):

- Embedder (bge-base, :8080) - dense/hybrid retrieval and index builds
- Reranker (bge-reranker-v2-m3, :8082) - the rerank condition
- Qwen3-8B (:8081) - legacy generation only (`GEN_BACKEND="local"`); not needed by default

Generation and judging run through `claude -p` (Claude CLI, Max subscription), not the local servers.

## Architecture

### Runtime pipeline (M1-M4, `src/`)

- `ingest/` - CORDIS CSVs into `data/processed/horizon.duckdb`; chunker (structure-first paragraph packing, policy in `config.py`) + FAISS build via the embedder
- `retrieval/` - four conditions behind one interface (`base.py`, `registry.py`): `lexical.py` (DuckDB FTS/BM25), `vector_search.py` (FAISS), `hybrid.py` (RRF), `rerank.py` (cross-encoder); plus `scoped.py` (SQL-filtered vector search) and `sql_path.py` (guardrailed text-to-SQL - `validate_sql` enforces a single read-only SELECT)
- `router/` -> sql / vector / scoped; `synthesis/` -> answer from evidence; `ask.py` -> end-to-end, logging every run to `data/logs/ask.jsonl`

**Transport and roles (v4, fixed).** `src/claude_cli.py` is the ONE `claude -p` transport, gated by ONE process-wide semaphore (cap 16) shared by generation AND judging - never add a second of either. `src/llm.py:make_llm()` picks the backend by `GEN_BACKEND` ("claude" = Haiku, default). Haiku generates, Sonnet judges (`src/judge/`, RAGAS + a rubric refusal-overlay for adversarial questions), Opus authors questions and references. No model wears two hats.

### The bank (M5, `src/eval/`, `eval/`)

- `bank.py` - schema v2 + a loud validator (reports every violation, not the first). Levels L1/L2/L3/ADV, route-scoped subtypes, SQL entries born verified (`answer_columns`, `level_evidence`, `schema_docs_hash`); vector levels are DEFINED by `|gold_project_ids|` (L1=1, L2=2-4, L3=5+)
- `eval/bank.jsonl` - authored ONLY through the drafting skills, one question per pass, execution-verified. Two sanctioned append paths, both human-gated: the per-question confirm inside an interactive drafting skill, or `/draft-batch` staging to `eval/drafts/` followed by the user's ticked report and `promote-drafts` (`promote.py`). Never hand-edit, never bulk-import. `eval/archive/` holds the retired pre-skill smoke set
- `mcp_server.py` - the read-only `horizon-draft` MCP server (`.mcp.json`): `run_sql`, `get_schema_docs`, `get_bank_questions`, `search_corpus` (pooled/per-condition retrieval), `get_project_text` (field selection + char budget), `get_corpus_profile`, and two deterministic self-gates - `precheck_record` (re-executes a finished draft's gold SQL, gold text, filter survivors, survivor-window and gold-bounds, schema-docs freshness) and `precheck_candidate` (one rung upstream: re-executes a candidate's evidence against its recorded numbers before a drafter is ever spawned). Both live here rather than in the CLI because they run inside an agent's own loop and those agents have no shell. Deliberately no write tools; safety in code (SQL guard + read-only connection); SQL errors returned as results; every call logged to `data/logs/draft_mcp.jsonl`

### Authoring pipelines

Everything is authored by skills (`.claude/skills/`) driving read-only subagents (`.claude/agents/`), over deterministic CLI nodes.

- **`/draft-batch`** - quota-driven batches. `src/eval/batch.py` owns everything with a right answer; state is one typed append-only journal (`eval/drafts/draft-batch-journal-<date>.jsonl`) whose slot envelopes are validated while `record` stays opaque mid-run; both canonical outputs are GENERATED by `write-batch`, not written by a model.
- **`/explore-corpus`** - cumulative corpus exploration. `src/eval/explore.py` is `batch.py`'s sibling: the frontier, the slice partition, the orientation block, id assignment, evidence verification, the width rule and the profile insertions are all code. Evidence is `{sql, key_result}` and **all** of it is re-executed at close-out - the profile's numbers are never trusted, only reproduced. Journal at `eval/exploration/journal-<date>.jsonl`.
- **Single questions** - `/draft-{sql,vector,hybrid,adversarial,ambiguous,compositional}-question`. `/review-question` (critic) and `/judge-question` (judge) also run inside the batch loop. `/review-bank` is STALE - see the note at the top of that file.
- `src/eval/bank_brief.md` - the shared standard read by drafter, critic and judge (`BANK_BRIEF_VERSION`), so "a good bank question" cannot drift between them. Section 7 (Seeds) extends it upstream to `corpus-explorer`; `frontier-report` pastes that section into every explorer spawn prompt.
- `src/retrieval/corpus_profile.md` - the cumulative map written by `/explore-corpus`: what each explored region is about, what questions it supports, query-verified seeds, and a `## Frontier` table over the 46 euroSciVoc buckets recording where exploration has and has not been. Merged by APPENDING, never rewriting; drafting skills read the frontier to stay wide.

**Two rules that hold both pipelines together:**

- **Split authority.** The drafter authors and self-verifies facts. The critic attacks and reports typed findings (class + `HIGH|MID|LOW` + executed evidence) with no verdict and no kill power. The judge rules `UPHELD`/`DISMISSED` on every HIGH and MID finding, and only then decides ACCEPT / FIX / ABANDON. The orchestrator is a message bus that judges nothing. No node both finds a problem and decides what it costs - do not collapse these roles back together.
- **Deterministic-first.** Anything with a right answer is code, not a model. When a check is possible by re-execution, do it exhaustively rather than sampling it with an expensive node - that is why `verify-evidence` re-runs every exploration claim instead of two per section. Model nodes are for authorship and judgement; adding one to do arithmetic is the anti-pattern.

## Conventions that matter here

- **Trace everything.** Every prompt asset carries a version label AND a content hash (`src/llm.py:fingerprint`); bump the version on any meaningful edit. Runs log model + prompt versions to `data/logs/*.jsonl`. New prompts or judged paths must follow this.
- `src/config.py` is the single source of truth for paths, models, thresholds and server launch commands. Its comments record WHY values are load-bearing - read them before changing anything.
- `src/retrieval/schema_docs.md`, `src/retrieval/corpus_profile.md` and `src/eval/bank_brief.md` are versioned prompt assets (`SCHEMA_DOCS_VERSION`, `CORPUS_PROFILE_VERSION`, `BANK_BRIEF_VERSION`). Bank questions record the schema-docs hash they were authored against; an older entry keeping an older hash is provenance, not staleness - the validator never re-checks it.
- Tests fake external transports (llama servers, `claude -p`, RAGAS internals); nothing in the suite requires a running server. Keep it that way. One known wrinkle: `tests/test_lexical.py` builds the FTS index and so needs a **read-write** DuckDB handle - it errors while any `horizon-draft` MCP server from another session holds the file open. That is a lock conflict, not a regression.
- `ragas==0.4.3` and `mcp` are version-pinned for documented reasons (see `requirements.txt`) - do not upgrade casually.
- The plan docs say `sql|vector|hybrid|ambiguous`; the runtime calls the hybrid mode "scoped". `ROUTE_TO_MODE` in `src/eval/bank.py` is the one place that mapping lives.
- Frozen artifacts (router prompt, bank, retrieval stack, judge thresholds) must never be edited after their freeze point in `working-plan.md`. Touching anything frozen needs the user's explicit say-so.

## How to talk about this project

**The goal of anything you write here is that we end up understanding the same thing.** Not that the writing sounds rigorous. If a sentence would make the user think "he sounds like he knows what he's doing" without making him know what is going on, it has failed.

Default mode - discussing, judging, deciding, explaining what an agent did or why something went wrong:

- Say what actually happened, with a concrete example. "The agent searched for CAR-T, read three of the 4,252 projects that matched, and then wrote its description of the whole area" - not "the map entries are grounded in a biased sample".
- Walk through a problem once, in order, the way you would for someone smart who has not been staring at this code. Then say why it matters.
- Do not reach for a term of art to compress an idea. Spell the idea out. Words like append-only, predicate, quota, grounding, circular, systematic appear only when there is no shorter honest way to say it, not because they sound precise.
- Do not invent names. Use what the code already calls things (`verify-evidence`, the frontier, a slice, a seed). Otherwise describe it.
- No scoring tables or rubrics unless asked for one.

On request, go as deep as asked. "Give me the technical detail" means real detail - schemas, SQL, line numbers, exact numbers - not a simplified version. Plain language is about how things are explained, never about withholding substance.

Reference: the "plain version" answer about the cp4 explorer (2026-07-26) is the target. The three long, headed, jargon-dense answers before it are what to avoid.
