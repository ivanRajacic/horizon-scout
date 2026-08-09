"""Absence handling (Fix A, 2026-08-09).

The system could not tell "I did not find it" from "it is not there": an empty
SQL result read as a failed search, a fact the schema does not hold was
answered from a proxy column, and an empty filter refused without ever
checking whether its own conditions matched anything. No servers needed - the
LLM, the searcher and the narrowing executor are faked.

Fixtures come from test_m4_pipeline so the fakes cannot drift apart.
"""

from src.retrieval.scoped import (NARROW_ROW_LIMIT, ScopedRetriever,
                                  split_where_conjuncts)
from src.synthesis.synthesizer import Synthesizer
from tests.test_m4_pipeline import (FakeLlm, FakeSearcher, FakeSql, R, _ask,
                                    mk_chunk)


class CountingSql(FakeSql):
    """A narrowing SqlPath whose single-condition probes answer from a table:
    the condition text -> how many projects it matches alone."""

    def __init__(self, result, counts, row_limit=NARROW_ROW_LIMIT):
        super().__init__(result, row_limit=row_limit)
        self.counts = counts
        self.probes = []

    def execute_trusted(self, sql):
        if "_zp" not in sql:
            return ["count"], [(1,)]           # value-gate lookup: alive
        self.probes.append(sql)
        for cond, n in self.counts.items():
            if cond in sql:
                return ["count"], [(n,)]
        return ["count"], [(0,)]


TWO_COND = ("SELECT DISTINCT p.id FROM project p JOIN organization o ON "
            "o.projectID = p.id WHERE o.country = 'SE' AND "
            "p.fundingScheme = 'SME-1' LIMIT 50000")


# --- the conjunct splitter ---

def test_split_where_conjuncts_top_level_only():
    prefix, parts = split_where_conjuncts(TWO_COND)
    assert prefix.endswith("o.projectID = p.id")
    assert parts == ["o.country = 'SE'", "p.fundingScheme = 'SME-1'"]


def test_split_where_conjuncts_keeps_a_parenthesized_or_group_whole():
    _prefix, parts = split_where_conjuncts(
        "SELECT p.id FROM project p WHERE (p.status = 'CLOSED' OR "
        "p.status = 'SIGNED') AND p.totalCost > 1000 ORDER BY p.id")
    assert parts == ["(p.status = 'CLOSED' OR p.status = 'SIGNED')",
                     "p.totalCost > 1000"]


def test_split_where_conjuncts_ignores_and_inside_a_literal_and_a_between():
    _prefix, parts = split_where_conjuncts(
        "SELECT p.id FROM project p WHERE p.acronym = 'health AND safety' AND "
        "p.startDate BETWEEN DATE '2020-01-01' AND DATE '2021-01-01'")
    assert parts == ["p.acronym = 'health AND safety'",
                     "p.startDate BETWEEN DATE '2020-01-01' AND "
                     "DATE '2021-01-01'"]


def test_split_where_conjuncts_refuses_a_bare_top_level_or():
    # DuckDB binds AND tighter than OR, so these are not conjuncts at all.
    assert split_where_conjuncts(
        "SELECT p.id FROM project p WHERE a = 1 AND b = 2 OR c = 3") is None
    assert split_where_conjuncts("SELECT DISTINCT id FROM project") is None
    assert split_where_conjuncts(None) is None


# --- the three zero-proof outcomes ---

def test_zero_match_is_refused_only_when_every_condition_matches_alone():
    searcher = FakeSearcher([mk_chunk(1, "A")])
    sql = CountingSql(R(True, rows=[], sql=TWO_COND),
                      {"o.country = 'SE'": 4000,
                       "p.fundingScheme = 'SME-1'": 900})
    res = ScopedRetriever(searcher, narrow_sql=sql).retrieve("q", k=10)
    assert res.status == "zero_match"
    assert res.trace["zero_proof"] == "proven"
    assert res.zero_conjunct_counts == [("o.country = 'SE'", 4000),
                                        ("p.fundingScheme = 'SME-1'", 900)]
    assert searcher.last_project_ids == "unset"    # still never widened
    assert len(sql.probes) == 2


def test_a_condition_matching_nothing_alone_makes_the_zero_unproven():
    # The intersection is empty because one side is empty, which says nothing
    # about the corpus - so this must not become a confident refusal.
    searcher = FakeSearcher([mk_chunk(1, "A")])
    sql = CountingSql(R(True, rows=[], sql=TWO_COND),
                      {"o.country = 'SE'": 4000,
                       "p.fundingScheme = 'SME-1'": 0})
    res = ScopedRetriever(searcher, narrow_sql=sql).retrieve("q", k=10)
    assert res.status == "unproven_zero" and res.degraded == "unproven_zero"
    assert res.trace["zero_dead_conjunct"] == "p.fundingScheme = 'SME-1'"
    assert searcher.last_project_ids is None       # unfiltered search instead


def test_an_unsplittable_zero_keeps_the_old_refusal():
    searcher = FakeSearcher([mk_chunk(1, "A")])
    sql = CountingSql(R(True, rows=[],
                        sql="SELECT DISTINCT id FROM project"), {})
    res = ScopedRetriever(searcher, narrow_sql=sql).retrieve("q", k=10)
    assert res.status == "zero_match"
    assert res.trace["zero_proof"] == "unparsed"
    assert res.zero_conjunct_counts is None
    assert sql.probes == []


def test_a_failing_probe_leaves_the_zero_unparsed():
    class Boom(CountingSql):
        def execute_trusted(self, sql):
            if "_zp" in sql:
                raise RuntimeError("Binder Error")
            return ["count"], [(1,)]

    searcher = FakeSearcher([mk_chunk(1, "A")])
    res = ScopedRetriever(
        searcher, narrow_sql=Boom(R(True, rows=[], sql=TWO_COND), {})
    ).retrieve("q", k=10)
    assert res.status == "zero_match"
    assert res.trace["zero_proof"] == "unparsed"


# --- how the routes say it ---

def test_ask_phrases_a_proven_zero_with_the_per_condition_counts(
        monkeypatch, tmp_path):
    from src import ask as ask_mod

    searcher = FakeSearcher([mk_chunk(1, "A")])
    monkeypatch.setattr(ask_mod, "build_retriever", lambda name: searcher)
    a = _ask(monkeypatch, tmp_path)
    a.scoped = ScopedRetriever(
        searcher, narrow_sql=CountingSql(
            R(True, rows=[], sql=TWO_COND),
            {"o.country = 'SE'": 4000, "p.fundingScheme = 'SME-1'": 900}))
    res = a.ask("q", k=10, mode="scoped")
    assert "no such projects exist" in res.answer
    assert "o.country = 'SE': 4,000" in res.answer
    assert res.degraded is None


def test_ask_falls_back_to_the_plain_refusal_without_a_proof(
        monkeypatch, tmp_path):
    from src import ask as ask_mod
    from src.retrieval.scoped import ScopedResult
    from tests.test_m4_pipeline import SpyScoped

    monkeypatch.setattr(ask_mod, "build_retriever",
                        lambda name: FakeSearcher([]))
    a = _ask(monkeypatch, tmp_path)
    a.scoped = SpyScoped(ScopedResult(question="q", status="zero_match",
                                      project_ids=set()))
    res = a.ask("q", k=10, mode="scoped")
    assert res.answer.startswith("No projects match the structured criteria")


def test_ask_discloses_the_unproven_zero_degrade(monkeypatch, tmp_path):
    from src import ask as ask_mod
    from src.retrieval.scoped import ScopedResult
    from tests.test_m4_pipeline import SpyScoped

    monkeypatch.setattr(ask_mod, "build_retriever",
                        lambda name: FakeSearcher([]))
    a = _ask(monkeypatch, tmp_path)
    a.scoped = SpyScoped(ScopedResult(
        question="q", status="unproven_zero", degraded="unproven_zero",
        chunks=[mk_chunk(1, "A")]))
    a.synth = Synthesizer(llm=FakeLlm(["answer [A, 1]."]))
    res = a.ask("q", k=10, mode="scoped")
    assert res.answer.startswith("[Note: no structured filter was applied")
    assert res.degraded == "unproven_zero"


def test_empty_sql_result_states_absence_and_shows_the_query():
    from src.ask import _templated_sql_answer

    answer = _templated_sql_answer([], "SELECT count(*) FROM project WHERE 0")
    assert "no such result exists" in answer
    assert "0 rows" not in answer                  # not a search-failure report
    assert "(Query: SELECT count(*) FROM project WHERE 0)" in answer
    # The other two templates are untouched.
    assert _templated_sql_answer([(7,)], "s") == "Result: 7"
    assert _templated_sql_answer([(1, 2), (3, 4)], "s") == \
        "Query returned 2 rows."


# --- the data-absent escape ---

def test_the_sql_prompt_carries_the_escape_and_owns_its_version():
    from src.retrieval import sql_path as sp

    p = sp.build_system_prompt()
    assert sp.NO_SUCH_DATA_MARKER in p
    assert "do not substitute a similar, related or proxy column" in p
    assert sp.SQL_PROMPT_VERSION == "q5-proxy"


def test_parse_no_such_data_reads_only_a_leading_marker():
    from src.retrieval.sql_path import parse_no_such_data

    assert parse_no_such_data("NO_SUCH_DATA: publication counts.") == \
        "publication counts"
    assert parse_no_such_data("```\nNO_SUCH_DATA: staff headcount\n```") == \
        "staff headcount"
    # A statement that merely mentions the marker is still a statement.
    assert parse_no_such_data(
        "SELECT 1 -- NO_SUCH_DATA: not here") is None


def test_no_such_data_marker_becomes_an_explicit_refusal(monkeypatch,
                                                         tmp_path):
    from src import ask as ask_mod
    from src.retrieval.sql_path import SqlPath

    monkeypatch.setattr(ask_mod, "build_retriever",
                        lambda name: FakeSearcher([]))
    a = _ask(monkeypatch, tmp_path)
    llm = FakeLlm(["NO_SUCH_DATA: the number of publications per project"])
    llm.model = "fake-model"
    a.sql_path = SqlPath(llm=llm, log_path=tmp_path / "sql.jsonl")
    res = a.ask("q", k=10, mode="sql")
    assert res.answer == ("The database does not record the number of "
                          "publications per project, so this question cannot "
                          "be answered from it.")
    assert res.trace["no_such_data"] is True
    assert res.degraded is None and res.sql is None
    assert llm.calls == 1                          # no retry, no execution


# --- the value gate reads IN lists (2026-08-09) ---

def test_filter_literals_reads_in_lists_as_one_membership_test():
    from src.retrieval.scoped import filter_literals
    sql = ("SELECT DISTINCT p.id FROM project p JOIN organization o ON "
           "o.projectID = p.id WHERE o.activityType IN ('HES', 'UNIVERSITY') "
           "AND o.country = 'SE'")
    lits = filter_literals(sql)
    assert ("activityType", "IN", "'HES', 'UNIVERSITY'") in lits
    assert ("country", "=", "SE") in lits


def test_filter_literals_exempts_not_in():
    from src.retrieval.scoped import filter_literals
    sql = ("SELECT DISTINCT p.id FROM project p JOIN organization o ON "
           "o.projectID = p.id WHERE o.country NOT IN ('Zzz', 'Yyy')")
    assert filter_literals(sql) == []


def test_a_partially_dead_in_list_is_not_flagged():
    # One dead member among live ones still filters: the gate re-executes the
    # whole membership test, so only an all-dead list is dead.
    from src.retrieval.scoped import ScopedRetriever

    class InAwareSql(FakeSql):
        def execute_trusted(self, sql):
            if "IN (" in sql:
                n = 5 if "'HES'" in sql else 0
                return ["count"], [(n,)]
            return ["count"], [(1,)]

    r = ScopedRetriever(FakeSearcher([]),
                        narrow_sql=InAwareSql(R(True, rows=[(1,)])))
    live = r._dead_values([("activityType", "IN", "'HES', 'UNIVERSITY'")])
    assert live == []
    dead = r._dead_values([("activityType", "IN", "'UNI', 'UNIVERSITY'")])
    assert dead == [("activityType", "'UNI', 'UNIVERSITY'")]


def test_uses_constant_comparison_flags_placeholders_only():
    from src.retrieval.scoped import uses_constant_comparison
    assert uses_constant_comparison("SELECT p.id FROM project p WHERE 0 = 1")
    assert uses_constant_comparison(
        "SELECT p.id FROM project p WHERE p.status = 'CLOSED' AND 1=1")
    # real conditions have a column on one side
    assert not uses_constant_comparison(
        "SELECT p.id FROM project p WHERE p.totalCost > 1000")
    assert not uses_constant_comparison(
        "SELECT p.id FROM project p WHERE p.acronym = '0 = 1'")
    assert not uses_constant_comparison(
        "SELECT p.id FROM project p WHERE p.startDate >= DATE '2021-01-01'")
    assert not uses_constant_comparison(None)


def test_uses_constant_comparison_flags_bare_boolean_literals():
    from src.retrieval.scoped import uses_constant_comparison
    # hyb-16, dev2: told to skip a constraint, the model wrote `AND false`
    assert uses_constant_comparison(
        "SELECT p.id FROM project p WHERE p.fundingScheme = 'ERC-STG' "
        "AND false")
    assert uses_constant_comparison("SELECT p.id FROM project p WHERE true")
    assert uses_constant_comparison(
        "SELECT p.id FROM project p WHERE (FALSE) AND p.status = 'CLOSED'")
    assert uses_constant_comparison(
        "SELECT p.id FROM project p WHERE NOT TRUE AND p.status = 'CLOSED'")
    # a boolean literal compared against a column is a real condition
    assert not uses_constant_comparison(
        "SELECT p.id FROM project p WHERE p.terminated = false")
    assert not uses_constant_comparison(
        "SELECT p.id FROM project p WHERE false = p.terminated")
    # column names containing the words do not match
    assert not uses_constant_comparison(
        "SELECT p.id FROM project p WHERE p.trueCost > 5")
    assert not uses_constant_comparison(
        "SELECT p.id FROM project p WHERE p.acronym = 'AND false'")
