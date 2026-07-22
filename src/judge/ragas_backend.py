"""RAGAS LLM backend over the `claude -p` transport (M5 judge, RAGAS edition).

ragas 0.4.x's legacy metric interface (BaseRagasLLM) is the integration point:
the modern `collections` metrics require an instructor-wrapped HTTP client,
which a CLI subprocess cannot be. ragas is version-pinned in requirements.txt;
the legacy interface's deprecation horizon is ragas v1.0.

Concurrency: one shared threading.Semaphore caps how many `claude -p`
processes run at once - the SAME semaphore gates every judging path (RAGAS
metric calls and the rubric-overlay judge), so the configured cap is global,
not per-component. v4: gating and transient-failure backoff moved to
src/claude_cli.py, shared with the generation clients too.
"""

import asyncio
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
