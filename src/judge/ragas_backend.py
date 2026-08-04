"""RAGAS LLM backends for the M5 judge: one per transport.

ragas 0.4.x's legacy metric interface (BaseRagasLLM) is the integration point:
the modern `collections` metrics require an instructor-wrapped HTTP client,
which neither transport can be. ragas is version-pinned in requirements.txt;
the legacy interface's deprecation horizon is ragas v1.0.

- OpenAICompatLLM (v5 default): the judge seat in src/openai_compat.py
  (DeepSeek V4 Flash), gated by that seat's OWN semaphore - the `claude -p`
  semaphore governs `claude -p` only and is not widened to cover it. It also
  counts completions that carry no parseable JSON, because that is where
  DeepSeek's loose JSON mode fails SILENTLY: ragas's parser retries with a
  fix-format call, and when the fix parses to an empty claims list the
  metric scores 0.0 - not NaN, not an exception - so without a counter a
  parse failure is indistinguishable from a wrong answer.
- ClaudeCliLLM (retired v4 seat): completions over the shared `claude -p`
  transport (src/claude_cli.py); gating and transient-failure backoff live
  there, shared with the legacy generation client.
"""

import asyncio
import json
import re
import sys
import threading
import types


def _install_vertexai_shim():
    """ragas 0.4.3 imports two VertexAI symbols that langchain-community
    0.4.x no longer ships, only to enumerate which providers support
    multiple completions. Stub them BEFORE importing ragas - they are never
    instantiated unless VertexAI is actually used (it is not, here)."""
    try:
        import langchain_community.chat_models.vertexai  # noqa: F401
    except ImportError:
        mod = types.ModuleType("langchain_community.chat_models.vertexai")

        class ChatVertexAI:  # placeholder, never instantiated
            pass

        mod.ChatVertexAI = ChatVertexAI
        sys.modules["langchain_community.chat_models.vertexai"] = mod
    import langchain_community.llms as _llms
    if not hasattr(_llms, "VertexAI"):
        class VertexAI:  # placeholder, never instantiated
            pass

        _llms.VertexAI = VertexAI


_install_vertexai_shim()

from langchain_core.outputs import Generation, LLMResult  # noqa: E402
from ragas.llms.base import BaseRagasLLM  # noqa: E402

from src.claude_cli import call_claude, call_claude_gated  # noqa: E402


class ClaudeCliLLM(BaseRagasLLM):
    """BaseRagasLLM whose completions run through the shared `claude -p`
    transport (src/claude_cli.py) - semaphore gating and transient-failure
    backoff live there, shared with generation and the rubric judge. A
    billing change swaps call_claude for an API call and this class never
    notices.
    """

    def __init__(self, model: str, semaphore: threading.Semaphore,
                 transport=call_claude):
        super().__init__()  # dataclass defaults: run_config, no cache
        self.model = model
        self.semaphore = semaphore
        self.transport = transport

    def _call(self, prompt: str) -> str:
        envelope = call_claude_gated(prompt, self.model,
                                     transport=self.transport,
                                     semaphore=self.semaphore)
        return str(envelope.get("result", ""))

    def generate_text(self, prompt, n=1, temperature=0.01, stop=None,
                      callbacks=None) -> LLMResult:
        # n>1 is unsupported (multiple_completion_supported=False) and
        # `claude -p` exposes no temperature control - both accepted, ignored.
        text = self._call(prompt.to_string())
        return LLMResult(generations=[[Generation(text=text)]])

    async def agenerate_text(self, prompt, n=1, temperature=0.01, stop=None,
                             callbacks=None) -> LLMResult:
        return await asyncio.to_thread(
            self.generate_text, prompt, n, temperature, stop, callbacks)

    def is_finished(self, response: LLMResult) -> bool:
        return True


# Every RAGAS metric prompt on this path demands JSON output; a completion
# with no JSON object or array in it is a parse failure in the making.
_JSON_BLOCK_RE = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


def _has_parseable_json(text: str) -> bool:
    for candidate in (text, *_JSON_BLOCK_RE.findall(text)):
        try:
            json.loads(candidate.strip())
            return True
        except (ValueError, TypeError):
            continue
    return False


class OpenAICompatLLM(BaseRagasLLM):
    """BaseRagasLLM over an OpenAI-compatible seat (v5 judge: DeepSeek V4
    Flash). Semaphore gating, transient-failure backoff and usage recording
    all live in src/openai_compat.py:call_api_gated. Temperature is pinned
    by the seat (0 for the judge); the temperature ragas passes per call is
    accepted and ignored, same as the claude backend always did.

    Counts every completion and every completion without parseable JSON
    (see module docstring for why that is the silent failure mode worth
    counting). Counters are cumulative for the life of the instance and
    read via stats().
    """

    def __init__(self, seat, semaphore: threading.Semaphore | None = None,
                 transport=None):
        super().__init__()  # dataclass defaults: run_config, no cache
        self.seat = seat
        self.model = seat.model
        self.semaphore = semaphore
        self.transport = transport
        self._lock = threading.Lock()
        self._calls = 0
        self._unparseable = 0

    def _call(self, prompt: str) -> str:
        from src.openai_compat import call_api_gated
        kwargs = {}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        envelope = call_api_gated([{"role": "user", "content": prompt}],
                                  self.seat, semaphore=self.semaphore,
                                  **kwargs)
        text = str(envelope.get("result", ""))
        with self._lock:
            self._calls += 1
            if not _has_parseable_json(text):
                self._unparseable += 1
        return text

    def stats(self) -> dict:
        with self._lock:
            return {"model": self.model, "completions": self._calls,
                    "unparseable_json": self._unparseable}

    def generate_text(self, prompt, n=1, temperature=0.01, stop=None,
                      callbacks=None) -> LLMResult:
        # n>1 is unsupported (multiple_completion_supported=False) and the
        # seat pins its own temperature - both accepted, ignored.
        text = self._call(prompt.to_string())
        return LLMResult(generations=[[Generation(text=text)]])

    async def agenerate_text(self, prompt, n=1, temperature=0.01, stop=None,
                             callbacks=None) -> LLMResult:
        return await asyncio.to_thread(
            self.generate_text, prompt, n, temperature, stop, callbacks)

    def is_finished(self, response: LLMResult) -> bool:
        return True
