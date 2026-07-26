"""Cost/token recording: envelope parsing, attribution across the concurrency
shapes judging actually uses, and the promise that a tracing bug never fails a
run. No `claude` CLI - the transport is faked."""

import asyncio
import threading

import pytest

from src import claude_cli
from src.claude_cli import ClaudeCliError, call_claude_gated
from src.eval import usage


@pytest.fixture(autouse=True)
def clean_recorder():
    usage.reset()
    yield
    usage.reset()


def envelope(cost=0.01, out=50, model="claude-haiku-4-5-20251001"):
    return {"type": "result", "result": "ok", "is_error": False,
            "total_cost_usd": cost, "duration_ms": 1234, "num_turns": 1,
            "usage": {"input_tokens": 9, "cache_read_input_tokens": 2000,
                      "cache_creation_input_tokens": 100,
                      "output_tokens": out},
            "modelUsage": {model: {"costUSD": cost}}}


# --- parsing ---

def test_records_every_field_off_the_envelope():
    usage.record_envelope(envelope(cost=0.25, out=70), "haiku")
    (r,) = usage.snapshot()
    assert (r.cost_usd, r.output, r.input_fresh) == (0.25, 70, 9)
    assert (r.cache_read, r.cache_creation) == (2000, 100)
    assert (r.duration_ms, r.num_turns) == (1234, 1)


def test_resolved_model_comes_from_model_usage_not_the_alias():
    """The alias we ask for is not the model that served it; pin the latter."""
    usage.record_envelope(envelope(model="claude-haiku-4-5-20251001"), "haiku")
    (r,) = usage.snapshot()
    assert r.model == "haiku"
    assert r.model_resolved == "claude-haiku-4-5-20251001"


def test_missing_model_usage_falls_back_to_the_requested_model():
    usage.record_envelope({"total_cost_usd": 0.1}, "sonnet")
    assert usage.snapshot()[0].model_resolved == "sonnet"


# A real `claude -p --model claude-sonnet-5` envelope: the harness spends a
# little Haiku on its own overhead, and dict order puts that overhead FIRST.
TWO_MODEL = {
    "total_cost_usd": 0.0812512, "duration_ms": 3000, "num_turns": 1,
    "usage": {"input_tokens": 2, "output_tokens": 4,
              "cache_read_input_tokens": 35024,
              "cache_creation_input_tokens": 11682},
    "modelUsage": {"claude-haiku-4-5-20251001": {"costUSD": 0.000586},
                   "claude-sonnet-5": {"costUSD": 0.0806652}},
}


def test_a_sonnet_call_is_not_reported_as_haiku():
    """The bug this test exists for: taking modelUsage's first key labelled
    every Sonnet judge call 'haiku', because the harness's own overhead model
    is listed first. A trace that lies about which model ran is worse than no
    trace - and role separation (Haiku generates, Sonnet judges) is exactly
    what these records are supposed to evidence."""
    usage.record_envelope(TWO_MODEL, "claude-sonnet-5")
    (r,) = usage.snapshot()
    assert r.model_resolved == "claude-sonnet-5"
    assert r.model_costs["claude-haiku-4-5-20251001"] == pytest.approx(0.000586)


def test_the_requested_model_wins_even_when_it_is_not_the_dearest():
    cheap_sonnet = {**TWO_MODEL, "modelUsage": {
        "claude-haiku-4-5-20251001": {"costUSD": 5.0},
        "claude-sonnet-5": {"costUSD": 0.01}}}
    usage.record_envelope(cheap_sonnet, "sonnet")     # alias, not the full id
    assert usage.snapshot()[0].model_resolved == "claude-sonnet-5"


def test_an_unrecognized_request_falls_back_to_the_dearest_model():
    usage.record_envelope(TWO_MODEL, "some-alias-we-do-not-know")
    assert usage.snapshot()[0].model_resolved == "claude-sonnet-5"


def test_per_model_cost_split_rolls_up():
    usage.record_envelope(TWO_MODEL, "claude-sonnet-5")
    usage.record_envelope(TWO_MODEL, "claude-sonnet-5")
    split = usage.total(usage.snapshot()).as_dict()["cost_by_model"]
    assert split["claude-sonnet-5"] == pytest.approx(0.1613304, abs=1e-6)
    assert split["claude-haiku-4-5-20251001"] == pytest.approx(0.001172)


@pytest.mark.parametrize("bad", [None, "a string", 42,
                                 {"usage": "not a dict"},
                                 {"total_cost_usd": "NaN-ish"}])
def test_a_malformed_envelope_never_raises(bad):
    """Instrumentation must never be the thing that fails a run."""
    usage.record_envelope(bad, "haiku")          # must not raise
    assert all(r.cost_usd == 0.0 for r in usage.snapshot())


# --- attribution ---

def test_unlabelled_calls_are_kept_not_dropped():
    usage.record_envelope(envelope(cost=0.5), "haiku")
    (r,) = usage.snapshot()
    assert r.label == usage.UNATTRIBUTED and r.stage == usage.UNATTRIBUTED
    assert usage.total(usage.snapshot()).cost_usd == 0.5


def test_stage_labels_and_restores():
    with usage.stage("sql-01", "gen"):
        usage.record_envelope(envelope(), "haiku")
        assert usage.current_stage() == ("sql-01", "gen")
    usage.record_envelope(envelope(), "haiku")
    labels = [r.label for r in usage.snapshot()]
    assert labels == ["sql-01", usage.UNATTRIBUTED]


def test_stage_nests():
    with usage.stage("q1", "gen"):
        with usage.stage("q1", "judge"):
            usage.record_envelope(envelope(), "haiku")
        usage.record_envelope(envelope(), "haiku")
    assert [r.stage for r in usage.snapshot()] == ["judge", "gen"]


def test_attribution_survives_asyncio_gather_and_to_thread():
    """The case a thread-local gets WRONG. Judging runs under asyncio.gather
    (ragas_judge.judge_batch) and asyncio.to_thread (ragas_backend), so a
    thread-local would lose every judge call and report a run as costing
    generation only."""
    async def one(qid):
        with usage.stage(qid, "judge"):
            # the metric fan-out inside a case...
            await asyncio.gather(
                asyncio.to_thread(usage.record_envelope, envelope(), "sonnet"),
                asyncio.to_thread(usage.record_envelope, envelope(), "sonnet"))

    async def batch():
        await asyncio.gather(*(one(f"q{i}") for i in range(4)))

    asyncio.run(batch())
    by_q = usage.by_label(usage.snapshot())
    assert sorted(by_q) == ["q0", "q1", "q2", "q3"]
    assert all(len(v) == 2 for v in by_q.values())
    assert all(r.stage == "judge" for r in usage.snapshot())


def test_concurrent_threads_do_not_cross_label():
    def work(qid):
        with usage.stage(qid, "gen"):
            usage.record_envelope(envelope(), "haiku")

    threads = [threading.Thread(target=work, args=(f"q{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    by_q = usage.by_label(usage.snapshot())
    assert len(by_q) == 8 and all(len(v) == 1 for v in by_q.values())


# --- collection ---

def test_take_removes_only_its_own_label():
    for qid in ("a", "b", "a"):
        with usage.stage(qid, "gen"):
            usage.record_envelope(envelope(), "haiku")
    assert len(usage.take("a")) == 2
    assert [r.label for r in usage.snapshot()] == ["b"]


def test_collect_clears():
    usage.record_envelope(envelope(), "haiku")
    assert len(usage.collect()) == 1
    assert usage.snapshot() == []


def test_rollups():
    with usage.stage("q1", "gen"):
        usage.record_envelope(envelope(cost=0.1, out=10), "haiku")
    with usage.stage("q1", "judge"):
        usage.record_envelope(envelope(cost=0.2, out=20), "sonnet")
        usage.record_envelope(envelope(cost=0.3, out=30), "sonnet")

    records = usage.snapshot()
    total = usage.total(records)
    assert total.calls == 3
    assert total.cost_usd == pytest.approx(0.6)
    assert total.output == 60
    assert total.input_total == (9 + 2000 + 100) * 3

    stages = usage.by_stage(records)
    assert stages["gen"].calls == 1
    assert stages["judge"].cost_usd == pytest.approx(0.5)
    assert usage.total(records).as_dict()["cost_usd"] == 0.6


# --- the hook in the transport ---

def test_gated_transport_records_on_success():
    with usage.stage("hyb-01", "gen"):
        out = call_claude_gated("p", "haiku",
                                transport=lambda *a, **k: envelope(cost=0.42),
                                semaphore=threading.Semaphore(1))
    assert out["result"] == "ok"
    (r,) = usage.snapshot()
    assert (r.label, r.stage, r.cost_usd) == ("hyb-01", "gen", 0.42)


def test_gated_transport_records_nothing_on_failure():
    def boom(*a, **k):
        raise ClaudeCliError("guardrail exploded")

    with pytest.raises(ClaudeCliError):
        call_claude_gated("p", "haiku", transport=boom,
                          semaphore=threading.Semaphore(1))
    assert usage.snapshot() == []


def test_transient_retry_records_only_the_successful_call(monkeypatch):
    monkeypatch.setattr(claude_cli, "_BACKOFF_S", (0, 0, 0))
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ClaudeCliError("529 overloaded")
        return envelope(cost=0.07)

    call_claude_gated("p", "haiku", transport=flaky,
                      semaphore=threading.Semaphore(1))
    assert calls["n"] == 2
    assert len(usage.snapshot()) == 1
    assert usage.total(usage.snapshot()).cost_usd == pytest.approx(0.07)
