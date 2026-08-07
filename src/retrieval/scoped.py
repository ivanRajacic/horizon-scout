"""Scoped retrieval: a structured SQL pre-filter narrows to a set of project
ids, then a semantic search runs WITHIN that scope.

This is the "structured constraint + topic" path (router mode 'scoped'). It is
distinct from hybrid.py, which fuses lexical and dense retrieval (RRF + rerank);
the semantic step here takes any base.Retriever. That swap has now happened:
since 2026-08-03 ask.py passes in config.RUNTIME_RETRIEVER (hybrid_rerank), and
HybridRetriever.search forwards project_ids to both legs. The two legs honour
the filter differently: lexical searches within the id set (SQL IN clause);
dense post-filters a global FAISS fetch, and since 2026-08-07 widens that fetch
until k survivors are found - before that, a narrow filter could silently empty
the dense leg (a fixed 2,000-vector slice of 190,248) and the route degraded to
lexical-only.

Since narrow-v3 (2026-08-05) the narrowing step TRANSLATES a constraint list
instead of deciding one. The hyb-filterfix-20260805 ablations showed every
gold-losing filter clause was invented - a status, a country, a fabricated
masterCall pattern that no reading of the question contains - while every
translation of a named constraint was correct. The r3-fields extractor already
produces the list: the router condition reuses its decision's list, and when
the mode is forced (always-hybrid) this class calls the extractor itself, so
both study arms feed the narrowing model identical input. An extraction
FAILURE (no parseable facts) falls back to raw-question narrowing - unknown is
not the same as "no constraints".

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
- SQL returns exactly the row cap -> the id set is TRUNCATED, so it is a
  partial answer to its own filter (a missing DISTINCT over the organization
  join turns 150k rows into the first 50000). Proceed - the ids that came
  back do satisfy the filter - but flag truncated=true, mark the filter weak,
  and drop the completeness sentence from the note so synthesis cannot claim
  the list is everything. Never refuse and never drop the filter: both would
  throw away a filter that worked, only not exhaustively.
- A subject filter survives the corrective re-ask, the filter tests a
  comparison's result with IS NULL, or the first column does not hold project
  ids -> same degrade as sql_failed. A query that was never legal to run must
  not produce a zero_match refusal (2026-08-05: t.topic = 'textiles' matched a
  call-code column, returned 0 rows, and the zero-match policy turned a
  malformed filter into a confident refusal).
- A filter VALUE that does not exist in its closed-set column (a funding
  scheme, status, activity type, role, country, or euroSciVoc term) -> one
  re-ask whose hint names the dead value and shows real candidates; a still-
  dirty re-ask degrades (status="value_not_found") to unfiltered search. The
  gate runs BEFORE the zero-ids branch so a misspelled value never becomes a
  confident refusal; each value is checked against its OWN column only, so a
  genuine empty intersection of all-valid values still refuses, unchanged.
  (2026-08-05: hyb-09 wrote fundingScheme = 'SME Instrument phase 1' where
  the stored code is 'SME-1', and the zero-match policy turned the
  misspelling into a refusal. The prompt already listed the real values -
  deterministic code has to check what the model wrote anyway.)
- An EMPTY constraint list -> unfiltered search with NO narrowing call at
  all. The model asked to write SQL for a question with nothing structured in
  it is the largest source of invented clauses.
"""

import re
from dataclasses import dataclass, field

from src.config import SCHEMA_DOCS_PATH
from src.retrieval.base import Retriever, SearchResult
from src.retrieval.sql_path import SqlPath, blank_strings

WEAK_FILTER = 5000
# Never truncate a real filter set; just bound pathology. replace_limit=True on
# the narrowing SqlPath extends the promise to model-written limits: hyb-13 and
# hyb-15 (2026-08-05) each ended in LIMIT 1 and collapsed the set to one project.
# When the cap DOES bite (2026-08-07), retrieve() flags trace["truncated"] -
# silence there is what let a partial id set be described as complete.
NARROW_ROW_LIMIT = 50000

# narrow-v2 (2026-08-05): the topics call-code line was added after hyb-06/
# hyb-08 filtered t.topic for a subject word, matched nothing and refused; the
# prompt also stopped reporting itself under the SQL route's q2-pilot label.
# narrow-v3 (2026-08-05): the prompt translates a constraint list instead of
# re-reading the question; euroSciVoc joins the allowed dimensions (9 of 11
# hybrid gold filters narrow on it); ORs must be parenthesized (hyb-03 wrote
# `A OR B AND C` and DuckDB's precedence silently dropped the threshold).
# narrow-v4 (2026-08-05): no text changed HERE - schema_docs.md went to sd3
# (all 56 fundingScheme codes), and this prompt pastes the doc in whole, so
# its content moved. Same rule as SQL_PROMPT_VERSION q3-sd3: the label
# follows the doc, because the label is the part a person reads. The value
# gate itself did NOT move it - the gate is code and its correction hints
# ride in the user message.
NARROW_PROMPT_VERSION = "narrow-v4"

# Subject-matter columns must never appear as a narrowing filter - they encode
# what a project is ABOUT, which is semantic search's job, not the metadata
# filter's. An 8B sometimes writes `topics LIKE '%...%'` anyway; catch it.
# `topics?` also catches the topics TABLE's singular `topic` column - a call
# code, so a subject match against it is doubly wrong.
_SUBJECT_FILTER_RE = re.compile(
    r"\b(topics?|keywords|objective|title|acronym)\b\s*(LIKE|ILIKE|=|~~|SIMILAR)",
    re.IGNORECASE)

# The euroscivoc table's columns are the LEGAL classification filter, but its
# `euroSciVocTitle` ends in "Title" only by luck of the schema - what trips the
# subject regex is a bare-column spelling like `euroscivoc.title` or an
# aliased `e.title`. Blank every euroscivoc-qualified column before the
# subject check so legal classification SQL is never routed into the degrade.
_EUROSCIVOC_COL_RE = re.compile(r"\beuroSciVoc\w*", re.IGNORECASE)
_EUROSCIVOC_QUALIFIED_RE = re.compile(r"\beuroscivoc\s*\.\s*\w+", re.IGNORECASE)
_EUROSCIVOC_ALIAS_RE = re.compile(r"\beuroscivoc\s+(?:AS\s+)?(\w+)",
                                  re.IGNORECASE)
_NOT_AN_ALIAS = {"ON", "WHERE", "JOIN", "AS", "GROUP", "ORDER", "LEFT",
                 "RIGHT", "INNER", "OUTER", "CROSS", "USING", "LIMIT", "AND",
                 "OR", "HAVING", "UNION", "SET"}


def _blank_euroscivoc(sql: str) -> str:
    # Aliases come from the ORIGINAL text: the column regex below also eats
    # the table name itself, so finding aliases after blanking finds nothing.
    aliases = [a for a in _EUROSCIVOC_ALIAS_RE.findall(sql)
               if a.upper() not in _NOT_AN_ALIAS]
    out = _EUROSCIVOC_QUALIFIED_RE.sub("EVCOL", sql)
    out = _EUROSCIVOC_COL_RE.sub("EVCOL", out)
    for alias in aliases:
        out = re.sub(rf"\b{re.escape(alias)}\s*\.\s*\w+", "EVCOL", out,
                     flags=re.IGNORECASE)
    return out


_HAS_WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)

# Every euroSciVoc comparison, for the value gate: column, NOT, operator and
# literal are ALL captured, because the gate re-executes the comparison exactly
# as the model wrote it.
#
# 2026-08-07, hyb-08: the gate used to keep only the literal, normalise it to a
# bare term, and look that term up with ILIKE '%term%'. The model had written
# `e.euroSciVocPath LIKE '%/ textiles%'` - a stray space after the slash. The
# gate looked up 'textiles', found 295 rows, called the value alive, and the
# filter it then ran matched 0 projects; the zero-match policy turned the typo
# into a confident refusal. Only the pattern AS WRITTEN can catch that.
_EUROSCIVOC_LITERAL_RE = re.compile(
    r"\b(euroSciVoc(?:Title|Path))\s*(NOT\s+)?(ILIKE|LIKE|=)\s*"
    r"'((?:[^']|'')*)'",
    re.IGNORECASE)
_CANON_EV_COLUMN = {"euroscivoctitle": "euroSciVocTitle",
                    "euroscivocpath": "euroSciVocPath"}

# Every column whose legal values are a closed set, and the table that holds
# it. Dates and money stay out - they are continuous, so no "nonexistent
# value" exists. euroSciVoc is folded into the same gate but keeps its own
# taxonomy lookup (a term may live in Title or anywhere in Path).
GUARDED_VALUE_COLUMNS = {
    "fundingScheme": "project",       # 56 values
    "status": "project",              # 3 values
    "activityType": "organization",   # 5 values
    "role": "organization",           # 5 values
    "country": "organization",        # 178 values
}
_CANON_COLUMN = {c.lower(): c for c in GUARDED_VALUE_COLUMNS}

# Each guarded column compared with = / LIKE / ILIKE against a string literal.
# The literal pattern honours '' escapes; \b matches the column with or
# without a table qualifier. A column name INSIDE a string literal cannot
# match - it is never followed by an operator and an opening quote there.
#
# NOT is CAPTURED, not skipped over (2026-08-07). Read as non-capturing, it
# made `country NOT LIKE 'Zzz'` arrive at the gate as ('country','LIKE','Zzz');
# the gate executed the POSITIVE form, found zero rows, called a perfectly good
# exclusion filter dead, and re-asked it away.
_VALUE_LITERAL_RE = re.compile(
    r"\b(" + "|".join(GUARDED_VALUE_COLUMNS) + r")\b\s*"
    r"(NOT\s+)?(=|ILIKE|LIKE)\s*'((?:[^']|'')*)'",
    re.IGNORECASE)


# A comparison whose RESULT is then tested for null: `col = 'x' IS NULL`.
# DuckDB reads it in two steps - `col = 'x'` answers true or false, and
# IS NULL then asks whether that answer is missing. The answer is missing
# only when the column itself is null, so the clause matches almost nothing
# and drags the whole filter to zero rows.
#
# 2026-08-05, hyb-06: told by the value gate to DROP a dead-value condition,
# the narrowing model wrote `e.euroSciVocTitle = 'graphene' IS NULL` instead
# of deleting the line. Every VALUE in it was live, so the value gate passed
# it, the filter returned 0 of the 18 real graphene x Sweden projects, and
# the zero-match policy turned it into a confident refusal - the same false
# refusal the value gate exists to stop, arriving by a different door.
#
# Structure, not values: the check runs on the blanked statement, so an
# `IS NULL` sitting INSIDE a string literal is data and never matches. A
# bare `col IS NOT NULL` has no comparison in front of it and stays legal.
_MALFORMED_NULL_RE = re.compile(
    r"(?:<=|>=|<>|!=|=|<|>|\bLIKE\b|\bILIKE\b)\s*"   # a comparison ...
    r"(?:''|[\w.]+)\s*\)?\s+"                        # ... its right operand
    r"IS\s+(?:NOT\s+)?NULL",                         # ... then IS [NOT] NULL
    re.IGNORECASE)


def uses_subject_filter(sql: str | None) -> bool:
    return bool(sql and _SUBJECT_FILTER_RE.search(_blank_euroscivoc(sql)))


def uses_malformed_null_test(sql: str | None) -> bool:
    """True when a comparison's RESULT is tested for null - a clause that can
    only empty the filter, never narrow it."""
    return bool(sql and _MALFORMED_NULL_RE.search(blank_strings(sql)))


def normalise_euroscivoc_term(literal: str) -> str:
    """The bare taxonomy term inside a euroSciVoc pattern: wildcards removed,
    `_` read as the single character it stands for, path slashes trimmed.

    This is the right input for a CANDIDATE lookup - '%/ textiles%' is meant to
    be about textiles, and the hint has to show real terms near it. It is the
    wrong input for the dead/alive DECISION, which must run the pattern as
    written; see _EUROSCIVOC_LITERAL_RE.
    """
    return literal.replace("%", "").replace("_", " ").strip("/").strip()


def euroscivoc_terms(sql: str | None) -> list[str]:
    """The bare terms this SQL filters the taxonomy on, wildcards stripped.

    Not the value gate's path any more (it needs the pattern as written) -
    kept because a bare term is still the readable name of the constraint.
    """
    if not sql:
        return []
    terms = []
    for m in _EUROSCIVOC_LITERAL_RE.finditer(sql):
        term = normalise_euroscivoc_term(m.group(4).replace("''", "'"))
        if term:
            terms.append(term)
    return terms


def filter_literals(sql: str | None) -> list[tuple[str, str, str]]:
    """Every (column, operator, literal) this SQL compares against a guarded
    closed-set column, deduplicated in order.

    euroSciVoc comparisons are folded into the same gate, keyed
    "euroscivoc:<column>" so the caller knows which taxonomy column to test,
    and carrying the literal EXACTLY as written - wildcards, spaces and all.
    Dedup is still (key, literal), so the same comparison written twice
    collapses while Title and Path comparisons stay separate: they are
    different questions to the database.

    NEGATED comparisons are skipped entirely. The gate exists to catch a value
    that matches nothing, because such a value silently empties an AND-filter.
    A NOT against a value that matches nothing does the opposite - it keeps
    every row - so it is harmless and there is nothing to correct. Treating it
    as dead (which is what dropping the NOT did) re-asked away correct
    exclusion filters.
    """
    if not sql:
        return []
    out = []
    for m in _VALUE_LITERAL_RE.finditer(sql):
        if m.group(2):                     # NOT: exempt, see the docstring
            continue
        col = _CANON_COLUMN[m.group(1).lower()]
        out.append((col, m.group(3).upper(), m.group(4).replace("''", "'")))
    for m in _EUROSCIVOC_LITERAL_RE.finditer(sql):
        if m.group(2):                     # NOT: exempt, same reason
            continue
        ev_col = _CANON_EV_COLUMN[m.group(1).lower()]
        out.append((f"euroscivoc:{ev_col}", m.group(3).upper(),
                    m.group(4).replace("''", "'")))
    seen: set[tuple[str, str]] = set()
    deduped = []
    for col, op, lit in out:
        if (col, lit) not in seen:
            seen.add((col, lit))
            deduped.append((col, op, lit))
    return deduped


def is_euroscivoc_key(col: str) -> bool:
    return col == "euroscivoc" or col.startswith("euroscivoc:")


def _euroscivoc_column(col: str) -> str:
    """The taxonomy column a gate key names; Title for the bare legacy key."""
    _, _, ev_col = col.partition(":")
    return _CANON_EV_COLUMN.get(ev_col.lower(), "euroSciVocTitle")


def filter_note(sql: str | None, n_ids: int,
                truncated: bool = False) -> str | None:
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

    truncated=True swaps the closing sentence. The id set then hit the row cap,
    so the projects that came back do satisfy the filter but are not all of the
    projects that satisfy it - and "every project shown satisfies it" reads, to
    the generator, as permission to describe the set as the complete answer.
    """
    if not sql or not _HAS_WHERE_RE.search(sql):
        return None
    head = ("Structured filter already applied. The excerpts below are drawn "
            f"ONLY from the {n_ids:,} projects returned by this query:\n"
            f"{sql}\n")
    if truncated:
        return head + ("Every project shown satisfies it, but the query hit "
                       "its row cap: this is a PARTIAL set and other projects "
                       "that also satisfy the filter are missing. Do not "
                       "describe the list as complete, and do not count it.")
    return head + "Every project shown satisfies it."


def build_id_narrowing_prompt() -> str:
    docs = SCHEMA_DOCS_PATH.read_text(encoding="utf-8")
    return (
        "You translate the STRUCTURED constraints of a question about the "
        "Horizon 2020 CORDIS database into a DuckDB SQL query returning the "
        "matching project ids. A separate semantic-search system handles what "
        "projects are ABOUT - your job is purely the hard metadata filter.\n\n"
        f"{docs}\n\n"
        "Output contract:\n"
        "- Return ONE DuckDB SELECT of the form `SELECT DISTINCT p.id FROM "
        "project p ...` (join organization o ON o.projectID = p.id when a "
        "country/role/SME/activity filter is needed; join euroscivoc e ON "
        "e.projectID = p.id when a classification filter is needed), "
        "selecting ONLY p.id.\n"
        "- You may filter ONLY on these structured dimensions: project.status, "
        "project.fundingScheme, project.startDate/endDate/ecSignatureDate, "
        "project.ecMaxContribution/totalCost, organization.country, "
        "organization.role, organization.sme, organization.activityType, "
        "organization.ecContribution, and the euroSciVoc classification.\n"
        "- euroSciVoc idiom: prefer `e.euroSciVocPath LIKE '%/<term>%'` - the "
        "prefix form also catches child terms. `e.euroSciVocTitle = '<term>'` "
        "matches only that exact term. Use the term the constraint gives, "
        "never a synonym or a broader word.\n"
        "- The user message contains either a CONSTRAINT LIST or a bare "
        "question.\n"
        "- With a constraint list: translate EXACTLY the listed constraints, "
        "one SQL condition per constraint, and NOTHING else. Never add a "
        "condition that is not in the list - no status, no country, no date, "
        "no scheme, no LIMIT, no IS NOT NULL - and never drop a listed "
        "constraint. The question shown under the list is wording context "
        "only, NEVER a source of conditions.\n"
        "- With a bare question: add a condition ONLY if the question "
        "EXPLICITLY states it. Never invent a country, role, funding-amount, "
        "date, funding-scheme, or status filter that the question does not "
        "mention. When in doubt, filter LESS - a missing filter is "
        "recoverable, an invented one silently drops the right projects. If "
        "the question has NO structured constraint at all (only a topic), "
        "return exactly `SELECT DISTINCT id FROM project`.\n"
        "- CRITICAL: the subject matter / topic / research area is a "
        "structured filter ONLY as a euroSciVoc classification. NEVER add "
        "conditions on topics, keywords, objective, title, or acronym to "
        "capture what a project is about. If you catch yourself writing "
        "`topics LIKE`, `objective LIKE`, or `keywords LIKE` for a subject, "
        "DROP that condition entirely.\n"
        "- The `topic` column (topics table) and `project.topics` hold CALL "
        "CODES like 'ERC' or 'EURATOM-COFUND', never subject matter. A "
        "subject word matched against them returns zero projects. Never "
        "filter on them.\n"
        "- Combine conditions with AND. If a condition needs OR, put the OR "
        "group in parentheses - DuckDB binds AND tighter than OR, and an "
        "unparenthesized OR silently drops conditions.\n"
        "- No commentary, no explanation, no markdown fences.\n\n"
        "Examples:\n"
        "Constraints to translate:\n"
        "- funding scheme SME-1\n"
        "- classified under viticulture\n"
        "SELECT DISTINCT p.id FROM project p JOIN euroscivoc e ON "
        "e.projectID = p.id WHERE p.fundingScheme = 'SME-1' AND "
        "e.euroSciVocPath LIKE '%/viticulture%'\n"
        "Constraints to translate:\n"
        "- coordinator country DE\n"
        "- start date 2021 or later\n"
        "SELECT DISTINCT p.id FROM project p JOIN organization o ON "
        "o.projectID = p.id WHERE o.role = 'coordinator' AND o.country = 'DE' "
        "AND p.startDate >= DATE '2021-01-01'\n"
        "Q: Which German-coordinated projects focus on battery recycling?\n"
        "SELECT DISTINCT p.id FROM project p JOIN organization o ON "
        "o.projectID = p.id WHERE o.role = 'coordinator' AND o.country = 'DE'\n"
        "Q: Summarise closed projects about ocean energy.\n"
        "SELECT DISTINCT p.id FROM project p WHERE p.status = 'CLOSED'\n"
        "Q: What MSCA fellowship projects work on marine biology?\n"
        "SELECT DISTINCT p.id FROM project p WHERE p.fundingScheme LIKE 'MSCA%'"
    )


def build_narrowing_user_message(question: str, constraints: list[str]) -> str:
    """The constraint list is the instruction; the question is context only."""
    listed = "\n".join(f"- {c}" for c in constraints)
    return (f"Constraints to translate:\n{listed}\n\n"
            "Original question, for wording context ONLY - never a source of "
            f"conditions:\n{question}")


@dataclass
class ScopedResult:
    question: str
    status: str    # "ok" | "zero_match" | "sql_failed" | "value_not_found"
    sql: str | None = None
    project_ids: set[int] | None = None
    chunks: list[SearchResult] = field(default_factory=list)
    degraded: str | None = None       # "sql_failed" | "value_not_found" when
                                      # the filter was dropped
    weak_filter: bool = False
    # What synthesis must be told the filter did. Set on "ok" only: there is no
    # synthesis on "zero_match", and on a degrade the filter was dropped, so
    # announcing it would be a lie (ask.py prefixes its own note there).
    filter_note: str | None = None
    constraints: list[str] | None = None
    trace: dict = field(default_factory=dict)


class ScopedRetriever:
    def __init__(self, searcher: Retriever, narrow_sql: SqlPath | None = None,
                 extractor=None):
        self.searcher = searcher
        # Anything with .extract(question) -> RouteFacts. In production this
        # is the Router (same object the router condition uses), so the
        # narrowing input is identical whether or not routing happened.
        self.extractor = extractor
        # Same SqlPath machinery, id-narrowing instruction, no row truncation -
        # replace_limit swaps a model-written trailing LIMIT for the bound too.
        self.narrow = narrow_sql or SqlPath(
            system_prompt=build_id_narrowing_prompt(),
            row_limit=NARROW_ROW_LIMIT, replace_limit=True,
            prompt_label=NARROW_PROMPT_VERSION)

    def _resolve_constraints(self, question, constraints, source):
        """(constraints, source) with extraction applied when the caller
        brought neither. constraints=None afterwards means UNKNOWN -> narrow
        from the raw question; [] means known-empty -> no filter at all."""
        if constraints is not None:
            return constraints, (source or "caller")
        if source is not None:      # caller already tried extraction upstream
            return None, source
        if self.extractor is None:
            return None, "raw"
        facts = self.extractor.extract(question)
        if facts.needs_project_text is None:
            # Fallback or an archived mode-only prompt: no facts were
            # reported, so the list is unknown, not empty.
            return None, "fallback-raw"
        return facts.structured_constraints, "scoped"

    def _dead_terms(self, terms: list[str]) -> list[str]:
        """The subset of terms with no match anywhere in the taxonomy."""
        dead = []
        for term in terms:
            esc = term.replace("'", "''")
            _, rows = self.narrow.execute_trusted(
                "SELECT count(*) FROM euroscivoc "
                f"WHERE euroSciVocTitle ILIKE '%{esc}%' "
                f"OR euroSciVocPath ILIKE '%{esc}%'")
            if not rows or rows[0][0] == 0:
                dead.append(term)
        return dead

    def _dead_values(self, literals) -> list[tuple[str, str]]:
        """The (column, literal) pairs with zero matches in the database.

        Each literal is checked against its OWN column only - the combined
        result is never consulted, so this can flag a misspelling but can
        never soften a genuine empty intersection of valid values. A LIKE /
        ILIKE literal is checked as the live pattern it is, so
        `LIKE 'MSCA%'` passes on the strength of its matches.

        euroSciVoc runs the same way: the comparison the model wrote, against
        the taxonomy column it wrote it against. Nothing is normalised first -
        a pattern that the database will not match is exactly what this method
        is here to find (hyb-08: `euroSciVocPath LIKE '%/ textiles%'`).
        """
        dead = []
        for col, op, lit in literals:
            esc = lit.replace("'", "''")
            if is_euroscivoc_key(col):
                # The column comes from a closed regex alternation and is
                # re-spelled from _CANON_EV_COLUMN, so it is never model text.
                table, column = "euroscivoc", _euroscivoc_column(col)
            else:
                table, column = GUARDED_VALUE_COLUMNS[col], col
            _, rows = self.narrow.execute_trusted(
                f"SELECT count(*) FROM {table} WHERE {column} {op} '{esc}'")
            if not rows or not rows[0][0]:
                dead.append((col, lit))
        return dead

    def _candidates(self, col: str, literal: str) -> list[str]:
        """Up to 10 real values near a dead literal: substring match on the
        whole literal, then on its first word, else the column's most common
        values (for a 5-value column that is simply all of them)."""
        if is_euroscivoc_key(col):
            table, lookup = "euroscivoc", "euroSciVocTitle"
            # The gate key carries the PATTERN ('%/ textiles%'); a substring
            # search on that finds nothing. Candidates want the bare term.
            literal = normalise_euroscivoc_term(literal)
        else:
            table, lookup = GUARDED_VALUE_COLUMNS[col], col
        cleaned = literal.replace("%", " ").replace("_", " ").strip("/").strip()
        first_word = cleaned.split()[0] if cleaned.split() else ""
        for frag in dict.fromkeys([cleaned, first_word]):
            if not frag:
                continue
            esc = frag.replace("'", "''")
            _, rows = self.narrow.execute_trusted(
                f"SELECT DISTINCT {lookup} FROM {table} "
                f"WHERE {lookup} ILIKE '%{esc}%' LIMIT 10")
            values = [r[0] for r in rows if r and r[0] is not None]
            if values:
                return values
        _, rows = self.narrow.execute_trusted(
            f"SELECT {lookup} FROM {table} WHERE {lookup} IS NOT NULL "
            f"GROUP BY {lookup} ORDER BY count(*) DESC LIMIT 10")
        return [r[0] for r in rows if r and r[0] is not None]

    def _value_hint(self, dead: list[tuple[str, str]]) -> str:
        """The corrective re-ask hint: each dead value by name, with the real
        candidates next to it. Rides in the user message - the system prompt
        (and NARROW_PROMPT_VERSION) is untouched."""
        lines = []
        for col, lit in dead:
            cands = self._candidates(col, lit)
            shown = (", ".join(f"'{c}'" for c in cands) if cands
                     else "none found")
            if is_euroscivoc_key(col):
                # Name the comparison, not a tidied-up term: the whole point
                # is that THIS pattern, against THIS column, matches nothing.
                lines.append(
                    f"- the euroSciVoc condition "
                    f"{_euroscivoc_column(col)} ... '{lit}' matches no row in "
                    f"the taxonomy. Stored terms closest to it: {shown}.")
            else:
                lines.append(f"- {col} value '{lit}' does not exist in the "
                             f"database. Stored values closest to it: {shown}.")
        return ("\n\n(Correction needed - these filter values do not exist:\n"
                + "\n".join(lines) + "\n"
                "Rewrite the SQL using a stored value EXACTLY as shown when "
                "one matches the constraint's intent. If none of them "
                "matches it, DROP that condition entirely. Keep every other "
                "condition unchanged.)")

    def _unfiltered(self, question, k, status, degraded, sql, trace,
                    constraints=None):
        chunks = self.searcher.search(question, k=k)
        trace = {**trace, "n_chunks": len(chunks)}
        return ScopedResult(
            question=question, status=status, sql=sql, project_ids=None,
            chunks=chunks, degraded=degraded, constraints=constraints,
            trace=trace)

    def retrieve(self, question: str, k: int = 10,
                 constraints: list[str] | None = None,
                 constraints_source: str | None = None) -> ScopedResult:
        constraints, source = self._resolve_constraints(
            question, constraints, constraints_source)
        base_trace = {"constraints": constraints, "constraints_source": source}

        if constraints == []:
            # Known-empty list: nothing structured to filter on. No narrowing
            # call at all - a model asked to write SQL with nothing to write
            # is the largest source of invented clauses - and unfiltered
            # search is what the full-corpus SELECT reduced to anyway.
            res = self._unfiltered(question, k, status="ok", degraded=None,
                                   sql=None,
                                   trace={**base_trace, "n_ids": None,
                                          "no_constraints": True})
            res.constraints = constraints
            return res

        narrow_input = (build_narrowing_user_message(question, constraints)
                        if constraints else question)
        sql_result = self.narrow.ask(narrow_input)
        subject_corrected = False

        # Enforce the topic/metadata separation in code, not just the prompt:
        # if the model filtered on a subject-matter column, re-ask once with a
        # pointed reminder and prefer the corrected query.
        if sql_result.ok and uses_subject_filter(sql_result.sql):
            hint = (narrow_input + "\n\n(Reminder: do NOT filter on topics, "
                    "keywords, objective, title or acronym. Use only country, "
                    "date, money, funding scheme, role, status, or the "
                    "euroSciVoc classification, and drop any other condition "
                    "about the subject matter.)")
            retry = self.narrow.ask(hint)
            subject_corrected = True
            if retry.ok and not uses_subject_filter(retry.sql):
                sql_result = retry

        # Everything that funnels here has the same meaning: no legal filter
        # exists, so search unfiltered and say the filter was dropped. Executing
        # a query on a banned column instead would let its (usually empty)
        # result masquerade as a real zero_match.
        failure = None
        if not sql_result.ok:
            failure = sql_result.error
        elif uses_subject_filter(sql_result.sql):
            failure = "subject filter survived the corrective re-ask"
        elif uses_malformed_null_test(sql_result.sql):
            failure = ("malformed filter: a comparison's result is tested "
                       "with IS NULL, which matches nothing")

        # Value gate, BEFORE the zero-ids branch: a filter value that does not
        # exist in its closed-set column can only produce a false zero_match.
        # One re-ask whose hint names each dead value and shows real
        # candidates, so the model can correct the value (hyb-09: 'SME
        # Instrument phase 1' -> 'SME-1') or drop the condition (hyb-06: an
        # activityType no candidate list can save). A still-dirty re-ask means
        # the filter is undeliverable - drop it loudly; the call budget does
        # not stretch to a third narrowing on a reduced list.
        #
        # "Dirty" includes a malformed IS NULL test, because that is how the
        # model half-obeys "drop the condition": it writes a clause that looks
        # like a removal and empties the query instead (hyb-06). Rejecting the
        # re-ask sends the question to an honest unfiltered search; accepting
        # it would execute a filter that can only refuse.
        dead: list[tuple[str, str]] = []
        value_reasked = False
        reask_rejected = None
        if failure is None:
            dead = self._dead_values(filter_literals(sql_result.sql))
        if failure is None and dead:
            value_reasked = True
            base = (build_narrowing_user_message(question, constraints)
                    if constraints else question)
            retry = self.narrow.ask(base + self._value_hint(dead))
            if not retry.ok:
                reask_rejected = "sql error"
            elif uses_subject_filter(retry.sql):
                reask_rejected = "subject filter"
            elif uses_malformed_null_test(retry.sql):
                reask_rejected = "malformed IS NULL test"
            elif self._dead_values(filter_literals(retry.sql)):
                reask_rejected = "dead value again"
            if reask_rejected is None:
                sql_result = retry
            else:
                return self._unfiltered(
                    question, k, status="value_not_found",
                    degraded="value_not_found", sql=sql_result.sql,
                    constraints=constraints,
                    trace={**base_trace,
                           "dead_values": [list(d) for d in dead],
                           "value_reasked": value_reasked,
                           "reask_rejected": reask_rejected,
                           "sql_retried": sql_result.retried,
                           "subject_corrected": subject_corrected})

        ids: set[int] = set()
        if failure is None:
            try:
                ids = {int(r[0]) for r in sql_result.rows
                       if r and r[0] is not None}
            except (TypeError, ValueError):
                failure = "first column does not hold project ids"

        if failure is not None:
            # Policy: SQL failed -> pure vector over everything, filter dropped.
            return self._unfiltered(
                question, k, status="sql_failed", degraded="sql_failed",
                sql=sql_result.sql,
                trace={**base_trace, "sql_error": failure,
                       "sql_retried": sql_result.retried,
                       "subject_corrected": subject_corrected})

        if not ids:
            # Policy: zero ids IS the answer. Do not widen. (Every value in
            # this SQL exists in its own column - the gate above ran first -
            # so this is a genuinely empty intersection, not a misspelling.)
            return ScopedResult(
                question=question, status="zero_match", sql=sql_result.sql,
                project_ids=set(), chunks=[], constraints=constraints,
                trace={**base_trace, "n_ids": 0,
                       "sql_retried": sql_result.retried,
                       "dead_values": [list(d) for d in dead],
                       "value_reasked": value_reasked,
                       "subject_corrected": subject_corrected})

        # The narrowing SQL always carries a trailing LIMIT (ensure_limit, with
        # replace_limit=True). A query that came back holding exactly that many
        # rows was almost certainly cut off - a missing DISTINCT over the
        # organization join can turn 150k rows into the first 50000 - and the
        # id set is then a partial answer to its own filter. Keep the filter
        # (the ids it did return are correct), but mark it weak and stop the
        # note claiming the set is everything. Refusing or dropping the filter
        # would throw away a filter that worked, only not exhaustively.
        row_limit = getattr(self.narrow, "row_limit", NARROW_ROW_LIMIT)
        truncated = len(sql_result.rows) >= row_limit
        weak = truncated or len(ids) > WEAK_FILTER
        chunks = self.searcher.search(question, k=k, project_ids=ids)
        note = filter_note(sql_result.sql, len(ids), truncated=truncated)
        return ScopedResult(
            question=question, status="ok", sql=sql_result.sql,
            project_ids=ids, chunks=chunks, weak_filter=weak,
            filter_note=note, constraints=constraints,
            trace={**base_trace, "n_ids": len(ids), "weak_filter": weak,
                   "truncated": truncated,
                   "sql_retried": sql_result.retried,
                   "dead_values": [list(d) for d in dead],
                   "value_reasked": value_reasked,
                   "subject_corrected": subject_corrected,
                   "n_chunks": len(chunks)})
