"""Scoped retrieval: a structured SQL pre-filter narrows to a set of project
ids, then a semantic search runs WITHIN that scope.

This is the "structured constraint + topic" path (router mode 'scoped'). It is
distinct from hybrid.py, which fuses lexical and dense retrieval (RRF + rerank);
the semantic step here takes any base.Retriever. That swap has now happened:
since 2026-08-03 ask.py passes in config.RUNTIME_RETRIEVER (hybrid_rerank), so
the filter narrows to a set of ids and lexical + dense both search WITHIN it -
HybridRetriever.search forwards project_ids to both legs.

Step 1 reuses SqlPath (guardrails + one-retry) with an id-narrowing system
prompt instead of a copy. Step 2 embeds the FULL original question - the
semantic part dominates embedding space, so we do not try to strip structured
phrases.

Edge policies (decided in the milestone, enforced here):
- SQL fails after its retry  -> degrade to pure vector over everything
  (status="sql_failed"); the caller must state the filter was not applied.
- SQL returns zero ids        -> that IS the answer (status="zero_match");
  never silently widen to unfiltered search.
- SQL returns > WEAK_FILTER   -> proceed; flag weak_filter=true in the trace.
"""

import re
from dataclasses import dataclass, field

from src.config import SCHEMA_DOCS_PATH
from src.retrieval.base import Retriever, SearchResult
from src.retrieval.sql_path import SqlPath

WEAK_FILTER = 5000
NARROW_ROW_LIMIT = 50000  # never truncate a real filter set; just bound pathology

# Subject-matter columns must never appear as a narrowing filter - they encode
# what a project is ABOUT, which is semantic search's job, not the metadata
# filter's. An 8B sometimes writes `topics LIKE '%...%'` anyway; catch it.
_SUBJECT_FILTER_RE = re.compile(
    r"\b(topics|keywords|objective|title|acronym)\b\s*(LIKE|ILIKE|=|~~|SIMILAR)",
    re.IGNORECASE)


_HAS_WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)


def uses_subject_filter(sql: str | None) -> bool:
    return bool(sql and _SUBJECT_FILTER_RE.search(sql))


def filter_note(sql: str | None, n_ids: int) -> str | None:
    """What the generator must be told about the filter that already ran.

    Without this the synthesizer sees prose only, is never told the excerpts
    were pre-selected, and refuses to assert the filter's own predicate - the
    pilot's hybrid route hedged on all seven scoped questions, twice with every
    gold document in the context window (pilot-router-findings.md Part 2 §1).

    The SQL goes in verbatim rather than paraphrased: it is already exact, and
    turning it into prose would cost another generation call.

    None when there is nothing to announce. build_id_narrowing_prompt emits
    `SELECT DISTINCT id FROM project` for a question with no structured
    constraint at all, and "every project satisfies: all projects" is noise that
    would only teach the model to over-assert.
    """
    if not sql or not _HAS_WHERE_RE.search(sql):
        return None
    return ("Structured filter already applied. The excerpts below are drawn "
            f"ONLY from the {n_ids:,} projects returned by this query:\n"
            f"{sql}\n"
            "Every project shown satisfies it.")


def build_id_narrowing_prompt() -> str:
    docs = SCHEMA_DOCS_PATH.read_text(encoding="utf-8")
    return (
        "You translate ONLY the STRUCTURED part of a question about the Horizon "
        "2020 CORDIS database into a DuckDB SQL query returning the matching "
        "project ids. A separate semantic-search system handles what projects "
        "are ABOUT - your job is purely the hard metadata filter.\n\n"
        f"{docs}\n\n"
        "Output contract:\n"
        "- Return ONE DuckDB SELECT of the form `SELECT DISTINCT p.id FROM "
        "project p ...` (join organization o ON o.projectID = p.id when a "
        "country/role/SME/activity filter is needed), selecting ONLY p.id.\n"
        "- You may filter ONLY on these structured dimensions: project.status, "
        "project.fundingScheme, project.startDate/endDate/ecSignatureDate, "
        "project.ecMaxContribution/totalCost, organization.country, "
        "organization.role, organization.sme, organization.activityType, "
        "organization.ecContribution.\n"
        "- CRITICAL: the subject matter / topic / research area of the question "
        "is NOT a structured filter. NEVER add conditions on topics, keywords, "
        "objective, title, or acronym to capture what a project is about. Those "
        "words are handled by semantic search. If you catch yourself writing "
        "`topics LIKE`, `objective LIKE`, or `keywords LIKE` for a subject, "
        "DROP that condition entirely.\n"
        "- CRITICAL: add a condition ONLY if the question EXPLICITLY states it. "
        "Never invent a country, role, funding-amount, date, funding-scheme, or "
        "status filter that the question does not mention. The examples below "
        "show different constraints; do NOT carry a constraint from an example "
        "into your query unless the current question asks for it. When in doubt, "
        "filter LESS - a missing filter is recoverable, an invented one silently "
        "drops the right projects.\n"
        "- If the question has NO structured constraint at all (only a topic), "
        "return exactly `SELECT DISTINCT id FROM project`.\n"
        "- No commentary, no explanation, no markdown fences.\n\n"
        "Examples:\n"
        "Q: Which German-coordinated projects focus on battery recycling?\n"
        "SELECT DISTINCT p.id FROM project p JOIN organization o ON "
        "o.projectID = p.id WHERE o.role = 'coordinator' AND o.country = 'DE'\n"
        "Q: Find AI-for-healthcare projects that received over 5 million euros.\n"
        "SELECT DISTINCT p.id FROM project p WHERE p.ecMaxContribution > 5000000\n"
        "Q: Among ERC Consolidator grants, which developed bioactive coatings "
        "for prosthetic heart valves?\n"
        "SELECT DISTINCT p.id FROM project p WHERE p.fundingScheme = 'ERC-COG'\n"
        "Q: Summarise closed projects about ocean energy.\n"
        "SELECT DISTINCT p.id FROM project p WHERE p.status = 'CLOSED'\n"
        "Q: What MSCA fellowship projects work on marine biology?\n"
        "SELECT DISTINCT p.id FROM project p WHERE p.fundingScheme LIKE 'MSCA%'"
    )


@dataclass
class ScopedResult:
    question: str
    status: str                       # "ok" | "zero_match" | "sql_failed"
    sql: str | None = None
    project_ids: set[int] | None = None
    chunks: list[SearchResult] = field(default_factory=list)
    degraded: str | None = None       # "sql_failed" when the filter was dropped
    weak_filter: bool = False
    # What synthesis must be told the filter did. Set on "ok" only: there is no
    # synthesis on "zero_match", and on "sql_failed" the filter was dropped, so
    # announcing it would be a lie (ask.py prefixes its own note there).
    filter_note: str | None = None
    trace: dict = field(default_factory=dict)


class ScopedRetriever:
    def __init__(self, searcher: Retriever, narrow_sql: SqlPath | None = None):
        self.searcher = searcher
        # Same SqlPath machinery, id-narrowing instruction, no row truncation.
        self.narrow = narrow_sql or SqlPath(
            system_prompt=build_id_narrowing_prompt(),
            row_limit=NARROW_ROW_LIMIT)

    def retrieve(self, question: str, k: int = 10) -> ScopedResult:
        sql_result = self.narrow.ask(question)
        subject_corrected = False

        # Enforce the topic/metadata separation in code, not just the prompt:
        # if the model filtered on a subject-matter column, re-ask once with a
        # pointed reminder and prefer the corrected query.
        if sql_result.ok and uses_subject_filter(sql_result.sql):
            hint = (question + "\n\n(Reminder: do NOT filter on topics, "
                    "keywords, objective, title or acronym. Use only country, "
                    "date, money, funding scheme, role, or status, and drop any "
                    "condition about the subject matter.)")
            retry = self.narrow.ask(hint)
            subject_corrected = True
            if retry.ok and not uses_subject_filter(retry.sql):
                sql_result = retry

        if not sql_result.ok:
            # Policy: SQL failed -> pure vector over everything, filter dropped.
            chunks = self.searcher.search(question, k=k)
            return ScopedResult(
                question=question, status="sql_failed", sql=sql_result.sql,
                project_ids=None, chunks=chunks, degraded="sql_failed",
                trace={"sql_error": sql_result.error,
                       "sql_retried": sql_result.retried,
                       "subject_corrected": subject_corrected,
                       "n_chunks": len(chunks)})

        ids = {int(r[0]) for r in sql_result.rows if r and r[0] is not None}

        if not ids:
            # Policy: zero ids IS the answer. Do not widen.
            return ScopedResult(
                question=question, status="zero_match", sql=sql_result.sql,
                project_ids=set(), chunks=[],
                trace={"n_ids": 0, "sql_retried": sql_result.retried,
                       "subject_corrected": subject_corrected})

        weak = len(ids) > WEAK_FILTER
        chunks = self.searcher.search(question, k=k, project_ids=ids)
        note = filter_note(sql_result.sql, len(ids))
        return ScopedResult(
            question=question, status="ok", sql=sql_result.sql,
            project_ids=ids, chunks=chunks, weak_filter=weak,
            filter_note=note,
            trace={"n_ids": len(ids), "weak_filter": weak,
                   "sql_retried": sql_result.retried,
                   "subject_corrected": subject_corrected,
                   "n_chunks": len(chunks)})
