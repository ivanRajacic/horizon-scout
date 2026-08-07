"""Promote approved /question-orchestrator drafts into the live bank.

/question-orchestrator stages accepted drafts as eval/drafts/draft-bank-<date>.jsonl plus
a review report carrying a `Draft-bank-file:` header and one machine-parsable
decision line per staged question (`Decision: [x] APPROVE  [ ] REJECT`). This
module parses the ticked boxes, validates existing-bank-plus-approved as a
whole BEFORE touching the bank, and only then appends. Deterministic - no LLM
anywhere; the human review happens in the report, this just executes it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.config import ROOT
from src.eval.bank import BankValidationError, load_bank


class PromoteError(Exception):
    """Refusal with every problem listed; the bank is untouched."""


DRAFT_FILE_RE = re.compile(r"^Draft-bank-file:\s*(?P<path>.+?)\s*$")
HEADING_ID_RE = re.compile(r"^##\s+((?:sql|vec|hyb|adv)-\d+)\b")
# A letter infix (`sql-s15`) marks a scratch id - a draft outside the bank's
# id space, like the Sonnet probe's. HEADING_ID_RE deliberately does not
# match it: not matching is the guard that keeps scratch drafts out of the
# bank. This second pattern only exists so the refusal can NAME the reason
# instead of misreporting a well-formed report as malformed.
SCRATCH_HEADING_RE = re.compile(r"^##\s+((?:sql|vec|hyb|adv)-[a-z]\d+)\b")
DECISION_RE = re.compile(
    r"^Decision:\s*\[(?P<approve>[ xX])\]\s*APPROVE\s+"
    r"\[(?P<reject>[ xX])\]\s*REJECT\s*$")


@dataclass
class PromoteResult:
    draft_file: Path
    promoted: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)


def _resolve_draft_file(raw: str, report_path: Path) -> Path:
    p = Path(raw)
    candidates = [p] if p.is_absolute() else [ROOT / p, report_path.parent / p.name]
    for c in candidates:
        if c.is_file():
            return c
    raise PromoteError(f"draft bank file not found: {raw!r} "
                       f"(tried {', '.join(str(c) for c in candidates)})")


def _parse_report(report_path: Path) -> tuple[Path, dict[str, bool]]:
    """Return (draft_file, {question_id: approved}). Every violation listed."""
    errors: list[str] = []
    draft_file_raw: str | None = None
    decisions: dict[str, bool] = {}
    current_id: str | None = None
    current_scratch: str | None = None

    for lineno, line in enumerate(
            report_path.read_text(encoding="utf-8").splitlines(), 1):
        m = DRAFT_FILE_RE.match(line)
        if m:
            if draft_file_raw is not None:
                errors.append(f"line {lineno}: second Draft-bank-file header")
            draft_file_raw = m.group("path")
            continue
        m = HEADING_ID_RE.match(line)
        if m:
            current_id = m.group(1)
            current_scratch = None
            continue
        m = SCRATCH_HEADING_RE.match(line)
        if m:
            current_id = None
            current_scratch = m.group(1)
            continue
        m = DECISION_RE.match(line)
        if not m:
            if line.strip().startswith("Decision:"):
                errors.append(f"line {lineno}: malformed decision line "
                              f"(expected 'Decision: [ ] APPROVE  [ ] REJECT' "
                              f"with exactly one box ticked)")
            continue
        approve = m.group("approve") != " "
        reject = m.group("reject") != " "
        if current_scratch is not None:
            errors.append(f"line {lineno}: scratch id {current_scratch} "
                          f"cannot be promoted (letter infix marks a "
                          f"non-bank draft)")
            continue
        if current_id is None:
            errors.append(f"line {lineno}: decision line outside any "
                          f"'## <question_id>' section")
            continue
        if current_id in decisions:
            errors.append(f"line {lineno}: second decision for {current_id}")
            continue
        if approve == reject:
            which = "both boxes ticked" if approve else "no box ticked"
            errors.append(f"line {lineno}: {current_id}: {which} - tick "
                          f"exactly one of APPROVE / REJECT")
            continue
        decisions[current_id] = approve

    if draft_file_raw is None:
        errors.append("report has no 'Draft-bank-file:' header line")
    if errors:
        raise PromoteError("\n".join(errors))
    return _resolve_draft_file(draft_file_raw, report_path), decisions


def _load_draft_lines(draft_file: Path) -> dict[str, str]:
    """question_id -> raw JSONL line, refusing duplicates and junk."""
    errors: list[str] = []
    lines: dict[str, str] = {}
    for lineno, line in enumerate(
            draft_file.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"{draft_file.name} line {lineno}: invalid JSON ({e})")
            continue
        qid = obj.get("question_id") if isinstance(obj, dict) else None
        if not isinstance(qid, str) or not qid:
            errors.append(f"{draft_file.name} line {lineno}: no question_id")
            continue
        if qid in lines:
            errors.append(f"{draft_file.name} line {lineno}: duplicate "
                          f"question_id {qid!r}")
            continue
        lines[qid] = line
    if errors:
        raise PromoteError("\n".join(errors))
    return lines


def _existing_bank_ids(bank_path: Path) -> set[str]:
    ids: set[str] = set()
    if not bank_path.is_file():
        return ids
    for line in bank_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue  # load_bank will report this loudly during validation
        if isinstance(obj, dict) and isinstance(obj.get("question_id"), str):
            ids.add(obj["question_id"])
    return ids


def promote(report_path: Path, bank_path: Path) -> PromoteResult:
    """Append every APPROVE-ticked draft to the bank, or refuse loudly.

    Validation-first: the combined bank (existing lines + approved lines) is
    run through the full loud validator on a temp file; the real bank is only
    written after it passes, so a refused promote never leaves a broken bank.
    """
    report_path, bank_path = Path(report_path), Path(bank_path)
    draft_file, decisions = _parse_report(report_path)
    draft_lines = _load_draft_lines(draft_file)

    errors: list[str] = []
    unknown = sorted(set(decisions) - set(draft_lines))
    for qid in unknown:
        errors.append(f"decision for {qid} but no such draft in "
                      f"{draft_file.name}")
    undecided = sorted(set(draft_lines) - set(decisions))
    for qid in undecided:
        errors.append(f"{qid} staged in {draft_file.name} but has no decision "
                      f"in the report - review is incomplete")
    approved = sorted(q for q, ok in decisions.items() if ok and q in draft_lines)
    rejected = sorted(q for q, ok in decisions.items() if not ok and q in draft_lines)
    already = sorted(set(approved) & _existing_bank_ids(bank_path))
    for qid in already:
        errors.append(f"{qid} is already in {bank_path.name} - refusing to "
                      f"promote it twice")
    if errors:
        raise PromoteError("\n".join(errors))

    result = PromoteResult(draft_file=draft_file, promoted=approved,
                           rejected=rejected)
    if not approved:
        return result

    bank_text = bank_path.read_text(encoding="utf-8") if bank_path.is_file() else ""
    if bank_text and not bank_text.endswith("\n"):
        bank_text += "\n"
    new_text = bank_text + "".join(draft_lines[q] + "\n" for q in approved)

    tmp = bank_path.with_name(bank_path.name + ".promote-tmp")
    try:
        tmp.write_text(new_text, encoding="utf-8")
        try:
            load_bank(tmp)
        except BankValidationError as e:
            raise PromoteError(
                "combined bank failed validation - nothing appended:\n"
                + "\n".join(e.errors)) from e
    finally:
        tmp.unlink(missing_ok=True)

    bank_path.write_text(new_text, encoding="utf-8")
    return result
