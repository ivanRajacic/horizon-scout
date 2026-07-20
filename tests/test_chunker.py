"""Unit tests for src.ingest.chunker (real bge tokenizer, no server needed)."""

import pytest

from src.config import load_tokenizer
from src.ingest.chunker import (Chunker, embedded_text, make_header,
                                paragraphs, sentences)

TOK = load_tokenizer()


@pytest.fixture(scope="module")
def chunker():
    return Chunker(TOK)


def n_tok(text):
    return len(TOK.encode(text, add_special_tokens=False).ids)


def sentence_para(n_sentences):
    """A paragraph of distinct ~14-token sentences."""
    return " ".join(
        f"Sentence number {i} reports on the experimental campaign of "
        f"work package {i}." for i in range(n_sentences))


HEADER = make_header("ACME", "A demo project title", "summary")


def test_header_composition():
    assert HEADER == "ACME - A demo project title | summary"
    assert make_header(None, None, "objective") == " -  | objective"
    assert embedded_text(HEADER, "body") == HEADER + "\nbody"


def test_small_paragraphs_pack_into_one_chunk(chunker):
    text = "First paragraph about hydrogen.\nSecond paragraph about wind."
    chunks = chunker.chunk_section(text, HEADER)
    assert chunks == [text]  # clean text preserved verbatim, no header


def test_packing_respects_target(chunker):
    paras = [sentence_para(8) for _ in range(10)]  # ~115 tokens each
    chunks = chunker.chunk_section("\n".join(paras), HEADER)
    assert len(chunks) > 1
    # every chunk stays within target on clean text (none was force-split)
    assert all(n_tok(c) <= chunker.chunk_target for c in chunks)
    # no paragraph was split across chunks
    reassembled = [p for c in chunks for p in paragraphs(c)]
    assert reassembled == paras


def test_oversized_paragraph_sentence_split_with_overlap(chunker):
    para = sentence_para(60)  # ~850 tokens, one paragraph
    assert n_tok(para) > chunker.cap
    chunks = chunker.chunk_section(para, HEADER)
    assert len(chunks) >= 2
    for c in chunks:
        assert chunker.n_tokens(HEADER, c) <= chunker.cap
    # consecutive pieces overlap: next chunk starts with the tail of previous
    for a, b in zip(chunks, chunks[1:]):
        first_sent = sentences(b)[0].strip()
        assert first_sent in a


def test_pathological_1000_token_paragraph_no_punctuation(chunker):
    words = " ".join(f"token{i}" for i in range(500))  # ~1500 tokens, no '.'
    assert n_tok(words) > 1000
    chunks = chunker.chunk_section(words, HEADER)
    assert len(chunks) >= 2
    for c in chunks:
        assert chunker.n_tokens(HEADER, c) <= chunker.cap
    # nothing lost: every word appears in some chunk
    joined = " ".join(chunks)
    assert all(f"token{i}" in joined for i in range(500))


def test_cap_enforced_on_embedded_string_not_clean_text(chunker):
    # a chunk may only use cap minus header/special tokens for its text
    para = sentence_para(60)
    for c in chunker.chunk_section(para, HEADER):
        assert chunker.n_tokens(HEADER, c) <= chunker.cap


def test_empty_and_degenerate_inputs(chunker):
    assert chunker.chunk_section("", HEADER) == []
    assert chunker.chunk_section("   \n  \n ", HEADER) == []
    assert chunker.chunk_report(1, "A", "T", {"summary": None}) == []
    assert chunker.chunk_objective(1, "A", "T", None) == []
    assert chunker.chunk_objective(1, "A", "T", "  ") == []


def test_report_docs_metadata(chunker):
    docs = chunker.chunk_report(
        42, "ACME", "A demo project title",
        {"summary": "Short summary.", "workPerformed": "Work done."})
    assert [d.metadata["section"] for d in docs] == ["summary", "workPerformed"]
    for d in docs:
        m = d.metadata
        assert m["source"] == "report"
        assert m["project_id"] == 42
        assert m["chunk_id"].startswith("42:report:")
        assert m["n_tokens"] <= chunker.cap
        assert not d.page_content.startswith("ACME - ")  # clean text only


def test_objective_docs_flagged_and_whole(chunker):
    obj = sentence_para(25)  # ~360 tokens: fits whole with header
    docs = chunker.chunk_objective(7, "ACME", "A demo project title", obj)
    assert len(docs) == 1
    assert docs[0].metadata["source"] == "objective"
    assert docs[0].metadata["section"] == "objective"
    assert docs[0].metadata["chunk_id"] == "7:objective:objective:000"
    assert docs[0].page_content == obj


def test_oversized_objective_gets_split(chunker):
    obj = sentence_para(60)  # over the cap: rare case, split like a paragraph
    docs = chunker.chunk_objective(7, "ACME", "A demo project title", obj)
    assert len(docs) >= 2
    assert all(d.metadata["source"] == "objective" for d in docs)
    assert all(d.metadata["n_tokens"] <= chunker.cap for d in docs)
