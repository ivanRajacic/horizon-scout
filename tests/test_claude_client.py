"""Generation-over-claude tests (v4): message rendering, the .chat contract,
the make_llm backend switch, and 16-way parallel gating through the shared
semaphore. Transport is faked - no `claude` CLI, no network."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from src import claude_cli
from src.config import CLAUDE_MAX_CONCURRENCY, GEN_MODEL
from src.llm import ClaudeClient, LlmClient, make_llm


def envelope(result: str) -> dict:
    return {"type": "result", "result": result, "is_error": False}


# --- rendering ---

def test_render_system_then_single_user_is_plain():
    msgs = [{"role": "system", "content": "SYS"},
            {"role": "user", "content": "Q?"}]
    assert ClaudeClient.render(msgs) == "SYS\n\nQ?"


def test_render_multi_turn_labels_dialogue():
    msgs = [{"role": "system", "content": "SYS"},
            {"role": "user", "content": "Q?"},
            {"role": "assistant", "content": "bad sql"},
            {"role": "user", "content": "fix it"}]
    out = ClaudeClient.render(msgs)
    assert out.startswith("SYS\n\n")
    assert "User:\nQ?" in out and "Assistant:\nbad sql" in out
    assert "User:\nfix it" in out
    assert out.index("User:\nQ?") < out.index("Assistant:\nbad sql")


# --- chat contract ---

def test_chat_returns_stripped_result_and_ignores_overrides():
    seen = {}

    def transport(prompt, model, **kw):
        seen["prompt"], seen["model"] = prompt, model
        return envelope("  SELECT 1;  ")

    c = ClaudeClient(model="m-test", transport=transport)
    out = c.chat([{"role": "system", "content": "S"},
                  {"role": "user", "content": "U"}],
                 max_tokens=128, temperature=0.7)  # accepted, ignored
    assert out == "SELECT 1;"
    assert seen["model"] == "m-test" and "S\n\nU" == seen["prompt"]


def test_chat_default_model_is_gen_model():
    assert ClaudeClient(transport=lambda p, m, **kw: envelope("x")).model \
        == GEN_MODEL


# --- factory ---

def test_make_llm_backend_switch(monkeypatch):
    import src.llm as llm_mod
    monkeypatch.setattr(llm_mod, "GEN_BACKEND", "claude")
    assert isinstance(make_llm(), ClaudeClient)
    monkeypatch.setattr(llm_mod, "GEN_BACKEND", "local")
    local = make_llm(max_tokens=99)
    assert isinstance(local, LlmClient) and local.max_tokens == 99


# --- concurrency ---

def test_parallel_generation_respects_global_cap(monkeypatch):
    """32 threads generating at once never exceed the shared semaphore cap."""
    cap = 3
    monkeypatch.setattr(claude_cli, "_SEMAPHORE", threading.Semaphore(cap))
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

    c = ClaudeClient(model="m", transport=transport)
    msgs = [{"role": "user", "content": "q"}]
    with ThreadPoolExecutor(max_workers=32) as pool:
        results = list(pool.map(lambda _: c.chat(msgs), range(32)))
    assert results == ["ok"] * 32
    assert peak <= cap


def test_hard_ceiling_is_sixteen():
    assert CLAUDE_MAX_CONCURRENCY == 16


# --- the command line itself ---

def test_the_transport_gives_claude_no_tools(monkeypatch):
    """A generator writes an answer and a judge scores one. Neither needs to
    run Bash, and leaving the tools on is not harmless: on 2026-07-26 the judge
    model answered two large cases by reaching for a tool, which with
    --max-turns 1 ended the session on stop_reason "tool_use" and exited 1 -
    two verdicts lost and $1.51 spent for nothing. Pinned here because the
    failure is invisible until a prompt happens to be big enough to provoke it.
    """
    seen = {}

    class Done:
        returncode = 0
        stdout = '{"type": "result", "result": "ok", "is_error": false}'
        stderr = ""

    monkeypatch.setattr(claude_cli.shutil, "which", lambda _: "/fake/claude")
    monkeypatch.setattr(claude_cli.subprocess, "run",
                        lambda cmd, **kw: seen.update(cmd=cmd) or Done())

    claude_cli.call_claude("q", "sonnet")
    cmd = seen["cmd"]
    assert cmd[cmd.index("--tools") + 1] == ""      # every built-in tool off
    assert cmd[cmd.index("--max-turns") + 1] == "1"
    assert cmd[cmd.index("--model") + 1] == "sonnet"
