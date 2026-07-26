"""What each agent in a run actually cost - time and tokens, per agent.

The drafting audit had to reconstruct spend as "~70% of a 5-hour window"
because nobody was counting. This counts.

Claude Code writes one transcript per subagent under
`~/.claude/projects/<project-slug>/<session-id>/subagents/agent-<id>.jsonl`,
with a sibling `.meta.json` naming the agent type and the spawn description.
Each transcript carries every assistant message's `usage` block and timestamp,
and every `tool_use` the agent made. So attribution is EXACT: an agent's tool
calls are counted from its own transcript, never guessed from a time window
over the shared MCP log (which cannot tell two concurrent agents apart).

Read-only, and defensive by design: this reads a harness-internal format that
is not ours to depend on. Anything unreadable degrades to a missing row rather
than an exception - a tracing tool must never be the thing that fails a run.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.config import ROOT

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"

# Claude Code's project directory name: EACH non-alphanumeric character
# becomes a dash, not each run of them - "C:\horizon-scout" is
# "C--horizon-scout" (colon and backslash are two dashes), not "C-horizon-scout".
def project_slug(path: Path = ROOT) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def project_dir(path: Path = ROOT,
                projects: Path = CLAUDE_PROJECTS) -> Path:
    return projects / project_slug(path)


@dataclass
class AgentTrace:
    agent_id: str
    agent_type: str
    description: str
    model: str
    turns: int = 0
    started: str | None = None
    ended: str | None = None
    input_fresh: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    output: int = 0
    tools: dict[str, int] = field(default_factory=dict)

    @property
    def seconds(self) -> float | None:
        return _elapsed(self.started, self.ended)

    @property
    def input_total(self) -> int:
        """Everything billed as input, cache included - the honest number.
        Reported alongside the split, because cache reads are most of it and
        they are the cheap part."""
        return self.input_fresh + self.cache_read + self.cache_creation

    @property
    def mcp_calls(self) -> int:
        return sum(n for tool, n in self.tools.items()
                   if tool.startswith("mcp__"))


def _elapsed(started: str | None, ended: str | None) -> float | None:
    if not started or not ended:
        return None
    try:
        a = datetime.fromisoformat(started.replace("Z", "+00:00"))
        b = datetime.fromisoformat(ended.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (b - a).total_seconds())


def _read_jsonl(path: Path) -> list[dict]:
    out = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue          # a transcript being written as we read it
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _accumulate(trace: AgentTrace, entries: list[dict]) -> AgentTrace:
    for entry in entries:
        stamp = entry.get("timestamp")
        if stamp:
            trace.started = trace.started or stamp
            trace.ended = stamp
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if isinstance(usage, dict):
            trace.turns += 1
            trace.model = trace.model or str(message.get("model") or "?")
            trace.input_fresh += int(usage.get("input_tokens") or 0)
            trace.cache_read += int(usage.get("cache_read_input_tokens") or 0)
            trace.cache_creation += int(
                usage.get("cache_creation_input_tokens") or 0)
            trace.output += int(usage.get("output_tokens") or 0)
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if (isinstance(block, dict)
                        and block.get("type") == "tool_use"):
                    name = str(block.get("name") or "?")
                    trace.tools[name] = trace.tools.get(name, 0) + 1
    return trace


def read_agent(transcript: Path) -> AgentTrace:
    meta_path = transcript.with_suffix("").with_suffix(".meta.json")
    meta = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
    trace = AgentTrace(
        agent_id=transcript.stem.replace("agent-", "")[:10],
        agent_type=str(meta.get("agentType") or "?"),
        description=str(meta.get("description") or ""),
        model="")
    return _accumulate(trace, _read_jsonl(transcript))


def session_dirs(path: Path = ROOT,
                 projects: Path = CLAUDE_PROJECTS) -> list[Path]:
    """Session directories that hold subagent transcripts, newest first."""
    root = project_dir(path, projects)
    if not root.is_dir():
        return []
    dirs = [d for d in root.iterdir()
            if d.is_dir() and (d / "subagents").is_dir()]
    return sorted(dirs, key=lambda d: (d / "subagents").stat().st_mtime,
                  reverse=True)


def trace_session(session: Path, since: str | None = None) -> list[AgentTrace]:
    """Every subagent of one session, oldest first. `since` (an ISO timestamp)
    keeps only agents that ended after it - how one run is separated from an
    earlier run in the same session."""
    traces = []
    for transcript in sorted((session / "subagents").glob("agent-*.jsonl")):
        trace = read_agent(transcript)
        if trace.turns == 0:
            continue
        if since and (trace.ended or "") < since:
            continue
        traces.append(trace)
    traces.sort(key=lambda t: t.started or "")
    return traces


def orchestrator_trace(session: Path) -> AgentTrace | None:
    """The parent session's own spend - the orchestrator's line in the table.

    Its transcript is the `<session-id>.jsonl` beside the session directory.
    """
    transcript = session.parent / f"{session.name}.jsonl"
    if not transcript.is_file():
        return None
    trace = AgentTrace(agent_id=session.name[:10], agent_type="(orchestrator)",
                       description="the session itself", model="")
    return _accumulate(trace, _read_jsonl(transcript))


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def _fmt_seconds(s: float | None) -> str:
    if s is None:
        return "-"
    return f"{s / 60:.1f}m" if s >= 60 else f"{s:.0f}s"


def render_traces(traces: list[AgentTrace],
                  orchestrator: AgentTrace | None = None) -> str:
    rows = ([orchestrator] if orchestrator else []) + traces
    if not rows:
        return ("No agent transcripts found. Either this session spawned no "
                "subagents, or the harness stores them somewhere new - this "
                "reads a format that is not ours to depend on.")
    out = ["| agent | type | turns | wall* | out tok | in tok (cache) | "
           "tool calls |",
           "|---|---|---|---|---|---|---|"]
    for t in rows:
        cached = (f"{_fmt_tokens(t.input_total)} "
                  f"({100 * t.cache_read // t.input_total if t.input_total else 0}% cache)")
        tools = (f"{sum(t.tools.values())} ({t.mcp_calls} MCP)"
                 if t.tools else "-")
        out.append(f"| {t.agent_id} | {t.agent_type} | {t.turns} | "
                   f"{_fmt_seconds(t.seconds)} | {_fmt_tokens(t.output)} | "
                   f"{cached} | {tools} |")

    total_out = sum(t.output for t in rows)
    total_in = sum(t.input_total for t in rows)
    total_cached = sum(t.cache_read for t in rows)
    sub_wall = [t.seconds for t in traces if t.seconds is not None]
    out += ["",
            f"**Totals:** {len(traces)} subagent(s) + "
            f"{'1 orchestrator' if orchestrator else 'no orchestrator row'}; "
            f"output {_fmt_tokens(total_out)}, input {_fmt_tokens(total_in)} "
            f"({100 * total_cached // total_in if total_in else 0}% cache "
            "reads)."]
    if sub_wall:
        out.append(f"**Subagent wall clock:** {_fmt_seconds(max(sub_wall))} "
                   f"slowest, {_fmt_seconds(sum(sub_wall))} summed - the gap "
                   "between them is what the parallelism bought.")
    for t in traces:
        if t.description:
            out.append(f"- `{t.agent_id}` {t.agent_type}: {t.description}")
    out += ["",
            "\\* first-to-last message timestamp. For a subagent that is "
            "close to its true run time; for the orchestrator row it is the "
            "whole session lifetime, idle included, so read it as a bound "
            "rather than as work done.",
            "",
            "Output tokens are the expensive direction; cache reads are the "
            "cheap part of input. Counted from each agent's own transcript, "
            "so concurrent agents cannot be confused."]
    return "\n".join(out)
