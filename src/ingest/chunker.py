"""Structure-first chunker (milestone 2 port of analysis/chunking_evidence).

Packs whole paragraphs per section to ~chunk_target tokens; never packs
across section boundaries; no overlap, except a sentence split with
~split_overlap tokens of overlap inside the rare paragraph that alone
exceeds the cap. Token counting uses the real bge tokenizer (the old
char-approximation emitted a 616-token chunk that llama-server rejects).

The 512 cap is enforced on the FULL string the server will see at embed
time - "ACRONYM - title | section\\n" + chunk text, including special
tokens - because that is what the 512 physical-batch limit applies to.
page_content stays CLEAN (no header); metadata.n_tokens records the
embedded-string count, asserted <= cap before any Document leaves here.
"""

import re

from langchain_core.documents import Document

from src.config import CHUNK_CAP, CHUNK_TARGET, SPLIT_OVERLAP

# margin between the approximate per-part token sums used while packing and
# the exact recount of the joined string (re-tokenization drift is tiny but
# can go either way when slicing inside words)
SAFETY = 8

SENT_END = re.compile(r'[.!?]["\')\]]?\s')


def sentences(t: str) -> list[str]:
    out, start = [], 0
    for m in SENT_END.finditer(t):
        out.append(t[start:m.end()])
        start = m.end()
    if start < len(t):
        out.append(t[start:])
    return out


def paragraphs(t: str) -> list[str]:
    return [p.strip() for p in t.split("\n") if p.strip()]


def make_header(acronym: str | None, title: str | None, section: str) -> str:
    return f"{acronym or ''} - {title or ''} | {section}"


def embedded_text(header: str, chunk_text: str) -> str:
    """The string actually sent to the embedder. Never stored or displayed."""
    return f"{header}\n{chunk_text}"


class Chunker:
    def __init__(self, tokenizer, chunk_target: int = CHUNK_TARGET,
                 cap: int = CHUNK_CAP, split_overlap: int = SPLIT_OVERLAP):
        self.tok = tokenizer
        self.chunk_target = chunk_target
        self.cap = cap
        self.split_overlap = split_overlap

    # ------------------------------------------------------------- counting
    def _count(self, text: str) -> int:
        """Token count without special tokens (a packable part)."""
        return len(self.tok.encode(text, add_special_tokens=False).ids)

    def n_tokens(self, header: str, chunk_text: str) -> int:
        """Exact token count of the embedded string, special tokens included."""
        return len(self.tok.encode(embedded_text(header, chunk_text)).ids)

    # ------------------------------------------------------------- splitting
    def _hard_split(self, text: str, budget: int) -> list[str]:
        """Token-window split for a single sentence over budget (no
        punctuation to split at). Slices the ORIGINAL string via token
        offsets so no text is rewritten."""
        enc = self.tok.encode(text, add_special_tokens=False)
        pieces, start = [], 0
        step = max(budget - self.split_overlap, 1)
        while start < len(enc.ids):
            end = min(start + budget, len(enc.ids))
            pieces.append(text[enc.offsets[start][0]:enc.offsets[end - 1][1]])
            if end == len(enc.ids):
                break
            start += step
        return pieces

    def _split_oversized(self, para: str, budget: int) -> list[str]:
        """Sentence-split a paragraph over budget, ~split_overlap-token
        overlap between consecutive pieces."""
        parts = []
        for s in sentences(para):
            c = self._count(s)
            if c > budget:
                parts += [(p, self._count(p))
                          for p in self._hard_split(s, budget)]
            else:
                parts.append((s, c))

        chunks, cur, cur_tok = [], [], 0
        for s, c in parts:
            if cur and cur_tok + c > budget:
                chunks.append("".join(x for x, _ in cur).strip())
                tail, tail_tok = [], 0
                for x, xc in reversed(cur):
                    if tail_tok + xc > self.split_overlap or len(tail) + 1 == len(cur):
                        break
                    tail.insert(0, (x, xc))
                    tail_tok += xc
                cur, cur_tok = tail, tail_tok
            cur.append((s, c))
            cur_tok += c
        if cur:
            chunks.append("".join(x for x, _ in cur).strip())
        return [c for c in chunks if c]

    # -------------------------------------------------------------- packing
    def chunk_section(self, text: str, header: str,
                      target: int | None = None) -> list[str]:
        """Clean chunk texts for one section. target defaults to
        chunk_target; passing target >= cap packs to the cap budget (the
        objective path, so objectives stay whole whenever they fit)."""
        if not text or not text.strip():
            return []
        budget = self.cap - self._count(header) - 2 - SAFETY  # 2 = CLS/SEP
        if budget < 32:
            raise ValueError(f"header leaves no room to chunk: {header!r}")
        target = min(target if target is not None else self.chunk_target,
                     budget)

        chunks, cur, cur_tok = [], [], 0

        def flush():
            nonlocal cur, cur_tok
            if cur:
                chunks.append("\n".join(cur))
                cur, cur_tok = [], 0

        for p in paragraphs(text):
            c = self._count(p)
            if c > budget:
                flush()
                chunks += self._split_oversized(p, budget)
                continue
            if cur and cur_tok + c > target:
                flush()
            cur.append(p)
            cur_tok += c
        flush()

        for chunk in chunks:
            nt = self.n_tokens(header, chunk)
            assert nt <= self.cap, (
                f"chunk over cap: {nt} > {self.cap} for header {header!r}")
        return chunks

    # ------------------------------------------------------------ documents
    def _docs(self, texts: list[str], header: str, project_id: int,
              source: str, section: str) -> list[Document]:
        return [
            Document(
                page_content=t,
                metadata={
                    "chunk_id": f"{project_id}:{source}:{section}:{i:03d}",
                    "project_id": project_id,
                    "source": source,
                    "section": section,
                    "n_tokens": self.n_tokens(header, t),
                })
            for i, t in enumerate(texts)
        ]

    def chunk_report(self, project_id: int, acronym: str | None,
                     title: str | None,
                     sections: dict[str, str | None]) -> list[Document]:
        docs = []
        for section, text in sections.items():
            if not text:
                continue
            header = make_header(acronym, title, section)
            docs += self._docs(self.chunk_section(text, header), header,
                               project_id, "report", section)
        return docs

    def chunk_objective(self, project_id: int, acronym: str | None,
                        title: str | None,
                        objective: str | None) -> list[Document]:
        if not objective or not objective.strip():
            return []
        header = make_header(acronym, title, "objective")
        texts = self.chunk_section(objective, header, target=self.cap)
        return self._docs(texts, header, project_id, "objective", "objective")
