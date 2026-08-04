"""Bank schema-v2 validator tests: valid records load; every violation class
fails LOUDLY (M5 principle: loud failure over silent wrongness)."""

import json

import pytest

from src.eval.bank import (BankValidationError, ROUTE_TO_MODE, load_bank,
                           validate_record)

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
    "term_style": "paraphrase",
    "gold_project_ids": [101],
    "pooling_evidence": {
        "conditions_run": ["lexical", "dense", "hybrid", "hybrid_rerank"],
        "k": 20, "pooled_candidate_count": 7, "accepted": [101],
        "rejected_count": 6, "index_fingerprint": "be84cbad9182"},
}


# An adversarial entry is born verified like every other: it names the
# answerable question it perturbs (VALID_VECTOR) and carries the typed proof
# of its own emptiness. Any bank written with it must contain the twin too.
VALID_ADV = {
    "question_id": "t-adv", "text": "Which projects cure dragons?",
    "expected_route": "vector", "level": "ADV", "subtype": "zero-match",
    "gold_project_ids": [], "twin_id": "t-02",
    "absence_evidence": [
        {"sql": "SELECT id FROM project WHERE title ILIKE '%dragon%'",
         "expect": "zero", "key_result": "no project mentions dragons"},
        {"sql": "SELECT id FROM project WHERE title ILIKE '%wyvern%'",
         "expect": "zero", "key_result": "near-miss synonym is empty too"},
    ],
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


def test_validate_record_accepts_a_clean_record():
    # The single-record gate /question-orchestrator runs at slot close.
    assert validate_record(VALID_SQL) == []
    assert validate_record(VALID_VECTOR) == []


def test_validate_record_reports_every_violation():
    broken = {**VALID_SQL, "subtype": "trap", "nope": 1}
    del broken["answer_columns"]
    errors = "\n".join(validate_record(broken, "slot sql-18"))
    assert "slot sql-18" in errors           # the caller's label, not a lineno
    assert "unknown field 'nope'" in errors
    assert "only legal at L3" in errors      # trap subtype on an L1 record
    assert "require answer_columns" in errors


def test_validate_record_skips_ladder_rules_when_the_level_is_unknown():
    # An unrecognised level is reported, and the level-dependent rules stay
    # silent rather than asserting a requirement against a cell that does not
    # exist - one loud root cause, not a cascade.
    errors = validate_record({**VALID_SQL, "level": "L9"})
    assert any("level must be one of" in e for e in errors)
    assert not any("answer_columns" in e for e in errors)


def test_validate_record_rejects_a_non_object():
    assert validate_record(["not", "a", "record"]) == [
        "record: record must be a JSON object"]


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
    qs = load_bank(write_bank(tmp_path, [VALID_VECTOR, VALID_ADV]))
    assert qs[1].is_adversarial
    assert "level=ADV requires subtype" in errors_of(
        tmp_path, [VALID_VECTOR, {**VALID_ADV, "subtype": "lookup"}])


def test_zero_match_must_have_empty_gold(tmp_path):
    assert "zero-match" in errors_of(
        tmp_path, [VALID_VECTOR, {**VALID_ADV, "gold_project_ids": [1]}])


def test_adv_requires_absence_evidence(tmp_path):
    rec = {k: v for k, v in VALID_ADV.items() if k != "absence_evidence"}
    assert "require absence_evidence" in errors_of(
        tmp_path, [VALID_VECTOR, rec])


def test_absence_evidence_entries_are_checked(tmp_path):
    def adv(evidence):
        return [VALID_VECTOR, {**VALID_ADV, "absence_evidence": evidence}]

    ok = VALID_ADV["absence_evidence"][0]
    assert "must be a single SELECT" in errors_of(
        tmp_path, adv([{**ok, "sql": "DROP TABLE project"}]))
    assert "expect must be one of" in errors_of(
        tmp_path, adv([{**ok, "expect": "maybe"}]))
    assert "key_result must be a non-empty string" in errors_of(
        tmp_path, adv([{**ok, "key_result": "  "}]))
    assert "missing keys: key_result" in errors_of(
        tmp_path, adv([{"sql": ok["sql"], "expect": "zero"}]))
    assert "non-empty list" in errors_of(tmp_path, adv([]))


def test_each_adv_subtype_demands_the_right_shape_of_proof(tmp_path):
    # A zero-match whose proof contains no query that must come back empty is
    # not proving the thing its label claims.
    rows_only = [{"sql": "SELECT id FROM project", "expect": "rows",
                  "key_result": "projects exist"}]
    assert "expect='zero'" in errors_of(
        tmp_path, [VALID_VECTOR,
                   {**VALID_ADV, "absence_evidence": rows_only}])
    # ...and a false-presupposition needs the refuting result to be FULL:
    # "the data is silent" is data-absent wearing the wrong label.
    fp = {**VALID_ADV, "question_id": "t-fp", "subtype": "false-presupposition"}
    del fp["gold_project_ids"]
    assert "expect='rows'" in errors_of(tmp_path, [VALID_VECTOR, fp])
    ok = load_bank(write_bank(tmp_path, [
        VALID_VECTOR, {**fp, "absence_evidence": rows_only}]))
    assert ok[1].absence_evidence == rows_only


def test_twin_id_is_required_forbidden_or_optional_by_subtype(tmp_path):
    no_twin = {k: v for k, v in VALID_ADV.items() if k != "twin_id"}
    assert "requires twin_id" in errors_of(tmp_path, [VALID_VECTOR, no_twin])

    # unanswerable derives from nothing, so a twin is a contradiction.
    una = {**VALID_ADV, "question_id": "t-un", "subtype": "unanswerable"}
    del una["gold_project_ids"]
    assert "carry no twin_id" in errors_of(tmp_path, [VALID_VECTOR, una])

    # data-absent may carry one, and may not.
    absent = {k: v for k, v in VALID_ADV.items()
              if k not in ("twin_id", "gold_project_ids")}
    absent |= {"question_id": "t-da", "subtype": "data-absent"}
    assert len(load_bank(write_bank(tmp_path, [VALID_VECTOR, absent]))) == 2
    assert len(load_bank(write_bank(
        tmp_path, [VALID_VECTOR, {**absent, "twin_id": "t-02"}]))) == 2


def test_twin_must_resolve_to_an_answerable_question(tmp_path):
    assert "not a question in this bank" in errors_of(
        tmp_path, [VALID_VECTOR, {**VALID_ADV, "twin_id": "t-nope"}])
    # Two refusals pointing at each other control for nothing.
    other_adv = {**VALID_ADV, "question_id": "t-adv2", "twin_id": "t-adv"}
    assert "is itself an ADV question" in errors_of(
        tmp_path, [VALID_VECTOR, VALID_ADV, other_adv])
    assert "must not point at the question itself" in errors_of(
        tmp_path, [VALID_VECTOR, {**VALID_ADV, "twin_id": "t-adv"}])


def test_adv_only_fields_are_rejected_on_the_ladder(tmp_path):
    assert "only legal on level=ADV" in errors_of(
        tmp_path, [VALID_VECTOR, {**VALID_SQL, "twin_id": "t-02"}])
    assert "only legal on level=ADV" in errors_of(
        tmp_path, [{**VALID_SQL,
                    "absence_evidence": VALID_ADV["absence_evidence"]}])


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
    rec = {**VALID_VECTOR, "gold_project_ids": [1, 2, 3],
           "pooling_evidence": {**VALID_VECTOR["pooling_evidence"],
                                "accepted": [1, 2, 3]}}
    assert "requires |gold_project_ids|" in errors_of(tmp_path, [rec])
    rec = {**rec, "level": "L2", "subtype": "comparison"}
    assert len(load_bank(write_bank(tmp_path, [rec]))) == 1


def test_vector_ladder_requires_verification_fields(tmp_path):
    for missing in ("gold_project_ids", "term_style", "pooling_evidence"):
        rec = dict(VALID_VECTOR)
        del rec[missing]
        assert f"require {missing}" in errors_of(tmp_path, [rec]), missing


def test_pooling_evidence_keys_and_accepted_must_match_gold(tmp_path):
    rec = {**VALID_VECTOR,
           "pooling_evidence": {"conditions_run": ["lexical"], "k": 20}}
    text = errors_of(tmp_path, [rec])
    assert "pooling_evidence missing keys" in text
    assert "pooled_candidate_count" in text
    rec = {**VALID_VECTOR,
           "pooling_evidence": {**VALID_VECTOR["pooling_evidence"],
                                "accepted": [101, 202]}}
    assert "must equal gold_project_ids" in errors_of(tmp_path, [rec])


def test_pooling_evidence_requires_gold_project_ids(tmp_path):
    rec = {**VALID_SQL,
           "pooling_evidence": VALID_VECTOR["pooling_evidence"]}
    assert "pooling_evidence requires gold_project_ids" in errors_of(
        tmp_path, [rec])


VALID_HYBRID = {
    "question_id": "t-04",
    "text": "What do the Croatian coastal-monitoring projects funded after "
            "2020 monitor?",
    "expected_route": "hybrid", "level": "L1", "subtype": "filter-read",
    "term_style": "exact-term",
    "gold_project_ids": [301],
    "filter_evidence": {
        "filter_sql": "SELECT id FROM project WHERE x", "survivor_count": 3,
        "survivor_ids": [301, 302, 303], "schema_docs_hash": "c3435815b331"},
    "pooling_evidence": {
        "conditions_run": ["lexical", "dense", "hybrid", "hybrid_rerank"],
        "k": 20, "pooled_candidate_count": 3, "accepted": [301],
        "rejected_count": 2, "index_fingerprint": "be84cbad9182"},
}


def test_valid_hybrid_loads(tmp_path):
    q = load_bank(write_bank(tmp_path, [VALID_HYBRID]))[0]
    assert q.filter_evidence["survivor_count"] == 3
    assert q.is_topical


def test_hybrid_subtypes_are_level_bound(tmp_path):
    assert "only legal at L1" in errors_of(
        tmp_path, [{**VALID_HYBRID, "level": "L2"}])
    assert "only legal at L3" in errors_of(
        tmp_path, [{**VALID_HYBRID, "level": "L1",
                    "subtype": "filter-survey"}])
    assert "hybrid subtype must be one of" in errors_of(
        tmp_path, [{**VALID_HYBRID, "subtype": "identify"}])


def test_hybrid_ladder_requires_verification_fields(tmp_path):
    for missing in ("gold_project_ids", "term_style", "pooling_evidence",
                    "filter_evidence"):
        rec = dict(VALID_HYBRID)
        del rec[missing]
        assert f"require {missing}" in errors_of(tmp_path, [rec]), missing


def test_hybrid_gold_bounds_per_subtype(tmp_path):
    # filter-read needs exactly one gold project.
    rec = {**VALID_HYBRID, "gold_project_ids": [301, 302],
           "pooling_evidence": {**VALID_HYBRID["pooling_evidence"],
                                "accepted": [301, 302]}}
    assert "|gold_project_ids| == 1" in errors_of(tmp_path, [rec])
    # filter-survey needs 5+; 3 golds fail even though survivors allow them.
    rec = {**VALID_HYBRID, "level": "L3", "subtype": "filter-survey",
           "gold_project_ids": [301, 302, 303],
           "pooling_evidence": {**VALID_HYBRID["pooling_evidence"],
                                "accepted": [301, 302, 303]}}
    assert "|gold_project_ids| >= 5" in errors_of(tmp_path, [rec])


def test_hybrid_gold_must_be_subset_of_survivors(tmp_path):
    rec = {**VALID_HYBRID, "gold_project_ids": [999],
           "pooling_evidence": {**VALID_HYBRID["pooling_evidence"],
                                "accepted": [999]}}
    assert "subset" in errors_of(tmp_path, [rec])


def test_filter_evidence_shape_and_route(tmp_path):
    rec = {**VALID_HYBRID,
           "filter_evidence": {"filter_sql": "DROP TABLE project",
                               "survivor_count": 2,
                               "survivor_ids": [301, 301],
                               "schema_docs_hash": "x"}}
    text = errors_of(tmp_path, [rec])
    assert "single SELECT" in text and "duplicates" in text
    rec = {**VALID_HYBRID,
           "filter_evidence": {**VALID_HYBRID["filter_evidence"],
                               "survivor_count": 7}}
    assert "must equal len(survivor_ids)" in errors_of(tmp_path, [rec])
    assert "only legal on hybrid" in errors_of(
        tmp_path, [{**VALID_VECTOR,
                    "filter_evidence": VALID_HYBRID["filter_evidence"]}])


def test_adv_vector_needs_no_pooling_evidence(tmp_path):
    # ADV is off-ladder: zero-match has an empty gold set by definition and
    # carries no pooled-verification requirement. What it does carry is its
    # own proof - absence_evidence - and the twin that proves the near miss.
    rec = {**VALID_ADV, "question_id": "t-adv2"}
    assert len(load_bank(write_bank(tmp_path, [VALID_VECTOR, rec]))) == 2


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
