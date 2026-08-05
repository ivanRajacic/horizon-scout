"""M4 unit tests: router fallback, synthesis citation post-check + context
assertion, scoped edge policies. No servers needed - LLM and searcher faked."""

import json

import pytest

from src.retrieval.scoped import (WEAK_FILTER, ScopedRetriever, filter_note,
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
