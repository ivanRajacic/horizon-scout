"""Deterministic nodes of the /question-orchestrator pipeline.

Everything in the batch loop that has a right answer lives here rather than in
an Opus subagent: what the allocation table says, which ids are free, whether
accepted slots collide with each other or with the promoted bank, and how the
two canonical outputs are rendered from the working journal.

The journal is the typed state that flows between nodes: one append-only JSONL
file, one line per transition, latest line per `question_id` wins. Line 0 is a
batch header. The ENVELOPE is always valid; `record` is an opaque payload that
may be schema-invalid mid-run (schema validation happens once, at slot close,
via `python -m src.cli validate-record`). This module validates envelopes
loudly when it reads them and never touches `record` except to copy it.

`src/eval/promote.py` and the report contract it parses are untouched by this
module: the writer emits exactly the `Draft-bank-file:` header and
`Decision: [ ] APPROVE  [ ] REJECT` lines promote-drafts expects, so a written
report round-trips through promotion unchanged.
"""

from __future__ import annotations

import datetime
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from src.config import (BANK_BRIEF_PATH, BANK_BRIEF_VERSION, BANK_PATH,
                        CORPUS_PROFILE_PATH, CORPUS_PROFILE_VERSION, ROOT,
                        SCHEMA_DOCS_PATH, SCHEMA_DOCS_VERSION)
from src.eval.bank import LADDER, LEVELS
from src.eval.promote import DECISION_RE, HEADING_ID_RE
from src.llm import fingerprint

PLAN_DOC_PATH = ROOT / "horizon-scout.md"
DRAFTS_DIR = ROOT / "eval" / "drafts"
ARCHIVE_DIR = ROOT / "eval" / "archive"

# Routes /question-orchestrator can fill, and their id prefixes. The other allocation
# rows (ambiguous, adversarial, compositional) are interactive-only.
ROUTE_PREFIX = {"sql": "sql", "vector": "vec", "hybrid": "hyb"}
ID_RE = re.compile(r"^(sql|vec|hyb)-(\d+)$")

SLOT_STATUSES = ("DRAFTING", "REVIEWING", "JUDGING", "FIXING",
                 "ACCEPTED", "FAILED", "BLOCKED")


class BatchError(Exception):
    """Refusal with every problem listed; nothing is written."""


# --------------------------------------------------------------------------
# Allocation table (parsed LIVE from horizon-scout.md, never from memory)
# --------------------------------------------------------------------------

_ALLOCATION_HEADING = re.compile(r"^#{2,4}\s+Allocation\b")
_ROUTE_ALIASES = {"sql": "sql", "vector": "vector", "hybrid": "hybrid",
                  "ambiguous-route": "ambiguous", "ambiguous": "ambiguous",
                  "adversarial": "adversarial",
                  "compositional": "compositional"}


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _as_count(cell: str) -> int | None:
    value = cell.replace("*", "").replace("\\", "").strip()
    return int(value) if value.isdigit() else None


def parse_allocation(path: Path = PLAN_DOC_PATH) -> dict[str, dict[str, int]]:
    """Read the plan doc's route x level allocation table.

    Returns {route: {"L1": n, "L2": n, "L3": n, "total": n}} with only the
    keys the table actually states (the non-ladder rows carry `total` alone).
    Raises BatchError rather than guessing: a gap report against invented
    targets is worse than no gap report.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise BatchError(f"cannot read the allocation table: {e}") from e

    start = next((i for i, line in enumerate(lines)
                  if _ALLOCATION_HEADING.match(line)), None)
    if start is None:
        raise BatchError(f"no '### Allocation' heading in {path.name} - the "
                         "allocation table is the batch's target source")

    header: list[str] | None = None
    targets: dict[str, dict[str, int]] = {}
    for line in lines[start + 1:]:
        if not line.strip().startswith("|"):
            if header is not None:      # table ended
                break
            continue
        cells = _cells(line)
        if header is None:
            header = [c.strip().lower() for c in cells]
            continue
        if set("".join(cells)) <= set("-: "):
            continue                    # markdown separator row
        label = re.sub(r"\(.*?\)", "", cells[0]).replace("*", "").strip()
        route = _ROUTE_ALIASES.get(label.lower())
        if route is None:
            continue                    # the Total row, or anything new
        row: dict[str, int] = {}
        for column, cell in zip(header[1:], cells[1:]):
            count = _as_count(cell)
            if count is None:
                continue
            if column.upper() in LEVELS:
                row[column.upper()] = count
            elif "total" in column:
                row["total"] = count
        targets[route] = row
    if not targets:
        raise BatchError(f"the '### Allocation' table in {path.name} parsed "
                         "to zero routes - has its shape changed?")
    return targets


# --------------------------------------------------------------------------
# Bank / staged-draft inventory
# --------------------------------------------------------------------------

def read_records(path: Path) -> list[dict]:
    """Parse a bank or staged-draft JSONL into dicts. Junk lines are skipped
    loudly via BatchError - counting against a half-parsed file lies."""
    records, errors = [], []
    if not path.is_file():
        return records
    for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"{path.name} line {lineno}: invalid JSON ({e})")
            continue
        if not isinstance(obj, dict):
            errors.append(f"{path.name} line {lineno}: not a JSON object")
            continue
        records.append(obj)
    if errors:
        raise BatchError("\n".join(errors))
    return records


def staged_files(drafts_dir: Path = DRAFTS_DIR) -> list[Path]:
    return sorted(drafts_dir.glob("draft-bank-*.jsonl"))


def archived_ids(archive_dir: Path = ARCHIVE_DIR) -> set[str]:
    """Ids `archive-questions` moved out of the bank.

    An archived id is DECIDED and its number stays permanently taken. Without
    this, removing a question from the bank would hand its id straight back to
    the next drafter (two different vec-42s), and its still-on-disk staged twin
    would read as pending work forever - the same defect already fixed once for
    promoted records and once for rejected ones.

    Envelope shape is `archive.py`'s: provenance outside, the record inside.
    Tolerant like `rejected_ids` - an unreadable or foreign file in the archive
    dir means "nothing archived here", never a crash, so a gap report stays
    readable. `bank_pilot.jsonl` (the pre-skill smoke set, bare old-schema
    records with no envelope) is skipped by exactly that tolerance.
    """
    ids: set[str] = set()
    for path in sorted(archive_dir.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            record = obj.get("record") if isinstance(obj, dict) else None
            qid = record.get("question_id") if isinstance(record, dict) else None
            if isinstance(qid, str) and qid:
                ids.add(qid)
    return ids


def rejected_ids(drafts_dir: Path = DRAFTS_DIR) -> set[str]:
    """Ids a review report ticked REJECT, across every report in the dir.

    A rejected record stays in its draft file for the record, exactly as a
    promoted one does, so without this it would read as pending work forever
    and inflate a cell the batch is still supposed to fill. Deliberately
    tolerant where `promote.py` is strict: an unticked or malformed report
    means "no decision yet", never a crash, because a gap report must be
    readable mid-review.
    """
    ids: set[str] = set()
    for path in sorted(drafts_dir.glob("draft-report-*.md")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        current: str | None = None
        for line in lines:
            heading = HEADING_ID_RE.match(line)
            if heading:
                current = heading.group(1)
                continue
            decision = DECISION_RE.match(line)
            if decision and current is not None:
                approve = decision.group("approve") != " "
                reject = decision.group("reject") != " "
                if reject and not approve:
                    ids.add(current)
                current = None
    return ids


def next_ids(counts: dict[str, int], bank_path: Path = BANK_PATH,
             drafts_dir: Path = DRAFTS_DIR,
             archive_dir: Path = ARCHIVE_DIR) -> dict[str, list[str]]:
    """Assign the next free `sql-NN` / `vec-NN` / `hyb-NN` per route.

    Counts the bank, every staged draft file AND the archive: a staged id is
    taken even before promotion, and an archived id stays taken forever. Failed
    and archived slots leave id gaps, which is harmless - the counter never
    reuses a number.
    """
    highest: dict[str, int] = {p: 0 for p in ROUTE_PREFIX.values()}
    taken = archived_ids(archive_dir)
    for path in [bank_path, *staged_files(drafts_dir)]:
        for record in read_records(path):
            taken.add(str(record.get("question_id", "")))
    for qid in taken:
        match = ID_RE.match(qid)
        if match:
            prefix, number = match.group(1), int(match.group(2))
            highest[prefix] = max(highest[prefix], number)
    assigned: dict[str, list[str]] = {}
    for route, n in counts.items():
        prefix = ROUTE_PREFIX.get(route)
        if prefix is None:
            raise BatchError(f"route {route!r} has no id prefix; /question-orchestrator "
                             f"fills {', '.join(ROUTE_PREFIX)} only")
        start = highest[prefix]
        assigned[route] = [f"{prefix}-{start + i:02d}"
                           for i in range(1, max(0, n) + 1)]
        highest[prefix] = start + max(0, n)
    return assigned


def _tally(records: list[dict]) -> Counter:
    return Counter((r.get("expected_route"), r.get("level"))
                   for r in records)


def gap_report(bank_path: Path = BANK_PATH, drafts_dir: Path = DRAFTS_DIR,
               plan_path: Path = PLAN_DOC_PATH,
               archive_dir: Path = ARCHIVE_DIR) -> str:
    """filled / staged / target per cell, plus the coverage the batch's cell
    negotiation needs: subtypes, term_style balance, and the next free ids."""
    targets = parse_allocation(plan_path)
    bank = read_records(bank_path)
    staged_paths = staged_files(drafts_dir)
    # Staged means staged-but-UNDECIDED. A promoted, rejected OR archived
    # batch's draft file stays on disk for the record, and counting those
    # records again would show a filled cell as half pending (promoted), or a
    # cell the batch still has to fill as already covered (rejected), or an
    # archived question as work still on its way in (archived).
    decided_ids = ({r.get("question_id") for r in bank}
                   | rejected_ids(drafts_dir)
                   | archived_ids(archive_dir))
    staged, live_paths = [], []
    for path in staged_paths:
        undecided = [r for r in read_records(path)
                     if r.get("question_id") not in decided_ids]
        staged.extend(undecided)
        if undecided:
            live_paths.append(path)
    filled, pending = _tally(bank), _tally(staged)

    out = [f"Bank: {bank_path} ({len(bank)} question(s))",
           f"Staged (undecided): {len(staged)} record(s) across "
           f"{len(live_paths)} draft file(s)"
           + (" - " + ", ".join(p.name for p in live_paths)
              if live_paths else ""),
           f"Allocation: {plan_path.name} '### Allocation' (read live)",
           "",
           "route x level - filled + staged / target",
           "",
           "| route | L1 | L2 | L3 | route total |",
           "|---|---|---|---|---|"]
    for route in ROUTE_PREFIX:
        row = targets.get(route, {})
        cells = []
        for level in LADDER:
            f, s = filled[(route, level)], pending[(route, level)]
            cells.append(f"{f}+{s}/{row.get(level, '?')}")
        f = sum(filled[(route, lv)] for lv in LADDER)
        s = sum(pending[(route, lv)] for lv in LADDER)
        cells.append(f"{f}+{s}/{row.get('total', '?')}")
        out.append(f"| {route} | " + " | ".join(cells) + " |")

    out += ["", "interactive only - NOT draftable by /question-orchestrator:"]
    for route in ("ambiguous", "adversarial", "compositional"):
        row = targets.get(route, {})
        if route == "adversarial":
            f = sum(n for (_, level), n in filled.items() if level == "ADV")
            s = sum(n for (_, level), n in pending.items() if level == "ADV")
        else:
            f = sum(n for (r, _), n in filled.items() if r == route)
            s = sum(n for (r, _), n in pending.items() if r == route)
        out.append(f"  {route:14s} {f}+{s}/{row.get('total', '?')}")

    out.append("")
    for route in ROUTE_PREFIX:
        subs = Counter(r.get("subtype") for r in bank
                       if r.get("expected_route") == route)
        subs_staged = Counter(r.get("subtype") for r in staged
                              if r.get("expected_route") == route)
        both = sorted(set(subs) | set(subs_staged))
        detail = ", ".join(f"{s}={subs[s]}+{subs_staged[s]}" for s in both
                           if s is not None) or "none yet"
        out.append(f"  subtypes {route:7s} {detail}")

    out.append("")
    for route in ("vector", "hybrid"):
        styles = Counter(r.get("term_style") for r in bank
                         if r.get("expected_route") == route)
        styles_staged = Counter(r.get("term_style") for r in staged
                                if r.get("expected_route") == route)
        out.append(
            f"  term_style {route:7s} "
            f"exact-term={styles['exact-term']}+{styles_staged['exact-term']} "
            f"paraphrase={styles['paraphrase']}+{styles_staged['paraphrase']}"
            "  (aim ~50/50 within each route)")

    free = next_ids({r: 1 for r in ROUTE_PREFIX}, bank_path, drafts_dir,
                    archive_dir)
    out += ["", "next free id per route: "
            + ", ".join(f"{route}={ids[0]}" for route, ids in free.items())]
    return "\n".join(out)


# --------------------------------------------------------------------------
# batch-crosscheck: what no per-slot node can see
# --------------------------------------------------------------------------

# The critic's NEAR-DUPLICATE check reads get_bank_questions, which returns the
# PROMOTED bank - so two parallel slots can converge on the same question and
# nothing in the per-slot loop notices. This is the sweep that catches it.
# Deliberately embedder-free (token and character-trigram overlap only), so
# close-out never depends on a llama server being up.

TOKEN_JACCARD_FLAG = 0.50
TRIGRAM_JACCARD_FLAG = 0.45

_STOPWORDS = frozenset("""
a an the and or but of in on at to for from by with without within into over
under about across between during before after is are was were be been being
do does did doing have has had having how what which who whom whose when where
why that this these those it its as than then there their them they he she his
her you your we our us not no nor if so such can could may might must shall
should will would does project projects question answer eu european union
""".split())

# Acronym-shaped tokens that are corpus-wide furniture rather than distinctive
# entities; flagging them would drown the real collisions. (Programme codes
# used as FILTER values are caught by the axis check instead, where the
# collision actually means something.)
_GENERIC_ACRONYMS = frozenset({
    "ERC", "MSCA", "H2020", "FP7", "HORIZON", "SME", "SMES", "ICT", "CSA",
    "TRL", "III"})

_ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9]{2,}(?:-[A-Z0-9]+)*\b")
_LITERAL_RE = re.compile(r"'([^']{1,80})'")
_FILTER_COLUMN_RE = re.compile(
    r"(?:\b\w+\.)?(\w+)\s*(?:=|!=|<>|>=|<=|>|<|\bLIKE\b|\bIN\b|\bBETWEEN\b)",
    re.IGNORECASE)


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", (text or "").lower())
            if len(w) > 2 and w not in _STOPWORDS}


def _trigrams(text: str) -> set[str]:
    flat = " ".join((text or "").lower().split())
    return {flat[i:i + 3] for i in range(max(0, len(flat) - 2))}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def entities_in(blob: str) -> set[str]:
    """Acronym-shaped names, minus the corpus-wide furniture."""
    return {t for t in _ACRONYM_RE.findall(blob or "")
            if t not in _GENERIC_ACRONYMS}


def _entities(record: dict) -> set[str]:
    return entities_in(" ".join(str(record.get(k) or "")
                                for k in ("text", "reference_answer")))


# Public aliases. src/eval/explore.py reuses these for the explorer's width and
# near-duplicate checks, so "too similar" and "a named entity" mean the same
# thing at both ends of the pipeline instead of drifting apart in two copies.
words, trigrams, jaccard = _words, _trigrams, _jaccard


def _sql_of(record: dict) -> str:
    parts = [record.get("gold_sql") or ""]
    filter_evidence = record.get("filter_evidence")
    if isinstance(filter_evidence, dict):
        parts.append(filter_evidence.get("filter_sql") or "")
    return "\n".join(parts)


def _axis_signature(record: dict) -> frozenset[str]:
    sql = _sql_of(record)
    return frozenset(m.group(1).lower()
                     for m in _FILTER_COLUMN_RE.finditer(sql)
                     if m.group(1).lower() not in ("id", "projectid"))


def _literals(record: dict) -> set[str]:
    return {v for v in _LITERAL_RE.findall(_sql_of(record)) if v.strip()}


@dataclass
class Flag:
    kind: str
    level: str           # FLAG (adjudicate at promote time) | INFO
    detail: str


def crosscheck(accepted: list[dict], bank: list[dict]) -> list[Flag]:
    """Collision and spread flags across the batch's accepted slots.

    Output is FLAGS on the report, never a gate and never a redraft: the user
    adjudicates at promote time. INFO lines carry the spread picture that
    makes a flag judgeable.
    """
    flags: list[Flag] = []
    labelled = ([("batch", r) for r in accepted]
                + [("bank", r) for r in bank])
    qid = [str(r.get("question_id", "?")) for _, r in labelled]
    words = [_words(r.get("text", "")) for _, r in labelled]
    trigrams = [_trigrams(r.get("text", "")) for _, r in labelled]

    def pairs():
        """Every (batch record, other record) pair exactly once."""
        for i, (side_a, _) in enumerate(labelled):
            if side_a != "batch":
                continue
            for j, (side_b, _) in enumerate(labelled):
                if i == j or (side_b == "batch" and j <= i):
                    continue
                yield i, j, side_b

    for i, j, side in pairs():
        tok = _jaccard(words[i], words[j])
        tri = _jaccard(trigrams[i], trigrams[j])
        if tok >= TOKEN_JACCARD_FLAG or tri >= TRIGRAM_JACCARD_FLAG:
            flags.append(Flag(
                "NEAR-DUPLICATE", "FLAG",
                f"{qid[i]} vs {qid[j]} ({side}): token overlap {tok:.2f}, "
                f"trigram overlap {tri:.2f}"))

    # Gold-set overlap: two questions whose gold projects intersect are
    # measuring partly the same evidence.
    golds = [set(r.get("gold_project_ids") or []) for _, r in labelled]
    for i, j, side in pairs():
        shared = sorted(golds[i] & golds[j])
        if shared:
            flags.append(Flag(
                "GOLD-OVERLAP", "FLAG",
                f"{qid[i]} and {qid[j]} ({side}) share gold project(s) "
                f"{shared}"))

    seen_entities: dict[str, list[str]] = {}
    for i, (side, record) in enumerate(labelled):
        for entity in _entities(record):
            seen_entities.setdefault(entity, []).append(f"{qid[i]}({side})")
    for entity, users in sorted(seen_entities.items()):
        batch_users = [u for u in users if u.endswith("(batch)")]
        if batch_users and len(users) > 1:
            flags.append(Flag("ENTITY-COLLISION", "FLAG",
                              f"{entity} appears in {', '.join(users)}"))

    axes = Counter()
    for side, record in labelled:
        signature = _axis_signature(record)
        if signature:
            axes[(signature, side)] += 1
    # Sorted so the report is byte-stable across runs (set iteration order
    # over frozensets is hash-seed dependent).
    for signature in sorted({sig for sig, _ in axes}, key=sorted):
        in_batch, in_bank = axes[(signature, "batch")], axes[(signature, "bank")]
        columns = "+".join(sorted(signature))
        if in_batch >= 2:
            flags.append(Flag(
                "AXIS-COLLISION", "FLAG",
                f"{in_batch} accepted slots filter on the same axis "
                f"[{columns}] ({in_bank} already in the bank)"))
        elif in_batch:
            flags.append(Flag("AXIS", "INFO",
                              f"[{columns}] - 1 this batch, {in_bank} in bank"))

    literals = Counter(v for r in accepted for v in _literals(r))
    if literals:
        flags.append(Flag(
            "SPREAD", "INFO",
            "filter literals used by this batch: "
            + ", ".join(f"{v}x{n}" if n > 1 else v
                        for v, n in sorted(literals.items()))))
    cells = Counter(f"{r.get('expected_route')}/{r.get('level')}"
                    for r in accepted)
    flags.append(Flag("SPREAD", "INFO", "cells filled: " + ", ".join(
        f"{c}x{n}" if n > 1 else c for c, n in sorted(cells.items()))))
    styles = Counter(r.get("term_style") for r in accepted
                     if r.get("term_style"))
    if styles:
        flags.append(Flag("SPREAD", "INFO", "term_style: " + ", ".join(
            f"{s}={n}" for s, n in sorted(styles.items()))))
    return flags


def render_flags(flags: list[Flag]) -> str:
    hard = [f for f in flags if f.level == "FLAG"]
    info = [f for f in flags if f.level == "INFO"]
    lines = []
    if hard:
        lines += [f"- **{f.kind}** - {f.detail}" for f in hard]
    else:
        lines.append("- no collisions found (near-duplicate, gold overlap, "
                     "entity, axis)")
    lines += [f"- _{f.kind}_ - {f.detail}" for f in info]
    lines.append("- Lexical checks only (token and character-trigram overlap, "
                 "no embedder). Flags are for your promote-time judgement; "
                 "nothing here gates or redrafts.")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# The journal: typed slot state
# --------------------------------------------------------------------------

@dataclass
class Journal:
    header: dict = field(default_factory=dict)
    slots: dict[str, dict] = field(default_factory=dict)   # latest line wins
    order: list[str] = field(default_factory=list)


def _require(where: str, obj: dict, key: str, kind: type, errors: list[str]):
    value = obj.get(key)
    if not isinstance(value, kind) or (kind is str and not value.strip()):
        errors.append(f"{where}: {key} must be a non-empty {kind.__name__}")
        return None
    return value


def load_journal(path: Path) -> Journal:
    """Read the append-only journal; latest line per question_id wins.

    Validates the ENVELOPE only. `record` stays opaque - mid-run it may hold a
    half-finished or schema-invalid draft by design, and schema validation is
    a separate node.
    """
    journal, errors = Journal(), []
    for n, line in enumerate(read_journal_lines(path), 1):
        where = f"{path.name} entry {n}"   # blank lines already dropped
        kind = line.get("kind")
        if kind == "batch":
            journal.header = line
            continue
        if kind != "slot":
            errors.append(f"{where}: kind must be 'slot' or 'batch', "
                          f"got {kind!r}")
            continue
        qid = _require(where, line, "question_id", str, errors)
        status = line.get("status")
        if status not in SLOT_STATUSES:
            errors.append(f"{where}: status must be one of "
                          f"{SLOT_STATUSES}, got {status!r}")
        if not isinstance(line.get("cell"), dict):
            errors.append(f"{where}: cell must be an object "
                          "(route/level/subtype)")
        if qid is None:
            continue
        if qid not in journal.slots:
            journal.order.append(qid)
        journal.slots[qid] = line
    if not journal.header:
        errors.append(f"{path.name}: no line 0 batch header "
                      '(kind: "batch") - the report needs the order and the '
                      "asset versions")
    if errors:
        raise BatchError("\n".join(errors))
    return journal


def journal_append(path: Path, question_id: str, status: str,
                   payload: dict | None = None) -> dict:
    """Append one slot transition; the envelope bookkeeping in code.

    Latest-line-wins means every journal line must be COMPLETE (a new line
    REPLACES the slot's state, it does not patch it) - which is why the
    2026-07-25 run needed eighteen hand-written scripts, each re-marshalling
    `record`, `evidence`, `findings` and `history` verbatim. This node does
    the merge instead: `payload` holds only what changed, it is merged over
    the slot's latest line, and the complete merged line is appended. The
    envelope (kind, question_id, status, cell) is enforced here so a bad
    line is refused at append time, not discovered by write-batch at
    close-out. `record` stays opaque and may be schema-invalid mid-run -
    that distinction is deliberate and preserved.

    This is marshalling only: nothing here computes, compares or judges.
    """
    payload = payload if payload is not None else {}
    if not isinstance(payload, dict):
        raise BatchError("payload must be a JSON object (the fields to set)")
    if status not in SLOT_STATUSES:
        raise BatchError(f"status must be one of {SLOT_STATUSES}, "
                         f"got {status!r}")
    if not isinstance(question_id, str) or not question_id.strip():
        raise BatchError("question_id must be a non-empty string")
    fixed = {"kind": "slot", "question_id": question_id, "status": status}
    for key, want in fixed.items():
        if key in payload and payload[key] != want:
            raise BatchError(
                f"payload carries {key}={payload[key]!r} but the command "
                f"says {want!r} - one of the two is wrong; drop the payload "
                "field or fix the flag")
    # load_journal validates every existing envelope loudly, so a corrupt
    # journal is caught before anything is appended to it.
    journal = load_journal(Path(path))
    base = journal.slots.get(question_id, {})
    line = {**base, **payload, **fixed}
    if not isinstance(line.get("cell"), dict):
        raise BatchError(
            f"{question_id}: cell must be an object (route/level/subtype) - "
            "the slot's first transition must carry it")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return line


def read_journal_lines(path: Path) -> list[dict]:
    lines, errors = [], []
    if not path.is_file():
        raise BatchError(f"journal not found: {path}")
    for lineno, raw in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            errors.append(f"{path.name} line {lineno}: invalid JSON ({e})")
            continue
        if not isinstance(obj, dict):
            errors.append(f"{path.name} line {lineno}: not a JSON object")
            continue
        lines.append(obj)
    if errors:
        raise BatchError("\n".join(errors))
    return lines


# --------------------------------------------------------------------------
# The writer: journal -> staged jsonl + review report
# --------------------------------------------------------------------------

def _asset_versions(header: dict) -> str:
    """The provenance line. Prefer what the batch recorded at start; fall back
    to the live assets so a header written by hand is still usable."""
    versions = header.get("versions") or {}

    def part(key: str, label: str, path: Path, version: str) -> str:
        block = versions.get(key) or {}
        v = block.get("version") or version
        h = block.get("content_hash")
        if h is None:
            try:
                h = fingerprint(path.read_text(encoding="utf-8"))
            except OSError:
                h = "unreadable"
        return f"{label}: {v} {h}"

    index = (versions.get("index") or {}).get("fingerprint", "n/a")
    return " | ".join([
        part("corpus_profile", "Corpus profile", CORPUS_PROFILE_PATH,
             CORPUS_PROFILE_VERSION),
        part("schema_docs", "schema_docs", SCHEMA_DOCS_PATH,
             SCHEMA_DOCS_VERSION),
        part("bank_brief", "bank_brief", BANK_BRIEF_PATH, BANK_BRIEF_VERSION),
        f"index: {index}"])


def _cell_label(slot: dict) -> str:
    cell = slot.get("cell") or {}
    label = "/".join(str(cell.get(k)) for k in ("route", "level", "subtype")
                     if cell.get(k))
    if cell.get("term_style"):
        label += f" ({cell['term_style']})"
    return label or "?"


def _table_cell(text: str) -> str:
    """One markdown table cell: no pipes, no newlines, bounded length."""
    flat = " ".join(str(text).replace("|", "/").split())
    return (flat[:97] + "...") if len(flat) > 100 else (flat or "-")


def _candidate_label(slot: dict) -> str:
    candidates = slot.get("candidates") or []
    index = slot.get("candidate_index", 0)
    if not isinstance(index, int) or isinstance(index, bool):
        index = 0
    if isinstance(candidates, list) and 0 <= index < len(candidates):
        candidate = candidates[index]
        if isinstance(candidate, dict):
            return _table_cell(candidate.get("topic")
                               or candidate.get("id") or "-")
        lines = str(candidate).strip().splitlines()
        return _table_cell(lines[0]) if lines else "-"
    return "-"


def _findings_summary(slot: dict) -> str:
    findings = [f for f in (slot.get("findings") or [])
                if isinstance(f, dict)]
    if not findings:
        return "none"
    parts = []
    for severity in ("HIGH", "MID", "LOW"):
        same = [f for f in findings if f.get("severity") == severity]
        if not same:
            continue
        upheld = sum(f.get("ruling") == "UPHELD" for f in same)
        dismissed = sum(f.get("ruling") == "DISMISSED" for f in same)
        detail = f"{len(same)} {severity}"
        if upheld or dismissed:
            detail += f" ({upheld} upheld, {dismissed} dismissed)"
        parts.append(detail)
    return ", ".join(parts)


def _render_findings(slot: dict) -> list[str]:
    lines = []
    for finding in slot.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        head = (f"- **{finding.get('class', 'OTHER')}** "
                f"({finding.get('severity', '?')}, round "
                f"{finding.get('round', '?')})")
        ruling = finding.get("ruling")
        if ruling:
            head += f" - judge: **{ruling}**"
        lines.append(head)
        for label, key in (("Claim", "claim"), ("Evidence", "evidence"),
                           ("Fix direction", "fix_direction"),
                           ("Ruling", "ruling_why")):
            value = finding.get(key)
            if value:
                lines.append(f"  - {label}: {value}")
    for decision in slot.get("judge_decisions") or []:
        if not isinstance(decision, dict):
            continue
        targets = decision.get("targets") or []
        lines.append(
            f"- **judge round {decision.get('round', '?')}**: "
            f"{decision.get('disposition', '?')}"
            + (f" {list(targets)}" if targets else "")
            + (f" - {decision['rationale']}" if decision.get("rationale")
               else ""))
    return lines or ["- none"]


def _render_history(slot: dict) -> list[str]:
    history = slot.get("history") or []
    if isinstance(history, str):
        history = [history]
    lines = [f"- {h}" for h in history if str(h).strip()]
    return lines or ["- (none recorded)"]


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def _accepted_slots(journal: Journal) -> list[dict]:
    return [journal.slots[qid] for qid in journal.order
            if journal.slots[qid].get("status") == "ACCEPTED"]


def render_report(journal: Journal, draft_file: Path, date: str,
                  flags: list[Flag]) -> str:
    accepted = _accepted_slots(journal)
    failed = [journal.slots[q] for q in journal.order
              if journal.slots[q].get("status") == "FAILED"]
    blocked = [journal.slots[q] for q in journal.order
               if journal.slots[q].get("status") == "BLOCKED"]
    unresolved = [journal.slots[q] for q in journal.order
                  if journal.slots[q].get("status")
                  not in ("ACCEPTED", "FAILED", "BLOCKED")]

    reasons = Counter(s.get("terminal_reason") or "unspecified"
                      for s in failed)
    tally = (f"{len(accepted)} accepted / {len(failed)} failed"
             + (" (" + ", ".join(f"{r} x{n}" for r, n in sorted(
                 reasons.items())) + ")" if failed else "")
             + f" / {len(blocked)} blocked")
    if unresolved:
        tally += (f" / {len(unresolved)} UNRESOLVED "
                  "(journal never reached a terminal state)")

    out = [f"# Draft batch - {date}", "",
           f"Draft-bank-file: {_relative(draft_file)}",
           f"Order: {journal.header.get('order', '(not recorded)')}",
           _asset_versions(journal.header),
           f"Tally: {tally}",
           "",
           "Generated by `python -m src.cli write-batch` from the working "
           "journal - the accepted records below are byte-identical to what "
           "the drafters returned.",
           "", "## Summary", "",
           "| id | route/level/subtype | candidate topic | findings | "
           "decision |",
           "|----|---------------------|-----------------|----------|"
           "----------|"]
    for qid in journal.order:
        slot = journal.slots[qid]
        status = slot.get("status")
        decision = ("below" if status == "ACCEPTED"
                    else f"- ({status.lower()})")
        out.append(f"| {qid} | {_table_cell(_cell_label(slot))} | "
                   f"{_candidate_label(slot)} | "
                   f"{_table_cell(_findings_summary(slot))} | {decision} |")

    out += ["", "## Cross-check", "", render_flags(flags)]

    for slot in accepted:
        record = slot.get("record") or {}
        style = record.get("term_style")
        out += ["", "---", "",
                f"## {slot['question_id']} - ACCEPTED", "",
                f"**Question:** \"{record.get('text', '(missing)')}\"  "
                f"({_cell_label(slot)}"
                + (f", term_style {style}" if style else "")
                + f", {record.get('specification', 'well-specified')})",
                "",
                "**Gold + evidence:**", "",
                str(slot.get("evidence") or "(no evidence recorded)"),
                "",
                f"**Reference answer:** \"{record.get('reference_answer') or ''}\"",
                "",
                "**Why this is a good question:** "
                + str(slot.get("why_good") or "(not recorded)"),
                "",
                "**Findings and rulings:**", ""]
        out += _render_findings(slot)
        out += ["", "**Drafting history:**", ""]
        out += _render_history(slot)
        out += ["", "Decision: [ ] APPROVE  [ ] REJECT"]

    for slot in failed + blocked + unresolved:
        status = slot.get("status", "UNRESOLVED")
        record = slot.get("record") or {}
        out += ["", "---", "",
                f"## {slot['question_id']} - {status}", "",
                f"**Cell:** {_cell_label(slot)} | "
                f"**candidate:** {_candidate_label(slot)}",
                "",
                f"**Question (last draft):** \"{record.get('text', '-')}\"",
                "",
                "**Reason:** "
                + str(slot.get("terminal_reason") or "(not recorded)"),
                "",
                "**Findings and rulings:**", ""]
        out += _render_findings(slot)
        out += ["", "**Drafting history:**", ""]
        out += _render_history(slot)
        out += ["", "No decision line: this slot staged no record."]

    out.append("")
    return "\n".join(out)


@dataclass
class WriteResult:
    draft_file: Path
    report_file: Path
    accepted: list[str]
    failed: list[str]
    blocked: list[str]
    flags: list[Flag]


def write_batch(journal_path: Path, output_dir: Path | None = None,
                date: str | None = None, suffix: str = "",
                bank_path: Path = BANK_PATH,
                force: bool = False) -> WriteResult:
    """Render the two canonical outputs from the journal's ACCEPTED slots.

    Refuses to overwrite either file unless `force`: an existing pair may hold
    an unpromoted earlier batch. Both names carry the same suffix so they stay
    paired.
    """
    journal_path = Path(journal_path)
    journal = load_journal(journal_path)
    output_dir = Path(output_dir or journal_path.parent)
    date = date or journal.header.get("date") or _date_from_name(journal_path)

    draft_file = output_dir / f"draft-bank-{date}{suffix}.jsonl"
    report_file = output_dir / f"draft-report-{date}{suffix}.md"
    if not force:
        clash = [p for p in (draft_file, report_file) if p.exists()]
        if clash:
            raise BatchError(
                "refusing to overwrite: "
                + ", ".join(str(p) for p in clash)
                + " - it may hold an unpromoted batch. Pass --suffix -2 to "
                  "write a paired second set, or --force to replace.")

    accepted = _accepted_slots(journal)
    problems = []
    for slot in accepted:
        qid = slot["question_id"]
        record = slot.get("record")
        if not isinstance(record, dict):
            problems.append(f"{qid}: ACCEPTED with no record object")
            continue
        if record.get("question_id") != qid:
            problems.append(
                f"{qid}: record.question_id is "
                f"{record.get('question_id')!r} - the slot key and the record "
                "must agree")
        if not str(slot.get("evidence") or "").strip():
            problems.append(f"{qid}: ACCEPTED with no evidence - the report "
                            "must be readable cold")
    if problems:
        raise BatchError("\n".join(problems))

    records = [slot["record"] for slot in accepted]
    flags = crosscheck(records, read_records(Path(bank_path)))

    output_dir.mkdir(parents=True, exist_ok=True)
    draft_file.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
        encoding="utf-8")
    report_file.write_text(
        render_report(journal, draft_file, date, flags), encoding="utf-8")
    return WriteResult(
        draft_file=draft_file, report_file=report_file,
        accepted=[r["question_id"] for r in records],
        failed=[q for q in journal.order
                if journal.slots[q].get("status") == "FAILED"],
        blocked=[q for q in journal.order
                 if journal.slots[q].get("status") == "BLOCKED"],
        flags=flags)


_DATE_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _date_from_name(path: Path) -> str:
    match = _DATE_IN_NAME.search(path.name)
    if match:
        return match.group(1)
    return datetime.date.today().isoformat()
