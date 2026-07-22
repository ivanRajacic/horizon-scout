"""Answer synthesis from retrieved chunks (vector and scoped modes only).

Grounding is enforced in code, not just asked for in the prompt:
- context budget is asserted before the call; lowest-scoring chunks are dropped
  if the prompt would exceed the model context, and the drop is recorded.
- every [ACRONYM, projectID] citation in the answer must correspond to a
  retrieved chunk; unknown citations are stripped and logged as
  citation_violation (M5's faithfulness signal).
"""

import re
from dataclasses import dataclass, field

from src.config import LLM_CTX
from src.llm import make_llm
from src.retrieval.vector_search import SearchResult

# Context arithmetic: reserve room for the system/instruction prompt and the
# generated answer, leaving the rest for chunks. ~4 chars/token heuristic.
ANSWER_TOKENS = 512
PROMPT_OVERHEAD_TOKENS = 400
CHUNK_BUDGET_TOKENS = LLM_CTX - ANSWER_TOKENS - PROMPT_OVERHEAD_TOKENS

# Bump on ANY edit; traces log "label:content-hash".
SYNTH_PROMPT_VERSION = "s1-pilot"

SYSTEM_PROMPT = """You answer questions about Horizon 2020 research projects \
using ONLY the excerpts provided below. Rules:
- Use ONLY the provided excerpts. Do not use any outside knowledge about these \
or any other projects.
- If the excerpts do not contain the answer, say so plainly and do not guess.
- After every substantive claim, cite its source as [ACRONYM, projectID]. Every \
substantive sentence needs at least one citation.
- If the excerpts only partially cover the question, say what is and is not \
covered."""

_CITATION_RE = re.compile(r"\[([^\],]+),\s*(\d+)\]")


def estimate_tokens(text: str) -> int:
    return len(text) // 4


@dataclass
class SynthesisResult:
    answer: str
    used_chunks: list[SearchResult]
    dropped_for_budget: int = 0
    citation_violations: list[str] = field(default_factory=list)
    trace: dict = field(default_factory=dict)


def format_chunks(chunks: list[SearchResult]) -> str:
    """Group by project, header 'ACRONYM - title (projectID)', section labelled."""
    by_project: dict[int, list[SearchResult]] = {}
    for c in chunks:
        by_project.setdefault(c.project_id, []).append(c)
    blocks = []
    for pid, items in by_project.items():
        acr = items[0].acronym or "?"
        title = items[0].title or ""
        lines = [f"### {acr} - {title} ({pid})"]
        for c in items:
            lines.append(f"[{c.section}] {' '.join(c.text.split())}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def fit_to_budget(chunks: list[SearchResult],
                  budget: int = CHUNK_BUDGET_TOKENS) -> tuple[list[SearchResult], int]:
    """Drop the worst chunks until the formatted context fits the budget.

    Retrievers return results BEST-FIRST and `score` is not comparable across
    retrievers (base.Retriever contract: dense = L2 distance, lexical/RRF/rerank
    = higher-is-better), so we trust the incoming ORDER and drop from the tail
    rather than re-sorting by a score whose sign is retriever-specific."""
    kept = list(chunks)  # already best-first by contract
    while kept and estimate_tokens(format_chunks(kept)) > budget:
        kept.pop()  # remove worst (tail)
    return kept, len(chunks) - len(kept)


class Synthesizer:
    def __init__(self, llm=None):
        # max_tokens applies to the local backend only; the claude backend
        # has no sampling controls (make_llm ignores it there).
        self.llm = llm or make_llm(max_tokens=ANSWER_TOKENS)

    def synthesize(self, question: str,
                   chunks: list[SearchResult]) -> SynthesisResult:
        if not chunks:
            return SynthesisResult(
                answer="No relevant project excerpts were retrieved, so this "
                       "question cannot be answered from the corpus.",
                used_chunks=[], trace={"reason": "no_chunks"})

        kept, dropped = fit_to_budget(chunks)
        context = format_chunks(kept)
        prompt_tokens = estimate_tokens(SYSTEM_PROMPT) + estimate_tokens(context) \
            + estimate_tokens(question)
        # Enforced, not hoped: after budgeting, the prompt MUST fit.
        assert prompt_tokens + ANSWER_TOKENS <= LLM_CTX, (
            f"prompt {prompt_tokens} + answer {ANSWER_TOKENS} exceeds context "
            f"{LLM_CTX} even after dropping {dropped} chunks")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content":
                f"Excerpts:\n\n{context}\n\nQuestion: {question}"},
        ]
        raw = self.llm.chat(messages)
        answer, violations = self._strip_unknown_citations(raw, kept)
        return SynthesisResult(
            answer=answer, used_chunks=kept, dropped_for_budget=dropped,
            citation_violations=violations,
            trace={"chunks_used": len(kept), "dropped_for_budget": dropped,
                   "prompt_tokens_est": prompt_tokens,
                   "citation_violations": violations})

    @staticmethod
    def _strip_unknown_citations(answer: str,
                                 chunks: list[SearchResult]) -> tuple[str, list]:
        """Remove [ACRONYM, id] citations whose id is not in the retrieved set.
        Matching is on projectID (the acronym is the model's to render)."""
        valid_ids = {c.project_id for c in chunks}
        violations = []

        def repl(m):
            pid = int(m.group(2))
            if pid in valid_ids:
                return m.group(0)
            violations.append(m.group(0))
            return ""

        cleaned = _CITATION_RE.sub(repl, answer)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
        return cleaned, violations
