"""Read-only MCP server for question-bank drafting (M5).

Exposes exactly six tools to the drafting agents (/draft-sql-question, the
vector/hybrid skills) and the exploration agent (/explore-corpus): run_sql,
get_schema_docs, get_bank_questions, search_corpus, get_project_text,
get_corpus_profile. Deliberately minimal - the agent must be able to ground
questions in real data and verify gold labels by execution (SQL routes) or
pooled retrieval (vector/hybrid routes), and nothing more. No write tools:
bank appends are confirmation-gated in the skill layer, outside the MCP.

search_corpus runs the real retrieval stack (lexical | dense | hybrid |
hybrid_rerank, or "pooled" = all four) and returns PROJECT-level rankings -
gold_project_ids are project labels, never chunk labels, and the pooling
protocol ("label the union of all retrieval conditions' top-k") needs every
condition's view at once. get_project_text is the gold-evidence channel:
full objective + report sections for grounding, candidate relevance judging,
and reference writing. Retrieval failures (embed/rerank server down, missing
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
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb

from src.config import (BANK_PATH, CORPUS_PROFILE_PATH,
                        CORPUS_PROFILE_VERSION, DB_PATH, DRAFT_MCP_LOG_PATH,
                        INDEX_META_PATH, SCHEMA_DOCS_PATH,
                        SCHEMA_DOCS_VERSION, SQL_TIMEOUT_S)
from src.eval.bank import ROUTES
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


def _log(tool: str, **fields) -> None:
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": tool, **fields}
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


def get_schema_docs() -> dict:
    """Return schema_docs.md verbatim plus its version label and content
    hash, so drafted questions can record what they were authored against."""
    text = cfg.schema_docs_path.read_text(encoding="utf-8")
    content_hash = fingerprint(text)
    _log("get_schema_docs", version=SCHEMA_DOCS_VERSION,
         content_hash=content_hash)
    return {"markdown": text, "version": SCHEMA_DOCS_VERSION,
            "content_hash": content_hash}


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


def _profile_sections(text: str) -> dict[str, str]:
    """Split corpus_profile.md on H2 headings. Section key = the heading
    text kebab-cased ("## Coverage ledger" -> "coverage-ledger"), so the
    file stays human-readable while keys stay stable to call."""
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


def search_corpus(query: str, condition: str = "pooled",
                  k: int = SEARCH_K_DEFAULT,
                  scope_project_ids: list[int] | None = None) -> dict:
    """Run retrieval over the chunk corpus, returning PROJECT-level rankings.

    condition is one of lexical|dense|hybrid|hybrid_rerank, or "pooled"
    (default): all four at once - the honest-labeling protocol for
    gold_project_ids ("label the union of all retrieval conditions' top-k").
    k (capped at 50) counts distinct projects per condition. Each project
    carries its per-condition ranks (null = outside that condition's top-k)
    and the full text of its best-ranked chunk. scope_project_ids restricts
    the search to those projects (hybrid-route authoring). Results include
    index_meta so authored questions can record the index identity.

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
    _log("search_corpus", query=query, condition=condition, k=k, ok=True,
         scope_size=len(scope) if scope is not None else None,
         per_condition_project_counts=counts, pooled_count=len(ordered))
    return {"query": query, "condition": condition, "k": k,
            "scope_size": len(scope) if scope is not None else None,
            "index_meta": index_meta,
            "per_condition_project_counts": counts,
            "projects": ordered}


def get_project_text(project_ids: list[int]) -> dict:
    """Full free-text fields for up to 10 projects: acronym, title, and
    objective from `project`, plus the published report sections (title,
    teaser, summary, workPerformed, finalResults) when a report exists.

    The gold-evidence channel for vector/hybrid authoring: grounding a seed
    project, judging pooled candidates in or out of gold_project_ids, and
    writing reference answers from gold evidence only. Ids not in the
    database are listed under `missing`, not errors.
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
    result_projects = [
        {"project_id": pid, "acronym": found[pid][0], "title": found[pid][1],
         "objective": found[pid][2], "report": reports.get(pid)}
        for pid in ids if pid in found]
    missing = [pid for pid in ids if pid not in found]
    _log("get_project_text", project_ids=ids, ok=True,
         found=len(result_projects), missing=len(missing))
    return {"projects": result_projects, "missing": missing}


def main() -> None:
    # SDK import stays local so tests of the tool functions above never
    # depend on it.
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("horizon-scout-draft")
    for fn in (run_sql, get_schema_docs, get_bank_questions,
               search_corpus, get_project_text, get_corpus_profile):
        server.tool()(fn)
    server.run()  # stdio


if __name__ == "__main__":
    main()
