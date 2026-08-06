"""Factory telemetry - what the question-authoring pipeline did, counted
from its own records.

The batch journals, the MCP log and the subagent transcripts already hold
every fact about how the bank was made: what was drafted, what the critic
found, what the judge upheld, what the deterministic gates killed, and what
it all cost. This module computes the totals. It is analysis, not pipeline:
nothing here is called by a run, and it writes only its own report.

Counting rule that matters: journals are append-only and every slot event
restates the whole envelope (findings, decisions, history included), so all
counts come from the LAST line per slot. Counting every line would inflate
each finding by the number of times its slot was restated.

Usage:
    ./.venv/Scripts/python.exe -m src.eval.telemetry [--skip-transcripts]

Writes docs/factory-telemetry.md and docs/factory-telemetry.json.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from src.config import ROOT
from src.eval.trace import (session_dirs, trace_session, orchestrator_trace,
                            slot_of, Spend, _fmt_tokens, _fmt_seconds)

DRAFTS = ROOT / "eval" / "drafts"
BANK = ROOT / "eval" / "bank.jsonl"
TRIMMED = ROOT / "eval" / "archive" / "bank-trimmed-2026-08-03.jsonl"
MCP_LOG = ROOT / "data" / "logs" / "draft_mcp.jsonl"
OUT_MD = ROOT / "docs" / "factory-telemetry.md"
OUT_JSON = ROOT / "docs" / "factory-telemetry.json"

# The three /question-orchestrator roles, as their .claude/agents names.
FACTORY_AGENTS = ("question-drafter", "question-reviewer", "question-judge")

# $ per million tokens (input, output), Anthropic API prices as of 2026-08-06.
# Cache reads bill at 0.1x the input price; cache writes at 1.25x (5-minute
# TTL - the 1-hour TTL bills 2x, so the write component below is a floor).
# Matched by substring, first hit wins - "opus-4-5" must precede "opus-5".
PRICES = [
    ("fable-5", 10.0, 50.0),
    ("opus-4-5", 5.0, 25.0),
    ("opus-4-6", 5.0, 25.0),
    ("opus-4-7", 5.0, 25.0),
    ("opus-4-8", 5.0, 25.0),
    ("opus-4-1", 15.0, 75.0),
    ("opus-5", 5.0, 25.0),
    ("sonnet", 3.0, 15.0),
    ("haiku", 1.0, 5.0),
]


def price_spend(model: str, s: Spend) -> float | None:
    """Dollars for one model's spend, or None when the model is unknown."""
    for key, inp, out in PRICES:
        if key in (model or ""):
            return ((s.input_fresh * inp
                     + s.cache_read * inp * 0.1
                     + s.cache_creation * inp * 1.25
                     + s.output * out) / 1_000_000)
    return None


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
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


# ---------------------------------------------------------------- journals

def journal_files() -> list[tuple[str, Path]]:
    """(run label, journal path) for every journal under eval/drafts.

    Discovery is recursive rather than a `batch*` glob, so a run that does not
    follow the batch naming - the 2026-08-06 Sonnet probe lives in
    `sonnet-probe/{sql,vec,hyb}/` - is counted instead of silently missing. The
    label is the path relative to eval/drafts, so `sonnet-probe/vec` and
    `batchI` are distinguishable in the per-run table.

    `eval/drafts/archive/` is skipped: it holds the retired pre-skill runs,
    whose journals predate the typed envelope and would distort every count.
    """
    out = []
    for j in sorted(DRAFTS.rglob("draft-batch-journal-*.jsonl")):
        rel = j.relative_to(DRAFTS)
        if "archive" in rel.parts:
            continue
        label = (str(rel.parent).replace("\\", "/") if rel.parent != Path(".")
                 else j.stem.replace("draft-batch-journal-", "batch-"))
        out.append((label, j))
    return out


def terminal_slots(journal: Path) -> tuple[dict | None, dict[str, dict]]:
    """The batch header line and the LAST slot line per question_id."""
    batch = None
    slots: dict[str, dict] = {}
    for e in _read_jsonl(journal):
        kind = e.get("kind")
        if kind == "batch" and batch is None:
            batch = e
        elif kind == "slot" and e.get("question_id"):
            slots[e["question_id"]] = e
    return batch, slots


def _is_upheld(ruling) -> bool:
    return isinstance(ruling, str) and ruling.startswith("UPHELD")


def journal_stats() -> dict:
    per_batch = []
    status = Counter()
    dispositions = Counter()
    rounds_dist = Counter()
    candidates_tried = 0
    sev = Counter()
    rulings = Counter()          # (severity, ruling bucket) over HIGH and MID
    classes = Counter()
    other_labels = Counter()
    accepted_with_upheld = 0
    accepted_with_fix = 0
    slots_total = 0

    for label, journal in journal_files():
        batch, slots = terminal_slots(journal)
        b_disp = Counter()
        for qid, e in slots.items():
            slots_total += 1
            status[e.get("status")] += 1
            candidates_tried += int(e.get("candidate_index") or 0) + 1
            decisions = e.get("judge_decisions") or []
            rounds_dist[len(decisions)] += 1
            slot_disp = Counter(d.get("disposition") for d in decisions
                                if isinstance(d, dict))
            dispositions.update(slot_disp)
            b_disp.update(slot_disp)

            upheld_here = False
            for f in (e.get("findings") or []):
                if not isinstance(f, dict):
                    continue
                severity = f.get("severity")
                sev[severity] += 1
                cls = f.get("class") or "?"
                if isinstance(cls, str) and cls.startswith("OTHER:"):
                    classes["OTHER:*"] += 1
                    other_labels[cls] += 1
                else:
                    classes[cls] += 1
                if severity in ("HIGH", "MID"):
                    ruling = f.get("ruling")
                    bucket = ("UPHELD" if _is_upheld(ruling)
                              else ruling if ruling in ("DISMISSED",
                                                        "RECORDED")
                              else "unadjudicated")
                    rulings[(severity, bucket)] += 1
                    if _is_upheld(ruling):
                        upheld_here = True

            if e.get("status") == "ACCEPTED":
                if upheld_here:
                    accepted_with_upheld += 1
                if slot_disp.get("FIX"):
                    accepted_with_fix += 1

        per_batch.append({
            "batch": label,
            "date": (batch or {}).get("date"),
            # None for every run before 2026-08-06 - the header only started
            # carrying the model when the role agents stopped pinning one.
            "model": (batch or {}).get("model"),
            "slots": len(slots),
            "accepted": sum(1 for e in slots.values()
                            if e.get("status") == "ACCEPTED"),
            "fix_rounds": b_disp.get("FIX", 0),
            "abandons": b_disp.get("ABANDON", 0),
        })

    return {
        "batches": len(per_batch),
        "per_batch": per_batch,
        "slots": slots_total,
        "final_status": dict(status),
        "candidates_tried": candidates_tried,
        "dispositions": dict(dispositions),
        "adjudication_rounds": dict(sorted(rounds_dist.items())),
        "findings_by_severity": dict(sev),
        "rulings": {f"{s}/{b}": n for (s, b), n in sorted(rulings.items())},
        "defect_classes": dict(classes.most_common()),
        "other_label_count": len(other_labels),
        "accepted_with_upheld_finding": accepted_with_upheld,
        "accepted_with_fix_round": accepted_with_fix,
    }


# ----------------------------------------------------------------- MCP log

def mcp_stats() -> dict:
    calls = _read_jsonl(MCP_LOG)
    tools = Counter(e.get("tool") for e in calls)
    errors = sum(1 for e in calls if e.get("ok") is False)
    prechecks = {}
    for name in ("precheck_record", "precheck_candidate"):
        mine = [e for e in calls if e.get("tool") == name]
        failed = sum(1 for e in mine if e.get("failures"))
        prechecks[name] = {"calls": len(mine), "with_failures": failed}
    days = sorted({e.get("ts", "")[:10] for e in calls if e.get("ts")})
    return {
        "calls": len(calls),
        "by_tool": dict(tools.most_common()),
        "errors": errors,
        "prechecks": prechecks,
        "first_day": days[0] if days else None,
        "last_day": days[-1] if days else None,
        "active_days": len(days),
    }


# ------------------------------------------------------------ bank linkage

def _qids(path: Path, inner: str | None = None) -> set[str]:
    out = set()
    for e in _read_jsonl(path):
        rec = e.get(inner) if inner else e
        if isinstance(rec, dict) and rec.get("question_id"):
            out.add(rec["question_id"])
    return out


def bank_stats() -> dict:
    bank = _qids(BANK)
    trimmed = _qids(TRIMMED, inner="record")
    batch_accepted = set()
    for d in sorted(DRAFTS.glob("batch*")):
        if d.is_dir():
            for f in d.glob("draft-bank-*.jsonl"):
                batch_accepted |= _qids(f)
    for f in DRAFTS.glob("draft-bank-*.jsonl"):
        batch_accepted |= _qids(f)
    return {
        "bank": len(bank),
        "trimmed": len(trimmed),
        "pipeline_total": len(bank | trimmed),
        "bank_from_batches": len(bank & batch_accepted),
        "bank_interactive": len(bank - batch_accepted),
        "trimmed_from_batches": len(trimmed & batch_accepted),
    }


# ------------------------------------------------------------- transcripts

def transcript_stats() -> dict:
    """Tokens and active time of the factory agents, from the subagent
    transcripts Claude Code keeps per session. Defensive by inheritance:
    trace.py degrades unreadable transcripts to missing rows, and this
    section degrades to an empty dict rather than failing the report."""
    by_type: dict[str, Spend] = {}
    counts = Counter()
    active: Counter = Counter()
    per_slot: dict[str, Spend] = {}
    by_model: dict[str, Spend] = {}
    try:
        for session in session_dirs():
            factory_here = False
            for t in trace_session(session):
                if t.agent_type not in FACTORY_AGENTS:
                    continue
                factory_here = True
                by_type.setdefault(t.agent_type, Spend()).absorb(t.spend)
                by_model.setdefault(t.model or "?", Spend()).absorb(t.spend)
                counts[t.agent_type] += 1
                if t.active_seconds:
                    active[t.agent_type] += t.active_seconds
                slot = slot_of(t)
                if slot:
                    per_slot.setdefault(slot, Spend()).absorb(t.spend)
            # The session that DROVE factory agents is factory cost too -
            # the orchestrator read every report and relayed every message.
            if factory_here:
                orch = orchestrator_trace(session)
                if orch and orch.turns:
                    by_type.setdefault("(orchestrator)", Spend()).absorb(
                        orch.spend)
                    by_model.setdefault(orch.model or "?", Spend()).absorb(
                        orch.spend)
                    counts["(orchestrator)"] += 1
                    if orch.active_seconds:
                        active["(orchestrator)"] += orch.active_seconds
    except Exception as ex:                      # a tracing tool must never
        return {"error": f"{type(ex).__name__}: {ex}"}   # fail the report
    cost_rows = []
    total_cost = 0.0
    unpriced = []
    for model, s in sorted(by_model.items(),
                           key=lambda kv: kv[1].output, reverse=True):
        dollars = price_spend(model, s)
        if dollars is None:
            unpriced.append(model)
        else:
            total_cost += dollars
        cost_rows.append({
            "model": model,
            "input_fresh": s.input_fresh,
            "cache_read": s.cache_read,
            "cache_creation": s.cache_creation,
            "output": s.output,
            "cost_usd": None if dollars is None else round(dollars, 2),
        })

    rows = {}
    for name in FACTORY_AGENTS + ("(orchestrator)",):
        s = by_type.get(name)
        if not s:
            continue
        rows[name] = {
            "agents": counts[name],
            "turns": s.turns,
            "input_total": s.input_total,
            "cache_read": s.cache_read,
            "output": s.output,
            "tool_calls": s.tool_calls,
            "mcp_calls": s.mcp_calls,
            "active_seconds": round(active[name], 1),
        }
    slot_outputs = sorted(s.output for s in per_slot.values())
    median_out = (slot_outputs[len(slot_outputs) // 2]
                  if slot_outputs else None)
    return {
        "by_type": rows,
        "by_model": cost_rows,
        "total_cost_usd": round(total_cost, 2),
        "unpriced_models": unpriced,
        "slots_traced": len(per_slot),
        "median_output_tokens_per_slot": median_out,
    }


# ------------------------------------------------------------------ report

def _table(headers: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return out


def render(j: dict, m: dict, b: dict, t: dict) -> str:
    L = ["# Factory telemetry",
         "",
         "Every number below is computed by `src/eval/telemetry.py` from "
         "the batch journals, the MCP log and the subagent transcripts. "
         "Counts are taken from the last journal line per slot, because "
         "the journal restates a slot's whole envelope on every event.",
         ""]

    L += ["## The funnel", ""]
    L += _table(
        ["batches", "slots", "candidates tried", "accepted", "failed",
         "FIX rounds", "candidate abandons"],
        [[j["batches"], j["slots"], j["candidates_tried"],
          j["final_status"].get("ACCEPTED", 0),
          j["final_status"].get("FAILED", 0),
          j["dispositions"].get("FIX", 0),
          j["dispositions"].get("ABANDON", 0)]])
    L += ["", "Adjudication rounds per slot (judge decisions until the "
          "slot closed): "
          + ", ".join(f"{k}: {v}" for k, v in
                      j["adjudication_rounds"].items()), ""]

    L += ["## Per run", ""]
    L += _table(["run", "date", "model", "slots", "accepted", "FIX rounds",
                 "abandons"],
                [[p["batch"], p["date"], p.get("model") or "unrecorded",
                  p["slots"], p["accepted"], p["fix_rounds"], p["abandons"]]
                 for p in j["per_batch"]])

    L += ["", "## The critic's findings (terminal, deduplicated)", ""]
    L += _table(["severity", "count"],
                [[s, n] for s, n in sorted(
                    j["findings_by_severity"].items())])
    L += ["", "Rulings on HIGH and MID findings "
          "(LOW is recorded, never adjudicated):", ""]
    L += _table(["severity/ruling", "count"],
                [[k, v] for k, v in j["rulings"].items()])
    L += ["", "Defect classes (typed; the OTHER:* long tail is "
          f"{j['defect_classes'].get('OTHER:*', 0)} findings across "
          f"{j['other_label_count']} distinct labels):", ""]
    L += _table(["class", "count"],
                [[c, n] for c, n in j["defect_classes"].items()
                 if c != "OTHER:*"][:12])

    L += ["", "## What review changed", "",
          f"- Accepted slots with at least one UPHELD finding: "
          f"**{j['accepted_with_upheld_finding']}**",
          f"- Accepted slots that went through at least one FIX round: "
          f"**{j['accepted_with_fix_round']}**",
          "",
          "Each of these is a question that entered the bank in a "
          "different state than its drafter first submitted - a defect "
          "or weakness the split-authority review caught before it "
          "shipped.", ""]

    L += ["## The deterministic gates", ""]
    for name, p in m["prechecks"].items():
        L.append(f"- `{name}`: {p['calls']} executions, "
                 f"{p['with_failures']} reported at least one failure")
    L += ["",
          f"MCP activity: {m['calls']} calls over {m['active_days']} days "
          f"({m['first_day']} to {m['last_day']}), {m['errors']} errored.",
          ""]
    L += _table(["tool", "calls"],
                [[k, v] for k, v in m["by_tool"].items()])

    L += ["", "## Bank linkage", "",
          f"- Bank today: {b['bank']} questions; "
          f"{b['bank_from_batches']} from batch runs, "
          f"{b['bank_interactive']} authored interactively",
          f"- Trimmed 2026-08-03 to the v5 allocation: {b['trimmed']} "
          f"(all still archived, ids permanently taken)",
          f"- Total questions that passed the pipeline: "
          f"{b['pipeline_total']}", ""]

    L += ["## Authoring spend (Claude-side, from transcripts)", ""]
    if t.get("error"):
        L.append(f"Not available: {t['error']}")
    elif not t.get("by_type"):
        L.append("No factory-agent transcripts found.")
    else:
        rows = []
        for name, r in t["by_type"].items():
            rows.append([name, r["agents"], r["turns"],
                         _fmt_tokens(r["input_total"]),
                         _fmt_tokens(r["cache_read"]),
                         _fmt_tokens(r["output"]),
                         f"{r['tool_calls']} ({r['mcp_calls']} MCP)",
                         _fmt_seconds(r["active_seconds"])])
        L += _table(["agent", "spawned", "turns", "input (total)",
                     "of which cache", "output", "tools", "active"], rows)
        L += ["",
              f"Slots traceable to a question id: {t['slots_traced']}; "
              f"median output tokens per slot: "
              f"{t['median_output_tokens_per_slot']}.", ""]
        L += ["### Cost in dollars, by model", ""]
        rows = []
        for r in t["by_model"]:
            rows.append([r["model"],
                         _fmt_tokens(r["input_fresh"]),
                         _fmt_tokens(r["cache_read"]),
                         _fmt_tokens(r["cache_creation"]),
                         _fmt_tokens(r["output"]),
                         ("?" if r["cost_usd"] is None
                          else f"${r['cost_usd']:.2f}")])
        L += _table(["model", "fresh input", "cache read", "cache write",
                     "output", "cost"], rows)
        L += ["",
              f"**Total factory cost: ${t['total_cost_usd']:.2f}** "
              "(drafter + critic + judge subagents plus their orchestrator "
              "sessions, priced at 2026-08-06 API rates; cache reads at "
              "0.1x input, cache writes at the 1.25x 5-minute rate - with "
              "the 1-hour cache TTL writes bill 2x, which would raise the "
              "write component by 60%).", ""]
        if t.get("unpriced_models"):
            L.append("Unpriced models (tokens counted, no rate known): "
                     + ", ".join(t["unpriced_models"]))
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-transcripts", action="store_true",
                    help="skip the 300MB transcript scan")
    args = ap.parse_args()

    j = journal_stats()
    m = mcp_stats()
    b = bank_stats()
    t = {} if args.skip_transcripts else transcript_stats()

    OUT_JSON.write_text(json.dumps(
        {"journals": j, "mcp": m, "bank": b, "transcripts": t}, indent=2),
        encoding="utf-8")
    OUT_MD.write_text(render(j, m, b, t), encoding="utf-8")
    print(f"wrote {OUT_MD}")
    print(f"wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
