"""Unit tests for index_meta validation (no server or index needed)."""

import pytest

from src.config import EMBED_DIM, EMBED_MODEL
from src.retrieval.vector_search import IndexMetaError, validate_meta

GOOD_HASH = "a" * 64


def good_meta():
    return {"embedding_model": EMBED_MODEL, "dim": EMBED_DIM,
            "gguf_sha256": GOOD_HASH}


def test_matching_meta_passes():
    validate_meta(good_meta(), GOOD_HASH)


@pytest.mark.parametrize("field,bad", [
    ("embedding_model", "some-other-model.gguf"),
    ("gguf_sha256", "b" * 64),
    ("dim", 384),
])
def test_any_mismatch_refuses(field, bad):
    meta = good_meta()
    meta[field] = bad
    with pytest.raises(IndexMetaError, match="mismatch"):
        validate_meta(meta, GOOD_HASH)


def test_missing_field_refuses():
    meta = good_meta()
    del meta["gguf_sha256"]
    with pytest.raises(IndexMetaError):
        validate_meta(meta, GOOD_HASH)
