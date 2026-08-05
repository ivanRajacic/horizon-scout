"""Shared OpenAI-compatible HTTP transport for both external API seats (v5).

Sibling of src/claude_cli.py, same shape on purpose: ONE transport function
(call_api), one gated wrapper with backoff (call_api_gated), and usage
recording at the single gate every call passes through. Generation
(gpt-5-nano) and judging (DeepSeek V4 Flash) each get an ApiSeat - the
frozen bundle of endpoint, model, temperature, thinking-off pin, prices and
concurrency from src/config.py - and each seat has its OWN semaphore. The
`claude -p` semaphore in src/claude_cli.py governs `claude -p` subprocesses
only and is deliberately not widened to cover these.

The envelope returned by call_api mirrors the `claude -p` JSON envelope
(result text, the usage token fields, total_cost_usd, modelUsage), so
src/eval/usage.py records both transports through the one record_envelope
hook, unchanged. These APIs return token counts, not dollars - cost is
computed here from the seat's pinned per-Mtok prices. Unlike the Max
subscription's priced-not-billed figures, these dollars are billed for real.
"""

import json
import os
import threading
import time
from dataclasses import dataclass, field

import requests

from src.config import (API_TIMEOUT_S, GEN_API_BASE_URL, GEN_API_CONCURRENCY,
                        GEN_API_EXTRA, GEN_API_KEY_ENV,
                        GEN_API_MAX_TOKENS_PARAM, GEN_API_MODEL,
                        GEN_API_PRICES_PER_MTOK, GEN_API_TEMPERATURE,
                        JUDGE_API_BASE_URL, JUDGE_API_CONCURRENCY,
                        JUDGE_API_EXTRA, JUDGE_API_KEY_ENV,
                        JUDGE_API_MAX_TOKENS_PARAM, JUDGE_API_MODEL,
                        JUDGE_API_PRICES_PER_MTOK, JUDGE_API_TEMPERATURE)


class ApiError(RuntimeError):
    """Any API transport failure. `transient` marks the retry-worthy kind
    (rate limit, overload, timeout, connection drop) - explicit at raise
    time, so the retry loop never has to guess from message substrings."""

    def __init__(self, message: str, transient: bool = False):
        super().__init__(message)
        self.transient = transient


@dataclass(frozen=True)
class ApiSeat:
    """One frozen seat: everything a request needs, pinned in config."""
    name: str                    # "gen" | "judge" - also picks the semaphore
    base_url: str
    model: str
    api_key_env: str
    temperature: float
    extra: dict                  # the thinking/reasoning OFF pin, merged in
    prices: dict                 # USD per MILLION tokens: input/cache_read/output
    concurrency: int
    timeout_s: float = API_TIMEOUT_S
    # OpenAI's GPT-5 family rejects "max_tokens" and wants
    # "max_completion_tokens"; DeepSeek keeps the classic name.
    max_tokens_param: str = "max_tokens"
    _sem: threading.Semaphore = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "_sem",
                           threading.Semaphore(max(1, self.concurrency)))

    @property
    def semaphore(self) -> threading.Semaphore:
        return self._sem


GEN_SEAT = ApiSeat(
    name="gen", base_url=GEN_API_BASE_URL, model=GEN_API_MODEL,
    api_key_env=GEN_API_KEY_ENV, temperature=GEN_API_TEMPERATURE,
    extra=GEN_API_EXTRA, prices=GEN_API_PRICES_PER_MTOK,
    concurrency=GEN_API_CONCURRENCY,
    max_tokens_param=GEN_API_MAX_TOKENS_PARAM)

JUDGE_SEAT = ApiSeat(
    name="judge", base_url=JUDGE_API_BASE_URL, model=JUDGE_API_MODEL,
    api_key_env=JUDGE_API_KEY_ENV, temperature=JUDGE_API_TEMPERATURE,
    extra=JUDGE_API_EXTRA, prices=JUDGE_API_PRICES_PER_MTOK,
    concurrency=JUDGE_API_CONCURRENCY,
    max_tokens_param=JUDGE_API_MAX_TOKENS_PARAM)


# HTTP statuses worth retrying: throttling, overload, gateway wobble.
_TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504, 529}
_BACKOFF_S = (5, 20, 60)


def check_key(seat: ApiSeat) -> dict:
    """Fail fast BEFORE any money is spent: the seat's key must be in the
    environment. Returns a small info dict for status printouts."""
    if not os.environ.get(seat.api_key_env):
        raise ApiError(
            f"{seat.api_key_env} is not set - the {seat.name} seat runs "
            f"{seat.model} at {seat.base_url} and needs it. Set it in the "
            f"environment before the run.")
    return {"backend": "api", "seat": seat.name, "model": seat.model,
            "key_env": seat.api_key_env}


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _cached_tokens(usage: dict) -> int:
    """Cached prompt tokens, wherever this provider puts them: OpenAI-style
    prompt_tokens_details.cached_tokens (Gemini), or DeepSeek's top-level
    prompt_cache_hit_tokens."""
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict) and details.get("cached_tokens") is not None:
        return _int(details.get("cached_tokens"))
    return _int(usage.get("prompt_cache_hit_tokens"))


def _cost_usd(prices: dict, fresh: int, cached: int, output: int) -> float:
    return (fresh * prices.get("input", 0.0)
            + cached * prices.get("cache_read", prices.get("input", 0.0))
            + output * prices.get("output", 0.0)) / 1_000_000


def call_api(messages: list[dict], seat: ApiSeat, *,
             max_tokens: int | None = None,
             timeout_s: float | None = None) -> dict:
    """THE transport function for one seat: one chat completion, returned as
    a `claude -p`-shaped envelope (text in envelope["result"], token counts
    under "usage", computed cost in "total_cost_usd")."""
    key = os.environ.get(seat.api_key_env)
    if not key:
        raise ApiError(
            f"{seat.api_key_env} is not set - the {seat.name} seat cannot "
            f"call {seat.model}.")

    payload = {"model": seat.model, "messages": messages,
               "temperature": seat.temperature, **seat.extra}
    if max_tokens:
        payload[seat.max_tokens_param] = int(max_tokens)

    started = time.perf_counter()
    try:
        r = requests.post(f"{seat.base_url}/chat/completions", json=payload,
                          headers={"Authorization": f"Bearer {key}"},
                          timeout=timeout_s or seat.timeout_s)
    except requests.Timeout:
        raise ApiError(f"{seat.name} seat ({seat.model}) timed out after "
                       f"{timeout_s or seat.timeout_s}s",
                       transient=True) from None
    except requests.ConnectionError as e:
        raise ApiError(f"{seat.name} seat connection failed: {e}",
                       transient=True) from None
    except requests.RequestException as e:
        raise ApiError(f"{seat.name} seat request failed: {e}") from None

    if r.status_code != 200:
        raise ApiError(
            f"{seat.name} seat ({seat.model}) HTTP {r.status_code}: "
            f"{r.text[:500]}",
            transient=r.status_code in _TRANSIENT_STATUS)
    try:
        data = r.json()
    except ValueError as e:
        raise ApiError(f"{seat.name} seat returned non-JSON body: {e}: "
                       f"{r.text[:500]}") from None

    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise ApiError(f"{seat.name} seat returned no choices: "
                       f"{json.dumps(data)[:500]}")
    text = (choices[0].get("message") or {}).get("content") or ""

    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    prompt = _int(usage.get("prompt_tokens"))
    cached = min(_cached_tokens(usage), prompt)
    fresh = prompt - cached
    output = _int(usage.get("completion_tokens"))
    cost = _cost_usd(seat.prices, fresh, cached, output)
    model = str(data.get("model") or seat.model)

    return {
        "result": text,
        "is_error": False,
        "usage": {"input_tokens": fresh,
                  "cache_read_input_tokens": cached,
                  "cache_creation_input_tokens": 0,
                  "output_tokens": output},
        "total_cost_usd": cost,
        "modelUsage": {model: {"costUSD": cost}},
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "num_turns": 1,
        "finish_reason": choices[0].get("finish_reason"),
    }


def call_api_gated(messages: list[dict], seat: ApiSeat, *,
                   max_tokens: int | None = None,
                   timeout_s: float | None = None,
                   transport=call_api,
                   semaphore: threading.Semaphore | None = None) -> dict:
    """Semaphore-gated transport with backoff on transient failures - the
    seat-side twin of call_claude_gated, and like it the ONE place every
    successful call's cost and tokens are recorded (src/eval/usage.py,
    attributed to whatever stage() block is in effect). Transient failures
    and empty completions back off and retry outside the semaphore;
    anything else raises immediately - loud failure stays loud."""
    from src.eval.usage import record_envelope   # local: keeps the lowest-level
    #                                              transport free of package deps

    sem = semaphore if semaphore is not None else seat.semaphore
    last: Exception | None = None
    for backoff in (0,) + _BACKOFF_S:
        if backoff:
            time.sleep(backoff)
        with sem:
            try:
                envelope = transport(messages, seat, max_tokens=max_tokens,
                                     timeout_s=timeout_s)
                # Recorded BEFORE the emptiness check: an empty completion
                # that gets retried was still billed, so its tokens belong
                # in usage.
                record_envelope(envelope, seat.model)
                # An empty completion is a success to the transport but
                # poison downstream - ragas scores it 0.0 silently and the
                # rubric judge burns one of its two parse attempts on it.
                # Empty at the token cap is deterministic misconfiguration
                # (gpt-5-nano's reasoning tokens count against
                # max_completion_tokens), so that one fails loud instead of
                # burning the whole backoff ladder on it.
                if not str(envelope.get("result") or "").strip():
                    reason = envelope.get("finish_reason")
                    if reason == "length":
                        raise ApiError(
                            f"{seat.name} seat ({seat.model}) spent its "
                            f"whole {seat.max_tokens_param} budget without "
                            f"emitting content (finish_reason=length) - "
                            f"raise the cap, retrying cannot fix this")
                    raise ApiError(
                        f"{seat.name} seat ({seat.model}) returned empty "
                        f"content (finish_reason={reason})", transient=True)
                return envelope
            except ApiError as e:
                if not getattr(e, "transient", False):
                    raise
                last = e
    raise ApiError(
        f"transient {seat.name}-seat failure persisted after "
        f"{len(_BACKOFF_S) + 1} attempts: {last}")
