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

A transcript is also cut into STEPS. An agent that stays warm across rounds -
the /draft-batch drafter through a fix round, the critic through a re-attack,
the judge across every round of its slot - receives a new instruction by
SendMessage and then sits idle until the next one arrives. One number per
agent cannot tell work from waiting: the hyb-09 judge of the 2026-07-25 batch
spans 38 minutes and worked for four of them. So an inbound instruction (the
spawn prompt, or a relayed message) opens a step, the step ends at the last
message the agent produced under it, and the idle gap before the next
instruction belongs to nobody. `active` is the sum of the steps; `span` is
first-to-last. Where they diverge, the agent was waiting on the bus.

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
class Spend:
    """Tokens and tool calls - the part that is identical for a step, an
    agent, and any rollup over agents."""
    turns: int = 0
    input_fresh: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    output: int = 0
    tools: dict[str, int] = field(default_factory=dict)

    @property
    def input_total(self) -> int:
        """Everything billed as input, cache included - the honest number.
        Reported alongside the split, because cache reads are most of it and
        they are the cheap part."""
        return self.input_fresh + self.cache_read + self.cache_creation

    @property
    def tool_calls(self) -> int:
        return sum(self.tools.values())

    @property
    def mcp_calls(self) -> int:
        return sum(n for tool, n in self.tools.items()
                   if tool.startswith("mcp__"))

    def add_usage(self, usage: dict) -> None:
        self.turns += 1
        self.input_fresh += int(usage.get("input_tokens") or 0)
        self.cache_read += int(usage.get("cache_read_input_tokens") or 0)
        self.cache_creation += int(
            usage.get("cache_creation_input_tokens") or 0)
        self.output += int(usage.get("output_tokens") or 0)

    def add_tool(self, name: str) -> None:
        self.tools[name] = self.tools.get(name, 0) + 1

    def absorb(self, other: "Spend") -> None:
        self.turns += other.turns
        self.input_fresh += other.input_fresh
        self.cache_read += other.cache_read
        self.cache_creation += other.cache_creation
        self.output += other.output
        for tool, n in other.tools.items():
            self.tools[tool] = self.tools.get(tool, 0) + n


@dataclass
class AgentStep:
    """One instruction and everything the agent did under it."""
    index: int
    label: str
    started: str | None = None
    ended: str | None = None
    spend: Spend = field(default_factory=Spend)

    @property
    def seconds(self) -> float | None:
        """Instruction to last message produced under it. Deliberately NOT to
        the next instruction: that gap is the agent waiting, not working."""
        return _elapsed(self.started, self.ended)


@dataclass
class AgentTrace:
    agent_id: str
    agent_type: str
    description: str
    model: str
    started: str | None = None
    ended: str | None = None
    spend: Spend = field(default_factory=Spend)
    steps: list[AgentStep] = field(default_factory=list)

    @property
    def seconds(self) -> float | None:
        """First to last message - the agent's whole lifetime, idle included.
        `active_seconds` is the part that was work."""
        return _elapsed(self.started, self.ended)

    @property
    def active_seconds(self) -> float | None:
        worked = [s.seconds for s in self.steps if s.seconds is not None]
        return sum(worked) if worked else None

    # The spend fields stay readable straight off the trace: every call site
    # and every test predates the Spend split, and `t.output` says what it
    # means more plainly than `t.spend.output` does.
    @property
    def turns(self) -> int:
        return self.spend.turns

    @property
    def input_fresh(self) -> int:
        return self.spend.input_fresh

    @property
    def cache_read(self) -> int:
        return self.spend.cache_read

    @property
    def cache_creation(self) -> int:
        return self.spend.cache_creation

    @property
    def output(self) -> int:
        return self.spend.output

    @property
    def tools(self) -> dict[str, int]:
        return self.spend.tools

    @property
    def input_total(self) -> int:
        return self.spend.input_total

    @property
    def mcp_calls(self) -> int:
        return self.spend.mcp_calls


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


# The harness wraps a relayed SendMessage in this preamble. Stripping it keeps
# the step label the instruction itself rather than the same boilerplate ten
# times down a column.
RELAY_PREFIX = "The coordinator sent a message while you were working:"


def _is_relay(entry: dict) -> bool:
    """A message the coordinator sent to a warm agent.

    These carry `isMeta` - which is the trap. Read the flag alone and every
    relay is filtered out as harness noise, so a warm agent reports one step
    and the whole point of the breakdown is lost. What separates a relay from
    a genuine injection (a system reminder, hook output) is that it was
    ADDRESSED to the agent, and the transcript says so: `origin.kind` is
    "coordinator". The text prefix is a fallback for the same fact.
    """
    origin = entry.get("origin")
    if isinstance(origin, dict) and origin.get("kind") == "coordinator":
        return True
    content = (entry.get("message") or {}).get("content")
    return isinstance(content, str) and content.lstrip().startswith(
        RELAY_PREFIX)


def instruction_text(entry: dict) -> str | None:
    """The prose of an inbound instruction, or None if this entry is not one.

    A step opens on an instruction: the spawn prompt, or a message relayed in
    while the agent was warm. Tool results are user entries too - they are the
    middle of a step, never the start of one - and so are the harness's own
    injected notes, which nobody asked the agent to act on.
    """
    if entry.get("type") != "user":
        return None
    if entry.get("isMeta") and not _is_relay(entry):
        return None
    message = entry.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        blocks = [b for b in content if isinstance(b, dict)]
        if any(b.get("type") == "tool_result" for b in blocks):
            return None
        text = " ".join(str(b.get("text") or "")
                        for b in blocks if b.get("type") == "text")
    else:
        return None
    text = " ".join(text.split())
    if text.startswith(RELAY_PREFIX):
        text = text[len(RELAY_PREFIX):].strip()
    return text or None


def _label(text: str, width: int = 56) -> str:
    # ASCII "..." rather than an ellipsis character: this table is read in a
    # Windows console, where a non-cp1252 character comes out as a replacement
    # box in every truncated row.
    if len(text) <= width:
        return text
    return text[:width - 3].rstrip() + "..."


def _accumulate(trace: AgentTrace, entries: list[dict]) -> AgentTrace:
    step: AgentStep | None = None

    def ensure_step(stamp: str | None) -> AgentStep:
        """A transcript should open with its instruction, but a truncated or
        resumed one may not. Rather than drop that work, give it a step that
        says what it is."""
        nonlocal step
        if step is None:
            step = AgentStep(index=len(trace.steps) + 1,
                             label="(before any recorded instruction)",
                             started=stamp)
            trace.steps.append(step)
        return step

    for entry in entries:
        stamp = entry.get("timestamp")
        if stamp:
            trace.started = trace.started or stamp
            trace.ended = stamp

        instruction = instruction_text(entry)
        if instruction is not None:
            step = AgentStep(index=len(trace.steps) + 1,
                             label=_label(instruction), started=stamp)
            trace.steps.append(step)
            continue

        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if isinstance(usage, dict):
            current = ensure_step(stamp)
            trace.spend.add_usage(usage)
            current.spend.add_usage(usage)
            trace.model = trace.model or str(message.get("model") or "?")
            if stamp:
                current.ended = stamp
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if (isinstance(block, dict)
                        and block.get("type") == "tool_use"):
                    name = str(block.get("name") or "?")
                    trace.spend.add_tool(name)
                    ensure_step(stamp).spend.add_tool(name)
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


def _as_utc(stamp: str) -> datetime | None:
    """Transcript timestamps are UTC ("...Z"); a `--since` typed from `date`
    is local. Comparing them as strings silently drops every agent when the
    machine is ahead of UTC - which is how the first live run traced empty."""
    try:
        parsed = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()          # naive input means local time
    return parsed


def trace_session(session: Path, since: str | None = None) -> list[AgentTrace]:
    """Every subagent of one session, oldest first. `since` (an ISO timestamp,
    local time unless it carries an offset) keeps only agents that ended after
    it - how one run is separated from an earlier run in the same session."""
    floor = _as_utc(since) if since else None
    traces = []
    for transcript in sorted((session / "subagents").glob("agent-*.jsonl")):
        trace = read_agent(transcript)
        if trace.turns == 0:
            continue
        if floor is not None:
            ended = _as_utc(trace.ended or "")
            if ended is None or ended < floor:
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


def _cache_pct(spend: Spend) -> int:
    return (100 * spend.cache_read // spend.input_total
            if spend.input_total else 0)


def _cache_share(spend: Spend) -> str:
    return f"{_fmt_tokens(spend.input_total)} ({_cache_pct(spend)}% cache)"


def _tool_cell(spend: Spend) -> str:
    return (f"{spend.tool_calls} ({spend.mcp_calls} MCP)"
            if spend.tools else "-")


# A /draft-batch agent works one slot, and its spawn description names it:
# "Draft hyb-09 candidate 2 viticulture", "Attack draft hyb-09 round 4",
# "Judge slot hyb-09". That is what lets the cost of a question be added up
# across the three agents that made it.
SLOT_ID = re.compile(r"\b((?:sql|vector|vec|hybrid|hyb|adv|amb|comp)-\d+)\b",
                     re.IGNORECASE)


def slot_of(trace: AgentTrace) -> str | None:
    match = SLOT_ID.search(trace.description or "")
    return match.group(1).lower() if match else None


Groups = list[tuple[str, list[AgentTrace], Spend]]


def _group(traces: list[AgentTrace], key) -> Groups:
    """Rollup by some key, in first-seen order, each with its summed spend."""
    groups: dict[str, list[AgentTrace]] = {}
    for trace in traces:
        name = key(trace)
        if name is None:
            continue
        groups.setdefault(name, []).append(trace)
    out = []
    for name, members in groups.items():
        total = Spend()
        for member in members:
            total.absorb(member.spend)
        out.append((name, members, total))
    return out


def _rollup_table(title: str, unit: str, groups: Groups) -> list[str]:
    out = ["", f"**{title}**", "",
           f"| {unit} | agents | turns | active | out tok | in tok (cache) | "
           "tool calls |",
           "|---|---|---|---|---|---|---|"]
    for name, members, total in groups:
        active = [t.active_seconds for t in members
                  if t.active_seconds is not None]
        out.append(f"| {name} | {len(members)} | {total.turns} | "
                   f"{_fmt_seconds(sum(active) if active else None)} | "
                   f"{_fmt_tokens(total.output)} | {_cache_share(total)} | "
                   f"{_tool_cell(total)} |")
    return out


def render_steps(traces: list[AgentTrace]) -> str:
    """Per agent, what each instruction cost. This is where a warm agent stops
    being one opaque number: a fix round and the draft that preceded it are
    separate lines, and the waiting between them is charged to neither."""
    out: list[str] = []
    for t in traces:
        if not t.steps:
            continue
        heading = f"{t.agent_id} {t.agent_type}"
        if t.description:
            heading += f" - {t.description}"
        out += ["", f"### {heading}", "",
                "| step | instruction | turns | active | out tok | "
                "in tok (cache) | tool calls |",
                "|---|---|---|---|---|---|---|"]
        for step in t.steps:
            out.append(f"| {step.index} | {step.label} | {step.spend.turns} | "
                       f"{_fmt_seconds(step.seconds)} | "
                       f"{_fmt_tokens(step.spend.output)} | "
                       f"{_cache_share(step.spend)} | "
                       f"{_tool_cell(step.spend)} |")
    if not out:
        return ""
    return "\n".join(["", "## Per step", *out])


def render_traces(traces: list[AgentTrace],
                  orchestrator: AgentTrace | None = None,
                  steps: bool = False) -> str:
    rows = ([orchestrator] if orchestrator else []) + traces
    if not rows:
        return ("No agent transcripts found. Either this session spawned no "
                "subagents, or the harness stores them somewhere new - this "
                "reads a format that is not ours to depend on.")
    out = ["| agent | type | turns | steps | active* | span* | out tok | "
           "in tok (cache) | tool calls |",
           "|---|---|---|---|---|---|---|---|---|"]
    for t in rows:
        out.append(f"| {t.agent_id} | {t.agent_type} | {t.turns} | "
                   f"{len(t.steps)} | {_fmt_seconds(t.active_seconds)} | "
                   f"{_fmt_seconds(t.seconds)} | {_fmt_tokens(t.output)} | "
                   f"{_cache_share(t.spend)} | {_tool_cell(t.spend)} |")

    total = Spend()
    for t in rows:
        total.absorb(t.spend)
    span = [t.seconds for t in traces if t.seconds is not None]
    active = [t.active_seconds for t in traces if t.active_seconds is not None]
    out += ["",
            f"**Totals:** {len(traces)} subagent(s) + "
            f"{'1 orchestrator' if orchestrator else 'no orchestrator row'}; "
            f"output {_fmt_tokens(total.output)}, input "
            f"{_fmt_tokens(total.input_total)} "
            f"({_cache_pct(total)}% cache reads)."]
    if active and span:
        out.append(f"**Subagent time:** {_fmt_seconds(sum(active))} active "
                   f"summed, {_fmt_seconds(sum(span))} spanned, "
                   f"{_fmt_seconds(max(span))} longest single agent. Active "
                   "summed above the run's wall clock is what real "
                   "concurrency looks like; span far above active is an agent "
                   "left warm and waiting between rounds.")

    types = _group(traces, lambda t: t.agent_type)
    if len(types) > 1:
        out += _rollup_table("By agent type - what each role costs",
                             "type", types)
    slots = _group(traces, slot_of)
    if slots:
        out += _rollup_table("By slot - what each question cost, across the "
                             "agents that made it", "slot", slots)

    out.append("")
    for t in traces:
        if t.description:
            out.append(f"- `{t.agent_id}` {t.agent_type}: {t.description}")
    out += ["",
            "\\* **active** sums the steps: each instruction (the spawn "
            "prompt, or a message relayed to a warm agent) to the last "
            "message the agent produced under it. **span** is first-to-last, "
            "idle included. For a one-shot agent they nearly agree; for a "
            "warm one the difference is time spent waiting on the bus, and "
            "for the orchestrator row span is the whole session lifetime, so "
            "read that one as a bound rather than as work done.",
            "",
            "Output tokens are the expensive direction; cache reads are the "
            "cheap part of input. Counted from each agent's own transcript, "
            "so concurrent agents cannot be confused."]
    if steps:
        out.append(render_steps(traces))
    return "\n".join(out)
