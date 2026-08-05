"""External API seat tests (v5): payload pinning (model, temperature,
thinking OFF), usage/cost mapping into the claude-shaped envelope, transient
retry, semaphore gating, the ApiClient .chat contract and the backend
switches. Transport is faked - no network, no keys beyond monkeypatched env
vars."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from src import openai_compat
from src.eval import usage
from src.llm import ApiClient, LlmServerError, make_llm
from src.openai_compat import (GEN_SEAT, JUDGE_SEAT, ApiError, ApiSeat,
                               call_api, call_api_gated)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def api_response(text="hello", prompt=1000, cached=0, completion=200,
                 model="m-served", cached_style=None):
    """cached_style: None | 'openai' (prompt_tokens_details, Gemini) |
    'deepseek' (top-level prompt_cache_hit_tokens)."""
    us = {"prompt_tokens": prompt, "completion_tokens": completion}
    if cached_style == "openai":
        us["prompt_tokens_details"] = {"cached_tokens": cached}
    elif cached_style == "deepseek":
        us["prompt_cache_hit_tokens"] = cached
        us["prompt_cache_miss_tokens"] = prompt - cached
    return {"model": model,
            "choices": [{"message": {"content": text},
                         "finish_reason": "stop"}],
            "usage": us}


def seat(concurrency=4, prices=None) -> ApiSeat:
    return ApiSeat(name="gen", base_url="https://api.test", model="m-test",
                   api_key_env="TEST_API_KEY", temperature=0.0,
                   extra={"reasoning_effort": "none"},
                   prices=prices or {"input": 1.0, "cache_read": 0.5,
                                     "output": 2.0},
                   concurrency=concurrency)


MSGS = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]


# --- payload pinning ---

def test_call_api_pins_the_frozen_seat(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k-gen")
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, payload=json, headers=headers, timeout=timeout)
        return FakeResponse(payload=api_response())

    monkeypatch.setattr(openai_compat.requests, "post", post)
    call_api(MSGS, GEN_SEAT)
    assert seen["url"] == f"{GEN_SEAT.base_url}/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer k-gen"
    assert seen["payload"]["model"] == "gpt-5-nano"
    assert seen["payload"]["temperature"] == 1.0   # GPT-5 family: locked at 1
    assert seen["payload"]["reasoning_effort"] == "minimal"  # GPT-5's floor
    assert seen["payload"]["messages"] == MSGS             # roles unflattened
    assert "max_tokens" not in seen["payload"]
    assert "max_completion_tokens" not in seen["payload"]


def test_gen_seat_uses_max_completion_tokens(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k-gen")
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen.update(payload=json)
        return FakeResponse(payload=api_response())

    monkeypatch.setattr(openai_compat.requests, "post", post)
    call_api(MSGS, GEN_SEAT, max_tokens=64)
    assert seen["payload"]["max_completion_tokens"] == 64
    assert "max_tokens" not in seen["payload"]


def test_judge_seat_disables_deepseek_thinking(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k-judge")
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen.update(payload=json)
        return FakeResponse(payload=api_response())

    monkeypatch.setattr(openai_compat.requests, "post", post)
    call_api(MSGS, JUDGE_SEAT, max_tokens=64)
    assert seen["payload"]["model"] == "deepseek-v4-flash"
    assert seen["payload"]["thinking"] == {"type": "disabled"}
    assert seen["payload"]["temperature"] == 0.0
    assert seen["payload"]["max_tokens"] == 64


def test_missing_key_names_the_env_var(monkeypatch):
    monkeypatch.delenv("TEST_API_KEY", raising=False)
    with pytest.raises(ApiError, match="TEST_API_KEY"):
        call_api(MSGS, seat())


# --- envelope: usage and cost ---

def test_usage_maps_openai_style_cache_and_cost(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "k")
    monkeypatch.setattr(
        openai_compat.requests, "post",
        lambda *a, **kw: FakeResponse(payload=api_response(
            prompt=1000, cached=400, completion=200, cached_style="openai")))
    env = call_api(MSGS, seat())
    assert env["result"] == "hello"
    assert env["usage"] == {"input_tokens": 600,
                            "cache_read_input_tokens": 400,
                            "cache_creation_input_tokens": 0,
                            "output_tokens": 200}
    # (600*1.0 + 400*0.5 + 200*2.0) per Mtok
    assert env["total_cost_usd"] == pytest.approx(1200 / 1_000_000)
    assert env["modelUsage"] == {"m-served":
                                 {"costUSD": env["total_cost_usd"]}}
    assert env["num_turns"] == 1 and not env["is_error"]


def test_usage_maps_deepseek_style_cache(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "k")
    monkeypatch.setattr(
        openai_compat.requests, "post",
        lambda *a, **kw: FakeResponse(payload=api_response(
            prompt=1000, cached=250, completion=100,
            cached_style="deepseek")))
    env = call_api(MSGS, seat())
    assert env["usage"]["input_tokens"] == 750
    assert env["usage"]["cache_read_input_tokens"] == 250


def test_empty_content_is_empty_string_not_crash(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "k")
    payload = api_response()
    payload["choices"][0]["message"]["content"] = None
    monkeypatch.setattr(openai_compat.requests, "post",
                        lambda *a, **kw: FakeResponse(payload=payload))
    assert call_api(MSGS, seat())["result"] == ""


# --- failure classification ---

@pytest.mark.parametrize("status,transient", [
    (429, True), (500, True), (503, True), (529, True),
    (400, False), (401, False), (404, False)])
def test_http_status_transience(monkeypatch, status, transient):
    monkeypatch.setenv("TEST_API_KEY", "k")
    monkeypatch.setattr(openai_compat.requests, "post",
                        lambda *a, **kw: FakeResponse(status_code=status,
                                                      text="nope"))
    with pytest.raises(ApiError) as err:
        call_api(MSGS, seat())
    assert err.value.transient is transient
    assert str(status) in str(err.value)


def test_timeout_is_transient(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "k")

    def post(*a, **kw):
        raise openai_compat.requests.Timeout("slow")

    monkeypatch.setattr(openai_compat.requests, "post", post)
    with pytest.raises(ApiError) as err:
        call_api(MSGS, seat())
    assert err.value.transient


def test_no_choices_is_loud(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "k")
    monkeypatch.setattr(openai_compat.requests, "post",
                        lambda *a, **kw: FakeResponse(payload={"usage": {}}))
    with pytest.raises(ApiError, match="no choices"):
        call_api(MSGS, seat())


# --- the gated wrapper ---

def test_gated_retries_transient_then_succeeds(monkeypatch):
    monkeypatch.setattr(openai_compat.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def transport(messages, s, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ApiError("HTTP 429: throttled", transient=True)
        return {"result": "ok", "usage": {}, "total_cost_usd": 0.0}

    env = call_api_gated(MSGS, seat(), transport=transport)
    assert env["result"] == "ok" and calls["n"] == 3


def test_gated_gives_up_after_backoff_exhausted(monkeypatch):
    monkeypatch.setattr(openai_compat.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def transport(messages, s, **kw):
        calls["n"] += 1
        raise ApiError("HTTP 503: overloaded", transient=True)

    with pytest.raises(ApiError, match="persisted"):
        call_api_gated(MSGS, seat(), transport=transport)
    assert calls["n"] == 1 + len(openai_compat._BACKOFF_S)


def test_gated_nontransient_fails_immediately():
    calls = {"n": 0}

    def transport(messages, s, **kw):
        calls["n"] += 1
        raise ApiError("HTTP 400: bad request")

    with pytest.raises(ApiError, match="400"):
        call_api_gated(MSGS, seat(), transport=transport)
    assert calls["n"] == 1


def test_gated_records_usage_with_stage_attribution():
    usage.reset()
    envelope = {"result": "ok", "total_cost_usd": 0.0012,
                "usage": {"input_tokens": 600,
                          "cache_read_input_tokens": 400,
                          "output_tokens": 200},
                "modelUsage": {"m-served": {"costUSD": 0.0012}},
                "duration_ms": 40, "num_turns": 1}
    with usage.stage("vec-09", "gen"):
        call_api_gated(MSGS, seat(), transport=lambda m, s, **kw: envelope)
    (rec,) = usage.collect()
    assert rec.label == "vec-09" and rec.stage == "gen"
    assert rec.cost_usd == 0.0012 and rec.output == 200
    assert rec.input_fresh == 600 and rec.cache_read == 400
    assert rec.model == "m-test" and rec.model_resolved == "m-served"


def test_gated_respects_the_seat_semaphore():
    cap = 3
    peak, cur = 0, 0
    lock = threading.Lock()

    def transport(messages, s, **kw):
        nonlocal peak, cur
        with lock:
            cur += 1
            peak = max(peak, cur)
        time.sleep(0.02)
        with lock:
            cur -= 1
        return {"result": "ok", "usage": {}, "total_cost_usd": 0.0}

    tight = seat(concurrency=cap)
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(
            lambda _: call_api_gated(MSGS, tight, transport=transport),
            range(16)))
    assert len(results) == 16 and peak <= cap


# --- the gated wrapper: empty completions ---
# call_api keeps returning "" for empty content (tested above); the GATE
# owns the retry policy, because only there is the envelope both recorded
# and retryable.

def _empty_envelope(finish_reason="stop"):
    return {"result": "", "usage": {}, "total_cost_usd": 0.0,
            "finish_reason": finish_reason}


def test_gated_retries_empty_content_then_succeeds(monkeypatch):
    monkeypatch.setattr(openai_compat.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def transport(messages, s, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            return _empty_envelope()
        return {"result": "ok", "usage": {}, "total_cost_usd": 0.0}

    env = call_api_gated(MSGS, seat(), transport=transport)
    assert env["result"] == "ok" and calls["n"] == 3


def test_gated_empty_content_exhausts_backoff(monkeypatch):
    monkeypatch.setattr(openai_compat.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def transport(messages, s, **kw):
        calls["n"] += 1
        return _empty_envelope()

    with pytest.raises(ApiError, match="persisted"):
        call_api_gated(MSGS, seat(), transport=transport)
    assert calls["n"] == 1 + len(openai_compat._BACKOFF_S)


def test_gated_empty_with_length_cap_is_loud(monkeypatch):
    # Empty at the token cap is deterministic misconfiguration - one loud
    # failure naming the cap parameter, not four wasted attempts.
    monkeypatch.setattr(openai_compat.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def transport(messages, s, **kw):
        calls["n"] += 1
        return _empty_envelope(finish_reason="length")

    with pytest.raises(ApiError, match="max_tokens") as err:
        call_api_gated(MSGS, seat(), transport=transport)
    assert not err.value.transient and calls["n"] == 1


def test_gated_records_usage_for_retried_empty_completions(monkeypatch):
    # The empty attempt was billed, so its tokens belong in usage even
    # though the call is retried.
    monkeypatch.setattr(openai_compat.time, "sleep", lambda s: None)
    usage.reset()
    calls = {"n": 0}

    def transport(messages, s, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"result": "", "finish_reason": "stop",
                    "total_cost_usd": 0.001,
                    "usage": {"input_tokens": 100, "output_tokens": 0}}
        return {"result": "ok", "total_cost_usd": 0.002,
                "usage": {"input_tokens": 100, "output_tokens": 50}}

    env = call_api_gated(MSGS, seat(), transport=transport)
    assert env["result"] == "ok"
    recs = usage.collect()
    assert len(recs) == 2
    assert [r.output for r in recs] == [0, 50]


# --- ApiClient ---

def test_api_client_chat_contract():
    seen = {}

    def transport(messages, s, **kw):
        seen["messages"], seen["kw"] = messages, kw
        return {"result": "  an answer  ", "usage": {},
                "total_cost_usd": 0.0}

    c = ApiClient(seat=seat(), max_tokens=333, transport=transport)
    out = c.chat(MSGS, temperature=0.9)          # temperature pinned: ignored
    assert out == "an answer"
    assert seen["messages"] == MSGS              # roles pass through untouched
    assert seen["kw"]["max_tokens"] == 333
    assert c.model == "m-test"


def test_api_client_max_tokens_override_per_call():
    seen = {}

    def transport(messages, s, **kw):
        seen.update(kw)
        return {"result": "x", "usage": {}, "total_cost_usd": 0.0}

    ApiClient(seat=seat(), max_tokens=100,
              transport=transport).chat(MSGS, max_tokens=7)
    assert seen["max_tokens"] == 7


# --- backend switches ---

def test_make_llm_api_backend(monkeypatch):
    import src.llm as llm_mod
    monkeypatch.setattr(llm_mod, "GEN_BACKEND", "api")
    client = make_llm(max_tokens=42)
    assert isinstance(client, ApiClient)
    assert client.max_tokens == 42
    assert client.seat is GEN_SEAT


def test_check_generator_api_needs_the_key(monkeypatch):
    import src.llm as llm_mod
    monkeypatch.setattr(llm_mod, "GEN_BACKEND", "api")
    monkeypatch.delenv(GEN_SEAT.api_key_env, raising=False)
    with pytest.raises(LlmServerError, match=GEN_SEAT.api_key_env):
        llm_mod.check_generator()
    monkeypatch.setenv(GEN_SEAT.api_key_env, "k")
    info = llm_mod.check_generator()
    assert info == {"backend": "api", "model": GEN_SEAT.model,
                    "key_env": GEN_SEAT.api_key_env}
