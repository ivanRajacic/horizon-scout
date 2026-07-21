"""RAGAS judge tests: backend concurrency cap + transient retry, pass-rule
derivation, overlay-vs-ragas dispatch, clamping, and logging. Transport and
metrics are faked - no `claude` CLI, no network, no real metric prompts."""

import asyncio
import json
import threading
import time

import pytest

from src.config import (JUDGE_MAX_CONCURRENCY, JUDGE_PASS_FACTUAL,
                        JUDGE_PASS_FAITHFULNESS)
from src.judge.judge import JudgeError
from src.judge import ragas_backend
from src.judge.ragas_backend import ClaudeCliLLM
from src.judge.ragas_judge import JudgePool, derive_ragas_pass


class FakePrompt:
    def to_string(self):
        return "prompt"


def envelope(result: str) -> dict:
    return {"type": "result", "result": result, "is_error": False}


RUBRIC_OK = json.dumps({"coverage": "full", "missing_facts": [],
                        "unsupported_claims": [], "reasoning": "refusal ok"})


class StubMetric:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    async def single_turn_ascore(self, sample, **kw):
        self.calls += 1
        return self.value


# --- pass rule ---

def test_derive_ragas_pass_rule():
    ok_f = JUDGE_PASS_FAITHFULNESS
    ok_c = JUDGE_PASS_FACTUAL
    assert derive_ragas_pass(ok_f, ok_c)
    assert derive_ragas_pass(None, ok_c)          # faithfulness unmeasurable
    assert not derive_ragas_pass(ok_f - 0.01, ok_c)
    assert not derive_ragas_pass(ok_f, ok_c - 0.01)
    assert not derive_ragas_pass(ok_f, None)      # factual NaN can never pass
    assert not derive_ragas_pass(None, None)


# --- backend ---

def test_backend_caps_concurrency():
    peak, cur = 0, 0
    lock = threading.Lock()

    def transport(prompt, model, **kw):
        nonlocal peak, cur
        with lock:
            cur += 1
            peak = max(peak, cur)
        time.sleep(0.02)
        with lock:
            cur -= 1
        return envelope("ok")

    llm = ClaudeCliLLM("m", threading.Semaphore(3), transport=transport)

    async def run():
        await asyncio.gather(
            *(llm.agenerate_text(FakePrompt()) for _ in range(12)))

    asyncio.run(run())
    assert peak <= 3


def test_backend_returns_llmresult_text():
    llm = ClaudeCliLLM("m", threading.Semaphore(1),
                       transport=lambda p, m, **kw: envelope("judged text"))
    out = llm.generate_text(FakePrompt())
    assert out.generations[0][0].text == "judged text"


def test_backend_retries_transient_then_succeeds(monkeypatch):
    monkeypatch.setattr(ragas_backend.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def transport(prompt, model, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise JudgeError("claude -p returned an error result: 429 "
                             "rate limit reached")
        return envelope("ok")

    llm = ClaudeCliLLM("m", threading.Semaphore(1), transport=transport)
    assert llm._call("p") == "ok" and calls["n"] == 3


def test_backend_gives_up_after_backoff_exhausted(monkeypatch):
    monkeypatch.setattr(ragas_backend.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def transport(prompt, model, **kw):
        calls["n"] += 1
        raise JudgeError("usage limit reached for this window")

    llm = ClaudeCliLLM("m", threading.Semaphore(1), transport=transport)
    with pytest.raises(JudgeError, match="persisted"):
        llm._call("p")
    assert calls["n"] == 1 + len(ragas_backend._BACKOFF_S)


def test_backend_nontransient_fails_immediately():
    calls = {"n": 0}

    def transport(prompt, model, **kw):
        calls["n"] += 1
        raise JudgeError("claude -p exited 1: boom")

    llm = ClaudeCliLLM("m", threading.Semaphore(1), transport=transport)
    with pytest.raises(JudgeError, match="boom"):
        llm._call("p")
    assert calls["n"] == 1


# --- pool ---

def mk_pool(tmp_path, transport, concurrency=4):
    return JudgePool(model_key="haiku", concurrency=concurrency,
                     log_path=tmp_path / "judge.jsonl", transport=transport)


def test_concurrency_clamped(tmp_path):
    t = lambda p, m, **kw: envelope("x")  # noqa: E731
    assert mk_pool(tmp_path, t, concurrency=99).concurrency == JUDGE_MAX_CONCURRENCY
    assert mk_pool(tmp_path, t, concurrency=0).concurrency == 1


def test_adversarial_dispatches_to_overlay(tmp_path):
    pool = mk_pool(tmp_path, lambda p, m, **kw: envelope(RUBRIC_OK))
    pool.faithfulness = StubMetric(1.0)   # must NOT be called
    pool.factual = StubMetric(1.0)
    case = {"question_id": "a-1", "question": "q?", "reference_answer": "ref",
            "answer": "none exist", "adversarial": "zero-match",
            "expect_pass": True}
    [v] = pool.judge_all([case])
    assert v.path == "overlay" and v.passed
    assert pool.faithfulness.calls == 0 and pool.factual.calls == 0


def test_ordinary_dispatches_to_ragas(tmp_path):
    pool = mk_pool(tmp_path, lambda p, m, **kw: envelope("unused"))
    pool.faithfulness = StubMetric(0.9)
    pool.factual = StubMetric(0.8)
    case = {"question_id": "r-1", "question": "q?", "reference_answer": "ref",
            "answer": "ans", "contexts": ["some evidence"]}
    [v] = pool.judge_all([case])
    assert v.path == "ragas" and v.passed
    assert v.faithfulness == 0.9 and v.factual_correctness == 0.8
    logged = json.loads((tmp_path / "judge.jsonl").read_text(encoding="utf-8"))
    assert logged["path"] == "ragas" and logged["passed"] is True
    assert logged["thresholds"] == {"factual": JUDGE_PASS_FACTUAL,
                                    "faithfulness": JUDGE_PASS_FAITHFULNESS}


def test_no_contexts_skips_faithfulness(tmp_path):
    pool = mk_pool(tmp_path, lambda p, m, **kw: envelope("unused"))
    pool.faithfulness = StubMetric(0.0)   # would fail if consulted
    pool.factual = StubMetric(0.9)
    case = {"question_id": "r-2", "question": "q?", "reference_answer": "ref",
            "answer": "ans"}
    [v] = pool.judge_all([case])
    assert v.passed and v.faithfulness is None
    assert pool.faithfulness.calls == 0 and "skipped" in v.detail


def test_nan_factual_fails_loudly_in_detail(tmp_path):
    pool = mk_pool(tmp_path, lambda p, m, **kw: envelope("unused"))
    pool.faithfulness = StubMetric(1.0)
    pool.factual = StubMetric(float("nan"))
    case = {"question_id": "r-3", "question": "q?", "reference_answer": "ref",
            "answer": "ans", "contexts": ["ev"]}
    [v] = pool.judge_all([case])
    assert not v.passed and v.factual_correctness is None
    assert "undefined" in v.detail


def test_batch_isolates_case_failures(tmp_path):
    pool = mk_pool(tmp_path, lambda p, m, **kw: envelope("unused"))

    class Boom:
        async def single_turn_ascore(self, sample, **kw):
            raise JudgeError("metric blew up")

    pool.factual = Boom()
    pool.faithfulness = StubMetric(1.0)
    good = {"question_id": "a-2", "question": "q?", "reference_answer": "ref",
            "answer": "none exist", "adversarial": "zero-match"}
    # overlay path is independent of the broken metric
    pool.rubric.transport = lambda p, m, **kw: envelope(RUBRIC_OK)
    bad = {"question_id": "r-4", "question": "q?", "reference_answer": "ref",
           "answer": "ans", "contexts": ["ev"]}
    results = pool.judge_all([bad, good])
    assert isinstance(results[0], JudgeError)
    assert results[1].passed
