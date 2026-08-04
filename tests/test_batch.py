"""/question-orchestrator's deterministic nodes: allocation parsing, id assignment, the
gap report, the batch cross-check, and the writer.

The load-bearing test here is the round-trip: a report the writer produced,
with its boxes ticked, must promote cleanly through `promote-drafts`. The two
formats are a contract between a generator and a parser, and nothing else
checks that they still agree.
"""

import json

import pytest

from src.config import ROOT
from src.eval.batch import (BatchError, archived_ids, crosscheck, gap_report,
                            journal_append, load_journal, next_ids,
                            packet_claims, parse_allocation, pick_parents,
                            twinned_ids,
                            write_batch)
from src.eval.promote import promote

PLAN = """
# Plan doc

### Allocation (~100 questions, RQ-weighted, not uniform)

| | L1 | L2 | L3 | route total |
|---|---|---|---|---|
| SQL | 7 | 11 | 5* | 23 |
| Vector | 7 | 10 | 7 | 24 |
| Hybrid | 6 | 10 | 7 | 23 |
| Ambiguous-route | - spread - | | | 10 |
| Adversarial (zero-match, false-presup., data-absent) | | | | 14 |
| Compositional | | | | 3 |
| **Total** | | | | **~97** |

\\* conditional on the pilot smoke test.

Prose after the table.
"""

SQL_A = {
    "question_id": "sql-01", "text": "How many projects were terminated?",
    "expected_route": "sql", "level": "L1", "subtype": "aggregate",
    "gold_sql": "SELECT COUNT(*) FROM project WHERE status = 'TERMINATED'",
    "answer_columns": ["count"],
    "level_evidence": {"join_count": 0, "non_trivial_where_count": 1,
                       "has_group_by": False, "has_order_by_limit": False,
                       "value_note_dependencies": [], "trap_documented": False},
    "reference_answer": "1,204 projects were terminated.",
    "schema_docs_hash": "c3435815b331",
}

HYB_A = {
    "question_id": "hyb-04", "text": "Among the Swedish graphene projects, "
                                     "which one targets antibiotic resistance?",
    "expected_route": "hybrid", "level": "L1", "subtype": "filter-read",
    "specification": "well-specified", "term_style": "exact-term",
    "gold_project_ids": [733297],
    "filter_evidence": {
        "filter_sql": "SELECT p.id FROM project p JOIN organization o "
                      "ON o.projectID = p.id WHERE o.country = 'SE'",
        "survivor_count": 2, "survivor_ids": [733297, 814316],
        "schema_docs_hash": "f8c001e8cc8f"},
    "pooling_evidence": {
        "conditions_run": ["lexical", "dense", "hybrid", "hybrid_rerank"],
        "k": 10, "pooled_candidate_count": 2, "accepted": [733297],
        "rejected_count": 1, "index_fingerprint": "be84cbad9182"},
    "reference_answer": "GRAPHENE-AMR uses graphene oxide coatings.",
}

VEC_A = {
    "question_id": "vec-01", "text": "Which project assembled structures "
                                     "with a robot swarm?",
    "expected_route": "vector", "level": "L1", "subtype": "identify",
    "term_style": "paraphrase", "gold_project_ids": [101],
    "pooling_evidence": {
        "conditions_run": ["lexical", "dense", "hybrid", "hybrid_rerank"],
        "k": 10, "pooled_candidate_count": 3, "accepted": [101],
        "rejected_count": 2, "index_fingerprint": "be84cbad9182"},
    "reference_answer": "SWARMBUILD.",
}


def plan_file(tmp_path, text=PLAN):
    p = tmp_path / "plan.md"
    p.write_text(text, encoding="utf-8")
    return p


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records),
                    encoding="utf-8")
    return path


# --- the transcription-boundary entity guard -------------------------------

def test_html_entity_hits_finds_entities_with_context():
    from src.cli import html_entity_hits

    raw = json.dumps({"text": "Projects with cost &lt; 1M &amp; ERC funding"})
    hits = html_entity_hits(raw)
    assert len(hits) == 2
    assert hits[0].startswith("&lt; in:") and "cost &lt; 1M" in hits[0]
    assert hits[1].startswith("&amp; in:")
    # Numeric and hex references are entities too.
    assert html_entity_hits("a &#8211; b") and html_entity_hits("a &#x2013; b")


def test_html_entity_hits_is_quiet_on_legitimate_ampersands():
    from src.cli import html_entity_hits

    # A CORDIS title carrying a literal & (or an &-word) must never trip it.
    assert html_entity_hits('{"text": "R&D in health & food"}') == []
    assert html_entity_hits(json.dumps(SQL_A)) == []


# --- allocation table ------------------------------------------------------

def test_parse_allocation_reads_ladder_rows_and_totals(tmp_path):
    targets = parse_allocation(plan_file(tmp_path))
    assert targets["sql"] == {"L1": 7, "L2": 11, "L3": 5, "total": 23}
    assert targets["vector"]["L3"] == 7 and targets["hybrid"]["total"] == 23
    # Non-ladder rows state a total only; the "- spread -" cell is not a count.
    assert targets["ambiguous"] == {"total": 10}
    assert targets["adversarial"] == {"total": 14}
    assert targets["compositional"] == {"total": 3}
    assert "total" not in targets          # the **Total** row is not a route


def test_parse_allocation_refuses_to_guess(tmp_path):
    with pytest.raises(BatchError, match="Allocation"):
        parse_allocation(plan_file(tmp_path, "# Plan\n\nno table here\n"))


def test_parse_allocation_against_the_live_plan_doc():
    # Guards the real table's shape: the gap report reads it LIVE, so a
    # reformat that silently breaks parsing must fail here, not in a batch.
    targets = parse_allocation(ROOT / "horizon-scout.md")
    for route in ("sql", "vector", "hybrid"):
        assert set(targets[route]) == {"L1", "L2", "L3", "total"}
        assert all(isinstance(v, int) for v in targets[route].values())
    assert targets["adversarial"]["total"] > 0


# --- id assignment ---------------------------------------------------------

def test_next_ids_counts_bank_and_every_staged_file(tmp_path):
    bank = write_jsonl(tmp_path / "bank.jsonl",
                       [{**SQL_A, "question_id": "sql-03"},
                        {**HYB_A, "question_id": "hyb-01"}])
    drafts = tmp_path / "drafts"
    write_jsonl(drafts / "draft-bank-2026-07-24.jsonl",
                [{"question_id": "sql-07"}])
    write_jsonl(drafts / "draft-bank-2026-07-25.jsonl",
                [{"question_id": "hyb-04"}, {"question_id": "vec-02"}])
    assigned = next_ids({"sql": 2, "vector": 1, "hybrid": 1}, bank, drafts,
                           tmp_path / "archive")
    assert assigned == {"sql": ["sql-08", "sql-09"], "vector": ["vec-03"],
                        "hybrid": ["hyb-05"]}


def test_next_ids_starts_at_01_on_an_empty_bank(tmp_path):
    bank = write_jsonl(tmp_path / "bank.jsonl", [])
    assert next_ids({"sql": 1}, bank, tmp_path / "none",
                     tmp_path / "archive")["sql"] == ["sql-01"]


def test_next_ids_rejects_a_cell_it_cannot_name(tmp_path):
    bank = write_jsonl(tmp_path / "bank.jsonl", [])
    with pytest.raises(BatchError, match="ambiguous"):
        next_ids({"ambiguous": 1}, bank, tmp_path, tmp_path / "archive")


def test_next_ids_assigns_adversarial_ids(tmp_path):
    # ADV is a level, not a route, so it is a cell key of its own - an ADV
    # record's expected_route is a costume and must not drive its id.
    bank = write_jsonl(tmp_path / "bank.jsonl",
                       [SQL_A, {**SQL_A, "question_id": "adv-03",
                                "level": "ADV", "subtype": "zero-match"}])
    assigned = next_ids({"adversarial": 2, "sql": 1}, bank,
                        tmp_path / "none", tmp_path / "archive")
    assert assigned["adversarial"] == ["adv-04", "adv-05"]
    assert assigned["sql"] == ["sql-02"]


def _packet(path, slots):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"kind": "packet", "slots": slots}),
                    encoding="utf-8")
    return path


def test_next_ids_respects_a_packet_that_has_not_closed_out_yet(tmp_path):
    # A tab writes its draft file only at close-out, so between launch and
    # close-out the packet is the only record that its ids are spoken for.
    # Without this, a second run launched in that window reissues them.
    bank = write_jsonl(tmp_path / "bank.jsonl", [])
    drafts = tmp_path / "drafts"
    _packet(drafts / "batchK" / "packet.json",
            [{"question_id": "adv-01"}, {"question_id": "adv-02"},
             {"question_id": "adv-03"}])
    assert next_ids({"adversarial": 2}, bank, drafts,
                    tmp_path / "archive")["adversarial"] == ["adv-04", "adv-05"]


def test_packet_claims_frees_unused_parents_once_the_group_closes_out(tmp_path):
    drafts = tmp_path / "drafts"
    slots = [{"question_id": "adv-01",
              "parents": [{"twin_id": "sql-01"}, {"twin_id": "sql-02"},
                          {"twin_id": "sql-03"}]}]
    packet = _packet(drafts / "batchK" / "packet.json", slots)

    # In flight: all three are held, because the tab may fall back to any.
    assert packet_claims(drafts)[1] == {"sql-01", "sql-02", "sql-03"}

    # Closed out: only the one actually used stays claimed, and it is claimed
    # by the draft file rather than the packet.
    write_jsonl(packet.parent / "draft-bank-2026-08-04.jsonl",
                [{"question_id": "adv-01", "twin_id": "sql-02"}])
    assert packet_claims(drafts)[1] == set()
    assert packet_claims(drafts)[0] == {"adv-01"}
    bank = write_jsonl(tmp_path / "bank.jsonl", [])
    assert twinned_ids(bank, drafts) == {"sql-02"}


def test_packet_claims_survives_a_broken_packet(tmp_path):
    drafts = tmp_path / "drafts"
    (drafts / "bad").mkdir(parents=True)
    (drafts / "bad" / "packet.json").write_text("{not json", encoding="utf-8")
    _packet(drafts / "good" / "packet.json", [{"question_id": "adv-09"}])
    assert packet_claims(drafts)[0] == {"adv-09"}


# --- adversarial parents ---------------------------------------------------

def test_pick_parents_spreads_across_route_and_subtype(tmp_path):
    bank = write_jsonl(tmp_path / "bank.jsonl", [
        {**SQL_A, "question_id": "sql-01"},
        {**SQL_A, "question_id": "sql-02"},
        {**SQL_A, "question_id": "sql-03"},
        {**HYB_A, "question_id": "hyb-01"},
        {**VEC_A, "question_id": "vec-01"},
    ])
    picked = pick_parents(3, bank, tmp_path / "none")
    assert {r["expected_route"] for r in picked} == {"sql", "vector", "hybrid"}


def test_pick_parents_skips_the_already_twinned_and_the_excluded(tmp_path):
    bank = write_jsonl(tmp_path / "bank.jsonl", [
        {**SQL_A, "question_id": "sql-01"},
        {**SQL_A, "question_id": "sql-02"},
        {**SQL_A, "question_id": "sql-03"},
        # An ADV entry already claims sql-01 as its control.
        {**SQL_A, "question_id": "adv-01", "level": "ADV",
         "subtype": "zero-match", "twin_id": "sql-01"},
    ])
    drafts = tmp_path / "drafts"
    # A staged draft claims its twin the moment it is written, not at promotion.
    write_jsonl(drafts / "draft-bank-2026-08-04.jsonl",
                [{"question_id": "adv-02", "twin_id": "sql-02"}])
    ids = [r["question_id"] for r in pick_parents(5, bank, drafts)]
    assert ids == ["sql-03"]
    assert pick_parents(5, bank, drafts, exclude=("sql-03",)) == []


def test_pick_parents_never_proposes_an_adv_question(tmp_path):
    bank = write_jsonl(tmp_path / "bank.jsonl", [
        {**SQL_A, "question_id": "adv-01", "level": "ADV",
         "subtype": "zero-match"},
        {**SQL_A, "question_id": "sql-01"},
    ])
    ids = [r["question_id"] for r in pick_parents(5, bank, tmp_path / "none")]
    assert ids == ["sql-01"]


# --- gap report ------------------------------------------------------------

def test_gap_report_counts_filled_staged_and_target(tmp_path):
    bank = write_jsonl(tmp_path / "bank.jsonl", [SQL_A, HYB_A])
    drafts = tmp_path / "drafts"
    write_jsonl(drafts / "draft-bank-2026-07-24.jsonl",
                [{**SQL_A, "question_id": "sql-02"}])
    text = gap_report(bank, drafts, plan_file(tmp_path),
                      tmp_path / "archive")
    assert "| sql | 1+1/7 |" in text
    assert "1+0/6" in text            # hybrid L1
    assert "0+0/24" in text           # vector route total, nothing authored
    assert "compositional  0+0/3" in text
    assert "term_style hybrid  exact-term=1+0 paraphrase=0+0" in text
    assert "next free id per cell: sql=sql-03" in text
    assert "adversarial=adv-01" in text
    # Adversarial has moved out of the interactive-only block.
    assert "adversarial (level ADV, any costume route)   0+0/14" in text
    assert "adversarial parents available" in text


def test_gap_report_keeps_adv_subtypes_out_of_the_route_lines(tmp_path):
    # An ADV entry wears a costume route, so counting its subtype under that
    # route would report zero-match as a vector subtype.
    bank = write_jsonl(tmp_path / "bank.jsonl", [
        VEC_A,
        {**VEC_A, "question_id": "adv-01", "level": "ADV",
         "subtype": "zero-match", "gold_project_ids": []}])
    text = gap_report(bank, tmp_path / "none", plan_file(tmp_path),
                      tmp_path / "archive")
    assert "subtypes vector  identify=1+0" in text
    assert "subtypes ADV     zero-match=1+0" in text


def test_gap_report_ignores_already_promoted_staged_records(tmp_path):
    # A promoted batch's draft file stays on disk for the record. Counting its
    # records as "staged" would show a filled cell as half pending.
    bank = write_jsonl(tmp_path / "bank.jsonl", [SQL_A])
    drafts = tmp_path / "drafts"
    write_jsonl(drafts / "draft-bank-2026-07-24.jsonl", [SQL_A])   # promoted
    text = gap_report(bank, drafts, plan_file(tmp_path),
                      tmp_path / "archive")
    assert "Staged (undecided): 0 record(s)" in text
    assert "| sql | 1+0/7 |" in text


def _report(drafts, name, decisions):
    """A minimal review report in the exact format promote-drafts parses."""
    drafts.mkdir(parents=True, exist_ok=True)
    body = ["Draft-bank-file: eval/drafts/draft-bank-2026-07-24.jsonl", ""]
    for qid, approve in decisions.items():
        box = ("[x] APPROVE  [ ] REJECT" if approve
               else "[ ] APPROVE  [x] REJECT")
        body += [f"## {qid} - ACCEPTED", "", f"Decision: {box}", ""]
    path = drafts / name
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def test_gap_report_ignores_rejected_staged_records(tmp_path):
    # A REJECTED record also stays in its draft file. Counting it as staged
    # would tell the next batch a cell is covered that it still has to fill.
    bank = write_jsonl(tmp_path / "bank.jsonl", [SQL_A])
    drafts = tmp_path / "drafts"
    write_jsonl(drafts / "draft-bank-2026-07-24.jsonl",
                [{**HYB_A, "question_id": "hyb-09"}])
    _report(drafts, "draft-report-2026-07-24.md", {"hyb-09": False})
    text = gap_report(bank, drafts, plan_file(tmp_path),
                      tmp_path / "archive")
    assert "Staged (undecided): 0 record(s)" in text
    assert "0+0/6" in text                     # hybrid L1 still unfilled
    # but the id stays taken, so the counter never reuses it
    assert next_ids({"hybrid": 1}, bank, drafts,
                    tmp_path / "archive")["hybrid"] == ["hyb-10"]


def test_gap_report_still_counts_an_approved_but_unpromoted_record(tmp_path):
    # Ticked APPROVE but promote-drafts not yet run: genuinely pending.
    bank = write_jsonl(tmp_path / "bank.jsonl", [SQL_A])
    drafts = tmp_path / "drafts"
    write_jsonl(drafts / "draft-bank-2026-07-24.jsonl",
                [{**HYB_A, "question_id": "hyb-09"}])
    _report(drafts, "draft-report-2026-07-24.md", {"hyb-09": True})
    assert "Staged (undecided): 1 record(s)" in gap_report(
        bank, drafts, plan_file(tmp_path), tmp_path / "archive")


def test_gap_report_treats_an_unticked_report_as_undecided(tmp_path):
    # A report mid-review has empty boxes; that must not crash and must not
    # be read as a rejection.
    bank = write_jsonl(tmp_path / "bank.jsonl", [SQL_A])
    drafts = tmp_path / "drafts"
    write_jsonl(drafts / "draft-bank-2026-07-24.jsonl",
                [{**HYB_A, "question_id": "hyb-09"}])
    (drafts / "draft-report-2026-07-24.md").write_text(
        "## hyb-09 - ACCEPTED\n\nDecision: [ ] APPROVE  [ ] REJECT\n",
        encoding="utf-8")
    assert "Staged (undecided): 1 record(s)" in gap_report(
        bank, drafts, plan_file(tmp_path), tmp_path / "archive")


# --- archived questions are decided, and their ids stay taken --------------

def _archive(tmp_path, *records, name="bank-trimmed-2026-08-03.jsonl"):
    """An archive file in archive.py's envelope shape."""
    d = tmp_path / "archive"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(
        "".join(json.dumps({"archived_at": "2026-08-03T00:00:00+00:00",
                            "archived_reason": "trimmed to target",
                            "archived_from": "bank.jsonl",
                            "record": r}) + "\n" for r in records),
        encoding="utf-8")
    return d


def test_archived_ids_reads_the_envelope_and_ignores_bare_records(tmp_path):
    d = _archive(tmp_path, {**HYB_A, "question_id": "hyb-09"})
    # bank_pilot.jsonl is the pre-skill smoke set: bare old-schema records with
    # no envelope. It must not contribute ids, and must not crash the read.
    (d / "bank_pilot.jsonl").write_text(
        json.dumps({"question_id": "vec-99"}) + "\nnot json\n", encoding="utf-8")
    assert archived_ids(d) == {"hyb-09"}


def test_archived_ids_are_never_handed_to_a_new_question(tmp_path):
    """The id an archived question used stays taken forever - otherwise a new
    vec-09 is authored while a different vec-09 sits in the archive."""
    bank = write_jsonl(tmp_path / "bank.jsonl", [HYB_A])          # hyb-01
    archive = _archive(tmp_path, {**HYB_A, "question_id": "hyb-09"})
    assert next_ids({"hybrid": 1}, bank, tmp_path / "none",
                    archive)["hybrid"] == ["hyb-10"]


def test_gap_report_does_not_count_an_archived_questions_staged_twin(tmp_path):
    """A trimmed question's draft file stays on disk. Without the archive it
    stops being 'banked' and reappears as pending work the batch must finish -
    the same defect already fixed for promoted and for rejected records."""
    bank = write_jsonl(tmp_path / "bank.jsonl", [SQL_A])
    drafts = tmp_path / "drafts"
    write_jsonl(drafts / "draft-bank-2026-07-24.jsonl",
                [{**HYB_A, "question_id": "hyb-09"}])
    archive = _archive(tmp_path, {**HYB_A, "question_id": "hyb-09"})

    text = gap_report(bank, drafts, plan_file(tmp_path), archive)

    assert "Staged (undecided): 0 record(s)" in text
    assert "| hybrid | 0+0/6 |" in text


def test_gap_report_refuses_a_half_parsed_bank(tmp_path):
    bank = tmp_path / "bank.jsonl"
    bank.write_text('{"question_id": "sql-01"}\nnot json\n', encoding="utf-8")
    with pytest.raises(BatchError, match="invalid JSON"):
        gap_report(bank, tmp_path / "none", plan_file(tmp_path),
                   tmp_path / "archive")


# --- cross-check -----------------------------------------------------------

def kinds(flags, level="FLAG"):
    return [f.kind for f in flags if f.level == level]


def test_crosscheck_flags_a_near_duplicate_within_the_batch():
    twin = {**SQL_A, "question_id": "sql-02",
            "text": "How many projects have been terminated?"}
    flags = crosscheck([SQL_A, twin], [])
    dupes = [f for f in flags if f.kind == "NEAR-DUPLICATE"]
    assert len(dupes) == 1
    assert "sql-01 vs sql-02 (batch)" in dupes[0].detail


def test_crosscheck_flags_a_near_duplicate_of_the_promoted_bank():
    # The critic's own NEAR-DUPLICATE check reads the PROMOTED bank, so this
    # is the only place a batch-vs-bank collision can surface.
    banked = {**SQL_A, "question_id": "sql-77"}
    flags = crosscheck([{**SQL_A, "question_id": "sql-01"}], [banked])
    assert any("sql-77 (bank)" in f.detail for f in flags
               if f.kind == "NEAR-DUPLICATE")


def test_crosscheck_flags_shared_gold_and_entities_and_axes():
    other = {**HYB_A, "question_id": "hyb-05",
             "text": "Which project applied GRAPHENE-AMR coatings in Sweden?",
             "reference_answer": "GRAPHENE-AMR again."}
    flags = crosscheck([HYB_A, other], [])
    assert "GOLD-OVERLAP" in kinds(flags)
    assert any("GRAPHENE-AMR" in f.detail for f in flags
               if f.kind == "ENTITY-COLLISION")
    assert any("country" in f.detail for f in flags
               if f.kind == "AXIS-COLLISION")


def test_crosscheck_does_not_flag_an_adv_question_against_its_own_parent():
    # Resembling the parent is the design: minimal edit distance is what makes
    # the pair a control. Flagging it would push drafters away from the parent.
    adv = {"question_id": "adv-01", "level": "ADV", "subtype": "zero-match",
           "expected_route": "sql", "twin_id": "sql-01",
           "text": "How many projects were suspended?",
           "gold_project_ids": [], "reference_answer": "None were."}
    assert [f for f in crosscheck([adv], [SQL_A])
            if f.kind == "NEAR-DUPLICATE"] == []
    # ...but it is still checked against everything else.
    other = {**SQL_A, "question_id": "sql-09",
             "text": "How many projects were suspended?"}
    assert any("sql-09" in f.detail for f in crosscheck([adv], [SQL_A, other])
               if f.kind == "NEAR-DUPLICATE")


def test_crosscheck_does_not_flag_entities_an_adv_question_inherits():
    # adv-01 mentions GRAPHENE-AMR only because its parent does. Two questions
    # sharing an entity because one is a copy of the other says nothing.
    adv = {"question_id": "adv-01", "level": "ADV", "subtype": "zero-match",
           "expected_route": "hybrid", "twin_id": "hyb-04",
           "text": "Which Finnish project applied GRAPHENE-AMR coatings?",
           "gold_project_ids": [], "reference_answer": "None did."}
    assert [f for f in crosscheck([adv], [HYB_A])
            if f.kind == "ENTITY-COLLISION"] == []

    # A THIRD user of the entity is a real collision, and the pair is shown.
    third = {**HYB_A, "question_id": "hyb-08",
             "text": "How does GRAPHENE-AMR coat its surfaces?",
             "reference_answer": "With graphene oxide."}
    detail = [f.detail for f in crosscheck([adv], [HYB_A, third])
              if f.kind == "ENTITY-COLLISION" and "GRAPHENE-AMR" in f.detail]
    assert detail and "hyb-08" in detail[0] and "hyb-04" in detail[0]


def test_crosscheck_ignores_the_currency():
    a = {**SQL_A, "question_id": "sql-20",
         "reference_answer": "It received EUR 1.2 million."}
    b = {**SQL_A, "question_id": "sql-21", "text": "What did BETA cost?",
         "reference_answer": "EUR 3.4 million."}
    assert not [f for f in crosscheck([a], [b])
                if f.kind == "ENTITY-COLLISION" and "EUR" in f.detail]


def test_crosscheck_flags_two_adv_questions_sharing_one_parent():
    a = {"question_id": "adv-01", "level": "ADV", "subtype": "zero-match",
         "expected_route": "sql", "twin_id": "sql-01",
         "text": "How many projects were suspended?", "gold_project_ids": []}
    b = {**a, "question_id": "adv-02",
         "text": "What was the total funding for withdrawn grants?"}
    flags = crosscheck([a, b], [])
    assert any("both derived from sql-01" in f.detail for f in flags
               if f.kind == "TWIN-COLLISION")


def test_crosscheck_is_quiet_on_a_well_spread_batch():
    flags = crosscheck([SQL_A, HYB_A], [])
    assert kinds(flags) == []
    assert "SPREAD" in kinds(flags, level="INFO")


# --- the journal and the writer -------------------------------------------

HEADER = {
    "kind": "batch", "date": "2026-07-25",
    "order": "2 sql slots (L1 aggregate, L2 join-lookup)",
    "budgets": {"candidates_per_slot": 3, "passes_budget": 6},
    "versions": {
        "corpus_profile": {"version": "cp3", "content_hash": "f33f150ff077"},
        "schema_docs": {"version": "sd2", "content_hash": "f8c001e8cc8f"},
        "bank_brief": {"version": "bb1", "content_hash": "aaaabbbbcccc"},
        "index": {"fingerprint": "be84cbad9182"}},
}


def slot(qid, status, record=None, **extra):
    line = {"kind": "slot", "question_id": qid, "status": status,
            "terminal_reason": None,
            "cell": {"route": "sql", "level": "L1", "subtype": "aggregate",
                     "term_style": None},
            "candidates": [{"id": "sql-cand-1", "topic": "terminated status"}],
            "candidate_index": 0,
            "budget": {"passes_spent": 1, "passes_budget": 6,
                       "fix_rounds_this_candidate": 0},
            "record": record, "evidence": "executed: 1,204 rows",
            "why_good": "clean-route L1 baseline, one scoreable reading",
            "checklist": "EXECUTED-GOLD PASS ...", "findings": [],
            "defect_classes_seen": [], "judge_decisions": [], "history": []}
    line.update(extra)
    return line


def journal_file(tmp_path, lines, name="draft-batch-journal-2026-07-25.jsonl"):
    return write_jsonl(tmp_path / name, lines)


def test_load_journal_keeps_the_latest_line_per_slot(tmp_path):
    path = journal_file(tmp_path, [
        HEADER,
        slot("sql-01", "DRAFTING"),
        slot("sql-02", "DRAFTING"),
        slot("sql-01", "ACCEPTED", record=SQL_A)])
    journal = load_journal(path)
    assert journal.order == ["sql-01", "sql-02"]        # first-seen order
    assert journal.slots["sql-01"]["status"] == "ACCEPTED"
    assert journal.header["order"].startswith("2 sql slots")


def test_load_journal_validates_the_envelope_not_the_record(tmp_path):
    # A record that the bank validator would reject is FINE mid-run.
    path = journal_file(tmp_path, [HEADER,
                                   slot("sql-01", "DRAFTING",
                                        record={"half": "finished"})])
    assert load_journal(path).slots["sql-01"]["record"] == {"half": "finished"}

    bad = journal_file(tmp_path, [HEADER, {"kind": "slot", "status": "NOPE"}],
                       name="bad.jsonl")
    with pytest.raises(BatchError) as ei:
        load_journal(bad)
    assert "question_id" in str(ei.value) and "status" in str(ei.value)


def test_load_journal_requires_a_batch_header(tmp_path):
    path = journal_file(tmp_path, [slot("sql-01", "ACCEPTED", record=SQL_A)],
                        name="headerless.jsonl")
    with pytest.raises(BatchError, match="batch header"):
        load_journal(path)


# --- journal-append: the transition bookkeeping, in code -------------------

def slot_payload(**extra):
    """A first-transition payload: everything slot() carries except the
    envelope fields journal-append owns."""
    payload = {k: v for k, v in slot("ignored", "ignored").items()
               if k not in ("kind", "question_id", "status")}
    payload.update(extra)
    return payload


def test_journal_append_merges_over_the_latest_line(tmp_path):
    path = journal_file(tmp_path, [HEADER])
    journal_append(path, "sql-01", "DRAFTING", slot_payload())
    # The second transition passes ONLY what changed; everything else is
    # carried forward, because latest-line-wins needs every line complete.
    journal_append(path, "sql-01", "ACCEPTED", {"record": SQL_A})
    journal = load_journal(path)
    line = journal.slots["sql-01"]
    assert line["status"] == "ACCEPTED"
    assert line["record"] == SQL_A
    assert line["cell"]["route"] == "sql"           # carried forward
    assert line["evidence"] == "executed: 1,204 rows"
    # Two slot lines on disk: append, never rewrite.
    assert len(path.read_text(encoding="utf-8").splitlines()) == 3


def test_journal_append_enforces_the_envelope(tmp_path):
    path = journal_file(tmp_path, [HEADER])
    # A first transition with no cell is refused at append time, not
    # discovered by write-batch at close-out.
    with pytest.raises(BatchError, match="cell"):
        journal_append(path, "sql-01", "DRAFTING", {"candidates": []})
    with pytest.raises(BatchError, match="status"):
        journal_append(path, "sql-01", "NOPE", slot_payload())
    with pytest.raises(BatchError, match="question_id"):
        journal_append(path, "", "DRAFTING", slot_payload())
    # Nothing was appended by any refusal.
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_journal_append_keeps_the_record_opaque(tmp_path):
    # A schema-invalid record mid-run is FINE - the envelope is typed, the
    # payload is not. That distinction is deliberate.
    path = journal_file(tmp_path, [HEADER])
    journal_append(path, "sql-01", "DRAFTING",
                   slot_payload(record={"half": "finished"}))
    assert load_journal(path).slots["sql-01"]["record"] == {
        "half": "finished"}


def test_journal_append_refuses_a_conflicting_payload_envelope(tmp_path):
    path = journal_file(tmp_path, [HEADER])
    with pytest.raises(BatchError, match="question_id"):
        journal_append(path, "sql-01", "DRAFTING",
                       slot_payload(question_id="sql-02"))


def test_journal_append_requires_a_header(tmp_path):
    path = journal_file(tmp_path, [], name="empty.jsonl")
    with pytest.raises(BatchError, match="batch header"):
        journal_append(path, "sql-01", "DRAFTING", slot_payload())


def test_journal_append_round_trips_through_write_batch(tmp_path):
    """The fixture replay: a journal built by journal-append must produce the
    two canonical outputs byte-identical to a hand-built journal's."""
    by_hand = journal_file(tmp_path / "hand", [
        HEADER,
        slot("sql-01", "DRAFTING"),
        slot("sql-01", "ACCEPTED", record=SQL_A)])
    appended = journal_file(tmp_path / "appended", [HEADER])
    journal_append(appended, "sql-01", "DRAFTING", slot_payload())
    journal_append(appended, "sql-01", "ACCEPTED", {"record": SQL_A})
    res_hand = write_batch(by_hand, bank_path=tmp_path / "no-bank.jsonl")
    res_app = write_batch(appended, bank_path=tmp_path / "no-bank.jsonl")
    assert (res_hand.draft_file.read_bytes()
            == res_app.draft_file.read_bytes())
    assert (res_hand.report_file.read_bytes()
            == res_app.report_file.read_bytes())


def test_write_batch_stages_accepted_records_and_accounts_for_every_slot(
        tmp_path):
    bank = write_jsonl(tmp_path / "bank.jsonl", [])
    path = journal_file(tmp_path, [
        HEADER,
        slot("sql-01", "ACCEPTED", record=SQL_A),
        slot("sql-02", "FAILED", terminal_reason="cross-candidate stop rule "
             "(MISSED-GOLD killed both candidates)",
             history=["candidate 1 abandoned", "candidate 2 abandoned"],
             findings=[{"round": 1, "class": "MISSED-GOLD", "severity": "HIGH",
                        "claim": "two projects outside gold satisfy it",
                        "evidence": "run_sql LIKE sweep, ids 42 and 77",
                        "ruling": "UPHELD", "ruling_why": "evidence stands"}]),
        slot("hyb-09", "BLOCKED", terminal_reason="retrieval servers down"),
    ])
    res = write_batch(path, bank_path=bank)

    assert res.accepted == ["sql-01"] and res.failed == ["sql-02"]
    assert res.blocked == ["hyb-09"]
    staged = [json.loads(l) for l in
              res.draft_file.read_text(encoding="utf-8").splitlines()]
    assert staged == [SQL_A]                       # byte-identical record
    report = res.report_file.read_text(encoding="utf-8")
    assert res.draft_file.name in report
    assert "Tally: 1 accepted / 1 failed" in report and "1 blocked" in report
    assert "cp3 f33f150ff077" in report and "bank_brief: bb1" in report
    # Every slot accounted for, and only the accepted one gets a decision box.
    for qid in ("sql-01", "sql-02", "hyb-09"):
        assert f"| {qid} |" in report
    assert report.count("Decision: [ ] APPROVE  [ ] REJECT") == 1
    assert "MISSED-GOLD" in report and "UPHELD" in report
    assert "cross-candidate stop rule" in report


def test_write_batch_stages_an_adversarial_record_and_names_its_parent(
        tmp_path):
    adv = {"question_id": "adv-01", "text": "How many projects were suspended?",
           "expected_route": "sql", "level": "ADV", "subtype": "zero-match",
           "twin_id": "sql-01", "gold_project_ids": [],
           "absence_evidence": [
               {"sql": "SELECT id FROM project WHERE status = 'SUSPENDED'",
                "expect": "zero", "key_result": "no suspended projects"}],
           "reference_answer": "No project carries a suspended status."}
    bank = write_jsonl(tmp_path / "bank.jsonl", [SQL_A])
    path = journal_file(tmp_path, [
        HEADER,
        slot("adv-01", "ACCEPTED", record=adv,
             cell={"route": "sql", "level": "ADV", "subtype": "zero-match"},
             candidates=[])])
    res = write_batch(path, bank_path=bank)

    assert res.accepted == ["adv-01"]
    staged = [json.loads(l) for l in
              res.draft_file.read_text(encoding="utf-8").splitlines()]
    assert staged == [adv]                         # twin_id and proof survive
    report = res.report_file.read_text(encoding="utf-8")
    # The parent is what a human approving the pair needs to see.
    assert "twin of sql-01" in report
    assert "sql/ADV/zero-match" in report


def test_write_batch_refuses_to_overwrite_and_honours_a_suffix(tmp_path):
    bank = write_jsonl(tmp_path / "bank.jsonl", [])
    path = journal_file(tmp_path, [HEADER,
                                   slot("sql-01", "ACCEPTED", record=SQL_A)])
    first = write_batch(path, bank_path=bank)
    with pytest.raises(BatchError, match="refusing to overwrite"):
        write_batch(path, bank_path=bank)
    second = write_batch(path, bank_path=bank, suffix="-2")
    assert second.draft_file.name.endswith("-2.jsonl")
    assert second.report_file.name.endswith("-2.md")
    assert first.draft_file.exists()


def test_write_batch_refuses_an_accepted_slot_with_no_evidence(tmp_path):
    bank = write_jsonl(tmp_path / "bank.jsonl", [])
    path = journal_file(tmp_path, [
        HEADER, slot("sql-01", "ACCEPTED", record=SQL_A, evidence="")])
    with pytest.raises(BatchError, match="no evidence"):
        write_batch(path, bank_path=bank)
    assert not list(tmp_path.glob("draft-bank-*.jsonl"))


def test_write_batch_refuses_a_slot_whose_record_id_disagrees(tmp_path):
    bank = write_jsonl(tmp_path / "bank.jsonl", [])
    path = journal_file(tmp_path, [
        HEADER, slot("sql-01", "ACCEPTED",
                     record={**SQL_A, "question_id": "sql-99"})])
    with pytest.raises(BatchError, match="must agree"):
        write_batch(path, bank_path=bank)


def test_written_report_round_trips_through_promote_drafts(tmp_path):
    """The contract test: writer output -> ticked by hand -> promote-drafts."""
    bank = write_jsonl(tmp_path / "bank.jsonl", [])
    path = journal_file(tmp_path, [
        HEADER,
        slot("sql-01", "ACCEPTED", record=SQL_A),
        slot("hyb-04", "ACCEPTED", record=HYB_A,
             cell={"route": "hybrid", "level": "L1",
                   "subtype": "filter-read", "term_style": "exact-term"}),
        slot("sql-02", "FAILED", terminal_reason="budget exhausted")])
    res = write_batch(path, bank_path=bank)

    # The human gate: tick APPROVE on one, REJECT on the other.
    report = res.report_file.read_text(encoding="utf-8")
    head, _, tail = report.partition("Decision: [ ] APPROVE  [ ] REJECT")
    report = (head + "Decision: [x] APPROVE  [ ] REJECT"
              + tail.replace("Decision: [ ] APPROVE  [ ] REJECT",
                             "Decision: [ ] APPROVE  [x] REJECT", 1))
    res.report_file.write_text(report, encoding="utf-8")

    result = promote(res.report_file, bank)
    assert result.promoted == ["sql-01"] and result.rejected == ["hyb-04"]
    assert [json.loads(l)["question_id"] for l in
            bank.read_text(encoding="utf-8").splitlines()] == ["sql-01"]
