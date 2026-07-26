"""/explore-corpus's deterministic nodes: the frontier, the typed journal,
exhaustive evidence verification, the cross-check, and the profile writer.

Two load-bearing tests here. `verify_evidence` must catch a recorded number
that no longer reproduces - that is the whole reason exploration stopped
sampling two queries per section. And `write_profile` must GROW the profile:
every byte an earlier run wrote is still there afterwards, because drafting
sessions may already have consumed it.

No running servers: everything below is DuckDB and text.
"""

import json

import duckdb
import pytest

from src.eval.explore import (Bucket, ExploreError, connect, crosscheck,
                              frontier_counters, load_journal, next_ids_for,
                              partition_buckets, profile_candidates,
                              read_profile, seed_standard, verify_evidence,
                              write_profile)

# --------------------------------------------------------------------------
# Fixtures: a six-project corpus with a bucket of every shape
# --------------------------------------------------------------------------

VOCAB = [
    (1, "natural sciences/biological sciences/ecology", "ecology"),
    (2, "natural sciences/biological sciences/genetics", "genetics"),
    (3, "social sciences/sociology/urban sociology", "urban sociology"),
    (4, "humanities/arts/music", "music"),
    (5, "natural sciences", ""),              # top-level-only path
    # project 6 has no euroSciVoc row at all -> the unclassified bucket
]

BANK = [
    {"question_id": "vec-01", "text": "Which project studies city life?",
     "expected_route": "vector", "level": "L1", "gold_project_ids": [3]},
    # No gold ids: a SQL question cannot be traced to a bucket, by design.
    {"question_id": "sql-01", "text": "How many projects are there?",
     "expected_route": "sql", "level": "L1",
     "gold_sql": "SELECT COUNT(*) FROM project"},
]

PROFILE = """# Horizon Scout corpus profile

## Header

- **Version:** cp3
- **Generated:** 2026-07-24
- **Corpus fingerprint:** 6 projects.

**Run log** (scope, cost, frontier movement - one line per run):

- cp3 (2026-07-24) scope `"structural"`: no exploration subagents.

## Frontier

| bucket | projects | status | map | seeds | bank |
|---|---|---|---|---|---|
| natural sciences / biological sciences | 2 | unexplored | - | vector-01 | - |
| social sciences / sociology | 1 | mined | - | - | vec-01 |
| humanities / arts | 1 | mapped | m01 | - | - |
| natural sciences / (top-level only) | 1 | unexplored | - | - | - |
| (unclassified - no euroSciVoc row) | 1 | unexplored | - | - | - |

`mapped 1/5 | mined 1/5 | unexplored 3/5`

Trailing prose after the counter.

## Corpus map

Format notes here.

- region: m01
  bucket: humanities / arts
  slice: euroSciVocPath LIKE 'humanities/arts%'
  about: Music-technology work.
  read: 4
  mapped: cp2

## Structural findings

- id: sf-01
  kind: value-inventory
  claim: paths carry no leading slash.
  evidence: `SELECT 1` -> 1

## Distributions

Not yet explored (scoped run "earlier", 2026-07-23).

## SQL

Not yet explored (scoped run "earlier", 2026-07-23).

## Vector

Preamble for the vector section.

- id: vector-01
  topic: A project about the ecology of boreal lake fungi.
  recommend: route=vector level=L1 subtype=identify
  bucket: natural sciences / biological sciences
  evidence: `SELECT 1` -> 1
  axes: branch=biological-sciences satisfying=1
  why: vivid single-project seed.

## Hybrid

Not yet explored (scoped run "earlier", 2026-07-23).

## Adversarial

Not yet explored (scoped run "earlier", 2026-07-23).

## Ambiguous

Not yet explored (scoped run "earlier", 2026-07-23).

## Coverage notes

Nothing recorded yet.
"""

CONFIG = 'CORPUS_PROFILE_VERSION = "cp3"\nOTHER = 1\n'


@pytest.fixture
def corpus(tmp_path):
    """A tiny CORDIS-shaped database plus the profile, bank and config the
    deterministic nodes read."""
    db = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db))
    con.execute(
        "CREATE TABLE project (id BIGINT, acronym VARCHAR, title VARCHAR, "
        "objective VARCHAR, startDate DATE, ecMaxContribution DECIMAL(18,2), "
        "fundingScheme VARCHAR, status VARCHAR)")
    con.execute(
        "INSERT INTO project VALUES "
        "(1,'ALPHA','Alpha','Lake fungi objective','2016-01-01',100000,"
        "'MSCA-IF','CLOSED'),"
        "(2,'BETA','Beta','Genome objective','2017-01-01',200000,'RIA',"
        "'CLOSED'),"
        "(3,'GAMMA','Gamma','Urban life objective','2018-01-01',300000,'RIA',"
        "'SIGNED'),"
        "(4,'DELTA','Delta','Music objective','2019-01-01',400000,'ERC-STG',"
        "'CLOSED'),"
        "(5,'EPS','Eps','Broad objective','2020-01-01',500000,'CSA','CLOSED'),"
        # No objective and no report row: the textless case.
        "(6,'ZETA','Zeta',NULL,'2021-01-01',NULL,NULL,'CLOSED')")
    con.execute(
        "CREATE TABLE report_text (projectID BIGINT, title VARCHAR, "
        "teaser VARCHAR, summary VARCHAR, workPerformed VARCHAR, "
        "finalResults VARCHAR)")
    con.execute(
        "INSERT INTO report_text VALUES "
        "(1,'r','teaser one','summary one','work','results'),"
        "(2,'r','teaser two','summary two','work','results'),"
        "(3,'r','teaser three','summary three','work','results')")
    con.execute(
        "CREATE TABLE euroscivoc (projectID BIGINT, euroSciVocPath VARCHAR, "
        "euroSciVocTitle VARCHAR)")
    con.executemany("INSERT INTO euroscivoc VALUES (?, ?, ?)", VOCAB)
    con.close()

    profile = tmp_path / "corpus_profile.md"
    profile.write_text(PROFILE, encoding="utf-8")
    bank = tmp_path / "bank.jsonl"
    bank.write_text("\n".join(json.dumps(r) for r in BANK), encoding="utf-8")
    config = tmp_path / "config.py"
    config.write_text(CONFIG, encoding="utf-8")
    log = tmp_path / "draft_mcp.jsonl"
    log.write_text(
        json.dumps({"ts": "2026-07-25T10:00:00", "tool": "run_sql"}) + "\n"
        + json.dumps({"ts": "2026-07-25T10:01:00", "tool": "run_sql"}) + "\n"
        # `found` is the field the MCP server actually logs.
        + json.dumps({"ts": "2026-07-25T10:02:00", "tool": "get_project_text",
                      "project_ids": [1, 2, 3], "ok": True, "found": 3}) + "\n"
        # Before the run window: must not be counted.
        + json.dumps({"ts": "2026-07-20T10:00:00", "tool": "run_sql"}) + "\n",
        encoding="utf-8")
    return {"db": db, "profile": profile, "bank": bank, "config": config,
            "log": log, "dir": tmp_path}


def journal_file(tmp_path, *slices, header=None):
    path = tmp_path / "journal-2026-07-25.jsonl"
    lines = [header or {"kind": "run", "date": "2026-07-25", "scope": "map=2",
                        "started": "2026-07-25T09:00:00"}]
    lines += list(slices)
    path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return path


def a_slice(**overrides):
    record = {
        "kind": "slice", "slice_id": "s01", "status": "VERIFIED",
        "mode": "topical",
        "buckets": ["natural sciences / biological sciences"],
        "map_entry": {
            "bucket": "natural sciences / biological sciences",
            "slice": "euroSciVocPath LIKE 'natural sciences/biological%'",
            "size": "2 projects",
            "about": "Two fellowships on lake fungi and on genome assembly; "
                     "both read as field-collection work rather than theory.",
            "texture": "Report text present for both; the tag words do not "
                       "appear in the objectives.",
            "read": [1, 2],
            "read_first": [1, 2],
            "good_for": "vector L2 comparison",
            "thin_for": "SQL, no distinctive column",
            "evidence": {
                "sql": "SELECT COUNT(DISTINCT projectID) FROM euroscivoc "
                       "WHERE euroSciVocPath LIKE "
                       "'natural sciences/biological sciences%'",
                "key_result": "2 projects"}},
        "candidates": [{
            "id": "vector-02",
            "topic": "A pair of projects reading DNA out of field samples.",
            "recommend": "route=vector level=L2 subtype=comparison",
            "bucket": "natural sciences / biological sciences",
            "satisfying_count": 2,
            "topic_filter": "p.objective ILIKE '%fungi%' "
                            "OR p.objective ILIKE '%genome%'",
            "evidence": [{
                "sql": "SELECT COUNT(*) AS n FROM project WHERE "
                       "objective ILIKE '%fungi%' OR objective ILIKE '%genome%'",
                "key_result": "2 projects"}],
            "axes": "branch=biological-sciences satisfying=2",
            "why": "Two members, distinct methods, same habitat."}],
        "findings": [],
        "short": None}
    record.update(overrides)
    return record


# --------------------------------------------------------------------------
# The frontier
# --------------------------------------------------------------------------

def test_frontier_derives_status_from_the_map_and_the_bank(corpus):
    from src.eval.explore import build_frontier
    from src.eval.batch import read_records

    con = connect(corpus["db"])
    try:
        buckets = build_frontier(con, read_profile(corpus["profile"]),
                                 read_records(corpus["bank"]))
    finally:
        con.close()
    by_label = {b.label: b for b in buckets}

    assert len(buckets) == 5
    # mined: traced through gold_project_ids -> euroscivoc, not asserted.
    assert by_label["social sciences / sociology"].status == "mined"
    assert by_label["social sciences / sociology"].bank == ["vec-01"]
    # mapped: a Corpus map entry exists, so the map is the source of truth.
    assert by_label["humanities / arts"].status == "mapped"
    assert by_label["humanities / arts"].map_id == "m01"
    assert by_label["natural sciences / biological sciences"].status \
        == "unexplored"
    # Both shapes of bucket that are not a plain branch/field pair exist.
    assert by_label["natural sciences / (top-level only)"].projects == 1
    assert by_label["(unclassified - no euroSciVoc row)"].projects == 1


def test_a_sql_question_without_gold_ids_mines_nothing(corpus):
    from src.eval.explore import build_frontier
    from src.eval.batch import read_records

    con = connect(corpus["db"])
    try:
        buckets = build_frontier(con, read_profile(corpus["profile"]),
                                 read_records(corpus["bank"]))
    finally:
        con.close()
    assert not any("sql-01" in b.bank for b in buckets)


def test_counter_line_partitions_the_buckets():
    buckets = [Bucket("a", 1, "mapped"), Bucket("b", 1, "mined"),
               Bucket("c", 1, "unexplored"), Bucket("d", 1, "unexplored")]
    assert frontier_counters(buckets) == \
        "`mapped 1/4 | mined 1/4 | unexplored 2/4`"


def test_seeds_union_carried_history_with_current_candidates(corpus):
    from src.eval.explore import build_frontier
    from src.eval.batch import read_records

    con = connect(corpus["db"])
    try:
        buckets = build_frontier(con, read_profile(corpus["profile"]),
                                 read_records(corpus["bank"]))
    finally:
        con.close()
    bio = next(b for b in buckets
               if b.label == "natural sciences / biological sciences")
    # vector-01 is recorded in the frontier row AND states the bucket itself:
    # the union must not double it.
    assert bio.seeds == ["vector-01"]


def test_next_ids_continue_and_never_restart(corpus):
    ids = next_ids_for(read_profile(corpus["profile"]))
    assert ids["vector"] == 1 and ids["map"] == 1 and ids["finding"] == 1
    assert ids["hybrid"] == 0 and ids["sql"] == 0


def test_partition_is_largest_first_and_chunked():
    buckets = [Bucket(f"b{i}", 100 - i) for i in range(7)]
    buckets[0].status = "mined"          # already been there
    groups = partition_buckets(buckets, count=4, per_slice=3)
    assert [b.label for g in groups for b in g] == ["b1", "b2", "b3", "b4"]
    assert [len(g) for g in groups] == [3, 1]


def test_seed_standard_reads_the_numbered_brief_heading():
    body = seed_standard()
    assert "Seeds" in body and "survivors" in body


def test_seed_standard_refuses_a_brief_without_one(tmp_path):
    brief = tmp_path / "bank_brief.md"
    brief.write_text("# Brief\n\n## 1. Something else\n\nprose\n",
                     encoding="utf-8")
    with pytest.raises(ExploreError, match="no '## Seeds' section"):
        seed_standard(brief)


# --------------------------------------------------------------------------
# The journal envelope
# --------------------------------------------------------------------------

def test_journal_latest_line_per_slice_wins(corpus):
    path = journal_file(corpus["dir"],
                        a_slice(status="RETURNED"),
                        a_slice(status="VERIFIED"),
                        a_slice(slice_id="s02", status="FAILED"))
    journal = load_journal(path)
    assert journal.order == ["s01", "s02"]
    assert journal.slices["s01"]["status"] == "VERIFIED"


@pytest.mark.parametrize("bad, message", [
    ({"kind": "nonsense", "slice_id": "s01"}, "kind must be"),
    ({"kind": "slice", "slice_id": "", "status": "VERIFIED",
      "mode": "topical", "buckets": []}, "slice_id must be"),
    ({"kind": "slice", "slice_id": "s01", "status": "DONE",
      "mode": "topical", "buckets": []}, "status must be"),
    ({"kind": "slice", "slice_id": "s01", "status": "VERIFIED",
      "mode": "wandering", "buckets": []}, "mode must be"),
    ({"kind": "slice", "slice_id": "s01", "status": "VERIFIED",
      "mode": "topical"}, "buckets must be a list"),
])
def test_journal_envelope_is_validated_loudly(corpus, bad, message):
    path = journal_file(corpus["dir"], bad)
    with pytest.raises(ExploreError, match=message):
        load_journal(path)


def test_journal_without_a_header_is_refused(corpus):
    path = corpus["dir"] / "journal-2026-07-25.jsonl"
    path.write_text(json.dumps(a_slice()), encoding="utf-8")
    with pytest.raises(ExploreError, match="no line 0 run header"):
        load_journal(path)


# --------------------------------------------------------------------------
# verify-evidence: exhaustive, not sampled
# --------------------------------------------------------------------------

def checks_for(corpus, *slices):
    journal = load_journal(journal_file(corpus["dir"], *slices))
    con = connect(corpus["db"])
    try:
        return verify_evidence(journal, con)
    finally:
        con.close()


def status_of(checks, name_fragment):
    return [c.status for c in checks if name_fragment in c.name]


def failures(checks):
    return [(c.name, c.detail) for c in checks if c.status == "FAIL"]


def test_a_clean_slice_passes_every_check(corpus):
    checks = checks_for(corpus, a_slice())
    assert failures(checks) == []
    assert "PASS" in status_of(checks, "EVIDENCE vector-02")
    assert status_of(checks, "MAP-READ") == ["PASS"]
    assert status_of(checks, "MAP-MEMBER") == ["PASS"]
    assert status_of(checks, "MAP-ORIGINAL") == ["PASS"]
    assert status_of(checks, "LEVEL vector-02") == ["PASS"]


def test_a_recorded_number_that_no_longer_reproduces_fails(corpus):
    """The defect the old two-queries-per-section spot-check would miss."""
    bad = a_slice()
    bad["candidates"][0]["evidence"][0]["key_result"] = "17 projects"
    named, detail = failures(checks_for(corpus, bad))[0]
    assert named == "EVIDENCE vector-02"
    assert "17" in detail and "live result does not contain" in detail


def test_a_number_that_only_appears_in_the_query_is_not_judged(corpus):
    """`WHERE id IN (1, 2)` puts 1 and 2 in the SQL; quoting a filter literal
    back in key_result must not read as an unreproducible claim."""
    ok = a_slice()
    ok["candidates"][0]["evidence"][0]["key_result"] = \
        "2 projects (ids 1 and 2)"
    assert failures(checks_for(corpus, ok)) == []


def test_an_empty_result_fails_unless_the_absence_is_the_claim(corpus):
    empty = a_slice()
    empty["candidates"][0]["evidence"] = [{
        "sql": "SELECT id FROM project WHERE acronym = 'NOPE'",
        "key_result": "0 rows"}]
    assert "0 rows" in failures(checks_for(corpus, empty))[0][1]

    declared = a_slice()
    declared["candidates"][0]["evidence"] = [{
        "sql": "SELECT id FROM project WHERE acronym = 'NOPE'",
        "key_result": "0 rows", "expect_empty": True}]
    # An absence has no satisfying set to count or to derive a level from.
    declared["candidates"][0].pop("satisfying_count")
    assert failures(checks_for(corpus, declared)) == []


def test_an_absence_that_is_not_absent_fails(corpus):
    wrong = a_slice()
    wrong["candidates"][0]["evidence"] = [{
        "sql": "SELECT id FROM project WHERE acronym = 'ALPHA'",
        "key_result": "no such project", "expect_empty": True}]
    assert "is not real" in failures(checks_for(corpus, wrong))[0][1]


def test_broken_and_guarded_sql_are_findings_not_crashes(corpus):
    broken = a_slice()
    broken["candidates"][0]["evidence"] = [
        {"sql": "SELECT * FROM nope", "key_result": "1"}]
    assert "did not execute" in failures(checks_for(corpus, broken))[0][1]

    guarded = a_slice()
    guarded["candidates"][0]["evidence"] = [
        {"sql": "DROP TABLE project", "key_result": "1"}]
    assert "guardrail" in failures(checks_for(corpus, guarded))[0][1]


def test_evidence_is_mandatory(corpus):
    bare = a_slice()
    bare["candidates"][0].pop("evidence")
    assert "no evidence recorded" in failures(checks_for(corpus, bare))[0][1]


def test_the_level_comes_from_the_count_taken_without_the_bucket(corpus):
    """cp4's defect: the explorer counts inside its own bucket, the question
    it becomes carries no bucket filter, so the level is derived from the
    unfenced count and a recommended level that disagrees is refused."""
    mislabelled = a_slice()
    # Matches 5 of the 6 projects corpus-wide - L3 - while the candidate
    # still claims the L2 its in-bucket count suggested.
    mislabelled["candidates"][0]["topic_filter"] = "p.objective IS NOT NULL"
    named, detail = next(f for f in failures(checks_for(corpus, mislabelled))
                         if f[0].startswith("LEVEL"))
    assert "L2" in detail and "L3" in detail and "corpus-wide" in detail


def test_a_level_that_agrees_with_the_unfenced_count_passes(corpus):
    checks = checks_for(corpus, a_slice())
    detail = next(c.detail for c in checks if c.name.startswith("LEVEL"))
    assert failures(checks) == []
    assert "L2 from 2 project(s) corpus-wide" in detail


def test_a_candidate_with_no_topic_filter_warns_that_its_level_is_a_guess(corpus):
    """Old candidates still render, but nobody is told their level is sound."""
    legacy = a_slice()
    legacy["candidates"][0].pop("topic_filter")
    checks = checks_for(corpus, legacy)
    level = next(c for c in checks if c.name.startswith("LEVEL"))
    assert failures(checks) == []
    assert level.status == "WARN" and "inside the bucket" in level.detail


def test_a_satisfying_count_from_no_query_fails(corpus):
    """It no longer decides the level, so it is exactly the number that would
    drift unnoticed."""
    invented = a_slice()
    invented["candidates"][0]["satisfying_count"] = 7
    named, detail = next(f for f in failures(checks_for(corpus, invented))
                         if f[0].startswith("COUNT"))
    assert "satisfying_count=7" in detail


def test_a_survivor_count_outside_the_subtype_window_fails(corpus):
    """The hyb-02 birth-failure, caught before a drafter is ever spawned."""
    combo = a_slice()
    combo["candidates"][0].update({
        "id": "hybrid-11",
        "recommend": "route=hybrid level=L1 subtype=filter-read",
        "survivor_count": 40})
    named, detail = next(f for f in failures(checks_for(corpus, combo))
                         if f[0].startswith("WINDOW"))
    assert "filter-read wants 2-10" in detail and "40" in detail


def test_an_unenumerable_survivor_set_fails(corpus):
    combo = a_slice()
    combo["candidates"][0].update({
        "id": "hybrid-11",
        "recommend": "route=hybrid level=L3 subtype=filter-survey",
        "survivor_count": 640})
    detail = next(f for f in failures(checks_for(corpus, combo))
                  if f[0].startswith("WINDOW"))[1]
    assert "ceiling" in detail


def test_a_candidate_outside_its_slice_fails(corpus):
    strayed = a_slice()
    strayed["candidates"][0]["bucket"] = "humanities / arts"
    detail = next(f for f in failures(checks_for(corpus, strayed))
                  if f[0].startswith("SLICE"))[1]
    assert "outside this slice's assignment" in detail


def test_a_failed_slice_is_skipped_not_verified(corpus):
    checks = checks_for(corpus, a_slice(status="FAILED"))
    assert [c.status for c in checks] == ["N/A"]


def test_a_returned_slice_with_no_payload_fails(corpus):
    empty = a_slice(map_entry=None, candidates=[], findings=[])
    assert "must carry something" in failures(checks_for(corpus, empty))[0][1]


# --- the map entry's own failure mode --------------------------------------

def test_a_map_entry_must_cite_projects_it_read(corpus):
    thin = a_slice()
    thin["map_entry"]["read"] = [1]
    detail = next(f for f in failures(checks_for(corpus, thin))
                  if f[0] == "MAP-READ")[1]
    assert "at least 2" in detail


def test_a_map_entry_cannot_cite_missing_or_textless_projects(corpus):
    absent = a_slice()
    absent["map_entry"]["read"] = [1, 999]
    assert "999" in next(f for f in failures(checks_for(corpus, absent))
                         if f[0] == "MAP-READ")[1]

    textless = a_slice()
    textless["map_entry"]["read"] = [1, 6]      # project 6 has no text at all
    assert "no stored text" in next(
        f for f in failures(checks_for(corpus, textless))
        if f[0] == "MAP-READ")[1]


def test_a_map_entry_cannot_describe_a_region_it_did_not_read(corpus):
    strayed = a_slice()
    strayed["map_entry"]["read"] = [1, 3]       # 3 is sociology, not biology
    detail = next(f for f in failures(checks_for(corpus, strayed))
                  if f[0] == "MAP-MEMBER")[1]
    assert "not in bucket" in detail


def test_a_map_entry_must_read_members_before_it_probes_for_topics(corpus):
    """cp4's real defect: 16 of the 17 projects the explorers read were
    members of a candidate's own result set, so `about:` described the seeds
    rather than the bucket - and the map is append-only, so it stays wrong."""
    unprompted = a_slice()
    unprompted["map_entry"].pop("read_first")
    detail = next(f for f in failures(checks_for(corpus, unprompted))
                  if f[0] == "MAP-FIRST")[1]
    assert "BEFORE any topic probe" in detail

    inconsistent = a_slice()
    inconsistent["map_entry"]["read_first"] = [1, 5]     # 5 is not in `read:`
    detail = next(f for f in failures(checks_for(corpus, inconsistent))
                  if f[0] == "MAP-FIRST")[1]
    assert "[5]" in detail


def test_reads_that_all_land_inside_the_candidates_warn_but_never_gate(corpus):
    huddled = a_slice()
    huddled["candidates"][0]["evidence"] = [{
        "sql": "SELECT COUNT(*) AS n FROM project WHERE id IN (1, 2)",
        "key_result": "2 projects"}]
    checks = checks_for(corpus, huddled)
    check = next(c for c in checks if c.name == "MAP-INDEPENDENT")
    assert check.status == "WARN" and "describing the seeds" in check.detail
    assert [f for f in failures(checks) if f[0] == "MAP-INDEPENDENT"] == []


def test_a_read_from_outside_the_candidates_passes_independence(corpus):
    check = next(c for c in checks_for(corpus, a_slice())
                 if c.name == "MAP-INDEPENDENT")
    assert check.status == "PASS" and "[1]" in check.detail


def test_a_map_entry_that_paraphrases_its_own_tag_fails(corpus):
    """cp1's lesson: an entry written from the taxonomy label is worthless."""
    echo = a_slice()
    echo["map_entry"]["about"] = "Natural sciences: biological sciences."
    echo["map_entry"]["texture"] = "Biological sciences, naturally."
    detail = next(f for f in failures(checks_for(corpus, echo))
                  if f[0] == "MAP-ORIGINAL")[1]
    assert "paraphrases its own tag" in detail


# --------------------------------------------------------------------------
# explore-crosscheck
# --------------------------------------------------------------------------

def flags_of(corpus, *slices, targets=None):
    header = {"kind": "run", "date": "2026-07-25", "scope": "map=2",
              "started": "2026-07-25T09:00:00", "targets": targets or {}}
    journal = load_journal(journal_file(corpus["dir"], *slices,
                                        header=header))
    return crosscheck(journal, read_profile(corpus["profile"]),
                      journal.header.get("targets"))


def kinds(flags, kind):
    return [f.detail for f in flags if f.kind == kind]


def test_crosscheck_flags_a_clustered_axis(corpus):
    clustered = a_slice()
    clustered["candidates"] = [
        {"id": f"vector-0{i}", "topic": f"topic number {i} about widgets",
         "axes": "country=IT scheme=RIA", "why": "-"}
        for i in range(2, 6)]
    assert any("country=IT" in d for d in kinds(flags_of(corpus, clustered),
                                                "WIDTH"))


def test_crosscheck_flags_an_entity_used_too_often(corpus):
    repeated = a_slice()
    repeated["candidates"] = [
        {"id": f"vector-0{i}", "topic": f"the AQUACOSM platform, angle {i}",
         "axes": f"leaf=x{i}", "why": "-"} for i in range(2, 6)]
    assert any("AQUACOSM" in d
               for d in kinds(flags_of(corpus, repeated), "ENTITY"))


def test_crosscheck_flags_a_near_duplicate_of_the_existing_profile(corpus):
    echo = a_slice()
    echo["candidates"] = [{
        "id": "vector-02",
        # vector-01 in the profile: "A project about the ecology of boreal
        # lake fungi."
        "topic": "A project about the ecology of boreal lake fungi.",
        "axes": "leaf=ecology", "why": "-"}]
    assert any("profile" in d for d in
               kinds(flags_of(corpus, echo), "NEAR-DUPLICATE"))


def test_crosscheck_reports_supply_against_this_runs_targets(corpus):
    flags = flags_of(corpus, a_slice(), targets={"vector": 3})
    supply = kinds(flags, "SUPPLY")
    assert supply == ["vector: 1/3 candidate(s) this run"]
    assert any(f.kind == "SUPPLY" and f.level == "FLAG" for f in flags)


def test_crosscheck_is_quiet_on_a_clean_run(corpus):
    hard = [f for f in flags_of(corpus, a_slice()) if f.level == "FLAG"]
    assert hard == []


# --------------------------------------------------------------------------
# write-profile: the profile GROWS
# --------------------------------------------------------------------------

def write(corpus, *slices, version="cp4", **kwargs):
    path = journal_file(corpus["dir"], *slices)
    return write_profile(path, version, profile_path=corpus["profile"],
                         db_path=corpus["db"], bank_path=corpus["bank"],
                         config_path=corpus["config"], log_path=corpus["log"],
                         date="2026-07-25", **kwargs)


@pytest.fixture
def canonical(corpus, monkeypatch):
    """Treat the fixture profile as THE profile, so the version-bump guard
    (which refuses to move the label when writing a copy) lets the bump run."""
    import src.eval.explore as explore_module
    monkeypatch.setattr(explore_module, "CORPUS_PROFILE_PATH",
                        corpus["profile"])
    return corpus


def test_write_inserts_and_numbers_from_the_highest_existing_id(corpus):
    result = write(corpus, a_slice())
    assert result.map_entries == ["m02"]        # m01 exists
    assert result.candidates == ["vector-02"]   # vector-01 exists
    text = corpus["profile"].read_text(encoding="utf-8")
    assert "- region: m02" in text
    assert "- id: vector-02" in text
    assert "mapped: cp4" in text


def test_write_sets_the_level_from_the_unfenced_count_and_shows_both(corpus):
    """A drafter reading the seed has to know which number carries a bucket
    filter and which does not, so the block states both and says which one
    the level came from."""
    write(corpus, a_slice())
    block = next(b for b in corpus["profile"].read_text(encoding="utf-8")
                 .split("- id: ") if b.startswith("vector-02"))
    assert "recommend: route=vector level=L2 subtype=comparison" in block
    assert "counts: 2 corpus-wide, 2 inside the bucket" in block
    assert "the question carries no bucket filter" in block


def test_write_overrides_a_level_the_explorer_guessed_wrong(corpus):
    guessed = a_slice()
    guessed["candidates"][0]["recommend"] = "route=vector level=L3 subtype=x"
    write(corpus, guessed)
    text = corpus["profile"].read_text(encoding="utf-8")
    assert "recommend: route=vector level=L2 subtype=x" in text


def test_write_renders_the_pre_probe_reads(corpus):
    write(corpus, a_slice())
    assert "read first: 1, 2" in corpus["profile"].read_text(encoding="utf-8")


def test_write_refuses_a_topic_filter_it_cannot_run(corpus):
    broken = a_slice()
    broken["candidates"][0]["topic_filter"] = "objective ILIKE (("
    with pytest.raises(ExploreError, match="topic_filter"):
        write(corpus, broken)


def test_write_leaves_a_candidate_with_no_topic_filter_alone(corpus):
    """Old journals still render - they just carry the level they guessed."""
    legacy = a_slice()
    legacy["candidates"][0].pop("topic_filter")
    write(corpus, legacy)
    text = corpus["profile"].read_text(encoding="utf-8")
    assert "recommend: route=vector level=L2 subtype=comparison" in text
    assert "counts:" not in text.split("- id: vector-02")[1].split("- id:")[0]


def test_write_preserves_every_byte_an_earlier_run_wrote(corpus):
    """Append-never-rewrite, enforced by construction rather than discipline."""
    before = corpus["profile"].read_text(encoding="utf-8")
    write(corpus, a_slice())
    after = corpus["profile"].read_text(encoding="utf-8")
    for block in ("- region: m01", "- id: vector-01", "- id: sf-01",
                  "Preamble for the vector section.",
                  "Trailing prose after the counter."):
        assert block in before and block in after
    # Untouched sections keep their stubs rather than being re-emitted.
    assert after.count('Not yet explored (scoped run "earlier"') == 5


def test_write_moves_the_frontier_and_recomputes_the_counter(corpus):
    write(corpus, a_slice())
    text = corpus["profile"].read_text(encoding="utf-8")
    row = next(line for line in text.splitlines()
               if line.startswith("| natural sciences / biological sciences"))
    assert "| mapped | m02 |" in row
    assert "`mapped 2/5 | mined 1/5 | unexplored 2/5`" in text


def test_write_appends_telemetry_counted_from_the_mcp_log(corpus):
    write(corpus, a_slice())
    text = corpus["profile"].read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if ln.startswith("- cp4 ("))
    # Slices, not agents: the journal cannot know how many subagents ran, and
    # the first live run reported "6 subagents" for 2 explorers + 1 critic.
    assert "1 slices" in line
    assert "2 `run_sql`" in line          # the pre-window call is excluded
    assert "3 projects read across 1 `get_project_text` calls" in line
    assert "+1 map entries, +1 candidates" in line
    assert "- cp3 (2026-07-24)" in text   # the earlier run log survives


def test_telemetry_names_the_agent_count_only_when_the_header_states_it(corpus):
    path = journal_file(corpus["dir"], a_slice(),
                        header={"kind": "run", "date": "2026-07-25",
                                "scope": "map=2", "subagents": 2,
                                "started": "2026-07-25T09:00:00"})
    write_profile(path, "cp4", profile_path=corpus["profile"],
                  db_path=corpus["db"], bank_path=corpus["bank"],
                  config_path=corpus["config"], log_path=corpus["log"],
                  date="2026-07-25")
    line = next(ln for ln in
                corpus["profile"].read_text(encoding="utf-8").splitlines()
                if ln.startswith("- cp4 ("))
    assert "2 subagents over 1 slices" in line


def test_the_critics_coverage_notes_reach_the_profile(corpus):
    """The one model-authored output. It goes through the journal because the
    first live run proved that leaving it to the orchestrator to paste
    afterwards means it is silently dropped."""
    path = journal_file(corpus["dir"], a_slice(),
                        {"kind": "critic",
                         "coverage_notes": "Sociology is unread; law is thin."})
    write_profile(path, "cp4", profile_path=corpus["profile"],
                  db_path=corpus["db"], bank_path=corpus["bank"],
                  config_path=corpus["config"], log_path=corpus["log"],
                  date="2026-07-25")
    text = corpus["profile"].read_text(encoding="utf-8")
    assert "Sociology is unread; law is thin." in text
    assert "**cp4 (2026-07-25)**" in text
    # ...inside Coverage notes, not appended to whatever section came last.
    tail = text.split("## Coverage notes")[1]
    assert "Sociology is unread" in tail


def test_a_stale_no_entries_stub_is_cleared_by_the_first_real_entry(corpus):
    """A stub left standing above six real map entries reads as a
    contradiction - which is what the first live run produced."""
    profile = corpus["profile"]
    profile.write_text(
        profile.read_text(encoding="utf-8").replace(
            "Format notes here.",
            "Format notes here.\n\n*No entries yet - the map is new at cp3.*"),
        encoding="utf-8")
    write(corpus, a_slice())
    assert "*No entries yet" not in profile.read_text(encoding="utf-8")


def test_telemetry_records_the_run_duration(corpus):
    """cp1/cp2 had a duration slot and never filled it, so every estimate
    since has been a guess. The run header's `started` to the last logged
    call, plus the time actually spent inside MCP calls."""
    write(corpus, a_slice())
    line = next(ln for ln in
                corpus["profile"].read_text(encoding="utf-8").splitlines()
                if ln.startswith("- cp4 ("))
    # header started 09:00:00, last in-window call logged at 10:02:00
    assert "62m wall" in line
    assert "in MCP calls)" in line


def test_telemetry_sums_per_call_ms_when_the_log_has_it(corpus):
    corpus["log"].write_text(
        json.dumps({"ts": "2026-07-25T10:00:00", "tool": "run_sql",
                    "ms": 1500}) + "\n"
        + json.dumps({"ts": "2026-07-25T10:00:30", "tool": "run_sql",
                      "ms": 500}) + "\n", encoding="utf-8")
    write(corpus, a_slice())
    line = next(ln for ln in
                corpus["profile"].read_text(encoding="utf-8").splitlines()
                if ln.startswith("- cp4 ("))
    assert "(2s in MCP calls)" in line


def test_write_bumps_both_version_labels(canonical):
    result = write(canonical, a_slice())
    assert result.version_bumped
    assert 'CORPUS_PROFILE_VERSION = "cp4"' in \
        canonical["config"].read_text(encoding="utf-8")
    assert "- **Version:** cp4" in \
        canonical["profile"].read_text(encoding="utf-8")


def test_writing_a_copy_never_moves_the_version_label(corpus):
    """Rehearsing into a scratch copy must not desynchronise config from the
    real profile - found by doing exactly that against the live file."""
    result = write(corpus, a_slice())
    assert not result.version_bumped
    assert 'CORPUS_PROFILE_VERSION = "cp3"' in \
        corpus["config"].read_text(encoding="utf-8")
    # The profile's own header still records what this write produced.
    assert "- **Version:** cp4" in corpus["profile"].read_text(encoding="utf-8")


def test_crosscheck_does_not_see_this_runs_own_insertions(corpus):
    """Flags are computed against the profile BEFORE insertion; otherwise
    every new candidate is a near-duplicate of itself."""
    result = write(corpus, a_slice())
    assert not [f for f in result.flags if f.kind == "NEAR-DUPLICATE"]


def test_write_skips_slices_that_never_reached_a_verified_state(corpus):
    result = write(corpus, a_slice(status="RETURNED"),
                   a_slice(slice_id="s02", status="FAILED"))
    assert result.map_entries == [] and result.candidates == []


def test_write_accepts_a_short_slice(corpus):
    result = write(corpus, a_slice(status="SHORT", short="1/3 - thin bucket"))
    assert result.candidates == ["vector-02"]


def test_write_refuses_to_reuse_a_recorded_version(corpus):
    with pytest.raises(ExploreError, match="already appears"):
        write(corpus, a_slice(), version="cp3")


def test_write_refuses_a_malformed_version(corpus):
    with pytest.raises(ExploreError, match="must look like"):
        write(corpus, a_slice(), version="v4")


def test_dry_run_writes_nothing(corpus):
    before = corpus["profile"].read_text(encoding="utf-8")
    result = write(corpus, a_slice(), dry_run=True)
    assert result.candidates == ["vector-02"]
    assert corpus["profile"].read_text(encoding="utf-8") == before
    assert 'CORPUS_PROFILE_VERSION = "cp3"' in \
        corpus["config"].read_text(encoding="utf-8")


def test_a_candidate_id_must_name_a_section(corpus):
    stray = a_slice()
    stray["candidates"][0]["id"] = "topic-01"
    with pytest.raises(ExploreError, match="does not name a section"):
        write(corpus, stray)


def test_written_candidates_parse_back_out_of_the_profile(corpus):
    """The writer's output must be readable by the same parser the next run's
    frontier uses - otherwise seeds silently stop being counted."""
    write(corpus, a_slice())
    parsed = {c.id: c for c in
              profile_candidates(read_profile(corpus["profile"]))}
    assert set(parsed) == {"vector-01", "vector-02"}
    assert parsed["vector-02"].bucket == "natural sciences / biological sciences"
    assert "satisfying=2" in parsed["vector-02"].fields["axes"]
