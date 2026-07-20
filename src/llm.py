"""One LLM client interface for everything downstream.

OpenAI-compatible chat-completions client against the local llama-server on
port 8081 (SQL generation now; router/synthesis in M4; judge in M5). A
base_url/model swap here is the local-vs-API comparison mechanism - nothing
downstream knows what is behind the endpoint.
"""

import re

import requests

from src.config import LLM_BASE_URL, LLM_MODEL, LLM_SERVER_LAUNCH_CMD

# Qwen-style thinking blocks: never part of the answer, strip defensively.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


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
