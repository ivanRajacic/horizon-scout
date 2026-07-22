"""One LLM client interface for everything downstream.

Two interchangeable generation clients share the .chat() contract:
- ClaudeClient (v4 default): Claude Haiku over the shared `claude -p`
  transport (src/claude_cli.py), gated by the process-wide semaphore so up
  to CLAUDE_CONCURRENCY generators can run in parallel.
- LlmClient (legacy): OpenAI-compatible chat-completions against the local
  llama-server on port 8081 (Qwen3-8B), kept for a possible RQ3 revival.

make_llm() picks by config.GEN_BACKEND - nothing downstream knows which
client is behind .chat(); traces pin the model per answer either way.
"""

import hashlib
import re
import shutil

import requests

from src.config import (GEN_BACKEND, GEN_MODEL, LLM_BASE_URL, LLM_MODEL,
                        LLM_SERVER_LAUNCH_CMD)

# Qwen-style thinking blocks: never part of the answer, strip defensively.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def fingerprint(text: str) -> str:
    """Short content hash for prompt versioning in traces. A version label can
    lie about edits; the hash cannot - traces log 'label:hash'."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


class LlmServerError(RuntimeError):
    pass


def check_server(base_url: str = LLM_BASE_URL) -> dict:
    """Return llama-server /props, or fail fast with the relaunch command."""
    try:
        r = requests.get(f"{base_url}/props", timeout=5)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise LlmServerError(
            f"LLM llama-server not reachable at {base_url} ({e}).\n"
            f"Launch it with:\n  {LLM_SERVER_LAUNCH_CMD}"
        ) from None


class LlmClient:
    def __init__(self, base_url: str = LLM_BASE_URL, model: str = LLM_MODEL,
                 temperature: float = 0.0, max_tokens: int = 512,
                 seed: int = 42, timeout: float = 180.0):
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.seed = seed
        self.timeout = timeout
        self._session = requests.Session()

    def chat(self, messages: list[dict], **overrides) -> str:
        """messages = [{'role': ..., 'content': ...}, ...] -> assistant text."""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "seed": self.seed,
        }
        payload.update(overrides)
        try:
            r = self._session.post(f"{self.base_url}/v1/chat/completions",
                                   json=payload, timeout=self.timeout)
            r.raise_for_status()
        except requests.RequestException as e:
            raise LlmServerError(f"chat completion failed: {e}") from None
        content = r.json()["choices"][0]["message"]["content"] or ""
        return _THINK_RE.sub("", content).strip()


class ClaudeClient:
    """Generation client over the shared `claude -p` transport (v4 default).

    Same .chat() contract as LlmClient. Sampling overrides (max_tokens,
    temperature, seed) are accepted and IGNORED - `claude -p` exposes no
    sampling controls - so determinism claims never apply to this backend.
    Thread-safe: instances hold no mutable state, and every call is gated by
    the process-wide semaphore, so batch runners may fan out up to
    CLAUDE_CONCURRENCY threads over one shared instance.
    """

    def __init__(self, model: str | None = None, transport=None):
        from src.claude_cli import call_claude
        self.model = model or GEN_MODEL
        self.transport = transport or call_claude  # injectable for tests

    @staticmethod
    def render(messages: list[dict]) -> str:
        """Flatten a chat into one `claude -p` prompt. System turns lead,
        untagged (they are the instructions). A single user turn follows
        plain; multi-turn histories (error-informed retries) keep explicit
        User:/Assistant: labels so the correction reads as a dialogue."""
        system = [m["content"] for m in messages if m["role"] == "system"]
        turns = [m for m in messages if m["role"] != "system"]
        parts = list(system)
        if len(turns) == 1:
            parts.append(turns[0]["content"])
        else:
            for m in turns:
                label = "User" if m["role"] == "user" else "Assistant"
                parts.append(f"{label}:\n{m['content']}")
            parts.append("Reply as the assistant, following the "
                         "instructions at the top.")
        return "\n\n".join(parts)

    def chat(self, messages: list[dict], **overrides) -> str:
        """messages = [{'role': ..., 'content': ...}, ...] -> assistant text.
        overrides are accepted for interface compatibility and ignored."""
        from src.claude_cli import call_claude_gated
        envelope = call_claude_gated(self.render(messages), self.model,
                                     transport=self.transport)
        return str(envelope.get("result", "")).strip()


def make_llm(**local_overrides):
    """Factory for the configured generation client (config.GEN_BACKEND).
    local_overrides (e.g. max_tokens) apply to the local backend only - the
    claude backend has no sampling controls to override."""
    if GEN_BACKEND == "claude":
        return ClaudeClient()
    return LlmClient(**local_overrides)


def check_generator() -> dict:
    """Fail fast before a run, whichever backend is configured: the claude
    backend needs the `claude` CLI on PATH; the local backend needs the
    llama-server up (with the relaunch command in the error)."""
    if GEN_BACKEND == "claude":
        exe = shutil.which("claude")
        if exe is None:
            raise LlmServerError(
                "GEN_BACKEND='claude' but the `claude` CLI is not on PATH - "
                "generation runs on the Claude Code subscription via "
                "`claude -p`.")
        return {"backend": "claude", "model": GEN_MODEL, "cli": exe}
    return {"backend": "local", "model": LLM_MODEL, **check_server()}
