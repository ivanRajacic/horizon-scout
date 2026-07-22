"""Bank schema-v2 validator tests: valid records load; every violation class
fails LOUDLY (M5 principle: loud failure over silent wrongness)."""

import json

import pytest

from src.eval.bank import BankValidationError, ROUTE_TO_MODE, load_bank

VALID_SQL = {
    "question_id": "t-01", "text": "How many projects were terminated?",
    "expected_route": "sql", "level": "L1", "subtype": "aggregate",
    "gold_sql": "SELECT COUNT(*) FROM project WHERE status = 'TERMINATED'",
    "answer_columns": ["count"],
    "level_evidence": {"join_count": 0, "non_trivial_where_count": 1,
                       "has_group_by": False, "has_order_by_limit": False,
                       "value_note_dependencies": [],
                       "trap_documented": False},
    "schema_docs_hash": "c3435815b331",
}

VALID_VECTOR = {
    "question_id": "t-02", "text": "Find the project about widget farming.",
    "expected_route": "vector", "level": "L1", "subtype": "identify",
    "gold_project_ids": [101],
}


def write_bank(tmp_path, records):
    p = tmp_path / "bank.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return p


def errors_of(tmp_path, records):
    with pytest.raises(BankValidationError) as ei:
        load_bank(write_bank(tmp_path, records))
    return "\n".join(ei.value.errors)


def test_valid_records_load(tmp_path):
    qs = load_bank(write_bank(tmp_path, [VALID_SQL, VALID_VECTOR]))
    assert len(qs) == 2
    q = qs[0]
    assert q.expected_route == "sql" and q.level == "L1"
    assert q.subtype == "aggregate" and q.answer_columns == ["count"]
    assert q.specification == "well-specified" and not q.compositional
    assert not q.reviewer_override and not q.is_adversarial


def test_route_to_mode_covers_all_concrete_routes():
    assert ROUTE_TO_MODE == {"sql": "sql", "vector": "vector",
                             "hybrid": "scoped"}


def test_old_schema_fields_are_unknown(tmp_path):
    text = errors_of(tmp_path, [
        {**VALID_SQL, "complexity": "L1", "adversarial": "zero-match"}])
    assert "unknown field 'complexity'" in text
    assert "unknown field 'adversarial'" in text


def test_duplicate_question_id_is_an_error(tmp_path):
    assert "duplicate question_id" in errors_of(
        tmp_path, [VALID_SQL, VALID_SQL])


def test_invalid_route_and_level(tmp_path):
    text = errors_of(tmp_path, [{**VALID_SQL, "expected_route": "sparql",
                                 "level": "L4"}])
    assert "expected_route" in text and "level" in text


def test_subtype_required(tmp_path):
    rec = dict(VALID_SQL)
    del rec["subtype"]
    assert "subtype is required" in errors_of(tmp_path, [rec])


def test_sql_subtype_level_binding(tmp_path):
    assert "only legal at L1" in errors_of(
        tmp_path, [{**VALID_SQL, "level": "L2", "subtype": "lookup"}])
    assert "only legal at L3" in errors_of(
        tmp_path, [{**VALID_SQL, "level": "L2", "subtype": "multi-join"}])
    assert "sql subtype must be one of" in errors_of(
        tmp_path, [{**VALID_SQL, "subtype": "identify"}])


@pytest.mark.parametrize("level", ["L1", "L2", "L3"])
def test_rank_is_legal_at_every_level(tmp_path, level):
    rec = {**VALID_SQL, "level": level, "subtype": "rank",
           "sql_comparison": "ordered"}
    assert len(load_bank(write_bank(tmp_path, [rec]))) == 1


def test_ordered_iff_rank_both_directions(tmp_path):
    assert "iff subtype is 'rank'" in errors_of(
        tmp_path, [{**VALID_SQL, "subtype": "rank"}])  # default set
    assert "iff subtype is 'rank'" in errors_of(
        tmp_path, [{**VALID_SQL, "sql_comparison": "ordered"}])


def test_sql_ladder_requires_verification_fields(tmp_path):
    for missing in ("answer_columns", "level_evidence", "schema_docs_hash"):
        rec = dict(VALID_SQL)
        del rec[missing]
        assert f"require {missing}" in errors_of(tmp_path, [rec]), missing


def test_answer_columns_and_evidence_require_gold_sql(tmp_path):
    text = errors_of(tmp_path, [
        {**VALID_VECTOR, "answer_columns": ["acronym"],
         "level_evidence": {"join_count": 0}}])
    assert "answer_columns requires gold_sql" in text
    assert "level_evidence requires gold_sql" in text


def test_answer_columns_must_be_nonempty_strings(tmp_path):
    assert "non-empty list" in errors_of(
        tmp_path, [{**VALID_SQL, "answer_columns": []}])
    assert "non-empty list" in errors_of(
        tmp_path, [{**VALID_SQL, "answer_columns": ["ok", ""]}])


def test_ambiguous_requires_acceptable_routes_and_no_subtype(tmp_path):
    amb = {"question_id": "t-a1", "text": "How many AI projects?",
           "expected_route": "ambiguous", "level": "L2"}
    assert "acceptable_routes" in errors_of(tmp_path, [amb])
    ok = {**amb, "acceptable_routes": ["sql", "vector"]}
    assert len(load_bank(write_bank(tmp_path, [ok]))) == 1
    assert "carries no subtype" in errors_of(
        tmp_path, [{**ok, "subtype": "lookup"}])


def test_acceptable_routes_only_on_ambiguous(tmp_path):
    assert "only allowed" in errors_of(
        tmp_path, [{**VALID_SQL, "acceptable_routes": ["sql", "vector"]}])


def test_adv_level_takes_adv_subtypes_on_any_route(tmp_path):
    ok = {"question_id": "t-adv", "text": "Which projects cure dragons?",
          "expected_route": "vector", "level": "ADV",
          "subtype": "zero-match", "gold_project_ids": []}
    assert load_bank(write_bank(tmp_path, [ok]))[0].is_adversarial
    assert "level=ADV requires subtype" in errors_of(
        tmp_path, [{**ok, "subtype": "lookup"}])


def test_zero_match_must_have_empty_gold(tmp_path):
    rec = {"question_id": "t-zm", "text": "q", "expected_route": "vector",
           "level": "ADV", "subtype": "zero-match", "gold_project_ids": [1]}
    assert "zero-match" in errors_of(tmp_path, [rec])


def test_term_style_rejected_on_pure_sql(tmp_path):
    assert "topical" in errors_of(
        tmp_path, [{**VALID_SQL, "term_style": "exact-term"}])


def test_term_style_allowed_via_ambiguous_topical_route(tmp_path):
    rec = {"question_id": "t-03", "text": "How many AI projects?",
           "expected_route": "ambiguous",
           "acceptable_routes": ["sql", "vector"],
           "level": "L2", "term_style": "paraphrase"}
    assert len(load_bank(write_bank(tmp_path, [rec]))) == 1


def test_vector_level_must_match_gold_count(tmp_path):
    rec = {**VALID_VECTOR, "gold_project_ids": [1, 2, 3]}
    assert "requires |gold_project_ids|" in errors_of(tmp_path, [rec])
    rec = {**rec, "level": "L2", "subtype": "comparison"}
    assert len(load_bank(write_bank(tmp_path, [rec]))) == 1


def test_gold_sql_must_be_select(tmp_path):
    assert "SELECT" in errors_of(
        tmp_path, [{**VALID_SQL, "gold_sql": "DROP TABLE project"}])


def test_sql_comparison_requires_gold_sql(tmp_path):
    assert "requires gold_sql" in errors_of(
        tmp_path, [{**VALID_VECTOR, "sql_comparison": "ordered"}])


def test_reviewer_override_must_be_bool(tmp_path):
    assert "reviewer_override" in errors_of(
        tmp_path, [{**VALID_SQL, "reviewer_override": "yes"}])
    rec = {**VALID_SQL, "reviewer_override": True}
    assert load_bank(write_bank(tmp_path, [rec]))[0].reviewer_override


def test_all_errors_reported_not_just_first(tmp_path):
    bad1 = {**VALID_SQL, "expected_route": "sparql"}
    bad2 = {**VALID_SQL, "question_id": "t-07", "level": "L9"}
    with pytest.raises(BankValidationError) as ei:
        load_bank(write_bank(tmp_path, [bad1, bad2]))
    assert len(ei.value.errors) == 2


def test_fresh_bank_file_is_valid():
    from src.config import BANK_PATH

    # Fresh (possibly empty) skill-authored bank must always load clean.
    load_bank(BANK_PATH)
