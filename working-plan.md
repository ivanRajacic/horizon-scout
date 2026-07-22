# Working plan - M5 execution

*Derived from horizon-scout.md (M5 eval plan v4). Last updated 2026-07-22.*

**v4 decisions (2026-07-22):** generator = Claude Haiku via `claude -p` (replaces local Qwen3-8B; llama-servers remain for embedder + reranker only); judge = Sonnet, fixed and unvalidated (results are "Sonnet-judged pass rates"); Opus authors questions and references. RQ3 and RQ5 scratched (tombstones in horizon-scout.md); H1 and H4b revised for a capable generator; manual review / hand-grading dropped. Code done (2026-07-22): shared `claude -p` transport extracted to `src/claude_cli.py` (one transport function + ONE process-wide semaphore, cap 16, gating generation AND judging); `ClaudeClient` generation client in `src/llm.py` with the same `.chat()` contract as the local `LlmClient`; `make_llm()` factory switched by `GEN_BACKEND` in config ("claude" default = Haiku, "local" = legacy Qwen for RQ3 revival); router/SQL/synthesis/ask default to the factory; `JUDGE_DEFAULT` = sonnet; CLI checks the configured backend. 147 tests passing incl. a 32-thread gating test.

## Where things stand

M1-M4 are done and committed:

- DuckDB ingest of the full CORDIS corpus (35,389 projects, verified against the codebook)
- Chunker + dev-slice index (2,000 projects, 11,684 vectors)
- Four-condition retrieval stack (FTS / dense / RRF / rerank) with bake-off harness and ranking metrics
- SQL path with guardrails, router, synthesizer, end-to-end `ask.py` with trace logging
- Reranker GGUF already downloaded (so §5A's "check the reranker runs before designing around it" is a quick verification, not a risk)

## Step 1 - finish the pilot (d1-2)

### Done

1. **Bank schema** (`src/eval/bank.py`, `validate-bank` CLI command). Every §1 field: `question_id`, `text`, `expected_route` (doc vocabulary `sql|vector|hybrid|ambiguous`; the single mapping to the runtime's `scoped` mode lives in `ROUTE_TO_MODE`), `complexity`, `specification`, `term_style`, `compositional`, `adversarial`, `gold_sql` + `sql_comparison` (set/ordered, BIRD's choice), `gold_project_ids`, `reference_answer`. Validation is loud and complete: every violation in the file is reported, not just the first. All 32 unique smoke questions migrated into `eval/bank_pilot.jsonl` (12 SQL with verified gold queries, 15 vector, 2 hybrid, 3 ambiguous), provisional labels marked in notes.
2. **Trace completion.** Every prompt (router / synth / SQL / id-narrowing) carries a version label plus a content hash, so a silent edit without a version bump is still visible in traces; the SQL fingerprint covers `schema_docs.md`. Every `ask.jsonl` entry logs a `versions` block (LLM model, embed model, all prompt versions) and first-class `rows_passed_to_gen` / `chunks_passed_to_gen` fields (RQ1's covariate). `sql_path.jsonl` logs model + prompt version per attempt.
3. **Judge path - RAGAS + refusal overlay** (decision changed from the original bespoke-rubric-only design, 2026-07-22):
   - Metrics: RAGAS 0.4.3 (version-pinned) `factual_correctness` (claim-level F1 vs reference) + `faithfulness` (claims vs retrieved contexts, skipped when no contexts exist, e.g. SQL-route answers). Pass rule in code: factual >= 0.75 AND faithfulness >= 0.80 when measurable - pilot draft thresholds in config, calibrated against hand grades and frozen at d10.
   - Backend: `ClaudeCliLLM` (`src/judge/ragas_backend.py`) implements RAGAS's `BaseRagasLLM` over the one-function `claude -p` transport (Max subscription, ~EUR 0 marginal; API swap = one function). Configurable concurrency, default 8, hard cap 16, one shared semaphore across ALL judging paths. Backoff retries on transient failures only.
   - Refusal overlay: adversarial questions (zero-match / false-presupposition / data-absent / unanswerable) bypass RAGAS - claim-decomposition metrics are structurally blind to correct refusals (H5c's cells) - and go to the rubric judge (`src/judge/judge.py`): pass iff the answer states nothing matches and invents nothing. Dispatch is by the bank's `adversarial` flag, pre-registered, not a grading-time judgment call.
   - NLI instruction amendment `n1-pilot`: stock RAGAS scored a verbatim-correct answer at F1=0 because "in the database" framing counted as an invented fact. One leniency paragraph appended to both metrics' NLI instructions (semantic support over verbatim wording), versioned + content-hashed in every logged verdict. Write-up must disclose: results are "RAGAS 0.4.3 with a one-paragraph NLI amendment", not stock.
   - Verified live: 4/4 smoke cases (`eval/judge_smoke.jsonl`, references computed from the DB) via `judge-file` CLI; both H5c trap cells handled. All verdicts logged to `data/logs/judge.jsonl` with model, versions, thresholds, scores.
   - Tests: 113 passing, judge paths covered with faked transport/metrics.
4. **Question-drafting infrastructure** (2026-07-22). Read-only MCP server (`src/eval/mcp_server.py`, registered as `horizon-draft` in `.mcp.json`): `run_sql` (SELECT-only guard + read-only connection, capped rows, true pre-cap row_count, SQL errors returned as results so trap authoring can reason about them), `get_schema_docs` (verbatim + version `sd1-pilot` + content hash), `get_bank_questions` (id/text/level/subtype only). Every call traced to `data/logs/draft_mcp.jsonl`; 14 smoke tests (127 total passing). First drafting skill `/draft-sql-question` (`.claude/skills/draft-sql-question/SKILL.md`): ground in observed data, gold SQL executed in-pass, level computed from level_evidence, mandatory reviewer checklist, append only on explicit user confirmation. Decisions locked: levels L1/L2/L3 with required `subtype`; `rank` legal at every level; `sql_comparison=ordered` iff `subtype=rank`; L2 evidence = join OR schema_docs value note OR GROUP BY without ranking; field name stays `expected_route`.

5. **Bank schema v2 + fresh bank file** (2026-07-22). `bank.py`/`validate-bank` rewritten to the skill's entry shape: `level` (L1|L2|L3|ADV, ADV off-ladder) replaces `complexity`; required route-scoped `subtype` (rank legal at every SQL level, others level-bound; ambiguous route carries none - vocabulary still undefined); ordered-iff-rank enforced both directions; SQL ladder entries must carry `answer_columns`, `level_evidence`, `schema_docs_hash`; `reviewer_override` recorded. Decision: fresh bank at `eval/bank.jsonl` (starts empty, skill-authored only); the 32-question pre-skill smoke set archived verbatim to `eval/archive/bank_pilot.jsonl` in the old schema, no longer validated. The full drafting loop (skill -> MCP -> append -> validate-bank) is now unblocked.

### Remaining

6. **Transfer questions from the old set.** The 32 archived entries are the old set, not the bank. Any that survive are re-authored one at a time through the drafting skills (execution-verified, checklist, confirm) - never hand-migrated or bulk-copied.
7. Author the rest of the ~12-15 pilot questions on the dev slice via the drafting skills (vector/hybrid/ADV skills still to be written), including the SQL smoke test (2-3 questions, failures coded against BIRD's taxonomy - decides whether L3-SQL is a live cell or a pre-registered zero-cell).
8. Run every path end-to-end on the Haiku generator (needs the embed + reranker llama servers up; Qwen server no longer required); wire real pipeline outputs into the judge (contexts = the chunks synthesis used, from `ask.jsonl`); add the deliberately-broken question; record the smoke-test verdict; then **freeze and version the router prompt**.

Nothing from the pilot counts as a result.

## Step 2 - d3: corpus exploration + category spec

Query-verified "what's in this database" doc (topic spread, funding percentiles, distributions, genuine absences, fields present in both columns and text). Doubles as the value-description source for Study 0.5's SQL prompt fix, so it is on the critical path twice. Then the category spec.

**Decision (2026-07-22, design pending):** this is to be done by a dedicated exploration AGENT over the `horizon-draft` MCP tools (`run_sql`, traced), producing a versioned `corpus_profile.md` (sibling to schema_docs) organized by what each bank category needs: value distributions + near-miss trap pairs (SQL), topic clusters via the euroscivoc taxonomy with sizes mapped to L1/L2/L3 (vector), topic x filter survivor counts (hybrid), query-verified genuine absences (adversarial). Ivan is still working out the exact shape - do NOT run it yet. Optional dessert, decided after the profile exists: 2D embedding projection (UMAP) of the 190k vectors to find structure euroscivoc misses.

## Step 3 - d4-6: bank v1.0

~100 questions per the allocation table (SQL 22 / vector 25 / hybrid 24 / ambiguous 10 / adversarial 12 / compositional 3), generated per §2's pipeline, human-verified on a random 20% + all flagged. **Freeze the bank at d6.** Also freeze chunking + index config and make the metadata-in-chunk decision consciously before the full-corpus index build (the current dev index is 11.7k vectors; the full build must exist before d8).

## Step 4 - d7: Study 0.5 (SQL gate)

~30 gold-SQL questions, execution accuracy, then the single pre-registered fix (value descriptions in the SQL prompt), before/after on the same 30, freeze the SQL prompt. **Commit `predictions.md` by end of d7** - it must land before Study 1 runs, hash cited in the write-up.

## Step 5 - d8: Study 1 (RQ2)

Retrieval ladder on the full index, topical subset, `term_style` crossover table. Freeze the winning stack - the single coupling point for everything after.

## Step 6 - d9-10: references (v4 - judge selection scratched with RQ5)

Opus writes references from gold evidence only, via the drafting/reference skills (SQL references = executed gold results, free). Judge is fixed: Sonnet as the RAGAS backend, frozen prompt + thresholds + NLI amendment + overlay rubric before Study 2 - no calibration study, no hand-graded set. Results are reported as Sonnet-judged pass rates.

## Step 7 - d11-14: Study 2 + tail

Study 2 (RQ1) on the full bank, results assembly + surprising-verdict pass (skim odd judge verdicts while reading results - a habit, not a protocol), agentic condition (RQ4, H4a/H4b), analysis buffer + failure-analysis pass. Drop-order under pressure is pre-decided in §3: compositional cells always run, then agentic H4b.

## Immediate next action

Step 1 remaining items, in order: transfer surviving old-set questions through the drafting skills (item 6), author the rest of the pilot set (item 7), then the end-to-end pilot runs and router-prompt freeze (item 8). **Full-corpus index: BUILT** (2026-07-22, 190,248 vectors, limit=null; FTS verified in sync) - vector/hybrid authoring is unblocked and grounds against the full corpus.
