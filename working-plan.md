# Working plan - M5 execution

*Derived from horizon-scout.md (M5 eval plan v3). Last updated 2026-07-22.*

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

### Remaining

4. Author the ~12-15 pilot questions on the dev slice, including the SQL smoke test (2-3 questions, failures coded against BIRD's taxonomy - decides whether L3-SQL is a live cell or a pre-registered zero-cell).
5. Run every path end-to-end (needs the three llama servers up); wire real pipeline outputs into the judge (contexts = the chunks synthesis used, from `ask.jsonl`); add the deliberately-broken question; record the smoke-test verdict; then **freeze and version the router prompt**.

Nothing from the pilot counts as a result.

## Step 2 - d3: corpus exploration + category spec

Query-verified "what's in this database" doc (topic spread, funding percentiles, distributions, genuine absences, fields present in both columns and text). Doubles as the value-description source for Study 0.5's SQL prompt fix, so it is on the critical path twice. Then the category spec.

## Step 3 - d4-6: bank v1.0

~100 questions per the allocation table (SQL 22 / vector 25 / hybrid 24 / ambiguous 10 / adversarial 12 / compositional 3), generated per §2's pipeline, human-verified on a random 20% + all flagged. **Freeze the bank at d6.** Also freeze chunking + index config and make the metadata-in-chunk decision consciously before the full-corpus index build (the current dev index is 11.7k vectors; the full build must exist before d8).

## Step 4 - d7: Study 0.5 (SQL gate)

~30 gold-SQL questions, execution accuracy, then the single pre-registered fix (value descriptions in the SQL prompt), before/after on the same 30, freeze the SQL prompt. **Commit `predictions.md` by end of d7** - it must land before Study 1 runs, hash cited in the write-up.

## Step 5 - d8: Study 1 (RQ2)

Retrieval ladder on the full index, topical subset, `term_style` crossover table. Freeze the winning stack - the single coupling point for everything after.

## Step 6 - d9-10: references + judge selection (RQ5 setup)

Opus writes references from gold evidence only; human-verify; Haiku and Sonnet as RAGAS backends over the hand-graded set; agreement + directional-bias tables, reported per judging path (ragas vs overlay). Calibrate pass thresholds against hand grades; freeze the judged metric (thresholds + NLI amendment + overlay rubric) before Study 2.

## Step 7 - d11-14: Study 2 + tail

Study 2 (RQ1) on the full bank, hand-grading + RQ5 tables, RQ3 subset, weak-agentic (H4a/H4b) if alive, analysis buffer + failure-analysis pass. Drop-order under pressure is pre-decided in §3: compositional cells always run, then weak-agentic H4b, then RQ3; RQ5 is never dropped.

## Immediate next action

Step 1 remaining items: author the pilot question set (item 4), then the end-to-end pilot runs and router-prompt freeze (item 5).
