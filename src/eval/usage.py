"""What a run cost - tokens and dollars, attributed to the question that spent
them.

`claude -p --output-format json` hands back a full envelope on every call:
`total_cost_usd`, the four token counts, `duration_ms`, and a `modelUsage` block
naming the model that actually served the request. Everything downstream only
ever read `envelope["result"]` and dropped the rest, so a run's spend was
unknowable after the fact - the same gap the drafting audit hit when it had to
reconstruct cost as "~70% of a 5-hour window" (see src/eval/trace.py, which
solves the same problem for Claude Code subagents by reading their transcripts).

This solves it at the source instead. `src/claude_cli.py:call_claude_gated` is
the ONE gate every `claude -p` call passes through - generation and judging
alike - so recording there captures a whole run with one hook.

Attribution rides a contextvars.ContextVar, NOT a thread-local. That is
load-bearing: judging runs under `asyncio.gather` (ragas_judge.judge_batch) and
`asyncio.to_thread` (ragas_backend.agenerate_text, and the rubric overlay).
contextvars propagate across both - an asyncio.Task copies the context at
creation and `asyncio.to_thread` copies it into the worker - while a
thread-local would silently lose every judge call and report a run as costing
generation only. Each Task gets its own COPY, so concurrent questions cannot
cross-label each other.

A call made outside any stage() block is recorded under UNATTRIBUTED rather than
dropped: totals stay correct even when a label is missed, so the worst a gap can
do is make the accounting coarser, never wrong.

Defensive throughout, for the reason trace.py states: instrumentation must never
be the thing that fails a run. A malformed envelope records a zeroed row.

Cost note: on the Max subscription the marginal spend is ~EUR 0. `cost_usd` is
what the calls WOULD have cost on the API - a priced figure, not a billed one,
and every report that shows it must say so.
"""

from __future__ import annotations

import contextvars
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field

UNATTRIBUTED = "(unattributed)"

# (label, stage) for the call currently in flight on this context.
_STAGE: contextvars.ContextVar[tuple[str, str]] = contextvars.ContextVar(
    "horizon_usage_stage", default=(UNATTRIBUTED, UNATTRIBUTED))

_LOCK = threading.Lock()
_RECORDS: list["CallRecord"] = []


@dataclass
class CallRecord:
    """One `claude -p` subprocess: who asked for it, and what it cost."""
    label: str                 # question_id, or UNATTRIBUTED
    stage: str                 # "gen" | "judge" | UNATTRIBUTED
    model: str                 # the model we asked for
    model_resolved: str        # the model that did the work (see _resolved_model)
    cost_usd: float = 0.0      # the WHOLE call, every model in it
    model_costs: dict[str, float] = field(default_factory=dict)
    input_fresh: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    output: int = 0
    duration_ms: int = 0
    num_turns: int = 0


@dataclass
class Cost:
    """A rollup over calls. Named Cost, not Spend, to stay distinct from
    trace.Spend - that one counts Claude Code subagent turns and tool calls and
    has no dollar figure; this one counts `claude -p` subprocesses and does."""
    calls: int = 0
    cost_usd: float = 0.0
    input_fresh: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    output: int = 0
    duration_ms: int = 0
    models: set[str] = field(default_factory=set)
    model_costs: dict[str, float] = field(default_factory=dict)

    @property
    def input_total(self) -> int:
        """Everything billed as input, cache included. Reported beside the
        split because cache reads are most of it and they are the cheap part."""
        return self.input_fresh + self.cache_read + self.cache_creation

    def add(self, r: CallRecord) -> "Cost":
        self.calls += 1
        self.cost_usd += r.cost_usd
        self.input_fresh += r.input_fresh
        self.cache_read += r.cache_read
        self.cache_creation += r.cache_creation
        self.output += r.output
        self.duration_ms += r.duration_ms
        if r.model_resolved:
            self.models.add(r.model_resolved)
        for name, cost in r.model_costs.items():
            self.model_costs[name] = self.model_costs.get(name, 0.0) + cost
        return self

    def as_dict(self) -> dict:
        return {"calls": self.calls, "cost_usd": round(self.cost_usd, 6),
                "input_fresh": self.input_fresh, "cache_read": self.cache_read,
                "cache_creation": self.cache_creation,
                "input_total": self.input_total, "output": self.output,
                "duration_ms": self.duration_ms,
                "models": sorted(self.models),
                "cost_by_model": {n: round(c, 6) for n, c
                                  in sorted(self.model_costs.items())}}


@contextmanager
def stage(label: str, name: str):
    """Attribute every `claude -p` call made inside this block to (label, name).

    Nests and restores, so a judge block inside a run loop cannot leak its label
    onto the next question.
    """
    token = _STAGE.set((label or UNATTRIBUTED, name or UNATTRIBUTED))
    try:
        yield
    finally:
        _STAGE.reset(token)


def current_stage() -> tuple[str, str]:
    return _STAGE.get()


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _model_costs(envelope: dict) -> dict[str, float]:
    """Per-model dollar split for one call.

    A `claude -p` call is a whole Claude Code session, so it usually bills TWO
    models: the one asked for, plus a little Haiku the harness spends on its own
    overhead. A judge call therefore shows up as mostly Sonnet with a few tenths
    of a cent of Haiku - real, and worth seeing rather than folding away.
    """
    usage = envelope.get("modelUsage")
    if not isinstance(usage, dict):
        return {}
    out = {}
    for name, block in usage.items():
        if isinstance(block, dict):
            out[str(name)] = _float(block.get("costUSD"))
    return out


def _resolved_model(costs: dict[str, float], requested: str) -> str:
    """Which model actually did the work.

    Prefer the one we asked for (alias-insensitive: we ask for "sonnet" or
    "claude-sonnet-5", the envelope answers "claude-sonnet-5"), else the one
    that cost the most. Taking modelUsage's first key instead would report a
    Sonnet judge call as Haiku, because dict order puts the harness's own
    overhead model first - a trace that lies about which model ran is worse
    than no trace.
    """
    if not costs:
        return requested
    want = requested.lower()
    for name in costs:
        low = name.lower()
        if low == want or want in low or low in want:
            return name
    return max(costs, key=lambda n: costs[n])


def record_envelope(envelope, model: str) -> CallRecord | None:
    """Record one call. Never raises - a tracing bug must not fail a run."""
    try:
        if not isinstance(envelope, dict):
            return None
        label, stage_name = _STAGE.get()
        usage = envelope.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        costs = _model_costs(envelope)
        record = CallRecord(
            label=label, stage=stage_name, model=model,
            model_resolved=_resolved_model(costs, model),
            cost_usd=_float(envelope.get("total_cost_usd")),
            model_costs=costs,
            input_fresh=_int(usage.get("input_tokens")),
            cache_read=_int(usage.get("cache_read_input_tokens")),
            cache_creation=_int(usage.get("cache_creation_input_tokens")),
            output=_int(usage.get("output_tokens")),
            duration_ms=_int(envelope.get("duration_ms")),
            num_turns=_int(envelope.get("num_turns")))
        with _LOCK:
            _RECORDS.append(record)
        return record
    except Exception:                                    # noqa: BLE001
        return None


def collect() -> list[CallRecord]:
    """Take everything recorded so far and clear the buffer, atomically."""
    global _RECORDS
    with _LOCK:
        taken, _RECORDS = _RECORDS, []
    return taken


def take(label: str) -> list[CallRecord]:
    """Remove and return just this label's calls, leaving everyone else's.

    The runner's per-question collection point. A plain collect() would work
    while phase A is sequential, but it would also swallow any call another
    thread was making at that moment - so this stays precise even if the
    concurrency knob is ever turned up.
    """
    global _RECORDS
    with _LOCK:
        mine = [r for r in _RECORDS if r.label == label]
        _RECORDS = [r for r in _RECORDS if r.label != label]
    return mine


def snapshot() -> list[CallRecord]:
    with _LOCK:
        return list(_RECORDS)


def reset() -> None:
    collect()


def total(records: list[CallRecord]) -> Cost:
    cost = Cost()
    for r in records:
        cost.add(r)
    return cost


def by_stage(records: list[CallRecord]) -> dict[str, Cost]:
    """stage -> Cost. The runner's per-question view: gen vs judge."""
    out: dict[str, Cost] = {}
    for r in records:
        out.setdefault(r.stage, Cost()).add(r)
    return out


def by_label(records: list[CallRecord]) -> dict[str, list[CallRecord]]:
    """question_id -> its calls. How one concurrent judge batch is split back
    out into per-question spend."""
    out: dict[str, list[CallRecord]] = {}
    for r in records:
        out.setdefault(r.label, []).append(r)
    return out
