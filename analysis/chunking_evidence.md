# Chunking decision - corpus evidence

Question: chunk the CORDIS report text structure-first (pack whole paragraphs to ~400 tokens, split per section) or fixed-size? External benchmarks favor recursive/structure splitting; this document tests the assumptions on our own corpus.

Reproduce with `python -m analysis.chunking_evidence` (seed 7, samples of 300 for the embedding analyses).

## 1. Paragraph anatomy (full corpus)

- 574,090 paragraphs across 34,712 reports x 3 sections.
- Tokens per paragraph: p25=24, p50=59, p75=126, p90=207, p99=453, max=1562.
- Only 0.59% of paragraphs exceed 512 tokens (would ever need sentence-splitting); 45% are under 50 tokens (greedy packing does real work vs one-paragraph-per-chunk).
- Sections that fit whole in 400 tokens: 38%; in 512: 55%.

**Verdict: paragraphs are small, clean packing units; the corpus is naturally shaped for structure-first packing.**

## 2. Chunker simulation (full corpus)

| strategy | chunks | mean tokens | boundaries not at a sentence end |
|---|---:|---:|---:|
| fixed-400 | 181,768 | 287 | 98.7% |
| fixed-400 + 50 overlap | 199,761 | 284 | 86.6% |
| structure-first-400 | 188,226 | 277 | 16.0% |

Structure-first boundaries always coincide with paragraph or sentence ends by construction; its non-zero figure above is paragraphs that end without punctuation (headings, list lines), not mid-sentence cuts.

- fixed-400 splits 13.7% of all paragraphs (78,686 of 574,090) across two chunks.
- Chunk counts are within ~4% of each other - structure-first costs nothing in index size.

**Verdict: fixed-size cuts mid-sentence at ~99% of boundaries and fragments 1 in 7 paragraphs; overlap does not fix this. Structure-first eliminates the damage for free.**

## 3. Are paragraph breaks semantic? (300-report sample, bge-small-en-v1.5)

Cosine similarity of *adjacent* sentence pairs, by the boundary between them:

| boundary between the two sentences | pairs | mean cos | median |
|---|---:|---:|---:|
| within-paragraph | 1,500 | 0.649 | 0.649 |
| across-paragraph | 1,500 | 0.631 | 0.629 |
| across-section | 583 | 0.596 | 0.590 |
| random-cross-doc | 1,500 | 0.484 | 0.483 |

**Verdict: monotone ordering (within-paragraph > across-paragraph > across-section >> random). Paragraph and section breaks in this corpus mark real topic shifts, so respecting them is signal, not aesthetics. The gap is modest, which is why the retrieval benchmark below is the deciding test.**

## 4. Retrieval micro-benchmark (300 projects, bge-small-en-v1.5)

Query = `project.objective` (pre-project text); a hit = retrieving a chunk of that project's own report (different text, same topic - free ground-truth labels). Rank over all chunks of all 300 projects.

| strategy | chunks | R@1 | R@5 | R@10 | MRR |
|---|---:|---:|---:|---:|---:|
| fixed-400, no header | 1,278 | 0.983 | 0.990 | 0.997 | 0.988 |
| structure-first-400, no header | 1,466 | 0.990 | 0.993 | 0.997 | 0.992 |
| structure-first-400 + title header | 1,466 | 0.993 | 0.997 | 0.997 | 0.994 |

**Verdict: with only 300 projects as the distractor pool the task runs near ceiling, so read the differences as directional, not decisive. The ordering is still consistent: structure-first beats fixed on every metric (5 vs 3 R@1 misses out of 300), and the title header helps further (2 misses). Nothing here contradicts the external evidence; combined with sections 1-3 the decision stands: structure-first-400 with title headers.**
