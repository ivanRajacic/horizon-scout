"""M4 unit tests: router fallback, synthesis citation post-check + context
assertion, scoped edge policies. No servers needed - LLM and searcher faked."""

import json
import re

import pytest

from src.retrieval.scoped import (WEAK_FILTER, ScopedRetriever,
                                  filter_literals, filter_note,
                                  uses_malformed_null_test,
                                  uses_subject_filter)
from src.retrieval.vector_search import SearchResult
from src.router import router as router_mod
from src.router.router import Router, derive_mode
from src.synthesis.synthesizer import Synthesizer, fit_to_budget
from src.synthesis import synthesizer as synth_mod


class FakeLlm:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.seen = []          # the messages each call was given

    def chat(self, messages, **kw):
        self.calls += 1
        self.seen.append(messages)
        return self.responses.pop(0)


def mk_chunk(pid, acr, score=0.5, text="some project content here", section="summary"):
    return SearchResult(chunk_id=f"{pid}-c", project_id=pid, acronym=acr,
                        title=f"{acr} title", source="report", section=section,
                        score=score, text=text)


# --- router ---

def fields(needs_text, constraints, reason="r"):
    """One r3-fields reply: the two facts the model reports."""
    return json.dumps({"needs_project_text": needs_text,
                       "structured_constraints": list(constraints),
                       "reason": reason})


# derive_mode is the routing rule itself. All four combinations, in code,
# because this is what stopped being the model's decision in r3-fields.

@pytest.mark.parametrize("needs_text,constraints,want", [
    (False, [],                          "sql"),     # nothing but columns
    (False, ["status terminated"],       "sql"),     # a count with a filter
    (True,  [],                          "vector"),  # pure topic
    (True,  ["funding scheme ERC-STG"],  "scoped"),  # filter plus project text
])
def test_derive_mode_covers_every_combination(needs_text, constraints, want):
    assert derive_mode(needs_text, constraints) == want


def test_router_parses_the_two_facts():
    r = Router(llm=FakeLlm([fields(False, ["status terminated"], "count")]))
    d = r.route("how many projects were terminated?")
    assert d.mode == "sql" and not d.router_fallback
    assert d.needs_project_text is False
    assert d.structured_constraints == ["status terminated"]


def test_router_extracts_json_from_noise():
    r = Router(llm=FakeLlm([f"Sure! {fields(True, [])} ok"]))
    assert r.route("q").mode == "vector"


def test_router_retries_then_succeeds():
    llm = FakeLlm(["not json at all", fields(True, ["country DE"])])
    d = Router(llm=llm).route("q")
    assert d.mode == "scoped" and not d.router_fallback and llm.calls == 2


def test_router_fallback_is_visible():
    llm = FakeLlm(["garbage", "still garbage"])
    d = Router(llm=llm).route("q")
    assert d.mode == "scoped" and d.router_fallback and llm.calls == 2
    # No facts were ever reported, so the decision must not claim any.
    assert d.needs_project_text is None and d.structured_constraints == []


def test_router_rejects_a_missing_or_non_boolean_fact():
    llm = FakeLlm(['{"structured_constraints": [], "reason": "x"}', "junk"])
    assert Router(llm=llm).route("q").router_fallback

    llm = FakeLlm(['{"needs_project_text": "yes", '
                   '"structured_constraints": [], "reason": "x"}', "junk"])
    assert Router(llm=llm).route("q").router_fallback


def test_router_rejects_constraints_that_are_not_a_list_of_strings():
    llm = FakeLlm(['{"needs_project_text": true, '
                   '"structured_constraints": "country DE", "reason": "x"}',
                   "junk"])
    assert Router(llm=llm).route("q").router_fallback


def test_router_drops_none_written_as_a_constraint():
    """A stray "none" entry would silently turn vector into scoped."""
    d = Router(llm=FakeLlm([fields(True, ["none"])])).route("q")
    assert d.mode == "vector" and d.structured_constraints == []


def test_router_still_reads_an_archived_prompts_reply():
    """The archive is only switchable if the parser still accepts its shape."""
    d = Router(llm=FakeLlm(['{"mode": "sql", "reason": "count"}'])).route("q")
    assert d.mode == "sql" and not d.router_fallback
    # r1-pilot / r2-columns report no facts, so none are invented here.
    assert d.needs_project_text is None and d.structured_constraints == []


def test_router_rejects_invalid_mode_from_an_archived_prompt():
    llm = FakeLlm(['{"mode": "banana", "reason": "x"}', "junk"])
    assert Router(llm=llm).route("q").router_fallback


def test_router_extract_exposes_the_facts_without_routing():
    r = Router(llm=FakeLlm([fields(True, ["country DE"], "x")]))
    f = r.extract("q")
    assert f.needs_project_text is True
    assert f.structured_constraints == ["country DE"]
    assert f.mode == "scoped" and not f.fallback


def test_router_extract_fallback_reports_no_facts():
    f = Router(llm=FakeLlm(["junk", "junk"])).extract("q")
    assert f.fallback and f.mode == "scoped"
    assert f.needs_project_text is None and f.structured_constraints == []


def test_every_archived_prompt_has_a_correction_hint():
    """The retry quotes the ACTIVE contract back at the model, so a prompt
    without a hint would correct it into the wrong shape."""
    assert set(router_mod.ROUTER_PROMPTS) == set(router_mod._CONTRACT_HINTS)
    assert router_mod.SYSTEM_PROMPT is \
        router_mod.ROUTER_PROMPTS[router_mod.ROUTER_PROMPT_VERSION]


# --- synthesizer citation post-check ---

def test_citation_violation_stripped_and_logged():
    chunks = [mk_chunk(111, "REALP")]
    llm = FakeLlm(["Real claim [REALP, 111]. Fake claim [GHOST, 999]."])
    s = Synthesizer(llm=llm).synthesize("q", chunks)
    assert "[REALP, 111]" in s.answer
    assert "[GHOST, 999]" not in s.answer
    assert s.citation_violations == ["[GHOST, 999]"]


def test_valid_citations_preserved():
    chunks = [mk_chunk(111, "A"), mk_chunk(222, "B")]
    llm = FakeLlm(["Claim one [A, 111]. Claim two [B, 222]."])
    s = Synthesizer(llm=llm).synthesize("q", chunks)
    assert not s.citation_violations


def test_no_chunks_says_so_without_calling_llm():
    llm = FakeLlm([])  # must not be called
    s = Synthesizer(llm=llm).synthesize("q", [])
    assert "cannot be answered" in s.answer and llm.calls == 0


def test_k25_drops_chunks_for_budget():
    # Real budget: 25 x ~400-token chunks cannot all fit in an 8k context, so
    # the budgeter must drop some and record it.
    big = "word " * 400
    chunks = [mk_chunk(i, f"P{i}", score=i / 100, text=big) for i in range(25)]
    llm = FakeLlm(["grounded answer [P0, 0]."])
    s = Synthesizer(llm=llm).synthesize("q", chunks)
    assert s.dropped_for_budget > 0
    assert len(s.used_chunks) + s.dropped_for_budget == 25


def test_context_assertion_fires_when_even_dropped_set_overflows(monkeypatch):
    # The assertion is a real guard, not decoration: shrink the context so that
    # even after dropping chunks the prompt cannot fit, and it must raise.
    monkeypatch.setattr(synth_mod, "LLM_CTX", 100)
    big = "word " * 400
    chunks = [mk_chunk(i, f"P{i}", score=i / 100, text=big) for i in range(25)]
    llm = FakeLlm(["should not get here"])
    with pytest.raises(AssertionError):
        Synthesizer(llm=llm).synthesize("q", chunks)


def test_fit_to_budget_drops_worst_first():
    chunks = [mk_chunk(1, "A", score=0.1, text="x " * 300),
              mk_chunk(2, "B", score=0.9, text="y " * 300)]
    kept, dropped = fit_to_budget(chunks, budget=180)
    assert dropped == 1 and kept[0].project_id == 1  # kept the closer (0.1)


# --- scoped edge policies ---

class FakeSql:
    def __init__(self, result):
        self._result = result

    def ask(self, q):
        return self._result

    def execute_trusted(self, sql):
        return ["count"], [(1,)]      # every value-gate lookup: alive


class FakeSearcher:
    def __init__(self, chunks):
        self.chunks = chunks
        self.last_project_ids = "unset"

    def search(self, q, k=10, project_ids=None, source=None, dedup_projects=False):
        self.last_project_ids = project_ids
        return self.chunks


class R:  # minimal SqlResult stand-in
    def __init__(self, ok, rows=(), sql="SELECT DISTINCT p.id FROM project p",
                 error=None, retried=False):
        self.ok = ok
        self.rows = list(rows)
        self.sql = sql
        self.error = error
        self.retried = retried


def test_scoped_ok_narrows_to_ids():
    searcher = FakeSearcher([mk_chunk(2, "B")])
    h = ScopedRetriever(searcher, narrow_sql=FakeSql(R(True, rows=[(2,), (3,)])))
    res = h.retrieve("german battery projects", k=10)
    assert res.status == "ok" and res.project_ids == {2, 3}
    assert searcher.last_project_ids == {2, 3} and not res.weak_filter


def test_scoped_zero_match_is_the_answer():
    searcher = FakeSearcher([mk_chunk(1, "A")])
    h = ScopedRetriever(searcher, narrow_sql=FakeSql(R(True, rows=[])))
    res = h.retrieve("projects coordinated on the moon", k=10)
    assert res.status == "zero_match" and res.chunks == []
    assert searcher.last_project_ids == "unset"  # never widened to a search


def test_scoped_sql_failure_degrades_to_vector():
    searcher = FakeSearcher([mk_chunk(1, "A")])
    h = ScopedRetriever(searcher,
                        narrow_sql=FakeSql(R(False, error="Binder Error", retried=True)))
    res = h.retrieve("q", k=10)
    assert res.status == "sql_failed" and res.degraded == "sql_failed"
    assert searcher.last_project_ids is None  # searched everything

def test_scoped_weak_filter_flagged():
    ids = [(i,) for i in range(WEAK_FILTER + 1)]
    searcher = FakeSearcher([mk_chunk(1, "A")])
    h = ScopedRetriever(searcher, narrow_sql=FakeSql(R(True, rows=ids)))
    res = h.retrieve("all closed projects about energy", k=10)
    assert res.status == "ok" and res.weak_filter


def test_uses_subject_filter_detection():
    assert uses_subject_filter("SELECT p.id FROM project p WHERE topics LIKE '%x%'")
    assert uses_subject_filter("SELECT p.id FROM project p WHERE p.objective = 'x'")
    assert not uses_subject_filter("SELECT p.id FROM project p WHERE status = 'CLOSED'")
    assert not uses_subject_filter("SELECT DISTINCT id FROM project")


def test_uses_subject_filter_catches_the_topics_tables_singular_column():
    # hyb-06 / hyb-08 (2026-08-05): t.topic holds call codes, the regex only
    # knew the plural project column, so the zero-row filter ran and refused.
    assert uses_subject_filter(
        "SELECT p.id FROM project p JOIN topics t ON t.projectID = p.id "
        "WHERE t.topic LIKE '%graphene%'")
    assert uses_subject_filter(
        "SELECT p.id FROM project p JOIN topics t ON t.projectID = p.id "
        "WHERE t.topic = 'textiles'")


# --- filter provenance: telling the generator what the filter already did ---

SWEDISH = ("SELECT DISTINCT p.id FROM project p JOIN organization o ON "
           "o.projectID = p.id WHERE o.country = 'SE'")


def test_filter_note_names_the_count_and_quotes_the_query():
    note = filter_note(SWEDISH, 1234)
    assert "1,234 projects" in note
    assert SWEDISH in note              # verbatim, not paraphrased


def test_filter_note_is_suppressed_when_nothing_was_filtered():
    """The narrowing prompt returns this form for a question with no structured
    constraint. 'Every project satisfies: all projects' is noise that would only
    teach the model to over-assert."""
    assert filter_note("SELECT DISTINCT id FROM project", 35389) is None
    assert filter_note(None, 0) is None


def test_scoped_ok_carries_the_note():
    searcher = FakeSearcher([mk_chunk(2, "B")])
    h = ScopedRetriever(searcher,
                        narrow_sql=FakeSql(R(True, rows=[(2,), (3,)],
                                             sql=SWEDISH)))
    res = h.retrieve("swedish pest control projects", k=10)
    assert res.filter_note and "2 projects" in res.filter_note


def test_zero_match_and_sql_failure_carry_no_note():
    searcher = FakeSearcher([mk_chunk(1, "A")])
    zero = ScopedRetriever(
        searcher, narrow_sql=FakeSql(R(True, rows=[], sql=SWEDISH))
    ).retrieve("q", k=10)
    assert zero.filter_note is None          # nothing is synthesised at all

    # The filter was DROPPED here - announcing it would be a lie.
    failed = ScopedRetriever(
        searcher, narrow_sql=FakeSql(R(False, error="Binder Error"))
    ).retrieve("q", k=10)
    assert failed.filter_note is None


def test_synthesizer_puts_the_note_ahead_of_the_excerpts():
    llm = FakeLlm(["answer [A, 1]."])
    s = Synthesizer(llm=llm).synthesize("q", [mk_chunk(1, "A")],
                                        filter_note="FILTER-BLOCK")
    user = llm.seen[0][1]["content"]
    assert user.index("FILTER-BLOCK") < user.index("Excerpts:")
    assert s.trace["filter_note"] is True


def test_synthesizer_without_a_note_is_unchanged():
    """The vector route and eval/retrieval_run.py call this the old way; the
    prompt they build must be byte-identical to before the note existed."""
    with_kw = FakeLlm(["a [A, 1]."])
    without = FakeLlm(["a [A, 1]."])
    Synthesizer(llm=with_kw).synthesize("q", [mk_chunk(1, "A")],
                                        filter_note=None)
    Synthesizer(llm=without).synthesize("q", [mk_chunk(1, "A")])
    assert with_kw.seen[0] == without.seen[0]
    assert without.seen[0][1]["content"].startswith("Excerpts:")


def test_the_pre_filter_rule_is_in_the_frozen_system_prompt():
    assert "Structured filter already applied" in synth_mod.SYSTEM_PROMPT
    assert synth_mod.SYNTH_PROMPT_VERSION == "s2-provenance"


def test_narrowing_prompt_names_the_call_code_trap_and_owns_its_version():
    from src.retrieval.scoped import (NARROW_PROMPT_VERSION,
                                      build_id_narrowing_prompt)
    assert NARROW_PROMPT_VERSION == "narrow-v3"
    prompt = build_id_narrowing_prompt()
    assert "CALL CODES" in prompt
    assert "euroSciVocPath" in prompt          # the classification is allowed
    assert "parenthes" in prompt.lower()       # the OR-precedence rule


class QueueSql:
    """Returns successive results; records the questions it was asked."""
    def __init__(self, results):
        self.results = list(results)
        self.asked = []

    def ask(self, q):
        self.asked.append(q)
        return self.results.pop(0)

    def execute_trusted(self, sql):
        return ["count"], [(1,)]      # every value-gate lookup: alive


def test_scoped_subject_filter_triggers_corrective_reask():
    # First narrowing pollutes with a topic filter; the reminder re-ask returns
    # a clean metadata-only query, which must be the one used.
    bad = R(True, rows=[(9,)], sql="SELECT p.id FROM project p WHERE status='CLOSED' AND topics LIKE '%energy%'")
    good = R(True, rows=[(1,), (2,)], sql="SELECT p.id FROM project p WHERE status='CLOSED'")
    narrow = QueueSql([bad, good])
    searcher = FakeSearcher([mk_chunk(1, "A")])
    res = ScopedRetriever(searcher, narrow_sql=narrow).retrieve(
        "closed projects about energy storage", k=10)
    assert res.status == "ok" and res.project_ids == {1, 2}
    assert res.trace["subject_corrected"] is True
    assert len(narrow.asked) == 2 and "Reminder" in narrow.asked[1]


def test_scoped_dirty_reask_degrades_instead_of_executing():
    """Both attempts kept a subject filter. Running it anyway is how a call-code
    match became a confident zero_match refusal (hyb-06/hyb-08); the filter must
    drop and the search must run unfiltered."""
    bad = R(True, rows=[], sql="SELECT p.id FROM project p JOIN topics t ON "
            "t.projectID = p.id WHERE t.topic = 'textiles'")
    still_bad = R(True, rows=[], sql="SELECT p.id FROM project p WHERE "
                  "topics LIKE '%textiles%'")
    narrow = QueueSql([bad, still_bad])
    searcher = FakeSearcher([mk_chunk(1, "A")])
    res = ScopedRetriever(searcher, narrow_sql=narrow).retrieve(
        "textile recycling projects", k=10)
    assert res.status == "sql_failed" and res.degraded == "sql_failed"
    assert searcher.last_project_ids is None      # searched everything
    assert "subject filter" in res.trace["sql_error"]
    assert res.trace["subject_corrected"] is True


def test_scoped_failed_reask_keeps_the_dirty_query_out_too():
    # Re-ask errors entirely: the surviving query is still the dirty one, and
    # it must not run either.
    bad = R(True, rows=[(9,)], sql="SELECT p.id FROM project p WHERE "
            "objective ILIKE '%coating%'")
    broken = R(False, error="Binder Error")
    narrow = QueueSql([bad, broken])
    searcher = FakeSearcher([mk_chunk(1, "A")])
    res = ScopedRetriever(searcher, narrow_sql=narrow).retrieve("q", k=10)
    assert res.status == "sql_failed"
    assert "subject filter" in res.trace["sql_error"]


# --- constraint-driven narrowing (narrow-v3, 2026-08-05) ---


class FakeExtractor:
    """Stands in for Router.extract; counts calls."""

    def __init__(self, needs, constraints):
        from src.router.router import RouteFacts
        self.facts = RouteFacts(needs_project_text=needs,
                                structured_constraints=list(constraints))
        self.calls = 0

    def extract(self, question):
        self.calls += 1
        return self.facts


class GateSql(QueueSql):
    """QueueSql plus a fake database for the value gate: execute_trusted
    answers the gate's real lookup SQL from a dict of column -> stored
    values, euroSciVoc titles under the "euroscivoc" key. Parsing the SQL
    (rather than stubbing return values) exercises the exact queries the
    gate builds, quote escaping included."""

    _EV_COUNT = re.compile(
        r"SELECT count\(\*\) FROM euroscivoc WHERE euroSciVocTitle ILIKE "
        r"'%((?:[^']|'')*)%' OR euroSciVocPath ILIKE")
    _COL_COUNT = re.compile(
        r"SELECT count\(\*\) FROM (?:project|organization) WHERE "
        r"(\w+) (=|LIKE|ILIKE) '((?:[^']|'')*)'")
    _CANDIDATES = re.compile(
        r"SELECT DISTINCT (\w+) FROM \w+ WHERE \w+ ILIKE "
        r"'%((?:[^']|'')*)%' LIMIT 10")
    _MOST_COMMON = re.compile(
        r"SELECT (\w+) FROM \w+ WHERE \w+ IS NOT NULL GROUP BY")

    def __init__(self, results, values=None, alive_terms=()):
        super().__init__(results)
        self.values = {k: list(v) for k, v in (values or {}).items()}
        if alive_terms:
            self.values["euroscivoc"] = list(alive_terms)
        self.lookups = []

    def _stored(self, column):
        key = "euroscivoc" if column == "euroSciVocTitle" else column
        return self.values.get(key, [])

    @staticmethod
    def _unesc(lit):
        return lit.replace("''", "'")

    @staticmethod
    def _like(value, pattern, ci):
        rx = "".join(".*" if ch == "%" else "." if ch == "_" else
                     re.escape(ch) for ch in pattern)
        return re.fullmatch(rx, value, re.IGNORECASE if ci else 0) is not None

    def execute_trusted(self, sql):
        self.lookups.append(sql)
        if m := self._EV_COUNT.search(sql):
            term = self._unesc(m.group(1)).lower()
            stored = self.values.get("euroscivoc", [])
            return ["count"], [(sum(term in v.lower() for v in stored),)]
        if m := self._COL_COUNT.search(sql):
            col, op, lit = m.group(1), m.group(2), self._unesc(m.group(3))
            vals = self._stored(col)
            n = (sum(v == lit for v in vals) if op == "=" else
                 sum(self._like(v, lit, ci=op == "ILIKE") for v in vals))
            return ["count"], [(n,)]
        if m := self._CANDIDATES.search(sql):
            frag = self._unesc(m.group(2)).lower()
            hits = [v for v in self._stored(m.group(1)) if frag in v.lower()]
            return [m.group(1)], [(v,) for v in hits[:10]]
        if m := self._MOST_COMMON.search(sql):
            return [m.group(1)], [(v,) for v in self._stored(m.group(1))[:10]]
        raise AssertionError(f"unexpected lookup SQL: {sql}")


def test_scoped_constraint_list_becomes_the_narrowing_instruction():
    narrow = QueueSql([R(True, rows=[(1,), (2,)],
                         sql="SELECT DISTINCT p.id FROM project p JOIN "
                             "organization o ON o.projectID = p.id WHERE "
                             "o.country = 'SE'")])
    searcher = FakeSearcher([mk_chunk(1, "A")])
    res = ScopedRetriever(searcher, narrow_sql=narrow).retrieve(
        "swedish pest control projects", k=10,
        constraints=["participant country SE"], constraints_source="router")
    assert res.status == "ok" and res.project_ids == {1, 2}
    assert res.constraints == ["participant country SE"]
    assert res.trace["constraints_source"] == "router"
    sent = narrow.asked[0]
    assert "Constraints to translate" in sent
    assert "- participant country SE" in sent
    assert "wording context ONLY" in sent
    assert "swedish pest control projects" in sent


def test_scoped_empty_constraint_list_skips_the_narrowing_model():
    narrow = QueueSql([])            # any ask() call would pop and crash
    searcher = FakeSearcher([mk_chunk(1, "A")])
    res = ScopedRetriever(searcher, narrow_sql=narrow).retrieve(
        "purely topical question", k=10, constraints=[],
        constraints_source="router")
    assert res.status == "ok" and res.degraded is None
    assert res.sql is None and narrow.asked == []
    assert searcher.last_project_ids is None      # unfiltered search ran
    assert res.trace["no_constraints"] is True


def test_scoped_calls_the_extractor_when_no_constraints_arrive():
    narrow = QueueSql([R(True, rows=[(5,)],
                         sql="SELECT DISTINCT p.id FROM project p WHERE "
                             "p.fundingScheme = 'ERC-STG'")])
    extractor = FakeExtractor(True, ["funding scheme ERC-STG"])
    searcher = FakeSearcher([mk_chunk(5, "E")])
    res = ScopedRetriever(searcher, narrow_sql=narrow,
                          extractor=extractor).retrieve("q", k=10)
    assert extractor.calls == 1
    assert res.trace["constraints_source"] == "scoped"
    assert "- funding scheme ERC-STG" in narrow.asked[0]


def test_scoped_extractor_skipped_when_the_caller_already_extracted():
    narrow = QueueSql([R(True, rows=[(5,)])])
    extractor = FakeExtractor(True, ["should not be used"])
    searcher = FakeSearcher([mk_chunk(5, "E")])
    ScopedRetriever(searcher, narrow_sql=narrow, extractor=extractor).retrieve(
        "q", k=10, constraints=["country DE"], constraints_source="router")
    assert extractor.calls == 0


def test_scoped_extraction_without_facts_narrows_from_the_raw_question():
    """A fallback (or an archived mode-only prompt) reports no facts. Unknown
    is not empty: the narrowing model must read the question as before, never
    short-circuit to an unfiltered search."""
    narrow = QueueSql([R(True, rows=[(5,)])])
    extractor = FakeExtractor(None, [])
    searcher = FakeSearcher([mk_chunk(5, "E")])
    res = ScopedRetriever(searcher, narrow_sql=narrow,
                          extractor=extractor).retrieve("the question", k=10)
    assert extractor.calls == 1
    assert narrow.asked[0] == "the question"      # raw, no constraint block
    assert res.trace["constraints_source"] == "fallback-raw"


def test_scoped_fallback_source_prevents_a_second_extraction():
    # ask.py already ran the router and got no facts; scoped must not pay for
    # another extraction call.
    narrow = QueueSql([R(True, rows=[(5,)])])
    extractor = FakeExtractor(True, ["would be wrong to use"])
    searcher = FakeSearcher([mk_chunk(5, "E")])
    ScopedRetriever(searcher, narrow_sql=narrow, extractor=extractor).retrieve(
        "q", k=10, constraints=None, constraints_source="fallback-raw")
    assert extractor.calls == 0
    assert narrow.asked[0] == "q"


# --- the value gate (euroSciVoc terms folded in) ---

EV_DEAD = ("SELECT DISTINCT p.id FROM project p JOIN euroscivoc e ON "
           "e.projectID = p.id WHERE e.euroSciVocPath LIKE '%/viticultura%' "
           "AND p.fundingScheme = 'SME-1'")
EV_ALIVE = ("SELECT DISTINCT p.id FROM project p JOIN euroscivoc e ON "
            "e.projectID = p.id WHERE e.euroSciVocPath LIKE '%/viticulture%' "
            "AND p.fundingScheme = 'SME-1'")


def test_filter_literals_collects_every_guarded_comparison():
    sql = ("SELECT DISTINCT p.id FROM project p "
           "JOIN organization o ON o.projectID = p.id "
           "JOIN euroscivoc e ON e.projectID = p.id "
           "WHERE p.fundingScheme = 'SME-1' AND p.fundingScheme = 'SME-1' "
           "AND o.country ILIKE 'se' AND status LIKE 'CLOSED' "
           "AND p.startDate >= DATE '2020-01-01' "
           "AND e.euroSciVocPath LIKE '%/viticulture%'")
    got = filter_literals(sql)
    assert ("fundingScheme", "=", "SME-1") in got
    assert ("country", "ILIKE", "se") in got          # qualified or bare
    assert ("status", "LIKE", "CLOSED") in got
    assert ("euroscivoc", "term", "viticulture") in got
    assert len([g for g in got if g[0] == "fundingScheme"]) == 1  # deduped
    assert not any(lit == "2020-01-01" for _, _, lit in got)  # dates unguarded
    assert filter_literals(None) == []


def test_value_gate_reask_keeps_the_list_and_names_the_dead_term():
    # The re-ask carries the FULL constraint list plus a corrective hint - the
    # model corrects or drops the value itself; nothing is silently removed.
    narrow = GateSql([R(True, rows=[], sql=EV_DEAD),
                      R(True, rows=[(1,), (2,)], sql=EV_ALIVE)],
                     values={"fundingScheme": ["SME-1"]},
                     alive_terms=["viticulture"])
    searcher = FakeSearcher([mk_chunk(1, "A")])
    res = ScopedRetriever(searcher, narrow_sql=narrow).retrieve(
        "q", k=10,
        constraints=["classified under viticultura", "funding scheme SME-1"],
        constraints_source="router")
    assert res.status == "ok" and res.project_ids == {1, 2}
    assert res.trace["dead_values"] == [["euroscivoc", "viticultura"]]
    assert res.trace["value_reasked"] is True
    hint = narrow.asked[1]
    assert "- classified under viticultura" in hint   # the list is intact
    assert "'viticultura' does not exist" in hint
    assert "'viticulture'" in hint                    # the real candidate


def test_value_gate_corrects_a_dead_funding_scheme():
    # hyb-09's shape: the model wrote the scheme's NAME, the column stores the
    # CODE. The hint's candidates come from the first-word fragment lookup.
    dead_sql = ("SELECT DISTINCT p.id FROM project p WHERE "
                "p.fundingScheme = 'SME Instrument phase 1'")
    good_sql = ("SELECT DISTINCT p.id FROM project p WHERE "
                "p.fundingScheme = 'SME-1'")
    narrow = GateSql(
        [R(True, rows=[], sql=dead_sql), R(True, rows=[(7,), (8,)],
                                           sql=good_sql)],
        values={"fundingScheme": ["SME-1", "SME-2", "SME", "SME-2b",
                                  "MSCA-IF"]})
    searcher = FakeSearcher([mk_chunk(7, "A")])
    res = ScopedRetriever(searcher, narrow_sql=narrow).retrieve(
        "q", k=10, constraints=["funding scheme SME Instrument phase 1"],
        constraints_source="router")
    assert res.status == "ok" and res.project_ids == {7, 8}
    assert res.trace["dead_values"] == [["fundingScheme",
                                         "SME Instrument phase 1"]]
    hint = narrow.asked[1]
    assert "'SME Instrument phase 1' does not exist" in hint
    assert "'SME-1'" in hint and "'SME-2b'" in hint
    assert "MSCA-IF" not in hint    # fragment matched, no most-common fallback


def test_value_gate_dead_value_with_no_candidates_lets_the_model_drop_it():
    # hyb-06's shape: an activityType no candidate list can save. The hint
    # falls back to the column's stored values and the model drops the clause,
    # keeping the rest of the filter.
    dead_sql = ("SELECT DISTINCT p.id FROM project p JOIN organization o ON "
                "o.projectID = p.id WHERE o.activityType = "
                "'ANTIBIOTIC-RESISTANT BACTERIAL INFECTIONS' "
                "AND o.country = 'SE'")
    kept_sql = ("SELECT DISTINCT p.id FROM project p JOIN organization o ON "
                "o.projectID = p.id WHERE o.country = 'SE'")
    narrow = GateSql(
        [R(True, rows=[], sql=dead_sql), R(True, rows=[(3,)], sql=kept_sql)],
        values={"activityType": ["HES", "PRC", "REC", "PUB", "OTH"],
                "country": ["SE", "DE"]})
    searcher = FakeSearcher([mk_chunk(3, "A")])
    res = ScopedRetriever(searcher, narrow_sql=narrow).retrieve("q", k=10)
    assert res.status == "ok" and res.project_ids == {3}
    assert res.trace["dead_values"] == \
        [["activityType", "ANTIBIOTIC-RESISTANT BACTERIAL INFECTIONS"]]
    assert "'HES'" in narrow.asked[1]     # most-common fallback candidates
    assert searcher.last_project_ids == {3}


def test_value_gate_live_pattern_passes_without_a_reask():
    sql = ("SELECT DISTINCT p.id FROM project p WHERE "
           "p.fundingScheme LIKE 'MSCA%'")
    narrow = GateSql([R(True, rows=[(1,)], sql=sql)],
                     values={"fundingScheme": ["MSCA-IF", "RIA"]})
    searcher = FakeSearcher([mk_chunk(1, "A")])
    res = ScopedRetriever(searcher, narrow_sql=narrow).retrieve("q", k=10)
    assert res.status == "ok" and len(narrow.asked) == 1
    assert res.trace["dead_values"] == []
    assert res.trace["value_reasked"] is False


def test_value_gate_dirty_reask_degrades_to_unfiltered():
    # The re-ask wrote ANOTHER dead value: the filter is undeliverable. Drop
    # it loudly and search unfiltered - never let the dead filter run into a
    # confident zero_match refusal.
    dead_sql = ("SELECT DISTINCT p.id FROM project p WHERE "
                "p.fundingScheme = 'SME Instrument phase 1'")
    still_dead = R(True, rows=[], sql="SELECT DISTINCT p.id FROM project p "
                                      "WHERE p.fundingScheme = 'SME One'")
    narrow = GateSql([R(True, rows=[], sql=dead_sql), still_dead],
                     values={"fundingScheme": ["SME-1"]})
    searcher = FakeSearcher([mk_chunk(1, "A")])
    res = ScopedRetriever(searcher, narrow_sql=narrow).retrieve(
        "q", k=10, constraints=["funding scheme SME Instrument phase 1"],
        constraints_source="router")
    assert res.status == "value_not_found"
    assert res.degraded == "value_not_found"
    assert len(narrow.asked) == 2
    assert searcher.last_project_ids is None      # unfiltered search ran
    assert res.trace["dead_values"] == [["fundingScheme",
                                         "SME Instrument phase 1"]]
    assert res.trace["value_reasked"] is True
    assert "dead_terms" not in res.trace          # the old label is gone
    assert "term_reasked" not in res.trace


def test_malformed_null_test_detection():
    # The shape hyb-06's re-ask produced: a comparison, then IS NULL on its
    # RESULT. True only where the column itself is null, so the filter empties.
    assert uses_malformed_null_test(
        "SELECT p.id FROM project p WHERE e.euroSciVocTitle = 'graphene' "
        "IS NULL")
    assert uses_malformed_null_test(
        "SELECT p.id FROM project p WHERE (p.status = 'CLOSED') IS NOT NULL")
    assert uses_malformed_null_test(
        "SELECT p.id FROM project p WHERE o.country = 'SE' AND "
        "p.fundingScheme LIKE 'MSCA%' IS NULL")
    # Legal SQL the check must never touch. hyb-16 really wrote the second
    # one; flagging it would degrade a working filter.
    assert not uses_malformed_null_test(
        "SELECT p.id FROM project p WHERE p.acronym IS NOT NULL")
    assert not uses_malformed_null_test(
        "SELECT p.id FROM project p WHERE p.fundingScheme = 'ERC-STG' "
        "AND p.title IS NOT NULL AND p.objective IS NOT NULL")
    assert not uses_malformed_null_test(
        "SELECT p.id FROM project p WHERE p.status = 'CLOSED'")
    # An IS NULL inside a VALUE is data, not syntax.
    assert not uses_malformed_null_test(
        "SELECT p.id FROM project p WHERE p.acronym = 'X IS NULL'")
    assert not uses_malformed_null_test(None)


def test_value_gate_rejects_a_reask_that_empties_the_filter_instead():
    """hyb-06 exactly: the gate caught the dead term, and the model answered
    'drop that condition' by writing a clause that matches nothing. Every
    value in it is live, so only the structural check can stop it - and it
    must, or the empty filter becomes a third false refusal."""
    dead_sql = ("SELECT DISTINCT p.id FROM project p JOIN organization o ON "
                "o.projectID = p.id JOIN euroscivoc e ON e.projectID = p.id "
                "WHERE e.euroSciVocPath LIKE '%/graphene%' AND "
                "o.country = 'SE' AND e.euroSciVocPath LIKE "
                "'% antibiotic-resistant bacterial infections%'")
    empty_sql = ("SELECT DISTINCT p.id FROM project p JOIN organization o ON "
                 "o.projectID = p.id JOIN euroscivoc e ON e.projectID = p.id "
                 "WHERE e.euroSciVocPath LIKE '%/graphene%' AND "
                 "o.country = 'SE' AND e.euroSciVocTitle = 'graphene' "
                 "IS NULL")
    narrow = GateSql([R(True, rows=[], sql=dead_sql),
                      R(True, rows=[], sql=empty_sql)],
                     values={"country": ["SE"]},
                     alive_terms=["graphene"])
    searcher = FakeSearcher([mk_chunk(1, "A")])
    res = ScopedRetriever(searcher, narrow_sql=narrow).retrieve(
        "q", k=10,
        constraints=["euroSciVoc includes graphene",
                     "participant country Sweden",
                     "topic antibiotic-resistant bacterial infections"],
        constraints_source="router")
    assert res.status == "value_not_found"        # NOT zero_match
    assert res.trace["reask_rejected"] == "malformed IS NULL test"
    assert searcher.last_project_ids is None      # unfiltered search ran


def test_malformed_first_attempt_degrades_before_the_value_gate():
    # No dead value to trigger a re-ask - the clause is simply unrunnable, so
    # it must never execute into a zero_match.
    sql = ("SELECT DISTINCT p.id FROM project p WHERE "
           "p.fundingScheme = 'ERC-STG' IS NULL")
    narrow = GateSql([R(True, rows=[], sql=sql)],
                     values={"fundingScheme": ["ERC-STG"]})
    searcher = FakeSearcher([mk_chunk(1, "A")])
    res = ScopedRetriever(searcher, narrow_sql=narrow).retrieve("q", k=10)
    assert res.status == "sql_failed" and res.degraded == "sql_failed"
    assert "IS NULL" in res.trace["sql_error"]
    assert searcher.last_project_ids is None
    assert narrow.lookups == []                   # rejected before any lookup


def test_value_gate_records_why_a_reask_was_rejected():
    dead_sql = ("SELECT DISTINCT p.id FROM project p WHERE "
                "p.fundingScheme = 'SME Instrument phase 1'")
    narrow = GateSql([R(True, rows=[], sql=dead_sql),
                      R(False, error="Binder Error")],
                     values={"fundingScheme": ["SME-1"]})
    searcher = FakeSearcher([mk_chunk(1, "A")])
    res = ScopedRetriever(searcher, narrow_sql=narrow).retrieve("q", k=10)
    assert res.status == "value_not_found"
    assert res.trace["reask_rejected"] == "sql error"


def test_value_gate_lets_an_all_valid_empty_intersection_refuse():
    # The gate must never soften a genuine zero_match: every value exists in
    # its own column, the intersection is empty, the refusal stands.
    narrow = GateSql([R(True, rows=[], sql=EV_ALIVE)],
                     values={"fundingScheme": ["SME-1"]},
                     alive_terms=["viticulture"])
    searcher = FakeSearcher([mk_chunk(1, "A")])
    res = ScopedRetriever(searcher, narrow_sql=narrow).retrieve(
        "q", k=10, constraints=["classified under viticulture",
                                "funding scheme SME-1"],
        constraints_source="router")
    assert res.status == "zero_match" and res.chunks == []
    assert res.trace["dead_values"] == []


def test_value_gate_escapes_quotes_in_the_lookup():
    sql = ("SELECT DISTINCT p.id FROM project p WHERE "
           "p.fundingScheme = 'O''Brien scheme'")
    narrow = GateSql([R(True, rows=[(1,)], sql=sql)],
                     values={"fundingScheme": ["O'Brien scheme"]})
    searcher = FakeSearcher([mk_chunk(1, "A")])
    res = ScopedRetriever(searcher, narrow_sql=narrow).retrieve("q", k=10)
    assert res.status == "ok"                     # the escaped lookup matched
    assert any("'O''Brien scheme'" in look for look in narrow.lookups)


def test_scoped_non_id_first_column_degrades_not_crashes():
    # Nothing upstream guarantees column 0 holds ids; a VARCHAR result must
    # degrade like any other unusable filter, not raise out of retrieve().
    r = R(True, rows=[("BATTERY-X",), ("SOLAR-Y",)],
          sql="SELECT acronym FROM project")
    searcher = FakeSearcher([mk_chunk(1, "A")])
    res = ScopedRetriever(searcher, narrow_sql=FakeSql(r)).retrieve("q", k=10)
    assert res.status == "sql_failed" and res.degraded == "sql_failed"
    assert "project ids" in res.trace["sql_error"]
    assert searcher.last_project_ids is None


# --- the runtime retrieval stack (2026-08-03: dense-only -> hybrid_rerank) ---
#
# The wiring these cover is easy to break silently: Ask used to build a bare
# VectorSearcher, and a regression back to that would still answer questions,
# just worse and with no marker in the trace saying so.

def _ask(monkeypatch, tmp_path, **kw):
    """An Ask with every external dependency faked - no servers, no CLI."""
    from src import ask as ask_mod
    llm = FakeLlm([])
    llm.model = "fake-model"          # Ask pins llm.model into versions
    monkeypatch.setattr(ask_mod, "make_llm", lambda: llm)
    return ask_mod.Ask(log_path=tmp_path / "ask.jsonl", **kw)


def test_ask_builds_the_configured_runtime_retriever(monkeypatch, tmp_path):
    from src import ask as ask_mod
    from src.config import RUNTIME_RETRIEVER

    built = []

    def fake_build(name):
        built.append(name)
        return FakeSearcher([])

    monkeypatch.setattr(ask_mod, "build_retriever", fake_build)
    a = _ask(monkeypatch, tmp_path)
    assert built == [RUNTIME_RETRIEVER]
    assert RUNTIME_RETRIEVER == "hybrid_rerank"


def test_ask_shares_one_retriever_with_the_scoped_path(monkeypatch, tmp_path):
    # Both routes must see identical retrieval, and the lexical connection /
    # FAISS index / rerank client must be constructed once, not twice.
    from src import ask as ask_mod

    monkeypatch.setattr(ask_mod, "build_retriever", lambda name: FakeSearcher([]))
    a = _ask(monkeypatch, tmp_path)
    assert a.scoped.searcher is a.retriever


def test_ask_records_the_retriever_in_versions(monkeypatch, tmp_path):
    # Absent from a log row = dense-only, pre-2026-08-03. Without this the
    # re-baseline is unreadable.
    from src import ask as ask_mod
    from src.config import RERANKER_MODEL

    monkeypatch.setattr(ask_mod, "build_retriever", lambda name: FakeSearcher([]))
    a = _ask(monkeypatch, tmp_path)
    assert a.versions["retriever"] == "hybrid_rerank"
    assert a.versions["rerank_model"] == RERANKER_MODEL


def test_ask_omits_rerank_model_when_the_stack_does_not_rerank(monkeypatch, tmp_path):
    # Recording a reranker that never scored anything would be a false trace.
    from src import ask as ask_mod

    monkeypatch.setattr(ask_mod, "build_retriever", lambda name: FakeSearcher([]))
    a = _ask(monkeypatch, tmp_path, retriever_name="dense")
    assert a.versions["retriever"] == "dense"
    assert "rerank_model" not in a.versions


def test_ask_vector_route_uses_the_shared_retriever(monkeypatch, tmp_path):
    from src import ask as ask_mod

    searcher = FakeSearcher([mk_chunk(1, "A")])
    monkeypatch.setattr(ask_mod, "build_retriever", lambda name: searcher)
    a = _ask(monkeypatch, tmp_path)
    a.synth = Synthesizer(llm=FakeLlm(["grounded answer [A, 1]."]))
    res = a.ask("a topical question", k=10, mode="vector")
    assert res.mode == "vector"
    assert searcher.last_project_ids is None  # unfiltered, and it WAS called


class SpyScoped:
    """Records what retrieve() was given; returns a fixed result."""

    def __init__(self, result):
        self.result = result
        self.got = None

    def retrieve(self, question, k=10, constraints=None,
                 constraints_source=None):
        self.got = {"constraints": constraints, "source": constraints_source}
        return self.result


class FixedRouter:
    def __init__(self, decision):
        self.decision = decision

    def route(self, question):
        return self.decision


def _scoped_ok(chunk):
    from src.retrieval.scoped import ScopedResult
    return ScopedResult(question="q", status="ok", project_ids={1},
                        chunks=[chunk])


def test_ask_router_condition_reuses_the_decisions_constraints(
        monkeypatch, tmp_path):
    # One extraction per question: the routed constraints flow down, scoped
    # must not extract again.
    from src import ask as ask_mod
    from src.router.router import RouteDecision

    monkeypatch.setattr(ask_mod, "build_retriever",
                        lambda name: FakeSearcher([]))
    a = _ask(monkeypatch, tmp_path)
    a.router = FixedRouter(RouteDecision(
        mode="scoped", reason="r", needs_project_text=True,
        structured_constraints=["participant country SE"]))
    spy = SpyScoped(_scoped_ok(mk_chunk(1, "A")))
    a.scoped = spy
    a.synth = Synthesizer(llm=FakeLlm(["answer [A, 1]."]))
    a.ask("q", k=10)
    assert spy.got == {"constraints": ["participant country SE"],
                       "source": "router"}


def test_ask_router_fallback_hands_scoped_no_facts_and_no_reextraction(
        monkeypatch, tmp_path):
    from src import ask as ask_mod
    from src.router.router import RouteDecision

    monkeypatch.setattr(ask_mod, "build_retriever",
                        lambda name: FakeSearcher([]))
    a = _ask(monkeypatch, tmp_path)
    a.router = FixedRouter(RouteDecision(
        mode="scoped", reason="fallback", router_fallback=True))
    spy = SpyScoped(_scoped_ok(mk_chunk(1, "A")))
    a.scoped = spy
    a.synth = Synthesizer(llm=FakeLlm(["answer [A, 1]."]))
    a.ask("q", k=10)
    assert spy.got == {"constraints": None, "source": "fallback-raw"}


def test_ask_forced_mode_leaves_extraction_to_scoped(monkeypatch, tmp_path):
    # always-hybrid: no router decision exists; ScopedRetriever extracts.
    from src import ask as ask_mod

    monkeypatch.setattr(ask_mod, "build_retriever",
                        lambda name: FakeSearcher([]))
    a = _ask(monkeypatch, tmp_path)
    spy = SpyScoped(_scoped_ok(mk_chunk(1, "A")))
    a.scoped = spy
    a.synth = Synthesizer(llm=FakeLlm(["answer [A, 1]."]))
    a.ask("q", k=10, mode="scoped")
    assert spy.got == {"constraints": None, "source": None}


def test_ask_wires_the_router_as_the_scoped_extractor(monkeypatch, tmp_path):
    from src import ask as ask_mod

    monkeypatch.setattr(ask_mod, "build_retriever",
                        lambda name: FakeSearcher([]))
    a = _ask(monkeypatch, tmp_path)
    assert a.scoped.extractor is a.router


def test_ask_scoped_route_tells_the_generator_what_the_filter_did(
        monkeypatch, tmp_path):
    """End to end: the note the ScopedRetriever built has to reach the
    generator's prompt, or the route hedges on its own filter."""
    from src import ask as ask_mod

    searcher = FakeSearcher([mk_chunk(1, "A")])
    monkeypatch.setattr(ask_mod, "build_retriever", lambda name: searcher)
    a = _ask(monkeypatch, tmp_path)
    a.scoped = ScopedRetriever(
        searcher, narrow_sql=FakeSql(R(True, rows=[(1,)], sql=SWEDISH)))
    synth_llm = FakeLlm(["PEST-BIN has Swedish participants [A, 1]."])
    a.synth = Synthesizer(llm=synth_llm)

    res = a.ask("swedish pest control projects", k=10, mode="scoped")

    assert SWEDISH in synth_llm.seen[0][1]["content"]
    assert res.trace["filter_note_passed"] is True
    assert res.trace["rows_passed_to_gen"] == 0    # a description, not rows
    assert SWEDISH in res.filter_note


def test_ask_prefixes_the_value_not_found_note(monkeypatch, tmp_path):
    # The degrade must be DISCLOSED to the reader: the answer came from an
    # unfiltered search because a filter value does not exist.
    from src import ask as ask_mod
    from src.retrieval.scoped import ScopedResult

    monkeypatch.setattr(ask_mod, "build_retriever",
                        lambda name: FakeSearcher([]))
    a = _ask(monkeypatch, tmp_path)
    a.scoped = SpyScoped(ScopedResult(
        question="q", status="value_not_found", degraded="value_not_found",
        chunks=[mk_chunk(1, "A")]))
    a.synth = Synthesizer(llm=FakeLlm(["answer [A, 1]."]))
    res = a.ask("q", k=10, mode="scoped")
    assert res.answer.startswith("[Note: the structured filter was dropped")
    assert "does not exist in the database" in res.answer
    assert res.degraded == "value_not_found"
