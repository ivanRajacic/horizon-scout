"""promote-drafts tests: the deterministic bridge from a ticked /question-orchestrator
report to eval/bank.jsonl. Every refusal class fails LOUDLY and leaves the
bank byte-identical; only a fully decided, fully valid report appends."""

import json

import pytest

from src.eval.promote import PromoteError, promote

SQL_A = {
    "question_id": "sql-90", "text": "How many projects were terminated?",
    "expected_route": "sql", "level": "L1", "subtype": "aggregate",
    "gold_sql": "SELECT COUNT(*) FROM project WHERE status = 'TERMINATED'",
    "answer_columns": ["count"],
    "level_evidence": {"join_count": 0, "non_trivial_where_count": 1,
                       "has_group_by": False, "has_order_by_limit": False,
                       "value_note_dependencies": [],
                       "trap_documented": False},
    "schema_docs_hash": "c3435815b331",
}

VEC_A = {
    "question_id": "vec-90", "text": "Find the project about widget farming.",
    "expected_route": "vector", "level": "L1", "subtype": "identify",
    "term_style": "paraphrase",
    "gold_project_ids": [101],
    "pooling_evidence": {
        "conditions_run": ["lexical", "dense", "hybrid", "hybrid_rerank"],
        "k": 20, "pooled_candidate_count": 7, "accepted": [101],
        "rejected_count": 6, "index_fingerprint": "be84cbad9182"},
}


ADV_A = {
    "question_id": "adv-90", "text": "What score did the widget project get?",
    "expected_route": "sql", "level": "ADV", "subtype": "data-absent",
    "absence_evidence": [
        {"sql": "SELECT 1 AS x WHERE 1 = 0", "expect": "zero",
         "key_result": "no score facet anywhere"}],
}


def make_files(tmp_path, drafts, decisions, bank_records=(),
               draft_file_line=None):
    """Write a bank, a draft jsonl, and a report with the given decisions.
    decisions: {question_id: 'approve' | 'reject' | 'both' | 'none' | None}
    (None = no decision line at all)."""
    bank = tmp_path / "bank.jsonl"
    bank.write_text("".join(json.dumps(r) + "\n" for r in bank_records),
                    encoding="utf-8")
    draft = tmp_path / "draft-bank-2026-07-23.jsonl"
    draft.write_text("".join(json.dumps(r) + "\n" for r in drafts),
                     encoding="utf-8")
    boxes = {"approve": "[x] APPROVE  [ ] REJECT",
             "reject": "[ ] APPROVE  [x] REJECT",
             "both": "[x] APPROVE  [x] REJECT",
             "none": "[ ] APPROVE  [ ] REJECT"}
    lines = ["# Draft batch - test",
             draft_file_line if draft_file_line is not None
             else f"Draft-bank-file: {draft.name}", ""]
    for qid, decision in decisions.items():
        lines += [f"## {qid} - SOUND", "", "**Question:** \"...\"", ""]
        if decision is not None:
            lines += [f"Decision: {boxes[decision]}", ""]
    report = tmp_path / "draft-report-2026-07-23.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report, bank, draft


def bank_ids(bank):
    return [json.loads(line)["question_id"]
            for line in bank.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def refusal(report, bank):
    with pytest.raises(PromoteError) as ei:
        promote(report, bank)
    return str(ei.value)


def test_approved_appended_rejected_skipped(tmp_path):
    report, bank, draft = make_files(
        tmp_path, [SQL_A, VEC_A],
        {"sql-90": "approve", "vec-90": "reject"})
    res = promote(report, bank)
    assert res.promoted == ["sql-90"] and res.rejected == ["vec-90"]
    assert res.draft_file == draft
    assert bank_ids(bank) == ["sql-90"]
    assert bank.read_text(encoding="utf-8").endswith("\n")


def test_appends_after_existing_entries_without_trailing_newline(tmp_path):
    report, bank, _ = make_files(tmp_path, [VEC_A], {"vec-90": "approve"},
                                 bank_records=[SQL_A])
    # A bank whose last line lacks a newline must not get a glued line.
    bank.write_text(bank.read_text(encoding="utf-8").rstrip("\n"),
                    encoding="utf-8")
    promote(report, bank)
    assert bank_ids(bank) == ["sql-90", "vec-90"]


def test_adv_heading_recognized_and_promoted(tmp_path):
    report, bank, _ = make_files(tmp_path, [ADV_A], {"adv-90": "approve"})
    res = promote(report, bank)
    assert res.promoted == ["adv-90"]
    assert bank_ids(bank) == ["adv-90"]


def test_all_rejected_is_a_no_op(tmp_path):
    report, bank, _ = make_files(tmp_path, [SQL_A], {"sql-90": "reject"})
    res = promote(report, bank)
    assert res.promoted == [] and res.rejected == ["sql-90"]
    assert bank_ids(bank) == []


def test_duplicate_id_already_in_bank_refused(tmp_path):
    report, bank, _ = make_files(tmp_path, [SQL_A], {"sql-90": "approve"},
                                 bank_records=[SQL_A])
    text = refusal(report, bank)
    assert "already in" in text and "sql-90" in text
    assert bank_ids(bank) == ["sql-90"]  # untouched


@pytest.mark.parametrize("decision,fragment", [
    ("both", "both boxes ticked"),
    ("none", "no box ticked"),
])
def test_malformed_decision_refused(tmp_path, decision, fragment):
    report, bank, _ = make_files(tmp_path, [SQL_A], {"sql-90": decision})
    assert fragment in refusal(report, bank)
    assert bank_ids(bank) == []


def test_missing_decision_for_staged_draft_refused(tmp_path):
    report, bank, _ = make_files(
        tmp_path, [SQL_A, VEC_A], {"sql-90": "approve", "vec-90": None})
    text = refusal(report, bank)
    assert "vec-90" in text and "no decision" in text
    assert bank_ids(bank) == []


def test_decision_for_unknown_id_refused(tmp_path):
    report, bank, _ = make_files(
        tmp_path, [SQL_A], {"sql-90": "approve", "vec-99": "approve"})
    text = refusal(report, bank)
    assert "vec-99" in text and "no such draft" in text


def test_missing_draft_file_header_refused(tmp_path):
    report, bank, _ = make_files(tmp_path, [SQL_A], {"sql-90": "approve"},
                                 draft_file_line="Not-a-header: nope")
    assert "Draft-bank-file" in refusal(report, bank)


def test_invalid_approved_record_refused_and_bank_untouched(tmp_path):
    broken = dict(SQL_A)
    del broken["answer_columns"]  # SQL ladder entries require it
    report, bank, _ = make_files(tmp_path, [broken], {"sql-90": "approve"},
                                 bank_records=[VEC_A])
    before = bank.read_text(encoding="utf-8")
    text = refusal(report, bank)
    assert "answer_columns" in text
    assert bank.read_text(encoding="utf-8") == before
    assert not (tmp_path / "bank.jsonl.promote-tmp").exists()


def test_absolute_draft_file_path_resolves(tmp_path):
    _, bank, draft = make_files(tmp_path, [SQL_A], {"sql-90": "approve"})
    report = tmp_path / "abs-report.md"
    report.write_text(
        f"Draft-bank-file: {draft}\n\n## sql-90 - SOUND\n\n"
        "Decision: [x] APPROVE  [ ] REJECT\n", encoding="utf-8")
    res = promote(report, bank)
    assert res.promoted == ["sql-90"]
