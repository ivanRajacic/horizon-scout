"""archive-questions: the only sanctioned way a question leaves the bank.

The property under test throughout is that a refusal writes NOTHING - the bank
and the archive are both exactly as they were. A half-applied archive is worse
than no archive, because the bank would then disagree with every recorded run.
"""

from __future__ import annotations

import json

import pytest

from src.eval.archive import ArchiveError, archive_questions


def _q(qid: str, **over) -> dict:
    """A minimal record that passes the loud validator (vector/L1/identify)."""
    rec = {
        "question_id": qid,
        "text": f"Which project did the thing described in {qid}?",
        "expected_route": "vector",
        "level": "L1",
        "subtype": "identify",
        "term_style": "exact-term",
        "gold_project_ids": [101000001],
        "pooling_evidence": {
            "conditions_run": ["lexical", "dense", "hybrid", "hybrid_rerank"],
            "k": 20, "pooled_candidate_count": 7, "accepted": [101000001],
            "rejected_count": 6, "index_fingerprint": "be84cbad9182"},
        "reference_answer": f"The {qid} project did it.",
    }
    rec.update(over)
    return rec


@pytest.fixture
def bank(tmp_path):
    path = tmp_path / "bank.jsonl"
    path.write_text("".join(json.dumps(_q(f"vec-{i:02d}")) + "\n"
                            for i in range(1, 6)), encoding="utf-8")
    return path


@pytest.fixture
def archive(tmp_path):
    return tmp_path / "archive" / "trimmed.jsonl"


def _ids(path):
    return [json.loads(ln)["question_id"]
            for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_moves_the_named_questions_and_leaves_the_rest(bank, archive):
    before = bank.read_text(encoding="utf-8")

    res = archive_questions(["vec-02", "vec-04"], "trimming to target",
                            bank, archive)

    assert res.archived == ["vec-02", "vec-04"]
    assert res.remaining == 3
    assert _ids(bank) == ["vec-01", "vec-03", "vec-05"]
    # the surviving lines are byte-identical, not re-serialised
    kept = [ln for ln in before.splitlines()
            if json.loads(ln)["question_id"] in {"vec-01", "vec-03", "vec-05"}]
    assert bank.read_text(encoding="utf-8").splitlines() == kept


def test_archive_line_is_an_envelope_with_the_record_unchanged(bank, archive):
    original = next(json.loads(ln) for ln in bank.read_text(encoding="utf-8").splitlines()
                    if json.loads(ln)["question_id"] == "vec-03")

    archive_questions(["vec-03"], "run-level why", bank, archive)

    env = json.loads(archive.read_text(encoding="utf-8").strip())
    assert env["record"] == original          # payload untouched
    assert env["archived_reason"] == "run-level why"
    assert env["archived_from"] == "bank.jsonl"
    assert env["archived_at"]
    # the payload still validates as a bank entry, so it can be restored
    from src.eval.bank import validate_record
    assert validate_record(env["record"]) == []


def test_per_id_reasons_override_the_run_level_one(bank, archive):
    archive_questions(["vec-01", "vec-02"], "generic",
                      bank, archive,
                      per_id_reasons={"vec-02": "specific to this one"})

    envs = {json.loads(ln)["record"]["question_id"]: json.loads(ln)
            for ln in archive.read_text(encoding="utf-8").splitlines()}
    assert envs["vec-01"]["archived_reason"] == "generic"
    assert envs["vec-02"]["archived_reason"] == "specific to this one"


def test_appends_to_an_existing_archive(bank, archive):
    archive_questions(["vec-01"], "first pass", bank, archive)
    archive_questions(["vec-02"], "second pass", bank, archive)

    records = [json.loads(ln)["record"]["question_id"]
               for ln in archive.read_text(encoding="utf-8").splitlines()]
    assert records == ["vec-01", "vec-02"]
    assert _ids(bank) == ["vec-03", "vec-04", "vec-05"]


@pytest.mark.parametrize("ids,reason,expect", [
    (["vec-99"], "why", "not in"),
    (["vec-01", "vec-01"], "why", "listed twice"),
    ([], "why", "nothing to archive"),
    (["vec-01"], "   ", "--reason is required"),
])
def test_refusals_write_nothing(bank, archive, ids, reason, expect):
    before = bank.read_text(encoding="utf-8")

    with pytest.raises(ArchiveError) as e:
        archive_questions(ids, reason, bank, archive)

    assert expect in str(e.value)
    assert bank.read_text(encoding="utf-8") == before
    assert not archive.exists()


def test_refuses_to_archive_the_same_id_twice(bank, archive):
    archive_questions(["vec-01"], "first", bank, archive)
    archive_before = archive.read_text(encoding="utf-8")
    bank_before = bank.read_text(encoding="utf-8")

    # put it back so the id is in both files, then try again
    bank.write_text(bank_before + json.dumps(_q("vec-01")) + "\n",
                    encoding="utf-8")
    with pytest.raises(ArchiveError, match="already in"):
        archive_questions(["vec-01"], "again", bank, archive)

    assert archive.read_text(encoding="utf-8") == archive_before


def test_refuses_to_empty_the_bank(bank, archive):
    before = bank.read_text(encoding="utf-8")

    with pytest.raises(ArchiveError, match="empty the bank"):
        archive_questions([f"vec-{i:02d}" for i in range(1, 6)], "all of them",
                          bank, archive)

    assert bank.read_text(encoding="utf-8") == before
    assert not archive.exists()


def test_refuses_when_the_remaining_bank_would_not_validate(bank, archive):
    """A broken line already in the bank must surface here rather than being
    silently carried through an archive that reports success."""
    bank.write_text(bank.read_text(encoding="utf-8")
                    + json.dumps(_q("vec-06", level="L3")) + "\n",  # L3 needs 5+ gold
                    encoding="utf-8")
    before = bank.read_text(encoding="utf-8")

    with pytest.raises(ArchiveError, match="fails validation"):
        archive_questions(["vec-02"], "trim", bank, archive)

    assert bank.read_text(encoding="utf-8") == before
    assert not archive.exists()


def test_refuses_a_bank_with_a_duplicate_id(bank, archive):
    bank.write_text(bank.read_text(encoding="utf-8")
                    + json.dumps(_q("vec-01")) + "\n", encoding="utf-8")
    before = bank.read_text(encoding="utf-8")

    with pytest.raises(ArchiveError, match="duplicate"):
        archive_questions(["vec-02"], "trim", bank, archive)

    assert bank.read_text(encoding="utf-8") == before


def test_per_id_reason_for_a_question_not_being_archived_is_a_refusal(bank, archive):
    with pytest.raises(ArchiveError, match="not being archived"):
        archive_questions(["vec-01"], "why", bank, archive,
                          per_id_reasons={"vec-05": "stray"})

    assert not archive.exists()
