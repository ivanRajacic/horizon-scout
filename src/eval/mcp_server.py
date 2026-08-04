"""Read-only MCP server for question-bank drafting (M5).

Exposes exactly eight tools to the drafting agents (/draft-sql-question, the
vector/hybrid skills) and the exploration agent (/explore-corpus): run_sql,
get_schema_docs, get_bank_questions, search_corpus, get_project_text,
get_corpus_profile, precheck_record, precheck_candidate. Deliberately minimal -
the agent must be able to ground questions in real data, verify gold labels by
execution (SQL routes) or pooled retrieval (vector/hybrid routes), and re-check
its own factual claims before emitting them, and nothing more. No write tools:
bank appends are confirmation-gated in the skill layer, outside the MCP.

The two prechecks are the deterministic self-gates, one per authoring node.
precheck_record is the drafter's: everything a drafted record asserts that a
machine can settle by re-execution (gold SQL runs and is non-empty, gold
projects have text, filter survivors still match, schema_docs hash is the live
one). precheck_candidate is the explorer's, one rung upstream: an exploration
candidate's evidence re-executes to the numbers it recorded, its level agrees
with its own count, its survivor count is inside the subtype's drafting window,
and a map entry was written from projects that exist and were read. Both live
here rather than in the CLI because they run inside an agent's own loop, and
those agents have no shell. Schema validation is deliberately elsewhere
(`python -m src.cli validate-record`), as is close-out verification of a whole
exploration journal (`python -m src.cli verify-evidence`, the same code).

search_corpus runs the real retrieval stack (lexical | dense | hybrid |
hybrid_rerank, or "pooled" = all four) and returns PROJECT-level rankings -
gold_project_ids are project labels, never chunk labels. It defaults to
config.RUNTIME_RETRIEVER, the stack the system answers with; gold labelling
and adversarial absence proofs must ask for "pooled" explicitly, because the
pooling protocol ("label the union of all retrieval conditions' top-k") needs
every condition's view at once. get_project_text is the gold-evidence channel:
full objective + report sections for grounding, candidate relevance judging,
and reference writing. Its optional `fields` / `max_chars` arguments let a
caller that only needs the gist (corpus exploration confirming a theme) pull
a fraction of the ~8.1k-char full payload; both default to off, so the
drafting skills' full-evidence reads are unchanged. Retrieval failures (embed/rerank server down, missing
index) come back as {"error": ...} results, matching run_sql's contract.

Safety is enforced in code, not prompt - twice over: the statement guard
from sql_path.validate_sql (single SELECT/WITH, no forbidden keywords, no
multi-statement) AND a read-only DuckDB connection. SQL errors come back as
structured results ({"error": ...}), never tool failures, because
trap-question authoring needs to reason about broken queries.

Paths come from env (HS_DB_PATH, HS_BANK_PATH, HS_SCHEMA_DOCS_PATH,
HS_CORPUS_PROFILE_PATH, HS_DRAFT_MCP_LOG_PATH) with defaults from
src.config. Every call is logged
as JSONL - the same trace-everything discipline as the rest of the pipeline.

Run: python -m src.eval.mcp_server   (stdio transport; wired in .mcp.json)
"""

import datetime
import decimal
import functools
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb

from src.config import (BANK_PATH, CORPUS_PROFILE_PATH,
                        CORPUS_PROFILE_VERSION, DB_PATH, DRAFT_MCP_LOG_PATH,
                        INDEX_META_PATH, RUNTIME_RETRIEVER, SCHEMA_DOCS_PATH,
                        SCHEMA_DOCS_VERSION, SQL_TIMEOUT_S)
from src.eval.bank import HYBRID_SUBTYPE_GOLD_BOUNDS, ROUTES
from src.eval.explore import (LEVEL_WINDOWS, SURVIVOR_CEILING,
                              SURVIVOR_WINDOWS,
                              profile_sections as _profile_sections,
                              verify_map_entry, verify_payload)
from src.llm import fingerprint
from src.retrieval.sql_path import SqlGuardrailError, validate_sql

ROW_CAP_DEFAULT = 50
ROW_CAP_CEILING = 200  # hard ceiling regardless of the row_cap argument

# search_corpus: registry vocabulary (src.retrieval.registry.RETRIEVERS),
# plus "pooled" = all four. k counts distinct PROJECTS per condition; chunks
# are over-fetched so multiple chunks of one project don't eat k slots.
SEARCH_CONDITIONS = ("lexical", "dense", "hybrid", "hybrid_rerank")
SEARCH_K_DEFAULT = 20
SEARCH_K_CEILING = 50
SEARCH_CHUNK_OVERFETCH = 5
SCOPE_CEILING = 500  # scope_project_ids cap: filters must stay enumerable

PROJECT_TEXT_CAP = 10  # get_project_text ids per call

# get_project_text field selection. A full payload averages ~8.1k chars per
# project, of which workPerformed + finalResults are ~48% - the least useful
# half for "does this text actually carry the theme", which is all the
# exploration agent needs. Callers that only need the gist ask for a subset
# (e.g. ["objective", "teaser"], ~2.1k chars); fields=None keeps the full
# payload, so drafting (which reads gold evidence in full) is unchanged.
PROJECT_TEXT_FIELDS = ("acronym", "title", "objective",
                       "report_title", "teaser", "summary",
                       "workPerformed", "finalResults")
_PROJECT_FIELDS = ("acronym", "title", "objective")
# result key -> report_text column, for the report sub-dict
_REPORT_FIELDS = {"report_title": "title", "teaser": "teaser",
                  "summary": "summary", "workPerformed": "workPerformed",
                  "finalResults": "finalResults"}


@dataclass
class ServerConfig:
    db_path: Path
    bank_path: Path
    schema_docs_path: Path
    log_path: Path
    index_meta_path: Path = INDEX_META_PATH
    corpus_profile_path: Path = CORPUS_PROFILE_PATH
    timeout_s: float = SQL_TIMEOUT_S

    @classmethod
    def from_env(cls) -> "ServerConfig":
        def path(env: str, default: Path) -> Path:
            return Path(os.environ.get(env, str(default)))

        return cls(
            db_path=path("HS_DB_PATH", DB_PATH),
            bank_path=path("HS_BANK_PATH", BANK_PATH),
            schema_docs_path=path("HS_SCHEMA_DOCS_PATH", SCHEMA_DOCS_PATH),
            log_path=path("HS_DRAFT_MCP_LOG_PATH", DRAFT_MCP_LOG_PATH),
            index_meta_path=path("HS_INDEX_META_PATH", INDEX_META_PATH),
            corpus_profile_path=path("HS_CORPUS_PROFILE_PATH",
                                     CORPUS_PROFILE_PATH))


cfg = ServerConfig.from_env()


# Set by @traced on entry to every tool, read by _log on the way out, so each
# logged call carries how long it took without every tool having to time
# itself at each of its several exit points. Safe as a module global: the
# stdio server handles one call at a time over a single DuckDB connection.
_call_start: float | None = None


def traced(fn):
    """Stamp a tool's start time so its log line can carry `ms`."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        global _call_start
        _call_start = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            _call_start = None
    return wrapper


def _log(tool: str, **fields) -> None:
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": tool, **fields}
    if _call_start is not None:
        entry["ms"] = round((time.perf_counter() - _call_start) * 1000)
    cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg.log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(cfg.db_path), read_only=True)


def _jsonable(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.hex()
    return v


def _execute(con: duckdb.DuckDBPyConnection, sql: str,
             fetch: int | None = None) -> tuple[list[str], list[tuple]]:
    """Run one statement with the project's interrupt-based timeout."""
    timer = threading.Timer(cfg.timeout_s, con.interrupt)
    timer.start()
    try:
        cur = con.execute(sql)
        rows = cur.fetchmany(fetch) if fetch is not None else cur.fetchall()
        return [d[0] for d in cur.description], rows
    finally:
        timer.cancel()


@traced
def run_sql(query: str, row_cap: int = ROW_CAP_DEFAULT) -> dict:
    """Execute a single read-only SELECT against the CORDIS DuckDB.

    Returns {columns, rows, row_count, truncated}; row_count is the true
    result size before capping (null only if counting itself timed out).
    Rejected or failing SQL returns {"error": "..."} instead - never a tool
    failure - so broken queries can be reasoned about.
    """
    try:
        sql = validate_sql(query)
    except SqlGuardrailError as e:
        error = f"guardrail: {e}"
        _log("run_sql", query=query, ok=False, error=error)
        return {"error": error}

    cap = max(1, min(int(row_cap), ROW_CAP_CEILING))
    con = _connect()
    try:
        try:
            columns, rows = _execute(con, sql, fetch=cap + 1)
        except duckdb.InterruptException:
            error = f"timeout: query exceeded {cfg.timeout_s}s"
            _log("run_sql", query=sql, ok=False, error=error)
            return {"error": error}
        except duckdb.Error as e:
            error = f"{type(e).__name__}: {e}"
            _log("run_sql", query=sql, ok=False, error=error)
            return {"error": error}

        truncated = len(rows) > cap
        rows = rows[:cap]
        if truncated:
            # True pre-cap size; a plain SELECT/WITH always nests cleanly.
            try:
                _, count_rows = _execute(
                    con, f"SELECT COUNT(*) FROM ({sql}) AS _q")
                row_count = count_rows[0][0]
            except duckdb.Error:
                row_count = None
        else:
            row_count = len(rows)
    finally:
        con.close()

    result = {"columns": columns,
              "rows": [[_jsonable(v) for v in row] for row in rows],
              "row_count": row_count, "truncated": truncated}
    _log("run_sql", query=sql, ok=True, row_count=row_count,
         rows_returned=len(rows), truncated=truncated)
    return result


@traced
def get_schema_docs() -> dict:
    """Return schema_docs.md verbatim plus its version label and content
    hash, so drafted questions can record what they were authored against."""
    text = cfg.schema_docs_path.read_text(encoding="utf-8")
    content_hash = fingerprint(text)
    _log("get_schema_docs", version=SCHEMA_DOCS_VERSION,
         content_hash=content_hash)
    return {"markdown": text, "version": SCHEMA_DOCS_VERSION,
            "content_hash": content_hash}


@traced
def get_bank_questions(route: str) -> dict:
    """List existing bank questions for one route - id, text, level, subtype
    only (enough for near-duplicate avoidance, nothing that tempts copying
    reference answers). Accepts pre-migration banks where level is still
    called complexity and subtype does not exist yet."""
    if route not in ROUTES:
        error = f"unknown route {route!r}; valid routes: {', '.join(ROUTES)}"
        _log("get_bank_questions", route=route, ok=False, error=error)
        return {"error": error}
    if not cfg.bank_path.exists():
        error = f"bank file not found: {cfg.bank_path}"
        _log("get_bank_questions", route=route, ok=False, error=error)
        return {"error": error}

    questions = []
    for line in cfg.bank_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if obj.get("expected_route") != route:
            continue
        questions.append({
            "question_id": obj.get("question_id"),
            "text": obj.get("text"),
            "level": obj.get("level", obj.get("complexity")),
            "subtype": obj.get("subtype"),
        })
    _log("get_bank_questions", route=route, ok=True, n=len(questions))
    return {"route": route, "questions": questions}


@traced
def get_corpus_profile(section: str | None = None) -> dict:
    """Return corpus_profile.md - whole file, or one H2 section by key
    (kebab-cased heading, e.g. "vector", "coverage-ledger"). The version
    label and content hash are ALWAYS of the full file, so provenance is
    identical no matter how much a consumer read. A missing file (profile
    not built yet) or unknown section comes back as {"error": ...}."""
    if not cfg.corpus_profile_path.exists():
        error = (f"corpus profile not found: {cfg.corpus_profile_path} - "
                 "not built yet (/explore-corpus writes it)")
        _log("get_corpus_profile", section=section, ok=False, error=error)
        return {"error": error}
    text = cfg.corpus_profile_path.read_text(encoding="utf-8")
    content_hash = fingerprint(text)
    sections = _profile_sections(text)
    if section is None:
        markdown = text
    elif section in sections:
        markdown = sections[section]
    else:
        available = ", ".join(sections) if sections else "(no H2 headings)"
        error = f"unknown section {section!r}; available: {available}"
        _log("get_corpus_profile", section=section, ok=False, error=error)
        return {"error": error}
    _log("get_corpus_profile", section=section, ok=True,
         version=CORPUS_PROFILE_VERSION, content_hash=content_hash)
    return {"markdown": markdown, "section": section,
            "sections": list(sections), "version": CORPUS_PROFILE_VERSION,
            "content_hash": content_hash}


# Retrievers are cached for the server-process lifetime: the FAISS index
# (190k vectors) must load once, not per call. Construction is lazy so the
# SQL-only tools never touch the retrieval stack or its servers.
_retriever_cache: dict[str, object] = {}


def _get_retriever(name: str):
    if name in _retriever_cache:
        return _retriever_cache[name]
    # Imports stay local so importing this module (tests, SQL-only use)
    # never pulls the FAISS/langchain stack.
    from src.retrieval.lexical import LexicalRetriever
    from src.retrieval.registry import build_retriever
    from src.retrieval.vector_search import VectorSearcher

    if name == "lexical":
        retriever = LexicalRetriever(db_path=cfg.db_path)
    elif name == "dense":
        retriever = VectorSearcher(db_path=cfg.db_path,
                                   meta_path=cfg.index_meta_path)
    else:
        retriever = build_retriever(name, lexical=_get_retriever("lexical"),
                                    dense=_get_retriever("dense"))
    _retriever_cache[name] = retriever
    return retriever


def _index_meta() -> dict:
    """Dense-index identity, recorded per authored question (the analog of
    schema_docs_hash): what index the gold labels were verified against."""
    raw = cfg.index_meta_path.read_text(encoding="utf-8")
    meta = json.loads(raw)
    return {"embedding_model": meta.get("embedding_model"),
            "n_vectors": meta.get("n_vectors"),
            "built_at": meta.get("built_at"),
            "content_hash": fingerprint(raw)}


def _first_k_projects(hits, k: int) -> list:
    """Collapse a best-first chunk list to its first k distinct projects."""
    out, seen = [], set()
    for hit in hits:
        if hit.project_id in seen:
            continue
        seen.add(hit.project_id)
        out.append(hit)
        if len(out) == k:
            break
    return out


@traced
def search_corpus(query: str, condition: str = RUNTIME_RETRIEVER,
                  k: int = SEARCH_K_DEFAULT,
                  scope_project_ids: list[int] | None = None,
                  snippet_chars: int | None = None) -> dict:
    """Run retrieval over the chunk corpus, returning PROJECT-level rankings.

    condition is one of lexical|dense|hybrid|hybrid_rerank, or "pooled" (all
    four at once). The default is config.RUNTIME_RETRIEVER - the stack the
    system actually answers with - because that is what an ordinary
    retrievability or discrimination check should ask about.

    PASS condition="pooled" EXPLICITLY for the two jobs that need every
    condition's view at once, and do not economise on them:
      - labelling gold_project_ids ("label the union of all retrieval
        conditions' top-k"). All 51 gold-labelled bank entries were pooled
        over four conditions; a narrower gold set would make the bank two
        instruments instead of one.
      - proving ABSENCE for an adversarial question. Absence under one
        condition is a weaker claim than absence under four, and it is the
        claim the study leads with.

    k (capped at 50) counts distinct projects per condition. Each project
    carries its per-condition ranks (null = outside that condition's top-k)
    and the text of its best-ranked chunk. scope_project_ids restricts
    the search to those projects (hybrid-route authoring). Results include
    index_meta so authored questions can record the index identity.

    snippet_chars (optional) caps each best_chunk's text. A full chunk
    averages ~1,437 chars and rankings rarely need it: pass 0 for a liveness
    probe (ranks only, no text at all), ~400 for a discrimination sweep over
    rankings, ~600 for triage where borderline candidates get a full
    get_project_text read anyway. Omit it only when you will actually read
    the chunks. `truncated` reports what was cut, in get_project_text's
    shape. Omitted = full text, unchanged behaviour.

    Failures - embed/rerank server down, missing FTS or FAISS index - come
    back as {"error": ...}, never a tool failure. In pooled mode ANY
    condition failing fails the whole call: partial pooling would silently
    bias gold labels toward the conditions that happened to be up.
    """
    def fail(error: str) -> dict:
        _log("search_corpus", query=query, condition=condition, ok=False,
             error=error)
        return {"error": error}

    if not isinstance(query, str) or not query.strip():
        return fail("query must be a non-empty string")
    if condition not in (*SEARCH_CONDITIONS, "pooled"):
        return fail(f"unknown condition {condition!r}; valid: "
                    f"{', '.join((*SEARCH_CONDITIONS, 'pooled'))}")
    if scope_project_ids is not None:
        if (not isinstance(scope_project_ids, list) or not scope_project_ids
                or any(isinstance(i, bool) or not isinstance(i, int)
                       for i in scope_project_ids)):
            return fail("scope_project_ids must be a non-empty list of "
                        "integers when given")
        if len(scope_project_ids) > SCOPE_CEILING:
            return fail(f"scope_project_ids exceeds the {SCOPE_CEILING}-id "
                        f"ceiling ({len(scope_project_ids)} given) - tighten "
                        "the filter; survivor sets must stay enumerable")
    if snippet_chars is not None and (isinstance(snippet_chars, bool)
                                      or not isinstance(snippet_chars, int)
                                      or snippet_chars < 0):
        return fail("snippet_chars must be a non-negative integer when given")
    k = max(1, min(int(k), SEARCH_K_CEILING))
    try:
        index_meta = _index_meta()
    except (OSError, json.JSONDecodeError) as e:
        return fail(f"index_meta unreadable ({e}) - cannot record the index "
                    "identity gold labels are verified against")

    conditions = SEARCH_CONDITIONS if condition == "pooled" else (condition,)
    scope = set(scope_project_ids) if scope_project_ids is not None else None
    per_condition: dict[str, list] = {}
    for cond in conditions:
        try:
            hits = _get_retriever(cond).search(
                query, k=k * SEARCH_CHUNK_OVERFETCH, project_ids=scope)
        except Exception as e:  # noqa: BLE001 - any stack failure must be a result
            return fail(f"{cond}: {type(e).__name__}: {e}")
        per_condition[cond] = _first_k_projects(hits, k)

    projects: dict[int, dict] = {}
    best_rank: dict[int, int] = {}
    for cond in conditions:
        for rank, hit in enumerate(per_condition[cond], 1):
            entry = projects.get(hit.project_id)
            if entry is None:
                entry = {"project_id": hit.project_id,
                         "acronym": hit.acronym, "title": hit.title,
                         "ranks": {c: None for c in conditions},
                         "best_chunk": None}
                projects[hit.project_id] = entry
            entry["ranks"][cond] = rank
            # Strict < keeps the earliest condition on rank ties, so the
            # chosen best_chunk is deterministic.
            if rank < best_rank.get(hit.project_id, k + 1):
                best_rank[hit.project_id] = rank
                entry["best_chunk"] = {
                    "condition": cond, "chunk_id": hit.chunk_id,
                    "source": hit.source, "section": hit.section,
                    "text": hit.text}

    ordered = sorted(projects.values(), key=lambda p: (
        best_rank[p["project_id"]],
        -sum(r is not None for r in p["ranks"].values()),
        p["project_id"]))
    counts = {c: len(per_condition[c]) for c in conditions}

    # snippet_chars=None keeps the historical output byte-identical (no
    # `truncated` key at all); 0 drops the text key entirely; N truncates.
    truncated = None
    if snippet_chars is not None:
        cut = chunks_cut = 0
        for entry in ordered:
            chunk = entry["best_chunk"]
            text = chunk.get("text")
            if not isinstance(text, str):
                continue
            if snippet_chars == 0:
                cut += len(text)
                chunks_cut += 1
                del chunk["text"]
            elif len(text) > snippet_chars:
                cut += len(text) - snippet_chars
                chunks_cut += 1
                chunk["text"] = text[:snippet_chars]
        if chunks_cut:
            truncated = {"snippet_chars": snippet_chars,
                         "chars_dropped": cut,
                         "chunks_truncated": chunks_cut}

    _log("search_corpus", query=query, condition=condition, k=k, ok=True,
         scope_size=len(scope) if scope is not None else None,
         snippet_chars=snippet_chars,
         per_condition_project_counts=counts, pooled_count=len(ordered))
    result = {"query": query, "condition": condition, "k": k,
              "scope_size": len(scope) if scope is not None else None,
              "index_meta": index_meta,
              "per_condition_project_counts": counts,
              "projects": ordered}
    if snippet_chars is not None:
        result["truncated"] = truncated
    return result


def _waterfill_cap(lengths: list[int], budget: int) -> int:
    """Largest per-field char cap c with sum(min(len, c)) <= budget.

    Truncates the longest fields first and leaves short ones whole, so a
    budget is spent on breadth rather than on one runaway report.
    """
    lo, hi = 0, max(lengths)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if sum(min(n, mid) for n in lengths) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return lo


@traced
def get_project_text(project_ids: list[int],
                     fields: list[str] | None = None,
                     max_chars: int | None = None) -> dict:
    """Free-text fields for up to 10 projects: acronym, title and objective
    from `project`, plus the published report sections (title, teaser,
    summary, workPerformed, finalResults) when a report exists.

    The gold-evidence channel for vector/hybrid authoring: grounding a seed
    project, judging pooled candidates in or out of gold_project_ids, and
    writing reference answers from gold evidence only. Ids not in the
    database are listed under `missing`, not errors.

    fields (optional) selects a subset and keeps the payload small - one of
    acronym | title | objective | report_title | teaser | summary |
    workPerformed | finalResults. Omit it for the full payload (~8.1k chars
    per project). To confirm what a project is ABOUT, ["objective",
    "teaser"] costs ~2.1k chars and carries the theme; workPerformed and
    finalResults are ~48% of a full payload and are only worth pulling when
    you need the results themselves.

    max_chars (optional) is a ceiling on total text returned by this call.
    Over budget, the longest fields are truncated first (short ones stay
    whole) and `truncated` reports what was cut.
    """
    def fail(error: str) -> dict:
        _log("get_project_text", project_ids=project_ids, ok=False,
             error=error)
        return {"error": error}

    if (not isinstance(project_ids, list) or not project_ids
            or any(isinstance(i, bool) or not isinstance(i, int)
                   for i in project_ids)):
        return fail("project_ids must be a non-empty list of integers")
    ids = list(dict.fromkeys(project_ids))
    if len(ids) > PROJECT_TEXT_CAP:
        return fail(f"at most {PROJECT_TEXT_CAP} project_ids per call, "
                    f"got {len(ids)}")
    if fields is not None:
        if not isinstance(fields, list) or not fields:
            return fail("fields must be a non-empty list of strings when "
                        f"given; valid: {', '.join(PROJECT_TEXT_FIELDS)}")
        unknown = [f for f in fields if f not in PROJECT_TEXT_FIELDS]
        if unknown:
            return fail(f"unknown fields {unknown}; valid: "
                        f"{', '.join(PROJECT_TEXT_FIELDS)}")
        fields = list(dict.fromkeys(fields))
    if max_chars is not None and (isinstance(max_chars, bool)
                                  or not isinstance(max_chars, int)
                                  or max_chars < 1):
        return fail("max_chars must be a positive integer when given")

    placeholders = ", ".join("?" for _ in ids)
    con = _connect()
    try:
        try:
            proj_rows = con.execute(
                "SELECT id, acronym, title, objective FROM project "
                f"WHERE id IN ({placeholders})", ids).fetchall()
            report_rows = con.execute(
                "SELECT projectID, title, teaser, summary, workPerformed, "
                "finalResults FROM report_text "
                f"WHERE projectID IN ({placeholders})", ids).fetchall()
        except duckdb.Error as e:
            return fail(f"{type(e).__name__}: {e}")
    finally:
        con.close()

    reports: dict[int, dict] = {}
    for pid, title, teaser, summary, work, final in report_rows:
        reports.setdefault(pid, {
            "title": title, "teaser": teaser, "summary": summary,
            "workPerformed": work, "finalResults": final})
    found = {pid: (acronym, title, objective)
             for pid, acronym, title, objective in proj_rows}

    # fields=None keeps the historical shape exactly; a subset drops the
    # unasked keys, and the `report` sub-dict disappears entirely when no
    # report field was asked for.
    want_project = [f for f in _PROJECT_FIELDS
                    if fields is None or f in fields]
    want_report = [f for f in _REPORT_FIELDS
                   if fields is None or f in fields]
    result_projects = []
    for pid in ids:
        if pid not in found:
            continue
        row = dict(zip(_PROJECT_FIELDS, found[pid]))
        entry: dict = {"project_id": pid}
        entry.update({f: row[f] for f in want_project})
        if want_report:
            report = reports.get(pid)
            entry["report"] = None if report is None else {
                _REPORT_FIELDS[f]: report[_REPORT_FIELDS[f]]
                for f in want_report}
        result_projects.append(entry)

    truncated = None
    if max_chars is not None:
        # (container, key) for every text value actually being returned.
        slots = []
        for entry in result_projects:
            for key, value in entry.items():
                if isinstance(value, str):
                    slots.append((entry, key))
            report = entry.get("report")
            if isinstance(report, dict):
                slots.extend((report, key) for key, value in report.items()
                             if isinstance(value, str))
        lengths = [len(box[key]) for box, key in slots]
        total = sum(lengths)
        if lengths and total > max_chars:
            cap = _waterfill_cap(lengths, max_chars)
            cut = 0
            for box, key in slots:
                if len(box[key]) > cap:
                    cut += len(box[key]) - cap
                    box[key] = box[key][:cap]
            truncated = {"max_chars": max_chars, "field_char_cap": cap,
                         "chars_dropped": cut,
                         "fields_truncated": sum(1 for n in lengths
                                                 if n > cap)}

    missing = [pid for pid in ids if pid not in found]
    _log("get_project_text", project_ids=ids, ok=True,
         found=len(result_projects), missing=len(missing),
         fields=fields, max_chars=max_chars, truncated=truncated)
    return {"projects": result_projects, "missing": missing,
            "truncated": truncated}


# --- precheck_record: the drafter's deterministic self-gate ---------------
#
# Everything a drafted record claims that a machine can settle by execution.
# It lives on the MCP server rather than the CLI because it runs INSIDE the
# drafter's own loop, and the drafter has no shell and must stay read-only by
# construction. Schema validation is deliberately NOT here - that is
# `python -m src.cli validate-record`, run by the orchestrator at slot close.

# The survivor ceiling (a set that cannot be enumerated cannot be
# adjudicated) is SURVIVOR_CEILING, imported from src.eval.explore so the
# drafter's gate and the explorer's gate can never disagree on the number.
# Accepted spellings of the project-id column in a filter_sql result; the
# first matching column wins, else column 0.
_ID_COLUMN_NAMES = ("id", "project_id", "projectid")


def _precheck_execute(con, sql: str, fetch: int) -> tuple[list[str], list[tuple], str | None]:
    """(columns, rows, error) - guardrail and DuckDB failures as strings."""
    try:
        checked = validate_sql(sql)
    except SqlGuardrailError as e:
        return [], [], f"guardrail: {e}"
    try:
        columns, rows = _execute(con, checked, fetch=fetch)
    except duckdb.InterruptException:
        return [], [], f"timeout: query exceeded {cfg.timeout_s}s"
    except duckdb.Error as e:
        return [], [], f"{type(e).__name__}: {e}"
    return columns, rows, None


def _id_column(columns: list[str]) -> int:
    for i, name in enumerate(columns):
        if name.lower() in _ID_COLUMN_NAMES:
            return i
    return 0


@traced
def precheck_record(record: dict | str) -> dict:
    """Re-execute a drafted bank record's factual claims. Read-only.

    Checks, each PASS | FAIL | N/A with a one-line detail:

      GOLD-SQL         gold_sql executes and returns a non-empty result
      ANSWER-COLUMNS   every answer_column appears in that result
      GOLD-TEXT        every gold_project_id exists and has stored text
      FILTER-SURVIVORS filter_sql re-executes to exactly the recorded
                       survivor_ids, and the set is enumerable (<= 200)
      SURVIVOR-WINDOW  the LIVE survivor count sits inside the hybrid
                       subtype's drafting window (WARN, never FAIL: the
                       windows are guidance with a tilde, not law - but a
                       count outside them is worth the critic's and the
                       judge's attention)
      GOLD-SUBSET      gold_project_ids is a subset of the live survivors
      GOLD-BOUNDS      |gold_project_ids| against the route's bound - the
                       hybrid subtype's gold bound, or the vector level
                       window (vector level is DEFINED by the count)
      SCHEMA-DOCS      recorded schema_docs hashes match the live document

    `ok` is true iff nothing FAILed (a WARN never gates). A drafter must not emit a package until
    it does. Malformed input comes back as {"error": ...}, matching run_sql's
    contract; a failing check is a RESULT, not an error - the whole point is
    for the drafter to read it and fix the draft.

    This is a gate for a record being authored RIGHT NOW, which is why a
    schema_docs hash that is not the live one FAILs: the drafter called
    get_schema_docs this pass, so a different hash means it recorded the wrong
    thing. Pointed at older bank entries instead, SCHEMA-DOCS will FAIL on
    every entry authored against an earlier version - that is provenance, not
    a defect (bank.py deliberately never re-checks the field), so read those
    failures accordingly.
    """
    def fail(error: str) -> dict:
        _log("precheck_record", ok=False, error=error)
        return {"error": error}

    if isinstance(record, str):
        try:
            record = json.loads(record)
        except json.JSONDecodeError as e:
            return fail(f"record is not valid JSON ({e})")
    if not isinstance(record, dict):
        return fail("record must be a JSON object (the bank entry)")
    qid = record.get("question_id")
    if not isinstance(qid, str) or not qid.strip():
        return fail("record must carry a non-empty question_id")

    try:
        live_docs_hash = fingerprint(
            cfg.schema_docs_path.read_text(encoding="utf-8"))
    except OSError as e:
        return fail(f"schema_docs unreadable ({e})")

    checks: list[dict] = []

    def check(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    route, level = record.get("expected_route"), record.get("level")
    ladder = level in ("L1", "L2", "L3")
    sql_ladder = route == "sql" and ladder
    hybrid_ladder = route == "hybrid" and ladder

    con = _connect()
    try:
        # --- GOLD-SQL + ANSWER-COLUMNS ---
        gold_sql = record.get("gold_sql")
        gold_columns: list[str] | None = None
        if not isinstance(gold_sql, str) or not gold_sql.strip():
            check("GOLD-SQL", "FAIL" if sql_ladder else "N/A",
                  "no gold_sql recorded"
                  + (" - a SQL ladder entry requires one" if sql_ladder
                     else ""))
        else:
            columns, rows, error = _precheck_execute(
                con, gold_sql, fetch=ROW_CAP_CEILING + 1)
            if error is not None:
                check("GOLD-SQL", "FAIL", f"gold_sql did not execute: {error}")
            elif not rows:
                check("GOLD-SQL", "FAIL",
                      "gold_sql executed and returned 0 rows - an empty gold "
                      "answer is a zero-match ADV question wearing a ladder "
                      "label")
            else:
                gold_columns = columns
                more = "+" if len(rows) > ROW_CAP_CEILING else ""
                check("GOLD-SQL", "PASS",
                      f"executed, {len(rows)}{more} row(s), "
                      f"columns {columns}")

        answer_columns = record.get("answer_columns")
        if not isinstance(answer_columns, list) or not answer_columns:
            check("ANSWER-COLUMNS", "N/A", "no answer_columns recorded")
        elif gold_columns is None:
            check("ANSWER-COLUMNS", "N/A",
                  "gold_sql produced no result to check against")
        else:
            have = {str(c).lower() for c in gold_columns}
            missing = [c for c in answer_columns
                       if str(c).lower() not in have]
            if missing:
                check("ANSWER-COLUMNS", "FAIL",
                      f"answer_columns {missing} absent from the gold result "
                      f"columns {gold_columns}")
            else:
                check("ANSWER-COLUMNS", "PASS",
                      f"all {len(answer_columns)} present in the gold result")

        # --- GOLD-TEXT ---
        gold_ids = record.get("gold_project_ids")
        if not isinstance(gold_ids, list):
            check("GOLD-TEXT", "N/A", "no gold_project_ids recorded")
        elif not gold_ids:
            check("GOLD-TEXT", "N/A",
                  "gold_project_ids is empty - absence is the gold label")
        elif any(isinstance(i, bool) or not isinstance(i, int)
                 for i in gold_ids):
            check("GOLD-TEXT", "FAIL",
                  "gold_project_ids must be integers")
        else:
            placeholders = ", ".join("?" for _ in gold_ids)
            try:
                rows = con.execute(
                    "SELECT p.id, (COALESCE(NULLIF(TRIM(p.objective), ''),"
                    " NULLIF(TRIM(r.summary), ''),"
                    " NULLIF(TRIM(r.teaser), ''),"
                    " NULLIF(TRIM(r.workPerformed), ''),"
                    " NULLIF(TRIM(r.finalResults), '')) IS NOT NULL) "
                    "FROM project p LEFT JOIN report_text r "
                    "ON r.projectID = p.id "
                    f"WHERE p.id IN ({placeholders})", gold_ids).fetchall()
            except duckdb.Error as e:
                check("GOLD-TEXT", "FAIL",
                      f"gold text lookup failed: {type(e).__name__}: {e}")
            else:
                found = {pid: bool(has_text) for pid, has_text in rows}
                absent = [i for i in gold_ids if i not in found]
                textless = sorted(i for i, ok in found.items() if not ok)
                if absent or textless:
                    check("GOLD-TEXT", "FAIL",
                          f"not in the database: {absent or 'none'}; "
                          f"no stored text: {textless or 'none'}")
                else:
                    check("GOLD-TEXT", "PASS",
                          f"all {len(gold_ids)} gold project(s) exist and "
                          "carry text")

        # --- FILTER-SURVIVORS + GOLD-SUBSET ---
        filter_evidence = record.get("filter_evidence")
        live_survivors: set[int] | None = None
        if not isinstance(filter_evidence, dict):
            detail = ("no filter_evidence recorded"
                      + (" - a hybrid ladder entry requires one"
                         if hybrid_ladder else ""))
            check("FILTER-SURVIVORS", "FAIL" if hybrid_ladder else "N/A",
                  detail)
        else:
            filter_sql = filter_evidence.get("filter_sql")
            recorded = filter_evidence.get("survivor_ids")
            if not isinstance(filter_sql, str) or not filter_sql.strip():
                check("FILTER-SURVIVORS", "FAIL",
                      "filter_evidence.filter_sql is missing or empty")
            elif (not isinstance(recorded, list)
                  or any(isinstance(i, bool) or not isinstance(i, int)
                         for i in recorded)):
                check("FILTER-SURVIVORS", "FAIL",
                      "filter_evidence.survivor_ids must be a list of "
                      "integers to compare against")
            else:
                columns, rows, error = _precheck_execute(
                    con, filter_sql, fetch=SURVIVOR_CEILING + 1)
                if error is not None:
                    check("FILTER-SURVIVORS", "FAIL",
                          f"filter_sql did not execute: {error}")
                elif len(rows) > SURVIVOR_CEILING:
                    check("FILTER-SURVIVORS", "FAIL",
                          f"filter_sql returns more than {SURVIVOR_CEILING} "
                          "rows - the survivor set must stay enumerable; "
                          "tighten the filter")
                else:
                    col = _id_column(columns)
                    live_survivors = {r[col] for r in rows}
                    missing = sorted(set(recorded) - live_survivors)
                    extra = sorted(live_survivors - set(recorded))
                    if missing or extra:
                        check("FILTER-SURVIVORS", "FAIL",
                              f"live filter_sql ({columns[col]!r}, "
                              f"{len(live_survivors)} ids) does not match the "
                              f"recorded {len(recorded)}: recorded-but-gone "
                              f"{missing or 'none'}, live-but-unrecorded "
                              f"{extra or 'none'}")
                    else:
                        check("FILTER-SURVIVORS", "PASS",
                              f"{len(live_survivors)} survivor(s), identical "
                              "to survivor_ids")

        # --- SURVIVOR-WINDOW (WARN, never FAIL - the skill writes the
        # windows with a tilde, so a count outside one is a flag for the
        # critic and judge, not a forbidden state) ---
        subtype = record.get("subtype")
        if live_survivors is None:
            check("SURVIVOR-WINDOW", "N/A",
                  "no live survivor set to measure")
        elif subtype not in SURVIVOR_WINDOWS:
            check("SURVIVOR-WINDOW", "N/A",
                  f"subtype {subtype!r} has no survivor window")
        else:
            low, high = SURVIVOR_WINDOWS[subtype]
            n_live = len(live_survivors)
            if low <= n_live <= high:
                check("SURVIVOR-WINDOW", "PASS",
                      f"{n_live} live survivor(s), inside {subtype}'s "
                      f"{low}-{high} window")
            else:
                check("SURVIVOR-WINDOW", "WARN",
                      f"{n_live} live survivor(s) but {subtype} drafts at "
                      f"{low}-{high} - not a gate, but the cell may no "
                      "longer fit its own filter; say why it still does")

        if live_survivors is None:
            check("GOLD-SUBSET", "N/A",
                  "no live survivor set to compare gold against")
        elif not isinstance(gold_ids, list) or not gold_ids:
            check("GOLD-SUBSET", "N/A", "no gold_project_ids to place")
        else:
            outside = sorted(set(gold_ids) - live_survivors)
            if outside:
                check("GOLD-SUBSET", "FAIL",
                      f"gold {outside} lies outside the live survivor set - "
                      "gold outside the filter is a contradiction")
            else:
                check("GOLD-SUBSET", "PASS",
                      f"all {len(gold_ids)} gold id(s) are survivors")

        # --- GOLD-BOUNDS ---
        # Hybrid bounds hang off the SUBTYPE (bank.py's rule: filter-compare
        # is L3 with |gold| in [2,4], so a level-based bound would fail the
        # live hyb-03). The vector LEVEL_WINDOWS apply to vector ONLY, where
        # level is DEFINED by |gold_project_ids|. validate-record enforces
        # the same rule at slot close; checking it here moves the failure
        # inside the drafter's own loop, where it is cheap.
        gold_countable = (isinstance(gold_ids, list) and gold_ids
                          and not any(isinstance(i, bool)
                                      or not isinstance(i, int)
                                      for i in gold_ids))
        if not gold_countable:
            check("GOLD-BOUNDS", "N/A", "no gold_project_ids to bound")
        elif hybrid_ladder and subtype in HYBRID_SUBTYPE_GOLD_BOUNDS:
            lo, hi = HYBRID_SUBTYPE_GOLD_BOUNDS[subtype]
            n = len(gold_ids)
            if n < lo or (hi is not None and n > hi):
                want = f"== {lo}" if lo == hi else (
                    f">= {lo}" if hi is None else f"in [{lo},{hi}]")
                check("GOLD-BOUNDS", "FAIL",
                      f"hybrid subtype {subtype!r} requires "
                      f"|gold_project_ids| {want}, got {n}")
            else:
                check("GOLD-BOUNDS", "PASS",
                      f"|gold| {n} fits {subtype}'s bound")
        elif route == "vector" and level in LEVEL_WINDOWS:
            lo, hi = LEVEL_WINDOWS[level]
            n = len(gold_ids)
            if n < lo or (hi is not None and n > hi):
                want = f"== {lo}" if lo == hi else (
                    f">= {lo}" if hi is None else f"in [{lo},{hi}]")
                check("GOLD-BOUNDS", "FAIL",
                      f"vector {level} requires |gold_project_ids| {want}, "
                      f"got {n} - vector level is DEFINED by the count")
            else:
                check("GOLD-BOUNDS", "PASS",
                      f"|gold| {n} fits vector {level}")
        else:
            check("GOLD-BOUNDS", "N/A",
                  "no bound applies to this route/subtype")
    finally:
        con.close()

    # --- SCHEMA-DOCS ---
    recorded_hashes = []
    if isinstance(record.get("schema_docs_hash"), str):
        recorded_hashes.append(("schema_docs_hash",
                                record["schema_docs_hash"]))
    if isinstance(record.get("filter_evidence"), dict) and isinstance(
            record["filter_evidence"].get("schema_docs_hash"), str):
        recorded_hashes.append(("filter_evidence.schema_docs_hash",
                                record["filter_evidence"]["schema_docs_hash"]))
    required = sql_ladder or hybrid_ladder
    if not recorded_hashes:
        check("SCHEMA-DOCS", "FAIL" if required else "N/A",
              f"no schema_docs hash recorded (live is {live_docs_hash})"
              + (" - this route requires one" if required else ""))
    else:
        stale = [f"{where}={value}" for where, value in recorded_hashes
                 if value != live_docs_hash]
        if stale:
            check("SCHEMA-DOCS", "FAIL",
                  f"recorded {', '.join(stale)} but the live schema_docs "
                  f"({SCHEMA_DOCS_VERSION}) hashes to {live_docs_hash} - "
                  "record the hash this pass returned")
        else:
            check("SCHEMA-DOCS", "PASS",
                  f"matches live {SCHEMA_DOCS_VERSION} {live_docs_hash}")

    failures = [c["name"] for c in checks if c["status"] == "FAIL"]
    result = {"question_id": qid, "ok": not failures, "checks": checks,
              "failures": failures,
              "schema_docs": {"version": SCHEMA_DOCS_VERSION,
                              "content_hash": live_docs_hash}}
    _log("precheck_record", question_id=qid, ok=not failures,
         failures=failures)
    return result


@traced
def precheck_candidate(candidate: dict | str,
                       bucket: str | None = None) -> dict:
    """Re-execute one exploration candidate's claims. Read-only.

    The corpus-explorer's deterministic self-gate, and the exact same code the
    close-out `verify-evidence` runs over the whole journal - so a candidate
    that passes here cannot fail there. Checks, each PASS | FAIL | N/A:

      EVIDENCE     every evidence.sql executes, is non-empty (unless
                   expect_empty), and every number in key_result reproduces
      ONE-READING  each euroSciVoc term the evidence scopes on is a leaf
                   (one executable reading). A branch term WARNs with its
                   sibling paths attached - usable only if the question
                   names the subtree explicitly. WARN never gates.
      COUNT        satisfying_count / survivor_count reproduce from this
                   payload's own evidence rows
      LEVEL        topic_filter runs corpus-wide and the level is DERIVED
                   from that count. The explorer works one bucket at a time,
                   so its own counts are fenced by the bucket and the question
                   never is - a recommended level that disagrees with the
                   unfenced count fails here
      WINDOW       survivor_count is inside the recommended hybrid subtype's
                   drafting window and under the 200 enumerability ceiling
      SLICE        the candidate's bucket is the one this slice was assigned
      MAP-*        map entries only: `read:` ids exist, carry text and belong
                   to the bucket, `read_first:` names members read before any
                   topic probe, and the prose is not the taxonomy label back

    Pass a candidate block or a map entry; `bucket` is the slice's assigned
    bucket, which turns SLICE and the map checks on. A failing check is a
    RESULT, not an error - the explorer reads it and fixes or drops the
    candidate. Malformed input comes back as {"error": ...}, matching run_sql.

    This is a gate against BIRTH-FAILURES: `hyb-02` (musicology x MSCA-IF)
    burned a full drafter pass on a combo whose numbers already said it was
    unviable. Nothing upstream was checking them.
    """
    def fail(error: str) -> dict:
        _log("precheck_candidate", ok=False, error=error)
        return {"error": error}

    if isinstance(candidate, str):
        try:
            candidate = json.loads(candidate)
        except json.JSONDecodeError as e:
            return fail(f"candidate is not valid JSON ({e})")
    if not isinstance(candidate, dict):
        return fail("candidate must be a JSON object (the candidate block or "
                    "map entry)")

    label = str(candidate.get("id") or candidate.get("bucket") or "candidate")
    buckets = [bucket] if isinstance(bucket, str) and bucket.strip() else []
    is_map_entry = "about" in candidate or "read" in candidate

    con = _connect()
    try:
        checks = verify_payload(con, "candidate", label, candidate, buckets)
        if is_map_entry:
            checks += verify_map_entry(con, "candidate", candidate, buckets)
    finally:
        con.close()

    rendered = [{"name": c.name, "status": c.status, "detail": c.detail}
                for c in checks]
    failures = [c["name"] for c in rendered if c["status"] == "FAIL"]
    result = {"candidate": label, "ok": not failures, "checks": rendered,
              "failures": failures}
    _log("precheck_candidate", candidate=label, ok=not failures,
         failures=failures)
    return result


def main() -> None:
    # SDK import stays local so tests of the tool functions above never
    # depend on it.
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("horizon-scout-draft")
    for fn in (run_sql, get_schema_docs, get_bank_questions,
               search_corpus, get_project_text, get_corpus_profile,
               precheck_record, precheck_candidate):
        server.tool()(fn)
    server.run()  # stdio


if __name__ == "__main__":
    main()
