"""Factory telemetry - what the question-authoring pipeline did, counted
from its own records.

The batch journals, the MCP log and the subagent transcripts already hold
every fact about how the bank was made: what was drafted, what the critic
found, what the judge upheld, what the deterministic gates killed, and what
it all cost. This module computes the totals. It is analysis, not pipeline:
nothing here is called by a run, and it writes only its own report.

Counting rule that matters: journals are append-only, but not every journal
kept the slot envelope cumulative - some restate the full decision and
finding history on every event, others reset `judge_decisions` and
`findings` to the current round. So counts come from a MERGED view of every
line per slot, deduplicated by content: reading only the last line
undercounts the resetting journals, and counting every line raw would
double the cumulative ones.

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

def journal_files(drafts_dir: Path = DRAFTS) -> list[tuple[str, Path]]:
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
    for j in sorted(drafts_dir.rglob("draft-batch-journal-*.jsonl")):
        rel = j.relative_to(drafts_dir)
        if "archive" in rel.parts:
            continue
        label = (str(rel.parent).replace("\\", "/") if rel.parent != Path(".")
                 else j.stem.replace("draft-batch-journal-", "batch-"))
        out.append((label, j))
    return out


def _decision_key(d) -> tuple:
    """Content identity of one judge decision, for cross-line dedup."""
    if not isinstance(d, dict):
        return ("raw", json.dumps(d, sort_keys=True, default=str))
    return (d.get("round"), d.get("disposition"), str(d.get("targets")),
            str(d.get("rationale") or "")[:200])


def _merge_decisions(merged: list, line: list) -> list:
    """One line's judge_decisions folded into the slot's merged list.

    Journals restate decisions in two styles. Cumulative journals re-emit
    the whole history on every event, sometimes paraphrasing earlier
    rationales and reordering targets as they go (batchI does both) - so
    when the line extends the merged sequence positionally, same
    (round, disposition) sequence as a prefix, the line REPLACES the
    merged list wholesale. Resetting journals emit only the current
    round's decision, so a line that does not extend the sequence is
    appended instead, deduplicated by content so a decision restated
    verbatim across lines counts once. Two keys that do NOT work:
    (candidate_index, round) collapses a cumulative history where two
    candidates share a round-1 FIX (hyb-12), and content alone counts
    every paraphrased restatement as a new decision (also hyb-12, which
    it would inflate from 5 decisions to 13)."""
    def seq(ds):
        return [(d.get("round"), d.get("disposition")) if isinstance(d, dict)
                else ("raw",) for d in ds]
    if seq(merged) == seq(line)[:len(merged)]:
        return list(line)
    have = {_decision_key(d) for d in merged}
    return merged + [d for d in line if _decision_key(d) not in have]


def _finding_key(f) -> tuple:
    """Content identity of one critic finding, for cross-line dedup.

    The ruling fields are excluded on purpose: a finding first appears
    unruled and is restated ruled - same finding. The merge keeps the LAST
    occurrence per key, so the ruled restatement wins."""
    if not isinstance(f, dict):
        return ("raw", json.dumps(f, sort_keys=True, default=str))
    return (f.get("class"), f.get("severity"), str(f.get("claim") or "")[:200])


def merged_slots(journal: Path) -> tuple[dict | None, dict[str, dict]]:
    """The batch header line and one MERGED slot view per question_id.

    Not every journal restated the whole envelope cumulatively - six of the
    nine journal sets reset `judge_decisions` (and findings) to the current
    round on each event. So the last line per slot is not enough, and raw
    counting of every line would double the journals that DID accumulate.
    The merge: `status` from the last line, `candidate_index` as the max,
    `judge_decisions` folded line by line via `_merge_decisions`, and
    `findings` as the union across lines deduplicated by `_finding_key`
    (last occurrence wins, so a finding restated with its ruling keeps
    the ruling)."""
    batch = None
    slots: dict[str, dict] = {}
    for e in _read_jsonl(journal):
        kind = e.get("kind")
        if kind == "batch" and batch is None:
            batch = e
        elif kind == "slot" and e.get("question_id"):
            m = slots.setdefault(e["question_id"], {
                "status": None, "candidate_index": 0,
                "judge_decisions": [], "_findings": {}})
            m["status"] = e.get("status")
            m["candidate_index"] = max(m["candidate_index"],
                                       int(e.get("candidate_index") or 0))
            line = e.get("judge_decisions") or []
            if line:
                m["judge_decisions"] = _merge_decisions(
                    m["judge_decisions"], line)
            for f in (e.get("findings") or []):
                m["_findings"][_finding_key(f)] = f
    for m in slots.values():
        m["findings"] = list(m.pop("_findings").values())
    return batch, slots


def _is_upheld(ruling) -> bool:
    return isinstance(ruling, str) and ruling.startswith("UPHELD")


def journal_stats(drafts_dir: Path = DRAFTS) -> dict:
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

    for label, journal in journal_files(drafts_dir):
        batch, slots = merged_slots(journal)
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
         "Counts merge every journal line per slot with content-level "
         "dedup of decisions and findings, because not every journal kept "
         "the envelope cumulative - the last line alone undercounts the "
         "runs that reset it per round.",
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
