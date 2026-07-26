"""Shared `claude -p` transport for every Claude-backed path (M5 v4).

Generation (Haiku), the rubric judge and the RAGAS metrics (Sonnet) all run
through the ONE transport function below (call_claude) - a billing change
means swapping its body for an API call, nothing downstream moves.

Concurrency: ONE process-wide semaphore gates every `claude -p` subprocess,
generation and judging alike, so the configured cap is global - up to
CLAUDE_CONCURRENCY (hard ceiling CLAUDE_MAX_CONCURRENCY = 16) instances run
at once, whichever path spawned them. Max's constraint is the usage window
(total tokens), not request rate, so concurrency is effectively free; the
cap exists to bound local process sprawl.
"""

import json
import shutil
import subprocess
import threading
import time

from src.config import (CLAUDE_CONCURRENCY, CLAUDE_MAX_CONCURRENCY,
                        CLAUDE_TIMEOUT_S)


class ClaudeCliError(RuntimeError):
    """Any `claude -p` transport failure. src.judge.judge.JudgeError
    subclasses this, so fakes raising JudgeError stay catchable here."""


# Substrings marking a transient, retry-worthy transport failure (subscription
# window exhausted, service overloaded, slow response). Anything else fails
# immediately and loudly.
_TRANSIENT = ("rate limit", "usage limit", "429", "529", "overloaded",
              "timed out")
_BACKOFF_S = (5, 20, 60)

_SEMAPHORE = threading.Semaphore(
    max(1, min(CLAUDE_CONCURRENCY, CLAUDE_MAX_CONCURRENCY)))


def shared_semaphore() -> threading.Semaphore:
    """The process-wide gate for `claude -p` subprocesses (all paths)."""
    return _SEMAPHORE


def call_claude(prompt: str, model: str,
                timeout_s: float = CLAUDE_TIMEOUT_S) -> dict:
    """THE transport function. `claude -p` on the Max subscription; swap this
    body for an API call when billing changes (~EUR 3), nothing else moves.

    Returns the CLI's JSON envelope; the model text is in envelope["result"].
    """
    exe = shutil.which("claude")
    if exe is None:
        raise ClaudeCliError(
            "`claude` CLI not found on PATH - generation and judging run on "
            "the Claude Code subscription via `claude -p`.")
    # --tools "" disables the built-in tool set. Every caller here wants text
    # in, text out: a generator writing an answer from evidence it was handed,
    # or a judge scoring one. None of them has any business running Bash.
    #
    # Left on, this is not a theoretical risk - it broke the 2026-07-26 pilot.
    # `claude -p` is a whole Claude Code session, so the judge model could see
    # tools, and on the two largest questions (vec-04, hyb-09; ~50k tokens of
    # context each) it answered by reaching for one instead of writing text.
    # With --max-turns 1 that tool-use turn spent the whole budget, so the
    # session ended on stop_reason "tool_use" with is_error true and the CLI
    # exited 1 - two judge calls dead, $1.51 spent, no verdict. Removing the
    # tools makes stopping on a tool call impossible rather than unlikely.
    cmd = [exe, "-p", "--model", model, "--output-format", "json",
           "--max-turns", "1", "--tools", ""]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True,
                              text=True, encoding="utf-8", timeout=timeout_s)
    except subprocess.TimeoutExpired:
        raise ClaudeCliError(
            f"claude -p call timed out after {timeout_s}s") from None
    if proc.returncode != 0:
        raise ClaudeCliError(f"claude -p exited {proc.returncode}: "
                             f"{(proc.stderr or proc.stdout).strip()[:500]}")
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ClaudeCliError(f"claude -p emitted non-JSON envelope: {e}: "
                             f"{proc.stdout[:500]}") from None
    if envelope.get("is_error"):
        raise ClaudeCliError(f"claude -p returned an error result: "
                             f"{str(envelope.get('result'))[:500]}")
    return envelope


def call_claude_gated(prompt: str, model: str, *, timeout_s: float | None = None,
                      transport=call_claude,
                      semaphore: threading.Semaphore | None = None) -> dict:
    """Semaphore-gated transport with backoff on transient failures.

    Every concurrent caller (generation clients, RAGAS metrics, the rubric
    judge) funnels through here, so the global cap holds no matter how many
    threads fan out. Transient failures (rate/usage limits, overload,
    timeout) back off and retry outside the semaphore; anything else raises
    immediately - loud failure stays loud.
    """
    sem = semaphore if semaphore is not None else _SEMAPHORE
    last: Exception | None = None
    for backoff in (0,) + _BACKOFF_S:
        if backoff:
            time.sleep(backoff)
        with sem:
            try:
                if timeout_s is None:
                    return transport(prompt, model)
                return transport(prompt, model, timeout_s=timeout_s)
            except ClaudeCliError as e:
                msg = str(e).lower()
                if not any(t in msg for t in _TRANSIENT):
                    raise
                last = e
    raise ClaudeCliError(
        f"transient transport failure persisted after "
        f"{len(_BACKOFF_S) + 1} attempts: {last}")
