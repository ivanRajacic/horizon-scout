# Improvement research - what the literature says is worth doing (2026-07-27)

Five parallel web-research passes over recent (2024-2026) papers, leaderboards and engineering blogs, one per axis of the system: retrieval, text-to-SQL, routing/agentic orchestration, evaluation/LLM-judge, and chunking/indexing. Each claim below carries its measured number and source. The brief was big levers, not micro-tuning; everything judged not worth doing is listed too, with the evidence for skipping it.

**Reading note added 2026-08-06: the agentic round was cut** (`horizon-scout.md` §2), so **section 4 is dead as a plan** - it is the menu for a round that will not be run. It stays unedited as the record of what was researched. Nothing else in this file is affected. The research is findings, not a plan; it is never rewritten to match later decisions.

*Two more translation notes, added 2026-08-07: where the text says "three graphs" read two - the third was the cut agentic round. And where section 5 names Sonnet as the judge, the seat went to DeepSeek V4 Flash on 2026-08-04.*

How to read this against the rounds (`horizon-scout.md` §2 - the plan this was written against was `GOALS.md`, now folded in there): sections 1-3 are candidate changes for round two (improved), section 4 is the cut agentic round, section 5 makes the rounds more defensible, section 6 is what to skip, section 7 is what this research says about the write-ups.

One thing here is now decided elsewhere: retrieval is not measured, so section 3's newer-embedder-and-reranker candidates would change a frozen artifact and need an explicit decision before anyone acts on them.

---

## 1. Chunk representation: put the metadata in the text (best evidence-to-effort ratio)

**The change.** Prepend a deterministic header - title, acronym, euroSciVoc labels, programme, country, funding band - to every chunk before both the FAISS embedding and the FTS index. All of it already sits in DuckDB next to the text; nothing new is computed.

**The evidence.**

- A financial-QA study with exactly this data shape (rich structured metadata alongside embedded text) measured baseline F1 32.9 -> 38.9 with hybrid+rerank -> 44.1 by embedding metadata with the chunk text. That +5.2 F1 on top of hybrid+rerank was the single most consistent gain in their stack. Notably, metadata used as a hard pre-filter *hurt* recall (Claim Recall 50.7 -> 47.7) - enrichment beat filtering. https://arxiv.org/html/2510.24402v1
- Anthropic's contextual retrieval: prepending LLM-written chunk context cut top-20 retrieval failure 35% (embeddings alone), 49% (plus contextual BM25), 67% with reranking on top (5.7% -> 1.9%). Cost with prompt caching ~$1 per million document tokens; for this corpus roughly $30-60 one-time with Haiku. https://www.anthropic.com/engineering/contextual-retrieval
- An enterprise study of metadata-enriched embeddings: consistently beat content-only baselines, best nDCG 0.813 with prefix-fusion. https://arxiv.org/abs/2512.05411

**Why it fits here.** The failure contextual retrieval fixes is a chunk that has lost its document's identity ("the project will develop the platform" - which project? what field?). CORDIS objective-paragraph chunks have exactly that problem in mild form. Because the records are short and the useful context is structured, the deterministic header captures most of what an LLM would write; the Haiku-written variant is a cheap A/B on top, not a prerequisite.

**Related, worth measuring at the same time:**

- **Chunk-granularity ablation.** The index averages ~5.4 chunks per few-paragraph record. The short-document literature says standalone records this size often should not be chunked at all (https://www.pinecone.io/learn/chunking-strategies/); the chunk-size optimum is embedder-dependent, so it must be measured, not copied (https://arxiv.org/abs/2505.21700); fixed-size chunking matches or beats semantic chunking at far lower cost (https://arxiv.org/pdf/2504.19754). Nobody has published this exact ablation for ~1-2k-token records. Conditions: current chunker vs one-vector-per-section vs whole-record-with-header (chunk only when over the embedder's window). Cheap: `build-index` variants + `bench-retrievers`, no judge needed.
- **Doc2query-lite for the BM25 side only.** Appending LLM-predicted likely queries/keywords to the FTS document (not the dense index) is worth ~+15% lexical retrieval effectiveness in the doc2query line of work (MS MARCO Recall@1000 85.3 -> 89.3, https://arxiv.org/pdf/1904.08375; refinement https://arxiv.org/abs/2510.09557). Keep it out of the dense index - hallucinated-query noise is documented (Doc2Query--, https://arxiv.org/pdf/2301.03266). Guards against acronym/vocabulary mismatch in EU project prose.

---

## 2. The SQL route: sample candidates, show the model its errors

**The change with the biggest number: execution-consistency sampling.** Sample N=3-5 Haiku SQL candidates at temperature > 0, execute all (the SQL guard makes this safe), group candidates by result set, return the majority; prefer non-empty over empty on ties. Pure code plus a few extra cheap calls.

- "Query and Conquer": ~+10 points execution accuracy on BIRD for small models (+1.5-3.5 for frontier models), gains visible from 3 samples, ~15 the accuracy/cost sweet spot; lets a small model match o1-class at ~30x lower cost. The published fact that matters most: gains are largest for smaller models - largest exactly for a Haiku-class generator. https://arxiv.org/abs/2503.24364
- CHASE-SQL (76.02% BIRD test, ICLR 2025) confirms the shape: majority-vote consistency alone is worth several points; its trained selector adds +4.17 more but is the expensive, non-portable part. https://arxiv.org/abs/2410.01943

**One-round execution-feedback repair.** If `validate_sql` rejects, DuckDB errors (its messages suggest column names), or the result is empty/NULL-only: retry once with the error or result appended to the prompt.

- CHASE-SQL's query fixer: ~+2 points on every generator. https://arxiv.org/abs/2410.01943
- LitE-SQL: the first correction pass carries the gain; later passes diminish - 1-2 rounds is the sweet spot. https://arxiv.org/abs/2510.09014
- The negative result that shapes the design: self-correction *without* execution feedback ("re-read your SQL, is it right?") is worth only 1-3 points and can hurt. The value is showing the model the actual error or suspicious result.

**Schema doc enrichment, no schema linking.**

- M-Schema format (per column: type, description, PK marker, example values) beat plain DDL by +2.03% execution accuracy averaged over four models including Claude 3.5 Sonnet. https://arxiv.org/abs/2411.08599, https://github.com/XGenerationLab/M-Schema
- CHESS attributes ~+5 points to retrieving relevant database *values* into the prompt - so the model writes `WHERE country = 'EL'` not `'Greece'`. A tiny deterministic value grounder (exact/LIKE lookup of question literals against categorical columns, results pasted into the prompt) is that result in miniature. https://arxiv.org/abs/2405.16755
- "The Death of Schema Linking?" (71.83% BIRD without any schema filtering): when the whole schema fits in context, filtering columns *reduces* accuracy for strong models. One DuckDB database fits trivially - keep the schema whole, make it richer. https://arxiv.org/abs/2408.07702

Concretely for `src/retrieval/schema_docs.md`: add per column 3-5 real example values, distinct counts and format notes for categoricals (funding schemes, country codes, status values), explicit join keys and gotchas. This is a versioned prompt asset - bump `SCHEMA_DOCS_VERSION`.

**Few-shot from verified question-SQL pairs.** Similarity-retrieved few-shot (3-5 nearest pairs via the existing embedder) has large measured wins in single-domain settings: +32 points over fixed-shot on the ALeRCE astronomy database, the closest published analog to a single-scientific-DB setup (https://arxiv.org/abs/2606.18108); DAIL-SQL's ablations show joint question+SQL-skeleton similarity beats either alone (https://github.com/BeachWang/DAIL-SQL). The bank's verified SQL entries are exactly this asset - but never as few-shot when evaluating on that bank; that is leakage. Fine for production use or a held-out split.

**Model choice.** No clean published Haiku-vs-Sonnet text-to-SQL number exists. What is published: pipeline design moves BIRD scores 15-20 points on a fixed model, while frontier-model swaps within one pipeline move it a few points (XiYan-SQL ablations); execution-consistency lets 7B open models match o1-class (Query and Conquer). The regime distinction matters: Spider 2.0 (thousands of columns, multiple dialects) is where model reasoning dominates and even o1-class agents get ~23-36% (ReFoRCE, https://arxiv.org/abs/2502.00675) - this project is in the BIRD-like regime, not that one. Evidence-backed play: Haiku + sampling + execution feedback, then one cheap A/B against Sonnet with `run-bank` once those land.

---

## 3. Retrieval stack: newer embedder and reranker

**Embedder.** bge-base-en-v1.5 (2023, 110M, 768-dim, 512-token window) scores ~53.2 nDCG@10 on BEIR-style retrieval. Qwen3-Embedding scores 61.83 / 68.46 / 69.44 on MTEB Retrieval (English v2) at 0.6B / 4B / 8B (https://arxiv.org/html/2506.05176v1). The 0.6B is ~+8 points over bge-base, has official GGUFs (~600MB), and serves on llama-server with `--embedding` exactly like today; 32k context removes the 512-token truncation of long objectives. Credible alternates: snowflake-arctic-embed-l-v2.0 (568M, https://huggingface.co/Snowflake/snowflake-arctic-embed-l-v2.0), gte-multilingual-base (305M); field survey: https://www.bentoml.com/blog/a-guide-to-open-source-embedding-models. Caveat: leaderboard rank does not always transfer to a domain (one 2026 domain study found BGE-M3 beating Qwen3-Embedding on hit rate, https://arxiv.org/pdf/2605.22099) - verify on the bank before adopting. Cost: full re-embed of the 190k-vector index, roughly an evening.

**Reranker.** BEIR nDCG@10: bge-reranker-v2-m3 53.94; mxbai-rerank-base-v2 (0.5B, Apache 2.0) 55.57 at ~4.5x the speed (0.67s vs 3.05s, NFCorpus/A100); mxbai-rerank-large-v2 (1.5B) 57.49 (https://www.mixedbread.com/blog/mxbai-rerank-v2). Qwen3-Reranker-0.6B beats bge-v2-m3 by ~9 points on the Qwen paper's MTEB-R (65.80 vs 57.03) but is vendor-reported and causal-LM inference is slower per pair (https://arxiv.org/html/2506.05176v1; latency analysis https://zeroentropy.dev/articles/should-you-use-llms-for-reranking-a-deep-dive-into-pointwise-listwise-and-cross-encoders/). llama-server has a `--rerank` mode. Cross-encoder reranking itself stays: typically +2-5 nDCG on top of hybrid, and it compounds with contextual chunks (Anthropic's 49% -> 67% failure reduction).

**Embedder fine-tuning on the corpus (highest ceiling, most work).** ~+7 nDCG points repeatedly demonstrated from small-scale domain fine-tuning with synthetic queries: ~6.3k pairs sufficed in one recipe (https://www.philschmid.de/fine-tune-embedding-model-for-rag); +7.1-7.3 absolute nDCG@1 on enterprise docs (https://blogs.cisco.com/ai/fine-tuning-embedding-models-for-enterprise-retrieval-a-practical-guide-with-nvidia-nemotron-recipe); unsupervised automation with hard negatives (CustomIR, https://arxiv.org/html/2510.21729); also https://www.databricks.com/blog/improving-retrieval-and-rag-embedding-model-finetuning. Generate queries with Haiku over the 35k projects in a weekend. Do it after the model swap (fine-tune the better base), and hold out an eval so it does not overfit the bank.

**A fifth retrieval condition worth considering: late interaction.** answerai-colbert-small-v1 (33M params) beats bge-base across BEIR (~53.79 avg) at a third of its size (https://www.answer.ai/posts/2024-08-13-small-but-mighty-colbert.html); at 190k chunks the classic index-bloat and serving objections barely apply - the whole index fits in RAM (PLAID engine: https://arxiv.org/pdf/2205.09707; newer: https://arxiv.org/html/2510.14880v1). Caveats: PyTorch/ONNX path, not llama-server; and it competes with the cross-encoder rerank condition, which captures much of the same advantage - measure head-to-head. This is more a scientifically interesting condition than a production recommendation.

**Freeze-rule warning.** A new embedder or contextual chunks invalidates the frozen FAISS index and any bank questions whose gold sets were pooled from the current retrieval stack. Under the rules in CLAUDE.md / working-plan.md this needs explicit say-so and probably a new run generation, not an in-place edit.

---

## 4. The agentic phase: what to build for graph three

**Centerpiece: upgrade scoped to a TAG-style pipeline - the largest published delta in this entire document.** On Berkeley's TAG-Bench (BIRD databases modified so queries need semantic reasoning over text), text-to-SQL, RAG, and text-to-SQL+LM baselines all score <=20% exact match; hand-written pipelines that do SQL-enumerate -> LM reads survivors -> filter/rank/aggregate score ~55% overall, up to 65% on comparison queries, running up to 3.1x faster than LM-heavy baselines. https://arxiv.org/pdf/2408.14717, https://github.com/TAG-Research/TAG-Bench, summary https://venturebeat.com/data-infrastructure/table-augmented-generation-shows-promise-for-complex-dataset-querying-outperforms-text-to-sql

This is literally the scoped route with the LM moved from "synthesize over top-k vectors" to "read and judge the enumerated survivors" - and the bank's hybrid and compositional questions are built to detect exactly this gain. The LOTUS semantic-operator framework (sem_filter/sem_topk/sem_agg over dataframes, statistical accuracy guarantees, up to 3.6x faster than pipelined baselines) is the reference implementation shape; the compositional questions are literally sem_topk/sem_filter programs. https://arxiv.org/abs/2407.11418, https://www.vldb.org/pvldb/vol18/p4171-patel.pdf

**Second: CRAG-style evidence gate with route fallback.** After the routed path runs, a cheap check (retrieval-score threshold or one Haiku grading call) decides: answer / re-retrieve with a rewritten query / try the other route. CRAG measured +9.6 to +19.0 accuracy points on PopQA over vanilla RAG depending on generator (https://arxiv.org/abs/2401.15884; reproduction detail https://arxiv.org/pdf/2603.16169). It converts router errors from losses into a second chance, and yields a measurable "does fallback beat routing" cell. Nobody publishes a clean "run both routes and reconcile vs route" comparison - that cell is genuinely novel ground, not settled.

**Third: bounded iterative retrieval, max 2 rounds, vector route only.** The component-ablation paper ("Dissecting Agentic RAG", local 7B, multi-hop QA: full agentic EM 43.1 -> 53.2 over single-pass) found iteration depth the biggest lever but two iterations capture 95% of the gain of five; decomposition +1.4 EM at 2.2x latency; cross-encoder rerank +1.7 EM at negligible cost ("retain unconditionally"); and - counterintuitive - their adaptive routing lost to fixed hybrid RRF (53.2 vs 55.0). https://arxiv.org/html/2606.21553. Iterative retrieval's headline gains are on multi-hop QA (recall 61.5% -> 90.9%, PRISM https://arxiv.org/pdf/2510.14278) at a ~2.6x token multiplier (measured on BRIGHT, https://arxiv.org/pdf/2605.05538); FrugalRAG shows the first extra retrieval call carries almost all the gain and adaptive early-stopping halves cost with no accuracy loss (https://arxiv.org/abs/2507.07634). Given the pilot's biggest spend leak was effort amplification, cap loop depth at 2 by design.

**Fourth: a router bake-off as a study condition.** Cheap classifiers match LLM routers: TF-IDF+SVM hit 93.2% routing accuracy and recover 78-80% of a perfect router's savings, with lexical features beating MiniLM embeddings by 3.1 F1 (RAGRouter-Bench, https://arxiv.org/pdf/2604.03455); a WideMLP text classifier reaches 95.71% of the best LLM router at ~100x lower latency (https://arxiv.org/pdf/2505.14524); Adaptive-RAG's complexity classifier matches always-iterative accuracy at 1.03 retrieval steps instead of ~4 (https://www.alphaxiv.org/overview/2403.14403). The condition: LLM router vs a TF-IDF/logistic classifier trained on the bank vs oracle routing - oracle quantifies what routing errors actually cost. Caveat: keyword routers can systematically over-fire (one ablation's router sent 72% of queries to BM25 because named entities appear in nearly every question).

**Where agentic buys nothing vs a lot.** Nothing: single-hop factoid lookups (L1s) - iterative machinery adds cost, not accuracy, and extra rounds can hurt via noise. A lot: multi-hop composition, aggregation-over-text (TAG-Bench's <=20% -> 55-65%, the biggest gain anywhere here), and questions where retrieval quality is unreliable (CRAG's +10-19). Expected cost multiplier for agentic loops: ~2-3x tokens/latency.

**Anti-recommendations for this phase.** Self-RAG proper (needs a model fine-tuned to emit reflection tokens - wrong fit for a claude -p setup; the idea "grade your own evidence before answering" is prompt-implementable and is what the CRAG gate does). Always-on ensembles of full routes (cost multiplier without published accuracy evidence over routing+fallback).

---

## 5. Evaluation: making the three graphs defensible

**Claim-level scoring (RAGChecker-style), alongside or instead of RAGAS holistic scores.** RAGChecker (Amazon, NeurIPS 2024) decomposes answers into claims and entails them in both directions against gold and context, yielding overall metrics plus diagnostic ones that attribute error to the retriever (claim recall, context precision) or the generator (context utilization, noise sensitivity, hallucination, faithfulness). Meta-evaluation on 280 human-annotated instances: Pearson correlation with human overall assessment 61.9 vs RAGAS 48.3 vs TruLens 35.2. https://arxiv.org/abs/2408.08067

Three reasons this fits: (a) it dissolves the exact 0.75-threshold problem - paraphrase does not change which claims are supported, so correct-but-differently-phrased answers stop being penalized; (b) retrieval-vs-synthesis attribution is the routing study's actual question; (c) the execution-verified references already provide the ground-truth claims. Supporting evidence that RAGAS needs the help: a controlled biomedical study found RAGAS faithfulness at 0.893-0.902 across all retrieval strategies and 0.978 with no retrieval at all - failing to discriminate anything (https://arxiv.org/pdf/2605.02520).

**The statistics, from Anthropic's "Adding Error Bars to Evals" (Miller 2024).** https://arxiv.org/abs/2411.00640, https://www.anthropic.com/research/statistical-approach-to-model-evals

- Report SEM and 95% CI on every score; never binarize a continuous score unnecessarily (the move to continuous scores already made was the right one).
- Compare conditions with *paired* per-question deltas - free variance reduction. At n=50 unpaired, only ~20-point differences resolve at p<0.05; paired at typical between-condition correlation (rho ~= 0.7), ~11 points. The existing "under ~15 points is noise" rule is almost exactly what the math says.
- Cluster standard errors when questions share a source - questions drafted from the same corpus region/seed are clusters; naive SEs can understate uncertainty by up to 3x.
- State the minimum detectable effect alongside results ("this bank resolves differences of >= X points"); a 2026 formalization shows even public leaderboards fail this (11 of 40 Open LLM Leaderboard pairwise rankings unresolved, https://arxiv.org/html/2605.30315v1). Practical recipe (paired bootstrap, sign test): https://cameronrwolfe.substack.com/p/stats-llm-evals
- Subgroup claims (per-level, per-route, ~10-15 questions each) are descriptive, not tested.

**Validate the judge once against human labels.** Hand-label the judged answers of one run (~40-60 items), report Cohen's kappa and Spearman between Sonnet and the human labels, and place any threshold empirically from the labeled score distributions (https://arxiv.org/pdf/2412.12148) instead of a round number. The motivating result: a 21-judge, ~541k-judgment study found raw agreement overstates judge quality by 33-41 kappa points, and judges with test-retest reliability >0.95 still carried severe position bias - reproducible is not valid (https://arxiv.org/abs/2606.19544). References matter and are already in hand: reference-guided judging beats reference-free 79.1% vs ~71-72% accuracy (https://arxiv.org/pdf/2408.09235), raises inter-judge agreement 81.4% vs 76.6% (https://arxiv.org/pdf/2602.16802), and reference-free judges are systematically too generous (https://arxiv.org/html/2607.12885).

**If pairwise judging is ever added** (e.g. A/B between routing strategies): swap answer order, require a win in both orders, else tie (MT-Bench protocol, https://arxiv.org/abs/2306.05685). Pairwise preferences flip ~35% under perturbation vs ~9% for pointwise - pointwise-with-reference stays the right default for this bank. A small jury is the cheap reliability upgrade where holistic judging remains: a panel of three small judges from disjoint model families beat a single GPT-4 judge (kappa 0.763 vs 0.627) at ~1/7 the cost (PoLL, https://arxiv.org/abs/2404.18796).

---

## 6. What the evidence says to skip

- **HyDE and multi-query rewriting as default stages.** ARAGOG found multi-query did not significantly affect results (https://arxiv.org/pdf/2404.01037); a 2026 benchmark found HyDE and multi-query give limited benefit for precise queries (https://arxiv.org/pdf/2604.01733); HyDE adds a full LLM call of latency and its documented wins concentrate on multi-hop QA. Decomposition survives only as a routed special case for compositional/L3 questions (+2.5-3% answer accuracy, https://arxiv.org/pdf/2511.16283).
- **GraphRAG.** Systematic evaluation: wins only on multi-hop/temporal/comparison reasoning; loses or ties on single-hop detail QA; construction 41-57x slower than plain RAG indexing (https://arxiv.org/html/2502.11371v3). The relational structure it would extract (org-project-country-topic) is already in DuckDB, answered exactly and for free by the sql/scoped routes.
- **Late chunking.** Its own paper shows the gain correlates with document length and is zero on short texts (https://arxiv.org/abs/2409.04701) - these records are a few paragraphs.
- **Replacing RRF.** DBSF/relative-score fusion alternatives show single-digit dataset-dependent swings with no universal winner (https://qdrant.tech/documentation/search/hybrid-queries/, https://learn.microsoft.com/en-us/azure/search/hybrid-search-ranking, https://opensearch.org/blog/introducing-reciprocal-rank-fusion-hybrid-search/). RRF needs no tuning and is robust to incomparable score scales. At most, tune k as a cheap eval cell.
- **LLM listwise reranking as a production path.** RankZephyr/FIRST match GPT-4-level listwise quality open-source (https://arxiv.org/pdf/2312.02724, https://arxiv.org/pdf/2406.15657), but an EMNLP 2025 analysis finds LLM rerankers often do not beat good cross-encoders out of distribution at 10-100x the cost (https://aclanthology.org/2025.findings-emnlp.305.pdf). A Haiku listwise rerank is a cheap experiment cell at most.
- **Self-RAG proper and always-on route ensembles.** See section 4.
- **Schema linking / column filtering for SQL.** Actively harmful when the schema fits in context (https://arxiv.org/abs/2408.07702).

---

## 7. What this means for the write-ups

**Write-up 1 (system + improvement arc).** The literature hands graph two a clean named-change list where each change has a published expected effect to compare against: contextual/metadata chunks (+5 F1 class), SQL execution-consistency (+10 class for small models), SQL repair round (+2-5), schema-doc values (+2-5), embedder swap (+8 nDCG class), reranker swap (+2-4 nDCG class). Where the measured effect lands vs the published one is itself content.

**Write-up 2 (explorer + drafter pipeline).** The comparison class is YourBench (https://arxiv.org/abs/2504.01833), DataMorgana (https://arxiv.org/pdf/2501.12789), and ARES's synthetic-judge-training idea (https://arxiv.org/abs/2311.09476). None of them have deterministic re-execution gates or split drafter/critic/judge authority - YourBench's citation scoring is itself model-judged, not executed. That is the honest differentiator to claim. The one idea worth adopting from them: report diversity metrics of the finished bank (route/level/bucket coverage plus lexical/semantic diversity, DataMorgana-style). ARES's prediction-powered inference (few human labels + many judge labels -> calibrated confidence intervals) is the published mechanism if judge calibration ever needs to be formal.

**One honesty rule the eval literature reinforces** (already in `horizon-scout.md` §2): several proposed changes were found by staring at pilot failures; the write-up says so, and the paired statistics in section 5 are what keep the before/after graphs from quietly becoming circular.

---

## Appendix: full source list by area

**Retrieval / embedders / rerankers**

- Qwen3 Embedding paper - https://arxiv.org/html/2506.05176v1
- Mixedbread mxbai-rerank-v2 - https://www.mixedbread.com/blog/mxbai-rerank-v2
- Anthropic contextual retrieval - https://www.anthropic.com/engineering/contextual-retrieval
- snowflake-arctic-embed-l-v2.0 - https://huggingface.co/Snowflake/snowflake-arctic-embed-l-v2.0 (paper https://arxiv.org/abs/2412.04506)
- BentoML open-source embedding guide - https://www.bentoml.com/blog/a-guide-to-open-source-embedding-models
- OpenSearch RRF - https://opensearch.org/blog/introducing-reciprocal-rank-fusion-hybrid-search/
- Azure hybrid ranking - https://learn.microsoft.com/en-us/azure/search/hybrid-search-ranking
- DAPFAM patent benchmark - https://arxiv.org/pdf/2506.22141
- Qdrant hybrid queries - https://qdrant.tech/documentation/search/hybrid-queries/
- LlamaIndex fusion comparison - https://docs.llamaindex.ai/en/stable/examples/retrievers/relative_score_dist_fusion/
- ARAGOG RAG-technique evaluation - https://arxiv.org/pdf/2404.01037
- RAG linguistic-variation fragility - https://arxiv.org/pdf/2504.08231
- BM25-to-corrective-RAG benchmark - https://arxiv.org/pdf/2604.01733
- Query-expansion survey - https://arxiv.org/pdf/2509.07794
- MuISQA multi-intent decomposition - https://arxiv.org/pdf/2511.16283
- RankZephyr - https://arxiv.org/pdf/2312.02724
- FIRST - https://arxiv.org/pdf/2406.15657
- EMNLP 2025 LLM-reranker analysis - https://aclanthology.org/2025.findings-emnlp.305.pdf
- ZeroEntropy reranker deep dive - https://zeroentropy.dev/articles/should-you-use-llms-for-reranking-a-deep-dive-into-pointwise-listwise-and-cross-encoders/
- Philschmid embedding fine-tuning recipe - https://www.philschmid.de/fine-tune-embedding-model-for-rag
- Cisco/NVIDIA enterprise fine-tuning - https://blogs.cisco.com/ai/fine-tuning-embedding-models-for-enterprise-retrieval-a-practical-guide-with-nvidia-nemotron-recipe
- CustomIR unsupervised fine-tuning - https://arxiv.org/html/2510.21729
- Databricks embedding fine-tuning - https://www.databricks.com/blog/improving-retrieval-and-rag-embedding-model-finetuning
- Khmer domain-transfer study - https://arxiv.org/pdf/2605.22099

**Text-to-SQL**

- BIRD leaderboard - https://bird-bench.github.io/
- CHASE-SQL - https://arxiv.org/abs/2410.01943
- XiYan-SQL - https://arxiv.org/abs/2411.08599
- M-Schema - https://github.com/XGenerationLab/M-Schema
- CHESS - https://arxiv.org/abs/2405.16755
- The Death of Schema Linking? - https://arxiv.org/abs/2408.07702
- Query and Conquer (execution-consistency) - https://arxiv.org/abs/2503.24364
- LitE-SQL - https://arxiv.org/abs/2510.09014
- RetrySQL - https://arxiv.org/abs/2507.02529
- DAIL-SQL - https://github.com/BeachWang/DAIL-SQL
- OpenSearch-SQL - https://arxiv.org/abs/2502.14913
- ALeRCE astronomy text-to-SQL - https://arxiv.org/abs/2606.18108
- ReFoRCE (Spider 2.0 SOTA-tier) - https://arxiv.org/abs/2502.00675
- Spider 2.0 - https://spider2-sql.github.io/ , https://github.com/xlang-ai/Spider2
- DPC candidate selection - https://arxiv.org/abs/2604.15163
- Haiku 4.5 cost/capability analysis - https://www.caylent.com/blog/claude-haiku-4-5-deep-dive-cost-capabilities-and-the-multi-agent-opportunity

**Routing / agentic**

- RAGRouter-Bench - https://arxiv.org/pdf/2604.03455
- Guarded Query Routing - https://arxiv.org/pdf/2505.14524
- Adaptive-RAG - https://www.alphaxiv.org/overview/2403.14403
- EllieSQL complexity routing - https://arxiv.org/pdf/2503.22402
- RAGRouter - https://arxiv.org/pdf/2505.23052
- Dissecting Agentic RAG (component ablation) - https://arxiv.org/html/2606.21553
- Self-RAG - https://selfrag.github.io/
- CRAG - https://arxiv.org/abs/2401.15884 (reproduction https://arxiv.org/pdf/2603.16169)
- IRCoT - https://www.emergentmind.com/papers/2212.10509
- PRISM iterative retrieval - https://arxiv.org/pdf/2510.14278
- Agentic token-cost measurement on BRIGHT - https://arxiv.org/pdf/2605.05538
- FrugalRAG - https://arxiv.org/abs/2507.07634
- TAG paper - https://arxiv.org/pdf/2408.14717
- TAG-Bench - https://github.com/TAG-Research/TAG-Bench
- TAG press summary - https://venturebeat.com/data-infrastructure/table-augmented-generation-shows-promise-for-complex-dataset-querying-outperforms-text-to-sql
- LOTUS semantic operators - https://arxiv.org/abs/2407.11418 (VLDB https://www.vldb.org/pvldb/vol18/p4171-patel.pdf)
- Learning to Route (SQL vs text sources) - https://arxiv.org/pdf/2510.02388
- mmRAG routing benchmark - https://arxiv.org/pdf/2505.11180
- Milvus routing/hybrid blog - https://milvus.io/blog/build-smarter-rag-routing-hybrid-retrieval.md
- CQC-RAG cross-query consistency - https://arxiv.org/pdf/2606.13438
- vLLM Semantic Router - https://blog.vllm.ai/2025/09/11/semantic-router.html

**Evaluation / LLM-as-judge**

- MT-Bench / LLM-as-judge - https://arxiv.org/abs/2306.05685
- Reliability-without-validity (kappa deflation) - https://arxiv.org/abs/2606.19544
- Agreement metrics for LLM-as-judge - https://arxiv.org/html/2606.00093
- PoLL judge juries - https://arxiv.org/abs/2404.18796
- FActScore - https://arxiv.org/abs/2305.14251
- RAGChecker - https://arxiv.org/abs/2408.08067 (NeurIPS PDF https://proceedings.neurips.cc/paper_files/paper/2024/file/27245589131d17368cccdfa990cbf16e-Paper-Datasets_and_Benchmarks_Track.pdf)
- ARES - https://arxiv.org/abs/2311.09476 (https://github.com/stanford-futuredata/ARES)
- Biomedical RAGAS-faithfulness failure - https://arxiv.org/pdf/2605.02520
- RAGAS vs DeepEval prompt sensitivity - https://medium.com/@sjha979/ragas-vs-deepeval-measuring-faithfulness-and-response-relevancy-in-rag-evaluation-2b3a9984bc77
- GroUSE judge unit tests - https://arxiv.org/pdf/2409.06595
- Adding Error Bars to Evals (Miller 2024) - https://arxiv.org/abs/2411.00640 (https://www.anthropic.com/research/statistical-approach-to-model-evals)
- Resolution diagnostics for paired evaluation - https://arxiv.org/html/2605.30315v1
- Stats for LLM evals walkthrough - https://cameronrwolfe.substack.com/p/stats-llm-evals
- YourBench - https://arxiv.org/abs/2504.01833 (https://github.com/huggingface/yourbench)
- DataMorgana - https://arxiv.org/pdf/2501.12789 (ACL https://aclanthology.org/2025.acl-industry.33/ , LiveRAG https://arxiv.org/pdf/2507.04942)
- ARMOR cross-model generation - https://arxiv.org/pdf/2605.00245
- Reference-guided judging (RefEval) - https://arxiv.org/pdf/2408.09235
- References raise inter-judge agreement - https://arxiv.org/pdf/2602.16802
- Reference-free judges too generous - https://arxiv.org/html/2607.12885
- LLMs-as-judges survey (scalar pathologies) - https://arxiv.org/pdf/2412.05579
- Empirical threshold selection - https://arxiv.org/pdf/2412.12148
- Bias-mitigation comparison - https://arxiv.org/pdf/2604.23178

**Chunking / indexing / representation**

- Anthropic contextual retrieval - https://www.anthropic.com/engineering/contextual-retrieval
- Late Chunking - https://arxiv.org/abs/2409.04701 (https://jina.ai/news/late-chunking-in-long-context-embedding-models/)
- Rethinking Chunk Size - https://arxiv.org/abs/2505.21700
- Chunking-strategies evaluation - https://arxiv.org/pdf/2504.19754
- Pinecone chunking guide - https://www.pinecone.io/learn/chunking-strategies/
- Metadata-driven financial RAG - https://arxiv.org/html/2510.24402v1
- Enterprise LLM-metadata RAG - https://arxiv.org/abs/2512.05411
- Doc2query - https://arxiv.org/pdf/1904.08375
- Doc2Query++ - https://arxiv.org/abs/2510.09557
- Doc2Query-- (hallucinated-query noise) - https://arxiv.org/pdf/2301.03266
- RAG vs GraphRAG systematic evaluation - https://arxiv.org/html/2502.11371v3
- GraphRAG-Bench - https://github.com/GraphRAG-Bench/GraphRAG-Benchmark
- FalkorDB GraphRAG benchmark - https://www.falkordb.com/blog/graphrag-accuracy-diffbot-falkordb/
- answerai-colbert-small - https://www.answer.ai/posts/2024-08-13-small-but-mighty-colbert.html
- PLAID - https://arxiv.org/pdf/2205.09707
- mxbai-edge-colbert - https://arxiv.org/html/2510.14880v1
