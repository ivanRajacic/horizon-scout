"""Bank schema validator tests: valid records load; every violation class
fails LOUDLY (M5 principle: loud failure over silent wrongness)."""

import json

import pytest

from src.eval.bank import BankValidationError, ROUTE_TO_MODE, load_bank

VALID = {
    "question_id": "t-01", "text": "How many projects were terminated?",
    "expected_route": "sql", "complexity": "L1",
    "gold_sql": "SELECT COUNT(*) FROM project WHERE status = 'TERMINATED'",
}


def write_bank(tmp_path, records):
    p = tmp_path / "bank.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return p


def errors_of(tmp_path, records):
    with pytest.raises(BankValidationError) as ei:
        load_bank(write_bank(tmp_path, records))
    return "\n".join(ei.value.errors)


def test_valid_record_loads(tmp_path):
    qs = load_bank(write_bank(tmp_path, [VALID]))
    assert len(qs) == 1
    q = qs[0]
    assert q.expected_route == "sql" and q.complexity == "L1"
    assert q.specification == "well-specified" and not q.compositional


def test_route_to_mode_covers_all_concrete_routes():
    # The doc says sql|vector|hybrid; the runtime says sql|vector|scoped.
    assert ROUTE_TO_MODE == {"sql": "sql", "vector": "vector",
                             "hybrid": "scoped"}


def test_unknown_field_is_an_error(tmp_path):
    assert "unknown field 'goldsql'" in errors_of(
        tmp_path, [{**VALID, "goldsql": "typo"}])


def test_duplicate_question_id_is_an_error(tmp_path):
    assert "duplicate question_id" in errors_of(tmp_path, [VALID, VALID])


def test_invalid_route_and_complexity(tmp_path):
    text = errors_of(tmp_path, [{**VALID, "expected_route": "sparql",
                                 "complexity": "L4"}])
    assert "expected_route" in text and "complexity" in text


def test_ambiguous_requires_acceptable_routes(tmp_path):
    text = errors_of(tmp_path, [
        {**VALID, "expected_route": "ambiguous"},
        {**VALID, "question_id": "t-02", "expected_route": "ambiguous",
         "acceptable_routes": ["sql"]}])
    assert text.count("acceptable_routes") == 2


def test_acceptable_routes_only_on_ambiguous(tmp_path):
    assert "only allowed" in errors_of(
        tmp_path, [{**VALID, "acceptable_routes": ["sql", "vector"]}])


def test_term_style_rejected_on_pure_sql(tmp_path):
    assert "topical" in errors_of(
        tmp_path, [{**VALID, "term_style": "exact-term"}])


def test_term_style_allowed_via_ambiguous_topical_route(tmp_path):
    rec = {"question_id": "t-03", "text": "How many AI projects?",
           "expected_route": "ambiguous",
           "acceptable_routes": ["sql", "vector"],
           "complexity": "L2", "term_style": "paraphrase"}
    assert len(load_bank(write_bank(tmp_path, [rec]))) == 1


def test_vector_complexity_must_match_gold_count(tmp_path):
    rec = {"question_id": "t-04", "text": "q", "expected_route": "vector",
           "complexity": "L1", "gold_project_ids": [1, 2, 3]}
    assert "requires |gold_project_ids|" in errors_of(tmp_path, [rec])
    rec["complexity"] = "L2"
    assert len(load_bank(write_bank(tmp_path, [rec]))) == 1


def test_zero_match_must_have_empty_gold(tmp_path):
    rec = {"question_id": "t-05", "text": "q", "expected_route": "vector",
           "complexity": "L1", "adversarial": "zero-match",
           "gold_project_ids": [1]}
    assert "zero-match" in errors_of(tmp_path, [rec])


def test_gold_sql_must_be_select(tmp_path):
    assert "SELECT" in errors_of(
        tmp_path, [{**VALID, "gold_sql": "DROP TABLE project"}])


def test_sql_comparison_requires_gold_sql(tmp_path):
    rec = {"question_id": "t-06", "text": "q", "expected_route": "vector",
           "complexity": "L1", "sql_comparison": "ordered"}
    assert "requires gold_sql" in errors_of(tmp_path, [rec])


def test_all_errors_reported_not_just_first(tmp_path):
    bad1 = {**VALID, "expected_route": "sparql"}
    bad2 = {**VALID, "question_id": "t-07", "complexity": "L9"}
    with pytest.raises(BankValidationError) as ei:
        load_bank(write_bank(tmp_path, [bad1, bad2]))
    assert len(ei.value.errors) == 2


def test_pilot_bank_file_is_valid():
    from src.config import ROOT

    qs = load_bank(ROOT / "eval" / "bank_pilot.jsonl")
    assert len(qs) >= 30
    routes = {q.expected_route for q in qs}
    assert routes == {"sql", "vector", "hybrid", "ambiguous"}
