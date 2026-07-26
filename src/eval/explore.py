"""Deterministic nodes of the /explore-corpus pipeline.

Sibling of `src/eval/batch.py`, and the same argument: everything in the
exploration loop that has a right answer lives here rather than in an Opus
orchestrator. What the frontier says, which buckets are unexplored, which ids
are free, whether a subagent's claims re-execute to the numbers it recorded,
whether the merged material passes the width rule, and how the profile grows -
none of that is authorship, and all of it was being done by the most expensive
node in the graph.

The journal is the typed state that flows between nodes: one append-only JSONL
file, one line per slice transition, latest line per `slice_id` wins. Line 0 is
a run header. The ENVELOPE is always valid; `map_entry` / `candidates` /
`findings` are payload that may be half-finished mid-run. This module validates
envelopes loudly when it reads them, and reports payload problems as findings
rather than raising - a bad candidate is a result to act on, not a crash.

Four entry points, one per CLI subcommand:

    frontier_report()   where exploration has and has not been, the slice
                        partition for this run, the orientation block, next ids
    verify_evidence()   re-execute EVERY recorded claim (not a sample)
    crosscheck()        width, entity spread, near-duplicates, supply
    write_profile()     journal -> insertions into corpus_profile.md

`src/retrieval/corpus_profile.md` grows monotonically: the writer inserts and
updates in place and never re-emits a section it did not touch.
"""

from __future__ import annotations

import datetime
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from src.config import (BANK_BRIEF_PATH, BANK_PATH, CORPUS_PROFILE_PATH,
                        DB_PATH, DRAFT_MCP_LOG_PATH, ROOT)
from src.eval.batch import (Flag, entities_in, jaccard, read_records,
                            trigrams, words)
from src.retrieval.sql_path import SqlGuardrailError, validate_sql

CONFIG_PATH = ROOT / "src" / "config.py"
EXPLORATION_DIR = ROOT / "eval" / "exploration"

# Candidate sections, in profile order. The section key is also the candidate
# id prefix ("vector-07"), which is why they are one vocabulary and not two.
CANDIDATE_SECTIONS = ("sql", "vector", "hybrid", "adversarial", "ambiguous")

# Section key -> the H2 heading the writer inserts under.
SECTION_HEADINGS = {"sql": "SQL", "vector": "Vector", "hybrid": "Hybrid",
                    "adversarial": "Adversarial", "ambiguous": "Ambiguous"}

# Supply targets for a FULL run (2-3x the allocation, so drafting has slack).
# A scoped run's argument replaces these outright - they are caps as much as
# floors - so crosscheck() takes them as a parameter.
FULL_RUN_TARGETS = {"sql": 45, "vector": 50, "hybrid": 50,
                    "adversarial": 25, "ambiguous": 20}

SLICE_STATUSES = ("DISPATCHED", "RETURNED", "VERIFIED", "SHORT", "FAILED")
SLICE_MODES = ("topical", "structural", "distributions")

# Width rule: no axis value on more than a third of a section's candidates, no
# named entity in more than two. Stated in the skill; enforced here.
AXIS_SHARE_LIMIT = 1 / 3
ENTITY_LIMIT = 2

# Near-duplicate thresholds, matching batch.py's crosscheck so "too similar"
# means the same thing at both ends of the pipeline.
TOPIC_TOKEN_FLAG = 0.50
TOPIC_TRIGRAM_FLAG = 0.45

# A map entry whose `about:` is mostly its own taxonomy label back is the
# documented failure mode (cp1: `ethnomycology` on an aquatic-fungi project).
# Above this token overlap with the bucket label, say so.
LABEL_ECHO_FLAG = 0.34
MAP_READ_MINIMUM = 2        # project ids an `about:` must be written from

# Level is DEFINED by |satisfying projects| for the topical routes
# (src/eval/bank.py). A candidate that recommends a level its own count
# contradicts is mislabelled at birth.
LEVEL_WINDOWS = {"L1": (1, 1), "L2": (2, 4), "L3": (5, None)}

# Hybrid survivor windows per subtype, from /draft-hybrid-question. A combo
# outside its window cannot be drafted as that subtype - the hyb-02 lesson,
# stated as a number instead of a hope.
SURVIVOR_WINDOWS = {"filter-read": (2, 10),
                    "filter-synthesize": (5, 20),
                    "filter-compare": (2, 20),
                    "filter-survey": (5, 60)}
SURVIVOR_CEILING = 200      # hard: a set that cannot be enumerated cannot be gold

UNCLASSIFIED_BUCKET = "(unclassified - no euroSciVoc row)"
TOP_LEVEL_ONLY = "(top-level only)"

_H2_RE = re.compile(r"^## (.+)$", re.MULTILINE)
_MAP_ID_RE = re.compile(r"^\s*-\s*region:\s*(m\d+)\s*$", re.MULTILINE)
_FINDING_ID_RE = re.compile(r"^\s*-\s*id:\s*(sf-\d+)\s*$", re.MULTILINE)
_CANDIDATE_ID_RE = re.compile(
    r"^\s*-\s*id:\s*(" + "|".join(CANDIDATE_SECTIONS) + r")-(\d+)\s*$",
    re.MULTILINE)
_BUCKET_LINE_RE = re.compile(r"^\s*bucket:\s*(.+?)\s*$", re.MULTILINE)
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
# Placeholder lines a section carries until it has real content. Both shapes
# exist in the profile: the scoped-run stub and the italic "nothing here yet"
# note. A stub left standing above six real entries reads as a contradiction,
# which is exactly what the first live run produced.
_STUB_RE = re.compile(
    r"^(?:Not yet explored \(.*\)\.|\*No entries yet[^\n]*\*)\s*$",
    re.MULTILINE)


class ExploreError(Exception):
    """Refusal with every problem listed; nothing is written."""


# --------------------------------------------------------------------------
# corpus_profile.md - parsing (the profile is data, not just prose)
# --------------------------------------------------------------------------

def profile_sections(text: str) -> dict[str, str]:
    """Split corpus_profile.md on H2 headings. Section key = the heading text
    kebab-cased ("## Corpus map" -> "corpus-map"), so the file stays
    human-readable while keys stay stable to call.

    Canonical home for this split: `get_corpus_profile` serves sections by the
    same key, and the writer inserts by the same boundaries. One parser, so a
    section a tool can read is exactly a section the writer can grow.
    """
    sections: dict[str, str] = {}
    key: str | None = None
    buf: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.startswith("## "):
            if key is not None:
                sections[key] = "".join(buf)
            key = "-".join(line[3:].strip().lower().split())
            buf = []
        if key is not None:
            buf.append(line)
    if key is not None:
        sections[key] = "".join(buf)
    return sections


def read_profile(path: Path = CORPUS_PROFILE_PATH) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""        # bootstrap run: no profile yet, not an error


@dataclass
class ProfileCandidate:
    """One `- id: <section>-NN` block, parsed back out of the profile."""
    id: str
    section: str
    fields: dict[str, str]

    @property
    def topic(self) -> str:
        return self.fields.get("topic", "")

    @property
    def bucket(self) -> str:
        return self.fields.get("bucket", "-")

    @property
    def axes(self) -> list[str]:
        return _axis_pairs(self.fields.get("axes", ""))


def _parse_blocks(body: str, section: str) -> list[ProfileCandidate]:
    """Parse `- id: x-NN` / indented `key: value` blocks out of one section."""
    blocks: list[ProfileCandidate] = []
    current: ProfileCandidate | None = None
    for line in body.splitlines():
        match = re.match(r"^\s*-\s*id:\s*(\S+)\s*$", line)
        if match:
            current = ProfileCandidate(match.group(1), section, {})
            blocks.append(current)
            continue
        if current is None:
            continue
        pair = re.match(r"^\s+([a-z][a-z _-]*):\s*(.*)$", line)
        if pair:
            current.fields[pair.group(1).strip()] = pair.group(2).strip()
        elif not line.strip():
            current = None            # blank line ends a block
    return blocks


def profile_candidates(text: str) -> list[ProfileCandidate]:
    sections = profile_sections(text)
    out: list[ProfileCandidate] = []
    for section in CANDIDATE_SECTIONS:
        body = sections.get(section)
        if body:
            out.extend(_parse_blocks(body, section))
    return out


def next_ids_for(text: str) -> dict[str, int]:
    """Highest id in use per section, plus map and structural-finding ids.

    Never restarts and never renumbers: a drafting session may already have
    consumed `vector-07`, so the number is spent whatever happened to it.
    """
    highest = {section: 0 for section in CANDIDATE_SECTIONS}
    for section, number in _CANDIDATE_ID_RE.findall(text):
        highest[section] = max(highest[section], int(number))
    highest["map"] = max((int(m[1:]) for m in _MAP_ID_RE.findall(text)),
                         default=0)
    highest["finding"] = max(
        (int(m.split("-")[1]) for m in _FINDING_ID_RE.findall(text)),
        default=0)
    return highest


def _axis_pairs(axes: str) -> list[str]:
    """`country=IT scheme=EIC dates=2019-2021` -> ['country=IT', ...]."""
    return [tok for tok in re.split(r"[\s,]+", axes.strip()) if "=" in tok]


# --------------------------------------------------------------------------
# The frontier - recomputed from the data, never carried from prose
# --------------------------------------------------------------------------

_BUCKETS_SQL = """
SELECT split_part(euroSciVocPath, '/', 1) AS branch,
       split_part(euroSciVocPath, '/', 2) AS field,
       COUNT(DISTINCT projectID)          AS projects
FROM euroscivoc
GROUP BY 1, 2
"""

_UNCLASSIFIED_SQL = """
SELECT COUNT(*) FROM project p
WHERE NOT EXISTS (SELECT 1 FROM euroscivoc e WHERE e.projectID = p.id)
"""


@dataclass
class Bucket:
    label: str
    projects: int
    status: str = "unexplored"
    map_id: str = "-"
    seeds: list[str] = field(default_factory=list)
    bank: list[str] = field(default_factory=list)


def bucket_label(branch: str, field_name: str) -> str:
    return f"{branch} / {field_name or TOP_LEVEL_ONLY}"


def connect(db_path: Path = DB_PATH) -> duckdb.DuckDBPyConnection:
    """Read-only, like every other consumer of this database."""
    return duckdb.connect(str(db_path), read_only=True)


def compute_buckets(con) -> list[Bucket]:
    """The 46-bucket denominator, from euroscivoc plus the unclassified rest.

    A project carries 1-5 euroSciVoc rows, so buckets overlap: this is a cover,
    not a partition, and the counts sum to more than 35,389. Fine for a
    coverage checklist, and cheaper than inventing a clustering.
    """
    rows = con.execute(_BUCKETS_SQL).fetchall()
    buckets = [Bucket(bucket_label(branch, fld), int(n))
               for branch, fld, n in rows]
    (unclassified,) = con.execute(_UNCLASSIFIED_SQL).fetchone()
    buckets.append(Bucket(UNCLASSIFIED_BUCKET, int(unclassified)))
    buckets.sort(key=lambda b: (-b.projects, b.label))
    return buckets


def _split_label(label: str) -> tuple[str, str] | None:
    """'natural sciences / biological sciences' -> ('natural sciences',
    'biological sciences'); None for the unclassified bucket."""
    if label.strip() == UNCLASSIFIED_BUCKET:
        return None
    branch, _, field_name = label.partition(" / ")
    if field_name.strip() == TOP_LEVEL_ONLY:
        field_name = ""
    return branch.strip(), field_name.strip()


def buckets_of_projects(con, project_ids: list[int]) -> dict[int, set[str]]:
    """project id -> the bucket label(s) it sits in. Unclassified projects map
    to the unclassified bucket, so every id lands somewhere."""
    if not project_ids:
        return {}
    ids = sorted({int(i) for i in project_ids})
    placeholders = ", ".join("?" for _ in ids)
    rows = con.execute(
        "SELECT projectID, split_part(euroSciVocPath, '/', 1), "
        "       split_part(euroSciVocPath, '/', 2) "
        f"FROM euroscivoc WHERE projectID IN ({placeholders})", ids).fetchall()
    out: dict[int, set[str]] = {i: set() for i in ids}
    for pid, branch, field_name in rows:
        out[int(pid)].add(bucket_label(branch, field_name))
    for pid, labels in out.items():
        if not labels:
            labels.add(UNCLASSIFIED_BUCKET)
    return out


def bank_by_bucket(con, records: list[dict]) -> dict[str, list[str]]:
    """Bucket -> the bank question ids drawn from it, traced through
    `gold_project_ids`. SQL-route questions with no gold project ids do not
    appear - stated in the profile, and true by construction here."""
    every_id = [int(i) for r in records for i in (r.get("gold_project_ids") or [])
                if isinstance(i, int) and not isinstance(i, bool)]
    placement = buckets_of_projects(con, every_id)
    out: dict[str, set[str]] = {}
    for record in records:
        qid = str(record.get("question_id") or "?")
        for pid in record.get("gold_project_ids") or []:
            if not isinstance(pid, int) or isinstance(pid, bool):
                continue
            for label in placement.get(int(pid), ()):
                out.setdefault(label, set()).add(qid)
    return {label: sorted(qids) for label, qids in out.items()}


def carried_map_ids(text: str) -> dict[str, str]:
    """bucket label -> `m<NN>`, read off the Corpus map's own entries.

    Read from the map, not from the frontier's `map` column: the map entries
    are the thing that makes a bucket `mapped`, so deriving status from them
    means the two can never disagree.
    """
    section = profile_sections(text).get("corpus-map", "")
    out: dict[str, str] = {}
    current: str | None = None
    for line in section.splitlines():
        region = re.match(r"^\s*-\s*region:\s*(m\d+)\s*$", line)
        if region:
            current = region.group(1)
            continue
        bucket = re.match(r"^\s+bucket:\s*(.+?)\s*$", line)
        if bucket and current:
            label = bucket.group(1).strip()
            if not label.startswith("<"):        # skip the format template
                out[label] = current
            current = None
    return out


_FRONTIER_ROW_RE = re.compile(
    r"^\|\s*(?P<bucket>[^|]+?)\s*\|\s*(?P<projects>[\d,]+)\s*"
    r"\|\s*(?P<status>unexplored|mapped|mined)\s*\|\s*(?P<map>[^|]*?)\s*"
    r"\|\s*(?P<seeds>[^|]*?)\s*\|\s*(?P<bank>[^|]*?)\s*\|\s*$",
    re.MULTILINE)


def carried_seeds(text: str) -> dict[str, list[str]]:
    """The `seeds` column as the last run recorded it.

    Carried rather than recomputed for one honest reason: cp1/cp2 candidates
    predate the `bucket:` line, and their bucket was traced by hand at cp3.
    Throwing that away because the old format cannot be re-derived would lose
    real provenance, so the column is a union of what was recorded and what
    today's candidates state for themselves.
    """
    out: dict[str, list[str]] = {}
    for row in _FRONTIER_ROW_RE.finditer(text):
        seeds = [s.strip() for s in row.group("seeds").split(",")
                 if s.strip() and s.strip() != "-"]
        if seeds:
            out[row.group("bucket").strip()] = seeds
    return out


def seeds_by_bucket(text: str) -> dict[str, list[str]]:
    """Bucket -> candidate ids, from the candidates' own `bucket:` lines."""
    out: dict[str, list[str]] = {}
    for candidate in profile_candidates(text):
        label = candidate.bucket
        if label and label != "-" and not label.startswith("<"):
            out.setdefault(label, []).append(candidate.id)
    return out


def build_frontier(con, profile_text: str,
                   bank_records: list[dict]) -> list[Bucket]:
    """The frontier, recomputed. `status` and `bank` are derived every run;
    only the map id is carried, and even that comes from the map itself."""
    maps = carried_map_ids(profile_text)
    carried = carried_seeds(profile_text)
    derived = seeds_by_bucket(profile_text)
    banked = bank_by_bucket(con, bank_records)
    buckets = compute_buckets(con)
    for bucket in buckets:
        bucket.map_id = maps.get(bucket.label, "-")
        seen = carried.get(bucket.label, [])
        bucket.seeds = seen + [s for s in derived.get(bucket.label, [])
                               if s not in seen]
        bucket.bank = banked.get(bucket.label, [])
        if bucket.bank:
            bucket.status = "mined"
        elif bucket.map_id != "-":
            bucket.status = "mapped"
        else:
            bucket.status = "unexplored"
    return buckets


def frontier_counters(buckets: list[Bucket]) -> str:
    """The counter line. The three statuses PARTITION the 46 buckets, so
    `mapped` here means mapped-but-not-yet-mined and the numbers sum."""
    total = len(buckets)
    counts = Counter(b.status for b in buckets)
    return (f"`mapped {counts['mapped']}/{total} | "
            f"mined {counts['mined']}/{total} | "
            f"unexplored {counts['unexplored']}/{total}`")


def render_frontier_rows(buckets: list[Bucket]) -> str:
    lines = ["| bucket | projects | status | map | seeds | bank |",
             "|---|---|---|---|---|---|"]
    for b in buckets:
        lines.append(
            f"| {b.label} | {b.projects:,} | {b.status} | {b.map_id} | "
            f"{', '.join(b.seeds) or '-'} | {', '.join(b.bank) or '-'} |")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Slice partition + orientation block (built once, pasted into every spawn)
# --------------------------------------------------------------------------

BUCKETS_PER_SLICE = 3


def partition_buckets(buckets: list[Bucket], count: int,
                      per_slice: int = BUCKETS_PER_SLICE,
                      prefer: str = "unexplored") -> list[list[Bucket]]:
    """Assign the next `count` buckets to slices, largest-first.

    Largest-first because a big bucket has more to say and more questions to
    support; the frontier guarantees we never return to one we have mapped.
    """
    pool = [b for b in buckets if b.status == prefer][:max(0, count)]
    return [pool[i:i + per_slice] for i in range(0, len(pool), per_slice)]


_ORIENTATION_QUERIES = {
    "branches": """
        SELECT split_part(euroSciVocPath, '/', 1) AS branch,
               COUNT(DISTINCT projectID)          AS projects
        FROM euroscivoc GROUP BY 1 ORDER BY 2 DESC""",
    # NULLs are excluded and counted separately: a plain GROUP BY makes the
    # NULL row look like a 57th scheme, and schema_docs says 56. A value count
    # that disagrees with the schema docs is exactly the kind of number a
    # subagent would waste a turn re-deriving.
    "schemes": """
        SELECT fundingScheme, COUNT(*) AS projects
        FROM project WHERE fundingScheme IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC""",
    "scheme_nulls": """
        SELECT COUNT(*) FROM project WHERE fundingScheme IS NULL""",
    "dates": """
        SELECT MIN(startDate), MAX(startDate),
               COUNT(*) FILTER (WHERE startDate IS NULL), COUNT(*)
        FROM project""",
    "funding": """
        SELECT quantile_cont(CAST(ecMaxContribution AS DOUBLE),
                             [0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9])
        FROM project WHERE ecMaxContribution IS NOT NULL""",
    "text_coverage": """
        SELECT COUNT(DISTINCT projectID) FROM report_text""",
}

SCHEMES_SHOWN = 20


def orientation_block(con, buckets: list[Bucket]) -> str:
    """The shared corpus facts, built once and pasted into every spawn prompt.

    Not optional and not decoration: measured cp2 runs had both subagents
    independently re-deriving the same branch inventory and re-probing the path
    format - roughly a third of each subagent's queries spent getting oriented.
    ~1.5-2k prompt tokens buys back ~10 turns per subagent.
    """
    branches = con.execute(_ORIENTATION_QUERIES["branches"]).fetchall()
    schemes = con.execute(_ORIENTATION_QUERIES["schemes"]).fetchall()
    (scheme_nulls,) = con.execute(
        _ORIENTATION_QUERIES["scheme_nulls"]).fetchone()
    lo, hi, null_dates, projects = con.execute(
        _ORIENTATION_QUERIES["dates"]).fetchone()
    (deciles,) = con.execute(_ORIENTATION_QUERIES["funding"]).fetchone()
    (with_text,) = con.execute(_ORIENTATION_QUERIES["text_coverage"]).fetchone()

    named = [b for b in buckets
             if b.label != UNCLASSIFIED_BUCKET
             and not b.label.endswith(TOP_LEVEL_ONLY)]
    out = [
        "### Orientation block (established facts - do NOT re-derive these)",
        "",
        "**euroSciVocPath format.** Slash-separated, 1-7 levels, **no leading "
        "slash**: `natural sciences/physical sciences/nuclear physics/nuclear "
        "fusion`. Match a subtree with `LIKE 'natural sciences/%'`; "
        "`LIKE '/natural sciences/%'` returns 0 rows (the sibling column "
        "`euroSciVocCode` is the one that leads with a slash). "
        "`split_part(euroSciVocPath,'/',1)` = branch, `,'/',2` = field of "
        "science, `euroSciVocTitle` = the LAST component, not a level.",
        "",
        f"**The 6 branches** ({len(named)} named second-level fields under "
        "them):",
        "",
        "| branch | projects |", "|---|---|"]
    out += [f"| {branch} | {n:,} |" for branch, n in branches]
    out += ["",
            f"**fundingScheme** - {len(schemes)} distinct values"
            + (f" ({scheme_nulls} project(s) with none)" if scheme_nulls
               else "")
            + f", top {SCHEMES_SHOWN} by project count:",
            "",
            "| scheme | projects |", "|---|---|"]
    out += [f"| {scheme} | {n:,} |" for scheme, n in schemes[:SCHEMES_SHOWN]]
    if len(schemes) > SCHEMES_SHOWN:
        tail = sum(n for _, n in schemes[SCHEMES_SHOWN:])
        out.append(f"| _{len(schemes) - SCHEMES_SHOWN} more_ | {tail:,} |")

    money = ", ".join(f"d{i + 1} {v:,.0f}" for i, v in enumerate(deciles or []))
    coverage = 100.0 * with_text / projects if projects else 0.0
    out += ["",
            f"**startDate** {lo} .. {hi} ({null_dates} null of {projects:,} "
            "projects).",
            "",
            f"**ecMaxContribution deciles (EUR):** {money}.",
            "",
            f"**report_text coverage:** {with_text:,} of {projects:,} projects "
            f"({coverage:.1f}%) have a report row.",
            ""]
    return "\n".join(out)


def _named_section(sections: dict[str, str], name: str) -> str | None:
    """Find a section by name, tolerating the bank brief's numbered headings
    ("## 7. Seeds - the exploration standard" -> "seeds")."""
    for key, body in sections.items():
        stripped = re.sub(r"^\d+\.?-", "", key)
        if stripped == name or stripped.startswith(name + "-"):
            return body
    return None


def seed_standard(path: Path = BANK_BRIEF_PATH) -> str:
    """The `## Seeds` section of the shared bank brief.

    The explorer decides which seeds the drafter, critic and judge ever see, so
    it has to be held to the same definition of "good" they are. Pasted rather
    than read as a whole file: the brief is ~160 lines and only this part is
    the explorer's business.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ExploreError(f"cannot read the bank brief: {e}") from e
    section = _named_section(profile_sections(text), "seeds")
    if not section:
        raise ExploreError(
            f"{path.name} has no '## Seeds' section - the explorer's standard "
            "for a candidate lives there, versioned alongside the drafting "
            "standard, so it cannot drift")
    return section.rstrip() + "\n"


# --------------------------------------------------------------------------
# frontier-report: one call replaces the orchestrator's whole startup
# --------------------------------------------------------------------------

@dataclass
class FrontierReport:
    buckets: list[Bucket]
    partition: list[list[Bucket]]
    orientation: str
    next_ids: dict[str, int]
    counters: str


def frontier_report(map_count: int = 0,
                    db_path: Path = DB_PATH,
                    profile_path: Path = CORPUS_PROFILE_PATH,
                    bank_path: Path = BANK_PATH,
                    brief_path: Path = BANK_BRIEF_PATH) -> FrontierReport:
    profile_text = read_profile(profile_path)
    con = connect(db_path)
    try:
        buckets = build_frontier(con, profile_text, read_records(bank_path))
        partition = partition_buckets(buckets, map_count)
        orientation = orientation_block(con, buckets)
    finally:
        con.close()
    if map_count:
        orientation = orientation + "\n" + seed_standard(brief_path)
    return FrontierReport(buckets=buckets, partition=partition,
                          orientation=orientation,
                          next_ids=next_ids_for(profile_text),
                          counters=frontier_counters(buckets))


def render_frontier_report(report: FrontierReport, map_count: int) -> str:
    out = ["# Frontier", "", render_frontier_rows(report.buckets), "",
           report.counters, ""]
    if map_count:
        out += ["# Slice partition", "",
                f"{len(report.partition)} slice(s) for `map={map_count}`, "
                "largest unexplored buckets first:", ""]
        for i, group in enumerate(report.partition, 1):
            labels = "; ".join(f"{b.label} ({b.projects:,})" for b in group)
            out.append(f"- s{i:02d}: {labels}")
        out.append("")
    nxt = report.next_ids
    out += ["# Next free ids", "",
            "- map: m{:02d}".format(nxt["map"] + 1),
            "- structural finding: sf-{:02d}".format(nxt["finding"] + 1)]
    out += [f"- {section}: {section}-{nxt[section] + 1:02d}"
            for section in CANDIDATE_SECTIONS]
    out += ["", report.orientation]
    return "\n".join(out)


# --------------------------------------------------------------------------
# The journal: typed slice state
# --------------------------------------------------------------------------

@dataclass
class ExplorationJournal:
    header: dict = field(default_factory=dict)
    slices: dict[str, dict] = field(default_factory=dict)   # latest line wins
    order: list[str] = field(default_factory=list)
    critic: dict = field(default_factory=dict)


def read_journal_lines(path: Path) -> list[dict]:
    lines, errors = [], []
    if not path.is_file():
        raise ExploreError(f"journal not found: {path}")
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
        raise ExploreError("\n".join(errors))
    return lines


def load_journal(path: Path) -> ExplorationJournal:
    """Read the append-only journal; latest line per `slice_id` wins.

    Validates the ENVELOPE only. The payload stays opaque: a slice may be
    mid-flight, partial, or SHORT by design, and judging its contents is
    verify_evidence's job, not the loader's.
    """
    journal, errors = ExplorationJournal(), []
    for n, line in enumerate(read_journal_lines(path), 1):
        where = f"{path.name} entry {n}"
        kind = line.get("kind")
        if kind == "run":
            journal.header = line
            continue
        if kind == "critic":
            # The completeness critic's one output. It travels through the
            # journal like everything else so the writer inserts it: the first
            # live run proved that "the orchestrator pastes it afterwards"
            # means it gets dropped.
            journal.critic = line
            continue
        if kind != "slice":
            errors.append(f"{where}: kind must be 'slice', 'critic' or 'run', "
                          f"got {kind!r}")
            continue
        slice_id = line.get("slice_id")
        if not isinstance(slice_id, str) or not slice_id.strip():
            errors.append(f"{where}: slice_id must be a non-empty string")
            continue
        if line.get("status") not in SLICE_STATUSES:
            errors.append(f"{where}: status must be one of {SLICE_STATUSES}, "
                          f"got {line.get('status')!r}")
        if line.get("mode") not in SLICE_MODES:
            errors.append(f"{where}: mode must be one of {SLICE_MODES}, "
                          f"got {line.get('mode')!r}")
        if not isinstance(line.get("buckets"), list):
            errors.append(f"{where}: buckets must be a list (empty for "
                          "structural slices)")
        if slice_id not in journal.slices:
            journal.order.append(slice_id)
        journal.slices[slice_id] = line
    if not journal.header:
        errors.append(f"{path.name}: no line 0 run header (kind: \"run\") - "
                      "the writer needs the scope and the asset versions")
    if errors:
        raise ExploreError("\n".join(errors))
    return journal


def journal_candidates(journal: ExplorationJournal) -> list[tuple[str, dict]]:
    """(slice_id, candidate) for every candidate in a non-FAILED slice."""
    out = []
    for slice_id in journal.order:
        record = journal.slices[slice_id]
        if record.get("status") == "FAILED":
            continue
        for candidate in record.get("candidates") or []:
            if isinstance(candidate, dict):
                out.append((slice_id, candidate))
    return out


# --------------------------------------------------------------------------
# verify-evidence: re-execute EVERY claim, not two per section
# --------------------------------------------------------------------------

@dataclass
class Check:
    slice_id: str
    name: str
    status: str          # PASS | FAIL | N/A
    detail: str


def _numbers_in(text: str) -> list[str]:
    """Numeric tokens as written, thousands separators stripped."""
    return [m.group(0).replace(",", "") for m in _NUMBER_RE.finditer(text or "")]


def _live_number_strings(rows: list[tuple]) -> set[str]:
    """Every number a result could plausibly be quoted as.

    Cells are matched loosely on purpose: `2019` inside a DATE cell should
    satisfy a key_result that says "from 2019", and a DECIMAL that prints as
    8057.00 should satisfy "8,057". The check is aimed at numbers that do not
    reproduce AT ALL - a stale count, a number carried from another query -
    not at formatting.
    """
    out: set[str] = set()
    for row in rows:
        for cell in row:
            if cell is None:
                continue
            text = str(cell)
            out.add(text)
            for token in _NUMBER_RE.findall(text):
                flat = token.replace(",", "")
                out.add(flat)
                if flat.endswith(".0") or re.fullmatch(r"\d+\.00?", flat):
                    out.add(flat.split(".")[0])
                try:
                    out.add(str(int(float(flat))))
                except (TypeError, ValueError):
                    pass
    return out


def _at_least(number: str, floor: int) -> bool:
    try:
        return float(number) >= floor
    except ValueError:
        return False


def _reproduces(number: str, live: set[str], row_count: int) -> bool:
    if number in live or number == str(row_count):
        return True
    try:
        value = float(number)
    except ValueError:
        return True                     # not really a number; do not judge it
    if value == float(row_count):
        return True
    # A rounded quotation of a live value ("1.2m", "0.98") still counts.
    for candidate in live:
        try:
            other = float(candidate)
        except ValueError:
            continue
        if other == value:
            return True
        decimals = len(number.split(".")[1]) if "." in number else 0
        if decimals and round(other, decimals) == value:
            return True
    return False


# Verification reads wide: a recorded "412 leaves" must be confirmable against
# a result that actually has 412 rows. Above this the result is treated as
# truncated and row-count arithmetic is no longer trusted (see _verify_numbers).
VERIFY_ROW_CAP = 5000


def _execute(con, sql: str, fetch: int = VERIFY_ROW_CAP):
    """(rows, error). Guardrail and DuckDB failures come back as strings -
    a broken query is a finding, not a crash."""
    try:
        checked = validate_sql(sql)
    except SqlGuardrailError as e:
        return [], f"guardrail: {e}"
    try:
        return con.execute(checked).fetchmany(fetch), None
    except duckdb.Error as e:
        return [], f"{type(e).__name__}: {e}"


def _evidence_items(payload: dict) -> list[dict]:
    """Accept `evidence` as one object or a list of them; a candidate may
    need several queries and must not have to squash them into one."""
    raw = payload.get("evidence")
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [e for e in raw if isinstance(e, dict)]
    return []


def verify_evidence(journal: ExplorationJournal, con) -> list[Check]:
    """Re-execute every recorded claim in the journal.

    The old net was "re-execute at least two embedded queries per section",
    performed by the orchestrator. Every claim already carries its SQL and its
    key result by contract, which makes the whole set machine-checkable - so
    check the whole set. A subprocess doing this exhaustively is both cheaper
    and stricter than an Opus sampling two.
    """
    checks: list[Check] = []
    for slice_id in journal.order:
        record = journal.slices[slice_id]
        if record.get("status") == "FAILED":
            checks.append(Check(slice_id, "SLICE", "N/A",
                                "FAILED slice - nothing to verify"))
            continue
        buckets = [b for b in (record.get("buckets") or []) if isinstance(b, str)]
        payloads: list[tuple[str, dict]] = []
        map_entry = record.get("map_entry")
        if isinstance(map_entry, dict):
            payloads.append(("map_entry", map_entry))
        for candidate in record.get("candidates") or []:
            if isinstance(candidate, dict):
                payloads.append((str(candidate.get("id") or "candidate"),
                                 candidate))
        for finding in record.get("findings") or []:
            if isinstance(finding, dict):
                payloads.append((str(finding.get("id") or "finding"), finding))

        if not payloads:
            checks.append(Check(slice_id, "PAYLOAD", "FAIL",
                                "no map entry, candidates or findings - a "
                                "returned slice must carry something"))
        for label, payload in payloads:
            checks += verify_payload(con, slice_id, label, payload, buckets)
        if isinstance(map_entry, dict):
            checks += verify_map_entry(con, slice_id, map_entry, buckets)
    return checks


def verify_payload(con, slice_id: str, label: str, payload: dict,
                   buckets: list[str]) -> list[Check]:
    """Re-execute one payload's evidence and check its recommendation.

    Public because `precheck_candidate` (MCP) runs exactly this on a single
    candidate inside the explorer's own loop: the same gate at authoring time
    and at close-out, so a candidate cannot pass one and fail the other.
    """
    checks: list[Check] = []
    items = _evidence_items(payload)
    if not items:
        return [Check(slice_id, f"EVIDENCE {label}", "FAIL",
                      "no evidence recorded - a claim without its query does "
                      "not go in the profile")]
    for n, item in enumerate(items, 1):
        name = f"EVIDENCE {label}" + (f" #{n}" if len(items) > 1 else "")
        sql = item.get("sql")
        key_result = str(item.get("key_result") or "")
        expect_empty = bool(item.get("expect_empty"))
        if not isinstance(sql, str) or not sql.strip():
            checks.append(Check(slice_id, name, "FAIL", "evidence.sql missing"))
            continue
        rows, error = _execute(con, sql)
        if error is not None:
            checks.append(Check(slice_id, name, "FAIL",
                                f"did not execute: {error}"))
            continue
        if not rows and not expect_empty:
            checks.append(Check(
                slice_id, name, "FAIL",
                "executed and returned 0 rows - if the absence IS the claim, "
                "mark the evidence expect_empty"))
            continue
        if rows and expect_empty:
            checks.append(Check(
                slice_id, name, "FAIL",
                f"marked expect_empty but returned {len(rows)} row(s) - the "
                "absence this candidate rests on is not real"))
            continue
        if not key_result.strip():
            checks.append(Check(slice_id, name, "FAIL",
                                "evidence.key_result missing - the recorded "
                                "number is what makes the claim checkable"))
            continue
        live = _live_number_strings(rows)
        in_query = set(_numbers_in(sql))
        truncated = len(rows) >= VERIFY_ROW_CAP
        stale = [n_ for n_ in _numbers_in(key_result)
                 if n_ not in in_query
                 and not _reproduces(n_, live, len(rows))
                 # A result we had to cut off cannot disprove a large number.
                 and not (truncated and _at_least(n_, VERIFY_ROW_CAP))]
        if stale:
            sample = sorted(live, key=len)[:8]
            checks.append(Check(
                slice_id, name, "FAIL",
                f"recorded {stale} but the live result does not contain "
                f"{'it' if len(stale) == 1 else 'them'} "
                f"({len(rows)} row(s); values include {sample})"))
        else:
            checks.append(Check(
                slice_id, name, "PASS",
                f"re-executed, {len(rows)}{'+' if truncated else ''} row(s), "
                "recorded numbers reproduce"))

    checks += _verify_recommendation(slice_id, label, payload, buckets)
    return checks


def _verify_recommendation(slice_id: str, label: str, payload: dict,
                           buckets: list[str]) -> list[Check]:
    """Level-vs-count and survivor-window agreement, plus slice discipline.

    These are the birth-failure checks: `hyb-02` (musicology x MSCA-IF) was
    unviable before a drafter ever saw it, and the number that said so was
    already known.
    """
    checks: list[Check] = []
    recommend = str(payload.get("recommend") or "")
    if not recommend:
        return checks

    level = next((m for m in re.findall(r"level=(\w+)", recommend)), None)
    subtype = next((m for m in re.findall(r"subtype=([\w-]+)", recommend)), None)
    route = next((m for m in re.findall(r"route=(\w+)", recommend)), None)

    satisfying = payload.get("satisfying_count")
    if level in LEVEL_WINDOWS and isinstance(satisfying, int):
        low, high = LEVEL_WINDOWS[level]
        if satisfying < low or (high is not None and satisfying > high):
            window = f"{low}" if high == low else f"{low}-{high or 'inf'}"
            checks.append(Check(
                slice_id, f"LEVEL {label}", "FAIL",
                f"recommends {level} (|satisfying| {window}) but records "
                f"satisfying_count={satisfying} - level is DEFINED by the "
                "count, so one of the two is wrong"))
        else:
            checks.append(Check(slice_id, f"LEVEL {label}", "PASS",
                                f"{level} agrees with satisfying_count="
                                f"{satisfying}"))

    survivors = payload.get("survivor_count")
    if route == "hybrid" and isinstance(survivors, int):
        if survivors > SURVIVOR_CEILING:
            checks.append(Check(
                slice_id, f"WINDOW {label}", "FAIL",
                f"survivor_count={survivors} exceeds the {SURVIVOR_CEILING} "
                "ceiling - a survivor set that cannot be enumerated cannot be "
                "adjudicated"))
        elif subtype in SURVIVOR_WINDOWS:
            low, high = SURVIVOR_WINDOWS[subtype]
            if not low <= survivors <= high:
                checks.append(Check(
                    slice_id, f"WINDOW {label}", "FAIL",
                    f"subtype {subtype} wants {low}-{high} survivors, this "
                    f"combo has {survivors} - it would fail at birth"))
            else:
                checks.append(Check(
                    slice_id, f"WINDOW {label}", "PASS",
                    f"{survivors} survivors is inside {subtype}'s "
                    f"{low}-{high} window"))

    bucket = payload.get("bucket")
    if buckets and isinstance(bucket, str) and bucket not in ("-", ""):
        if bucket not in buckets:
            checks.append(Check(
                slice_id, f"SLICE {label}", "FAIL",
                f"bucket {bucket!r} is outside this slice's assignment "
                f"{buckets} - slices are disjoint so width emerges by "
                "construction"))
    return checks


def verify_map_entry(con, slice_id: str, entry: dict,
                     buckets: list[str]) -> list[Check]:
    """The map's own failure mode: an entry written from the tag, not the text.

    `read:` carries the project ids the `about:` was written from. They must
    exist, carry text, and sit in the bucket - and the prose must not simply be
    the taxonomy label back.
    """
    checks: list[Check] = []
    label = str(entry.get("bucket") or (buckets[0] if buckets else "?"))
    read_ids = [i for i in (entry.get("read") or [])
                if isinstance(i, int) and not isinstance(i, bool)]

    if len(read_ids) < MAP_READ_MINIMUM:
        checks.append(Check(
            slice_id, "MAP-READ", "FAIL",
            f"`read:` lists {len(read_ids)} project id(s); a map entry must be "
            f"written from at least {MAP_READ_MINIMUM} projects that were "
            "actually read"))
    else:
        placeholders = ", ".join("?" for _ in read_ids)
        rows = con.execute(
            "SELECT p.id, (COALESCE(NULLIF(TRIM(p.objective), ''),"
            " NULLIF(TRIM(r.summary), ''), NULLIF(TRIM(r.teaser), '')) "
            "IS NOT NULL) "
            "FROM project p LEFT JOIN report_text r ON r.projectID = p.id "
            f"WHERE p.id IN ({placeholders})", read_ids).fetchall()
        found = {int(pid): bool(has_text) for pid, has_text in rows}
        absent = [i for i in read_ids if i not in found]
        textless = sorted(i for i, ok in found.items() if not ok)
        if absent or textless:
            checks.append(Check(
                slice_id, "MAP-READ", "FAIL",
                f"not in the database: {absent or 'none'}; no stored text: "
                f"{textless or 'none'} - an entry cannot be written from text "
                "that does not exist"))
        else:
            checks.append(Check(slice_id, "MAP-READ", "PASS",
                                f"{len(read_ids)} read project(s) exist and "
                                "carry text"))
            checks.append(_map_membership(con, slice_id, label, read_ids))

    prose = " ".join(str(entry.get(k) or "") for k in ("about", "texture"))
    if not prose.strip():
        checks.append(Check(slice_id, "MAP-ORIGINAL", "FAIL",
                            "no `about:` / `texture:` prose"))
    else:
        overlap = jaccard(words(prose), words(label))
        if overlap >= LABEL_ECHO_FLAG:
            checks.append(Check(
                slice_id, "MAP-ORIGINAL", "FAIL",
                f"`about:`/`texture:` overlaps the bucket label {label!r} at "
                f"{overlap:.2f} - an entry that paraphrases its own tag is "
                "worthless"))
        else:
            checks.append(Check(slice_id, "MAP-ORIGINAL", "PASS",
                                f"prose is not the label back "
                                f"(overlap {overlap:.2f})"))
    return checks


def _map_membership(con, slice_id: str, label: str,
                    read_ids: list[int]) -> Check:
    split = _split_label(label)
    placeholders = ", ".join("?" for _ in read_ids)
    if split is None:
        rows = con.execute(
            f"SELECT p.id FROM project p WHERE p.id IN ({placeholders}) "
            "AND NOT EXISTS (SELECT 1 FROM euroscivoc e "
            "WHERE e.projectID = p.id)", read_ids).fetchall()
    else:
        branch, field_name = split
        rows = con.execute(
            "SELECT DISTINCT projectID FROM euroscivoc "
            f"WHERE projectID IN ({placeholders}) "
            "AND split_part(euroSciVocPath, '/', 1) = ? "
            "AND split_part(euroSciVocPath, '/', 2) = ?",
            [*read_ids, branch, field_name]).fetchall()
    inside = {int(r[0]) for r in rows}
    outside = [i for i in read_ids if i not in inside]
    if outside:
        return Check(slice_id, "MAP-MEMBER", "FAIL",
                     f"read project(s) {outside} are not in bucket {label!r} - "
                     "the entry describes a region it did not read")
    return Check(slice_id, "MAP-MEMBER", "PASS",
                 f"all {len(read_ids)} read project(s) are in {label!r}")


def render_checks(checks: list[Check]) -> str:
    failures = [c for c in checks if c.status == "FAIL"]
    by_slice: dict[str, list[Check]] = {}
    for check in checks:
        by_slice.setdefault(check.slice_id, []).append(check)
    out = [f"verify-evidence: {len(checks) - len(failures)} PASS/NA, "
           f"{len(failures)} FAIL", ""]
    for slice_id, group in by_slice.items():
        bad = [c for c in group if c.status == "FAIL"]
        out.append(f"## {slice_id} - {'FAIL' if bad else 'ok'} "
                   f"({len(group)} check(s))")
        for check in group:
            if check.status == "FAIL":
                out.append(f"  FAIL  {check.name}: {check.detail}")
        for check in group:
            if check.status != "FAIL":
                out.append(f"  {check.status:5s} {check.name}: {check.detail}")
        out.append("")
    if failures:
        out.append("A FAIL means re-spawn that slice with the finding, or drop "
                   "the item. Never hand-edit the number.")
    return "\n".join(out)


# --------------------------------------------------------------------------
# explore-crosscheck: what no single slice can see
# --------------------------------------------------------------------------

def crosscheck(journal: ExplorationJournal, profile_text: str,
               targets: dict[str, int] | None = None) -> list[Flag]:
    """Width, entity spread, near-duplicates and supply, across the whole run.

    Slices are disjoint by construction, so most collisions are impossible -
    but boundary overlap is real (a project carries up to 5 euroSciVoc rows),
    and a candidate can collide with what the profile already holds. Output is
    FLAGS for the review gate, never a gate and never a re-spawn.
    """
    flags: list[Flag] = []
    new = journal_candidates(journal)
    existing = profile_candidates(profile_text)

    by_section: dict[str, list[dict]] = {}
    for _, candidate in new:
        section = _section_of(candidate)
        if section:
            by_section.setdefault(section, []).append(candidate)

    # --- width: no axis value on more than a third of a section ---
    for section, candidates in sorted(by_section.items()):
        axes = Counter(pair for c in candidates
                       for pair in _axis_pairs(str(c.get("axes") or "")))
        limit = max(1, int(len(candidates) * AXIS_SHARE_LIMIT))
        for pair, n in sorted(axes.items()):
            if n > limit and len(candidates) > 2:
                flags.append(Flag(
                    "WIDTH", "FLAG",
                    f"{section}: {n} of {len(candidates)} candidates share "
                    f"axis {pair} (limit {limit})"))

    # --- entity spread across the whole run and the existing profile ---
    seen: dict[str, list[str]] = {}
    for _, candidate in new:
        blob = " ".join(str(candidate.get(k) or "")
                        for k in ("topic", "why", "claim"))
        for entity in entities_in(blob):
            seen.setdefault(entity, []).append(str(candidate.get("id") or "?"))
    for candidate in existing:
        blob = " ".join(candidate.fields.get(k, "")
                        for k in ("topic", "why", "claim"))
        for entity in entities_in(blob):
            if entity in seen:
                seen[entity].append(f"{candidate.id}(profile)")
    for entity, users in sorted(seen.items()):
        if len(users) > ENTITY_LIMIT:
            flags.append(Flag("ENTITY", "FLAG",
                              f"{entity} appears in {len(users)} candidates: "
                              f"{', '.join(users)}"))

    # --- near-duplicate topics, within the run and against the profile ---
    pool = [(str(c.get("id") or "?"), str(c.get("topic") or ""), "run")
            for _, c in new]
    pool += [(c.id, c.topic, "profile") for c in existing]
    tokens = [words(t) for _, t, _ in pool]
    grams = [trigrams(t) for _, t, _ in pool]
    for i in range(len(new)):          # every (new, other) pair exactly once
        for j in range(i + 1, len(pool)):
            tok, tri = jaccard(tokens[i], tokens[j]), jaccard(grams[i], grams[j])
            if tok >= TOPIC_TOKEN_FLAG or tri >= TOPIC_TRIGRAM_FLAG:
                flags.append(Flag(
                    "NEAR-DUPLICATE", "FLAG",
                    f"{pool[i][0]} vs {pool[j][0]} ({pool[j][2]}): token "
                    f"overlap {tok:.2f}, trigram {tri:.2f}"))

    # --- supply against this run's targets ---
    targets = targets or {}
    for section in CANDIDATE_SECTIONS:
        target = targets.get(section)
        if not target:
            continue
        have = len(by_section.get(section, []))
        level = "FLAG" if have < target else "INFO"
        flags.append(Flag("SUPPLY", level,
                          f"{section}: {have}/{target} candidate(s) this run"))

    shorts = [(sid, journal.slices[sid].get("short"))
              for sid in journal.order if journal.slices[sid].get("short")]
    for slice_id, note in shorts:
        flags.append(Flag("SHORT", "INFO", f"{slice_id}: {note}"))

    buckets = Counter(b for sid in journal.order
                      for b in (journal.slices[sid].get("buckets") or []))
    if buckets:
        flags.append(Flag("SPREAD", "INFO", "buckets touched: " + ", ".join(
            f"{b} x{n}" if n > 1 else b for b, n in sorted(buckets.items()))))
    return flags


def _section_of(candidate: dict) -> str | None:
    match = re.match(r"^(" + "|".join(CANDIDATE_SECTIONS) + r")-\d+$",
                     str(candidate.get("id") or ""))
    return match.group(1) if match else None


def render_flags(flags: list[Flag]) -> str:
    hard = [f for f in flags if f.level == "FLAG"]
    info = [f for f in flags if f.level == "INFO"]
    lines = []
    if hard:
        lines += [f"- **{f.kind}** - {f.detail}" for f in hard]
    else:
        lines.append("- no width, entity, near-duplicate or supply flags")
    lines += [f"- _{f.kind}_ - {f.detail}" for f in info]
    lines.append("- Lexical checks only (token and character-trigram overlap, "
                 "no embedder). Flags are for the review gate; nothing here "
                 "gates or re-spawns.")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# write-profile: the journal becomes the artifact
# --------------------------------------------------------------------------

MAP_FIELDS = ("bucket", "slice", "size", "about", "texture", "read",
              "good for", "thin for")
FINDING_FIELDS = ("kind", "claim", "evidence", "serves")
CANDIDATE_FIELDS = ("topic", "recommend", "bucket", "evidence", "axes",
                    "claim", "near-miss", "routes", "readings", "why")


def _render_evidence(payload: dict) -> str:
    items = _evidence_items(payload)
    return " ; ".join(f"`{i.get('sql', '').strip()}` -> {i.get('key_result')}"
                      for i in items)


def _render_map_entry(entry: dict, region: str, version: str) -> str:
    read_ids = ", ".join(str(i) for i in (entry.get("read") or []))
    lines = [f"- region: {region}"]
    for key in MAP_FIELDS:
        value = entry.get(key.replace(" ", "_"), entry.get(key))
        if key == "read":
            value = read_ids
        if value in (None, "", []):
            continue
        lines.append(f"  {key}: {value}")
    lines.append(f"  mapped: {version}")
    return "\n".join(lines)


def _render_finding(finding: dict, fid: str) -> str:
    lines = [f"- id: {fid}"]
    for key in FINDING_FIELDS:
        value = (_render_evidence(finding) if key == "evidence"
                 else finding.get(key))
        if value in (None, "", []):
            continue
        lines.append(f"  {key}: {value}")
    return "\n".join(lines)


def _render_candidate(candidate: dict, cid: str) -> str:
    lines = [f"- id: {cid}"]
    for key in CANDIDATE_FIELDS:
        value = (_render_evidence(candidate) if key == "evidence"
                 else candidate.get(key.replace("-", "_"), candidate.get(key)))
        if value in (None, "", []):
            continue
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        lines.append(f"  {key}: {value}")
    return "\n".join(lines)


def _section_bounds(text: str, heading: str) -> tuple[int, int]:
    """(start, end) character offsets of one H2 section's body."""
    matches = [(m.start(), m.group(1).strip()) for m in _H2_RE.finditer(text)]
    for i, (start, title) in enumerate(matches):
        if title.lower() == heading.lower():
            end = matches[i + 1][0] if i + 1 < len(matches) else len(text)
            return start, end
    raise ExploreError(
        f"corpus_profile.md has no '## {heading}' section - the writer inserts "
        "into the contract's headings and will not invent one")


def _insert_into(text: str, heading: str, block: str) -> str:
    """Append a block at the end of one section, replacing a stub if present.

    Insertion, never rewrite: the profile grows monotonically and re-emitting
    an untouched section costs output tokens and invites transcription drift.
    """
    start, end = _section_bounds(text, heading)
    body = text[start:end]
    stubbed = _STUB_RE.sub("", body)
    if stubbed != body:
        body = stubbed.rstrip() + "\n\n" + block.rstrip() + "\n\n"
    else:
        body = body.rstrip() + "\n\n" + block.rstrip() + "\n\n"
    return text[:start] + body + text[end:]


def _replace_frontier(text: str, buckets: list[Bucket]) -> str:
    start, end = _section_bounds(text, "Frontier")
    body = text[start:end]
    table = render_frontier_rows(buckets)
    body, n = re.subn(r"\| bucket \| projects \|.*?(?=\n\n)", table, body,
                      count=1, flags=re.DOTALL)
    if not n:
        raise ExploreError("could not find the frontier table to update")
    body = re.sub(r"`mapped \d+/\d+ \| mined \d+/\d+ \| unexplored \d+/\d+`",
                  frontier_counters(buckets), body, count=1)
    return text[:start] + body + text[end:]


@dataclass
class Telemetry:
    # Journal SLICES, not agents: one subagent may carry several buckets and
    # journals a line per bucket, so the two numbers differ (the first live run
    # reported "6 subagents" for 2 explorers + 1 critic). The journal cannot
    # know how many agents ran; `python -m src.cli agent-trace` can, and the
    # run header records it when the orchestrator states it.
    slices: int
    run_sql: int
    project_text_calls: int
    projects_read: int
    seconds: float | None = None
    mcp_ms: int = 0

    def _duration(self) -> str:
        if self.seconds is None:
            return ""
        wall = (f"{self.seconds / 60:.0f}m" if self.seconds >= 60
                else f"{self.seconds:.0f}s")
        return f"{wall} wall ({self.mcp_ms / 1000:.0f}s in MCP calls), "

    def line(self, version: str, date: str, scope: str,
             maps: int, candidates: int, findings: int,
             counters: str, agents: int | None = None) -> str:
        """One run-log line. The frontier is quoted with the same counter
        vocabulary the Frontier section uses, so the two can never read as
        different numbers; the deltas say what this run actually added."""
        added = ", ".join(
            f"+{n} {label}" for n, label in
            ((maps, "map entries"), (candidates, "candidates"),
             (findings, "structural findings")) if n)
        who = (f"{agents} subagents over {self.slices} slices"
               if agents else f"{self.slices} slices")
        return (f"- {version} ({date}) scope `\"{scope}\"`: {self._duration()}"
                f"{who}, {self.run_sql} `run_sql`, "
                f"{self.projects_read} projects read across "
                f"{self.project_text_calls} `get_project_text` calls; "
                f"{added or 'nothing added'}; frontier {counters}.")


def telemetry_since(started: str | None, slices: int,
                    log_path: Path = DRAFT_MCP_LOG_PATH) -> Telemetry:
    """Count this run's MCP traffic from the server's own log.

    Deterministic and free: the log is already written on every call, and the
    drafting audit had to reconstruct spend as "~70% of a 5-hour window"
    because nobody was counting.
    """
    run_sql = calls = projects = mcp_ms = 0
    last: str | None = None
    if log_path.is_file():
        for raw in log_path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            stamp = str(entry.get("ts", ""))
            if started and stamp < started:
                continue
            last = stamp
            mcp_ms += int(entry.get("ms") or 0)
            if entry.get("tool") == "run_sql":
                run_sql += 1
            elif entry.get("tool") == "get_project_text":
                calls += 1
                # `found` is what the server logs (mcp_server.get_project_text).
                projects += int(entry.get("found") or 0)
    # Wall clock spans the run header's `started` to the last logged call. It
    # is what the next estimate gets built on, so it is recorded rather than
    # remembered - cp1/cp2 had a duration slot and never filled it.
    seconds = None
    if started and last:
        try:
            seconds = max(0.0, (datetime.datetime.fromisoformat(last)
                                - datetime.datetime.fromisoformat(started)
                                ).total_seconds())
        except ValueError:
            seconds = None
    return Telemetry(slices, run_sql, calls, projects, seconds, mcp_ms)


@dataclass
class WriteResult:
    profile_path: Path
    version: str
    map_entries: list[str]
    findings: list[str]
    candidates: list[str]
    mapped_before: int
    mapped_after: int
    flags: list[Flag]
    telemetry: Telemetry
    version_bumped: bool = False


def write_profile(journal_path: Path, version: str,
                  profile_path: Path = CORPUS_PROFILE_PATH,
                  db_path: Path = DB_PATH,
                  bank_path: Path = BANK_PATH,
                  config_path: Path = CONFIG_PATH,
                  log_path: Path = DRAFT_MCP_LOG_PATH,
                  date: str | None = None,
                  dry_run: bool = False) -> WriteResult:
    """Grow corpus_profile.md from the journal's verified slices.

    The orchestrator's last contact with a slice's payload is the relay that
    wrote the journal line. Everything here reads from disk.
    """
    journal = load_journal(Path(journal_path))
    text = read_profile(profile_path)
    if not text:
        raise ExploreError(
            f"{profile_path} does not exist - write-profile grows a profile, "
            "it does not bootstrap one")
    if not re.fullmatch(r"cp\d+", version):
        raise ExploreError(f"version must look like 'cp4', got {version!r}")
    if re.search(rf"^- {version} \(", text, re.MULTILINE):
        raise ExploreError(
            f"{version} already appears in the Header run log - bump to the "
            "next cpN rather than overwriting a recorded run")

    date = date or journal.header.get("date") or datetime.date.today().isoformat()
    scope = str(journal.header.get("scope") or "unstated")
    ids = next_ids_for(text)

    written_maps, written_findings, written_candidates = [], [], []
    map_blocks, finding_blocks = [], []
    section_blocks: dict[str, list[str]] = {}

    for slice_id in journal.order:
        record = journal.slices[slice_id]
        if record.get("status") not in ("VERIFIED", "SHORT"):
            continue
        entry = record.get("map_entry")
        if isinstance(entry, dict):
            ids["map"] += 1
            region = f"m{ids['map']:02d}"
            map_blocks.append(_render_map_entry(entry, region, version))
            written_maps.append(region)
        for finding in record.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            ids["finding"] += 1
            fid = f"sf-{ids['finding']:02d}"
            finding_blocks.append(_render_finding(finding, fid))
            written_findings.append(fid)
        for candidate in record.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            section = _section_of(candidate)
            if section is None:
                raise ExploreError(
                    f"{slice_id}: candidate id {candidate.get('id')!r} does "
                    f"not name a section ({', '.join(CANDIDATE_SECTIONS)}-NN)")
            ids[section] += 1
            cid = f"{section}-{ids[section]:02d}"
            section_blocks.setdefault(section, []).append(
                _render_candidate(candidate, cid))
            written_candidates.append(cid)

    # Cross-check against the profile as it stands BEFORE this run's blocks go
    # in - otherwise every new candidate is a near-duplicate of itself.
    flags = crosscheck(journal, text, journal.header.get("targets"))

    con = connect(db_path)
    try:
        before = build_frontier(con, text, read_records(bank_path))
        mapped_before = sum(1 for b in before if b.status != "unexplored")
        if map_blocks:
            text = _insert_into(text, "Corpus map", "\n\n".join(map_blocks))
        if finding_blocks:
            text = _insert_into(text, "Structural findings",
                                "\n\n".join(finding_blocks))
        for section, blocks in section_blocks.items():
            text = _insert_into(text, SECTION_HEADINGS[section],
                                "\n\n".join(blocks))
        notes = str(journal.critic.get("coverage_notes") or "").strip()
        if notes:
            text = _insert_into(text, "Coverage notes",
                                f"**{version} ({date})**\n\n{notes}")
        after_buckets = build_frontier(con, text, read_records(bank_path))
    finally:
        con.close()

    mapped_after = sum(1 for b in after_buckets if b.status != "unexplored")
    text = _replace_frontier(text, after_buckets)

    telemetry = telemetry_since(journal.header.get("started"),
                                len(journal.order), log_path)
    text = _bump_header(text, version, date, telemetry.line(
        version, date, scope, len(written_maps), len(written_candidates),
        len(written_findings), frontier_counters(after_buckets),
        agents=journal.header.get("subagents")))

    # The version label describes the CANONICAL profile. Writing a copy
    # somewhere else (a rehearsal, a test) must never move it - that would
    # silently desynchronise src/config.py from the real file.
    canonical = profile_path.resolve() == CORPUS_PROFILE_PATH.resolve()
    if not dry_run:
        profile_path.write_text(text, encoding="utf-8")
        if canonical:
            _bump_config_version(config_path, version)
    return WriteResult(profile_path=profile_path, version=version,
                       map_entries=written_maps, findings=written_findings,
                       candidates=written_candidates,
                       mapped_before=mapped_before, mapped_after=mapped_after,
                       flags=flags, telemetry=telemetry,
                       version_bumped=canonical and not dry_run)


def _bump_header(text: str, version: str, date: str, telemetry: str) -> str:
    start, end = _section_bounds(text, "Header")
    body = text[start:end]
    body = re.sub(r"^- \*\*Version:\*\* .*$", f"- **Version:** {version}",
                  body, count=1, flags=re.MULTILINE)
    body = re.sub(r"^- \*\*Generated:\*\* .*$", f"- **Generated:** {date}",
                  body, count=1, flags=re.MULTILINE)
    lines = body.splitlines()
    last_run = max((i for i, line in enumerate(lines)
                    if re.match(r"^- cp\d+ \(", line)), default=None)
    if last_run is None:
        raise ExploreError("the Header has no run log to append to")
    lines.insert(last_run + 1, telemetry)
    return text[:start] + "\n".join(lines) + "\n" + text[end:]


def _bump_config_version(config_path: Path, version: str) -> None:
    text = config_path.read_text(encoding="utf-8")
    updated, n = re.subn(r'^CORPUS_PROFILE_VERSION = ".*?"',
                         f'CORPUS_PROFILE_VERSION = "{version}"', text,
                         count=1, flags=re.MULTILINE)
    if not n:
        raise ExploreError("could not find CORPUS_PROFILE_VERSION in "
                           f"{config_path}")
    config_path.write_text(updated, encoding="utf-8")


def render_write_result(result: WriteResult) -> str:
    total = len(result.map_entries) + len(result.findings) \
        + len(result.candidates)
    out = [f"write-profile: {result.version} -> {result.profile_path}",
           f"  map entries:  {', '.join(result.map_entries) or 'none'}",
           f"  findings:     {', '.join(result.findings) or 'none'}",
           f"  candidates:   {', '.join(result.candidates) or 'none'}",
           f"  frontier:     explored {result.mapped_before} -> "
           f"{result.mapped_after} of 46 buckets",
           f"  {total} block(s) inserted", "",
           "Cross-check:", render_flags(result.flags)]
    return "\n".join(out)
