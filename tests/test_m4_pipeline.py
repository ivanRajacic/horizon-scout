"""M4 unit tests: router fallback, synthesis citation post-check + context
assertion, scoped edge policies. No servers needed - LLM and searcher faked."""

import pytest

from src.retrieval.scoped import (WEAK_FILTER, ScopedRetriever,
                                  uses_subject_filter)
from src.retrieval.vector_search import SearchResult
from src.router.router import Router
from src.synthesis.synthesizer import Synthesizer, fit_to_budget
from src.synthesis import synthesizer as synth_mod


class FakeLlm:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, messages, **kw):
        self.calls += 1
        return self.responses.pop(0)


def mk_chunk(pid, acr, score=0.5, text="some project content here", section="summary"):
    return SearchResult(chunk_id=f"{pid}-c", project_id=pid, acronym=acr,
                        title=f"{acr} title", source="report", section=section,
                        score=score, text=text)


# --- router ---

def test_router_parses_valid_json():
    r = Router(llm=FakeLlm(['{"mode": "sql", "reason": "count"}']))
    d = r.route("how many projects?")
    assert d.mode == "sql" and not d.router_fallback


def test_router_extracts_json_from_noise():
    r = Router(llm=FakeLlm(['Sure! {"mode": "vector", "reason": "topic"} ok']))
    assert r.route("q").mode == "vector"


def test_router_retries_then_succeeds():
    llm = FakeLlm(["not json at all", '{"mode": "scoped", "reason": "x"}'])
    d = Router(llm=llm).route("q")
    assert d.mode == "scoped" and not d.router_fallback and llm.calls == 2


def test_router_fallback_is_visible():
    llm = FakeLlm(["garbage", "still garbage"])
    d = Router(llm=llm).route("q")
    assert d.mode == "scoped" and d.router_fallback and llm.calls == 2


def test_router_rejects_invalid_mode():
    llm = FakeLlm(['{"mode": "banana", "reason": "x"}', "junk"])
    d = Router(llm=llm).route("q")
    assert d.router_fallback


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


class QueueSql:
    """Returns successive results; records the questions it was asked."""
    def __init__(self, results):
        self.results = list(results)
        self.asked = []

    def ask(self, q):
        self.asked.append(q)
        return self.results.pop(0)


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
