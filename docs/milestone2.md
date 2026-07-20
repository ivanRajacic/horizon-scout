# Milestone 2 - chunk, embed, index, vector search

Status: **complete on the dev index** (limit 2000). The full ~223k-chunk build
is **deferred to pre-milestone-5**. The dev index is the working index for
milestones 2-4.

## Scope decision (deferred full build)
The `--limit 2000` dev index is the operational index for milestones 2-4. The
one-time full build (~28 min GPU) runs just before milestone 5, when the
chunk-size sweep (300/400/500) needs it anyway. Do not run the full build for
routine 2-4 work.

The dev subset is **deterministic**: `build_chunk_table` selects
`... ORDER BY p.id LIMIT N`, the first N projects by ascending id. `report_text`
is 1:1 with `project` (34,712 rows, 34,712 distinct projectID), so the LEFT JOIN
never multiplies rows or duplicates an objective. Re-chunking reproduces the
chunk table byte-for-byte (verified: identical `(chunk_id, n_tokens, text)`).

## Commands
```
# build (dev): chunk -> DuckDB chunk table -> embed via llama-server -> FAISS
python -m src.cli build-index --limit 2000

# full build (only pre-milestone-5)
python -m src.cli build-index

# search
python -m src.cli search "query" -k 10 [--source report|objective] \
    [--project-ids ID ...] [--dedup-projects]

# smoke eval (hit/miss@10 over eval/smoke_vector.jsonl; exits non-zero if <8/10)
python -m src.cli smoke
```

The embedding server must be up first (fail-fast otherwise):
```
C:\llama\llama-server.exe -m data\models\bge-base-en-v1.5-f16.gguf \
    --embedding --pooling cls -ngl 99 --port 8080 --cache-ram 0
```
`--pooling cls` and `--cache-ram 0` are load-bearing (see
`analysis/gpu_validation/REPORT.md`).

## Artifacts
- `data/processed/faiss_index/` - FAISS flat/IP index (LangChain vectorstore).
- `data/processed/index_meta.json` - model, dim, GGUF sha256, llama build,
  chunk counts by source, chunk policy string, build timing. The searcher
  refuses to load on any mismatch with the configured model/GGUF.
- DuckDB table `chunk(chunk_id, project_id, source, section, n_tokens, text)` -
  CLEAN text only, the durable source of truth. Header prefixes never stored.

## Resume model
Chunks go to the DuckDB `chunk` table first (CREATE OR REPLACE), then embed in
5,000-chunk checkpoints into `faiss_index.tmp/`. A killed run resumes: the
reloaded index's own `ntotal` is the authoritative resume point (never the
progress counter), so a crash between the index save and the progress write
cannot cause a batch to be re-added. Any parameter change invalidates the
fingerprint and restarts the build cleanly. On success the tmp dir is renamed
into place and the progress file removed.

## Acceptance criteria - all passing (dev index)
| criterion | result |
|---|---|
| rerun reproduces identical chunk counts | 11,684 identical, byte-for-byte |
| zero chunks over 512 tokens | 0 (max n_tokens = 504) |
| meta mismatch -> hard refusal to load | IndexMetaError raised |
| smoke hit@10 on eval/smoke_vector.jsonl (>=8/10) | 10/10 |
| project_ids filter restricts | only allowed pids returned; under-fill warns |
| source filter returns only that source | objective-only / report-only verified |
| DuckDB chunk text has no header prefixes | 0 prefixed rows |
| kill embed mid-run, rerun completes, no duplicate chunks | resumed 5000->11,684, 0 dups |

Chunk counts (dev): 9,677 report + 2,007 objective = 11,684 vectors.
