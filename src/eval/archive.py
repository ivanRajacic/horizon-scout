"""Move questions out of the live bank and into an archive file.

The bank is never hand-edited (see CLAUDE.md), which is why removing questions
is a command rather than a text edit. The one time a line was deleted by hand -
vec-28, on explicit instruction - is recorded as an exception precisely because
it was one.

Shape mirrors `promote.py`, the append-side sibling: parse, check everything
first, validate the resulting bank on a temp file, and only then write. A
refused archive leaves both files untouched.

Each archive line is an ENVELOPE, not a stamped record:

    {"archived_at": ..., "archived_reason": ..., "archived_from": ...,
     "record": {<the bank record, unchanged>}}

The record stays exactly what a drafter authored and a judge accepted, so it
still validates as a bank entry and can be restored without stripping anything.
Provenance lives outside it because `bank.py` rejects unknown fields - the same
envelope-with-opaque-payload convention the drafting and exploration journals
use.

Archived ids stay permanently taken: `batch.archived_ids` reads this file, and
both `next_ids` and `gap_report` count it, so an archived question's number is
never handed to a new one and its still-staged twin never reappears as pending
work. The vec-16 and vec-28 gaps work the same way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src.eval.bank import BankValidationError, load_bank


class ArchiveError(Exception):
    """Refusal with every problem listed; both files are untouched."""


@dataclass
class ArchiveResult:
    archive_file: Path
    archived: list[str] = field(default_factory=list)
    remaining: int = 0


def _read_bank_lines(bank_path: Path) -> tuple[list[str], dict[str, int]]:
    """Return (raw lines, {question_id: index}). Junk is reported, not skipped -
    counting against a half-parsed bank lies about what is being removed."""
    if not bank_path.is_file():
        raise ArchiveError(f"no bank at {bank_path}")
    lines = [ln for ln in bank_path.read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    errors: list[str] = []
    index: dict[str, int] = {}
    for i, line in enumerate(lines):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"{bank_path.name} line {i + 1}: invalid JSON ({e})")
            continue
        qid = obj.get("question_id") if isinstance(obj, dict) else None
        if not isinstance(qid, str) or not qid:
            errors.append(f"{bank_path.name} line {i + 1}: no question_id")
            continue
        if qid in index:
            errors.append(f"{bank_path.name} line {i + 1}: duplicate "
                          f"question_id {qid!r}")
            continue
        index[qid] = i
    if errors:
        raise ArchiveError("\n".join(errors))
    return lines, index


def _existing_archive_ids(archive_path: Path) -> set[str]:
    ids: set[str] = set()
    if not archive_path.is_file():
        return ids
    for line in archive_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        record = obj.get("record") if isinstance(obj, dict) else None
        if isinstance(record, dict) and isinstance(record.get("question_id"), str):
            ids.add(record["question_id"])
    return ids


def archive_questions(ids: list[str], reason: str, bank_path: Path,
                      archive_path: Path,
                      per_id_reasons: dict[str, str] | None = None,
                      now: datetime | None = None) -> ArchiveResult:
    """Move `ids` out of the bank into `archive_path`, or refuse loudly.

    `reason` is the run-level why; `per_id_reasons` optionally overrides it per
    question, so the cut is auditable id by id rather than asserted in bulk.
    """
    bank_path, archive_path = Path(bank_path), Path(archive_path)
    per_id_reasons = per_id_reasons or {}

    errors: list[str] = []
    if not ids:
        errors.append("no ids given - nothing to archive")
    if not reason or not reason.strip():
        errors.append("--reason is required: an archive with no recorded why "
                      "is indistinguishable from a mistake")
    seen: set[str] = set()
    for qid in ids:
        if qid in seen:
            errors.append(f"{qid} listed twice")
        seen.add(qid)
    if errors:
        raise ArchiveError("\n".join(errors))

    lines, index = _read_bank_lines(bank_path)

    for qid in ids:
        if qid not in index:
            errors.append(f"{qid} is not in {bank_path.name}")
    already = sorted(seen & _existing_archive_ids(archive_path))
    for qid in already:
        errors.append(f"{qid} is already in {archive_path.name} - refusing to "
                      f"archive it twice")
    unknown = sorted(set(per_id_reasons) - seen)
    for qid in unknown:
        errors.append(f"per-id reason for {qid}, which is not being archived")
    if len(ids) >= len(index):
        errors.append(f"that would empty the bank ({len(ids)} of "
                      f"{len(index)} questions)")
    if errors:
        raise ArchiveError("\n".join(errors))

    drop = {index[qid] for qid in ids}
    kept_text = "".join(ln + "\n" for i, ln in enumerate(lines) if i not in drop)

    tmp = bank_path.with_name(bank_path.name + ".archive-tmp")
    try:
        tmp.write_text(kept_text, encoding="utf-8")
        try:
            remaining = load_bank(tmp)
        except BankValidationError as e:
            raise ArchiveError(
                "the bank that would remain fails validation - nothing "
                "written:\n" + "\n".join(e.errors)) from e
    finally:
        tmp.unlink(missing_ok=True)

    stamp = (now or datetime.now(timezone.utc)).isoformat()
    envelopes = []
    for qid in ids:
        envelopes.append(json.dumps({
            "archived_at": stamp,
            "archived_reason": per_id_reasons.get(qid, reason),
            "archived_from": bank_path.name,
            "record": json.loads(lines[index[qid]]),
        }, ensure_ascii=False))

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    existing = (archive_path.read_text(encoding="utf-8")
                if archive_path.is_file() else "")
    if existing and not existing.endswith("\n"):
        existing += "\n"
    # Archive first: a crash between the two writes leaves a recoverable
    # duplicate rather than a question that exists nowhere.
    archive_path.write_text(existing + "".join(e + "\n" for e in envelopes),
                            encoding="utf-8")
    bank_path.write_text(kept_text, encoding="utf-8")

    return ArchiveResult(archive_file=archive_path, archived=list(ids),
                         remaining=len(remaining))
