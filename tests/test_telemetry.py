"""Telemetry's journal merge: counts must survive both restatement styles.

Not every journal kept the slot envelope cumulative - some restate the whole
decision history on every event (batchI, sometimes paraphrasing older
rationales as it goes), others reset `judge_decisions` and `findings` to the
current round (the Sonnet probe). The merge in `merged_slots` has to recover
the full history from a resetting journal without double-counting a
cumulative one. These tests pin both invariants with one fixture journal per
style, plus the finding case where the same finding appears unruled first and
ruled later and must count once, ruling kept.
"""

import json

from src.eval.telemetry import journal_files, journal_stats, merged_slots


def _write_journal(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n",
                    encoding="utf-8")


def _slot(qid, status, cand, decisions, findings=()):
    return {"kind": "slot", "question_id": qid, "status": status,
            "candidate_index": cand, "judge_decisions": decisions,
            "findings": list(findings)}


def _dec(round_, disp, targets=(), rationale="why"):
    return {"round": round_, "disposition": disp, "targets": list(targets),
            "rationale": rationale}


HEADER = {"kind": "batch", "date": "2026-08-07", "model": "opus"}


def test_cumulative_journal_merges_to_last_line(tmp_path):
    # Each line's decision list extends the previous one; the last line is
    # already the complete history and must come through exactly - the
    # invariant that protects batchI from double counting.
    fix1 = _dec(1, "FIX", ["MISSED-GOLD"], "candidate 0 misses the gold")
    ab1 = _dec(2, "ABANDON", [], "candidate 0 cannot recover")
    fix2 = _dec(1, "FIX", ["AMBIGUOUS-READING"], "candidate 1 is ambiguous")
    acc = _dec(2, "ACCEPT", [], "candidate 1 fixed")
    journal = tmp_path / "draft-batch-journal-2026-08-07.jsonl"
    _write_journal(journal, [
        HEADER,
        _slot("hyb-01", "FIXING", 0, [fix1]),
        _slot("hyb-01", "REVIEWING", 0, [fix1]),
        _slot("hyb-01", "DRAFTING", 1, [fix1, ab1]),
        _slot("hyb-01", "FIXING", 1, [fix1, ab1, fix2]),
        _slot("hyb-01", "ACCEPTED", 1, [fix1, ab1, fix2, acc]),
    ])
    _, slots = merged_slots(journal)
    m = slots["hyb-01"]
    assert m["judge_decisions"] == [fix1, ab1, fix2, acc]
    assert m["status"] == "ACCEPTED"
    assert m["candidate_index"] == 1


def test_cumulative_journal_with_paraphrased_restatement(tmp_path):
    # batchI's actual failure shape: later restatements paraphrase earlier
    # rationales and reorder targets. The positional extension rule must
    # replace the merged list, not append the paraphrase as a new decision.
    fix_v1 = _dec(1, "FIX", ["MISSED-GOLD", "AMBIGUOUS-READING"],
                  "This cell is supposed to measure a structured filter")
    fix_v2 = _dec(1, "FIX", ["AMBIGUOUS-READING", "MISSED-GOLD"],
                  "Candidate 0, round 1. Superseded - see history.")
    acc = _dec(2, "ACCEPT", [], "fixed")
    journal = tmp_path / "draft-batch-journal-2026-08-07.jsonl"
    _write_journal(journal, [
        HEADER,
        _slot("hyb-02", "FIXING", 0, [fix_v1]),
        _slot("hyb-02", "ACCEPTED", 0, [fix_v2, acc]),
    ])
    _, slots = merged_slots(journal)
    assert slots["hyb-02"]["judge_decisions"] == [fix_v2, acc]


def test_resetting_journal_recovers_all_decisions(tmp_path):
    # One decision per line, restated verbatim across the FIXING/REVIEWING
    # states, then replaced by the next round's decision: the merge must
    # recover both rounds - a FIX then ACCEPT slot counts exactly 1 FIX.
    fix = _dec(1, "FIX", ["AMBIGUOUS-READING"], "close the second reading")
    acc = _dec(2, "ACCEPT", [], "the leak does not change the cell")
    journal = tmp_path / "draft-batch-journal-2026-08-07.jsonl"
    _write_journal(journal, [
        HEADER,
        _slot("sql-01", "JUDGING", 0, []),
        _slot("sql-01", "FIXING", 0, [fix]),
        _slot("sql-01", "REVIEWING", 0, [fix]),
        _slot("sql-01", "ACCEPTED", 0, [acc]),
    ])
    _, slots = merged_slots(journal)
    m = slots["sql-01"]
    assert m["judge_decisions"] == [fix, acc]
    dispositions = [d["disposition"] for d in m["judge_decisions"]]
    assert dispositions.count("FIX") == 1


def test_finding_unruled_then_ruled_counts_once_with_ruling(tmp_path):
    unruled = {"round": 1, "class": "AMBIGUOUS-READING", "severity": "MID",
               "claim": "two defensible readings", "ruling": None}
    ruled = {"round": 1, "class": "AMBIGUOUS-READING", "severity": "MID",
             "claim": "two defensible readings", "ruling": "UPHELD"}
    journal = tmp_path / "draft-batch-journal-2026-08-07.jsonl"
    _write_journal(journal, [
        HEADER,
        _slot("vec-01", "JUDGING", 0, [], [unruled]),
        _slot("vec-01", "ACCEPTED", 0, [_dec(1, "ACCEPT")], [ruled]),
    ])
    _, slots = merged_slots(journal)
    assert slots["vec-01"]["findings"] == [ruled]


def test_journal_stats_over_mixed_tree(tmp_path):
    # One cumulative and one resetting journal in subdirectories, plus an
    # archived journal that must be skipped. journal_stats must count the
    # resetting slot's FIX round and not double the cumulative slot's.
    fix1 = _dec(1, "FIX", ["MISSED-GOLD"], "first fix")
    acc1 = _dec(2, "ACCEPT", [], "done")
    _write_journal(tmp_path / "batchX" / "draft-batch-journal-1.jsonl", [
        HEADER,
        _slot("hyb-01", "FIXING", 0, [fix1],
              [{"round": 1, "class": "MISSED-GOLD", "severity": "HIGH",
                "claim": "gold not in survivors", "ruling": "UPHELD"}]),
        _slot("hyb-01", "ACCEPTED", 0, [fix1, acc1]),
    ])
    _write_journal(tmp_path / "probe" / "draft-batch-journal-2.jsonl", [
        HEADER,
        _slot("sql-s01", "FIXING", 0, [_dec(1, "FIX", [], "reset style")]),
        _slot("sql-s01", "ACCEPTED", 0, [_dec(2, "ACCEPT", [], "ok")]),
    ])
    _write_journal(tmp_path / "archive" / "draft-batch-journal-0.jsonl", [
        HEADER,
        _slot("sql-99", "ACCEPTED", 0, [_dec(1, "ACCEPT")]),
    ])

    labels = [label for label, _ in journal_files(tmp_path)]
    assert labels == ["batchX", "probe"]

    s = journal_stats(tmp_path)
    assert s["slots"] == 2
    assert s["dispositions"] == {"FIX": 2, "ACCEPT": 2}
    assert s["adjudication_rounds"] == {2: 2}
    assert s["findings_by_severity"] == {"HIGH": 1}
    assert s["rulings"] == {"HIGH/UPHELD": 1}
    assert s["final_status"] == {"ACCEPTED": 2}
    assert s["candidates_tried"] == 2
