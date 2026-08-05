"""Question router: one LLM call decides sql | vector | scoped.

Strict JSON contract, one retry on malformed output, then a visible fallback
to scoped (router_fallback=true) - M5 counts fallbacks as router failures, so
they must never be silent.

Since r3-fields the model REPORTS two facts and `derive_mode` picks the mode
from them in code, the same split the judge already uses (judge.py:derive_pass,
ragas_judge.py:derive_ragas_pass): the model does the reading, the rule lives
here where it is versioned and visible instead of inside the model's head.

The reason is measured. In round1-router (r1-pilot) the model named the
constraint it had found and then denied a constraint existed, 5 times out of 7
hybrid misroutes. In r2-columns-phaseA all 3 remaining misroutes did the same:
every one stated the correct facts and then chose a mode those facts
contradict. No run has yet produced a case where the model disagreed with its
own stated facts and was right.
"""

import json
import re
from dataclasses import dataclass, field

from src.llm import make_llm

MODES = ("sql", "vector", "scoped")

# Every router prompt ever run lives in ROUTER_PROMPTS at the bottom of this
# block, keyed by version. To switch, change ROUTER_PROMPT_VERSION to another
# key - nothing else moves. Old versions are kept verbatim and never edited: a
# run's trace records "label:content-hash", so an archived prompt whose text
# drifted would make every number recorded against it unreadable.
#
#   r1-pilot    the pilot prompt. It defined "structured" by arithmetic
#               ("Counts, sums, averages, rankings"), so the router read a
#               subject classification, a funding scheme or a project name as
#               topic text. 12 of 58 misrouted in round1-router: 7 hybrid
#               questions to vector, 5 sql questions to scoped.
#   r2-columns  2026-08-05. It defines "structured" as "the value sits in a
#               column", names the euroSciVoc classification beside country /
#               date / money / scheme / role / status, and decides on whether
#               the project's own words are needed rather than on whether
#               anything is counted. 3 of 57 misrouted, and all 3 stated the
#               right facts and then chose a mode contradicting them.
#   r3-fields   active (2026-08-05). Same two questions as r2-columns, but the
#               model reports the two ANSWERS and derive_mode picks the mode.
#               On the r2-columns-phaseA replies this rule gets all 3 right.
ROUTER_PROMPT_VERSION = "r3-fields"

_R1_PILOT = """You route natural-language questions about a Horizon 2020 \
research-project database to one of three answering strategies. The database has \
structured fields (project counts, funding amounts in EUR, start/end dates, \
countries, funding schemes, organisation roles, status) AND free-text content \
(project objectives and periodic-report narratives describing what each project \
does and achieved).

Choose exactly one mode:

- "sql": answerable ENTIRELY from structured fields. Counts, sums, averages, \
rankings, and filters on country, date, money, funding scheme, organisation \
role, or status. No understanding of what a project is *about* is required.

- "vector": about the TOPICAL or SEMANTIC content of what projects do or \
achieved, with NO structured constraint. Finding projects by subject matter, \
summarising approaches or results.

- "scoped": needs BOTH - semantic/topical content AND a structured constraint \
(e.g. a topic combined with a country, funding range, date window, scheme, or \
role). If the question mixes "what it's about" with "and it must also satisfy X \
structured filter", it is scoped.

Reply with STRICT JSON only, no markdown, no commentary:
{"mode": "sql|vector|scoped", "reason": "<one short clause>"}

Examples:
Q: How many projects were terminated?
{"mode": "sql", "reason": "count with a status filter, structured only"}
Q: What is the total EU funding for projects coordinated in Spain?
{"mode": "sql", "reason": "sum with country and role filters, no topic"}
Q: Which projects work on hydrogen fuel cells for heavy transport?
{"mode": "vector", "reason": "topical search, no structured constraint"}
Q: Summarise how projects approached ocean plastic monitoring.
{"mode": "vector", "reason": "semantic summary of project content"}
Q: Which German-coordinated projects focus on battery recycling?
{"mode": "scoped", "reason": "topic plus a country/role constraint"}
Q: Find AI-for-healthcare projects that received over 5 million euros.
{"mode": "scoped", "reason": "topic plus a funding-amount constraint"}"""

_R2_COLUMNS = """You route natural-language questions about a Horizon 2020 \
research-project database to one of three answering strategies.

The database has STRUCTURED FIELDS, where every value sits in a column:
- project: status, funding scheme, start / end / signature dates, EC \
contribution, total cost
- organization: country, role (coordinator or participant), SME flag, \
activity type, EC contribution
- euroSciVoc: the science-subject classification carried by each project, \
e.g. "classified under volcanology", "classified under machine learning"

It also has FREE TEXT: every project's objective and its periodic-report \
narrative, describing what the project does, how it works and what it found.

Answer two questions, in this order.

1. Does answering need the project's OWN WORDS?
YES when the answer must come from what a project says about itself: its aim, \
its method, its results, or how several projects differ from one another.
NO when the answer is a count, a sum, an average, a ranking, a date, an \
amount, a status, or a name or role that a column already holds.

2. Does the question state a STRUCTURED constraint?
A constraint is structured when its value sits in one of the fields listed \
above. This has NOTHING to do with arithmetic: a constraint is structured \
whether or not the question counts or sums anything. It stays structured when \
the question states it in ordinary prose - "among ERC Starting Grant \
projects", "that include a Swedish participant", "classified under textiles" \
and "that started in 2021 or later" are all structured constraints. Naming \
one project by acronym or by grant agreement number is a structured \
constraint too: it is a lookup, not a topic.

Then choose exactly one mode:

- "sql": the project's own words are NOT needed. Use this whether or not the \
question states a structured constraint.
- "scoped": the project's own words ARE needed AND the question states a \
structured constraint.
- "vector": the project's own words ARE needed and the question states NO \
structured constraint.

Reply with STRICT JSON only, no markdown, no commentary:
{"mode": "sql|vector|scoped", "reason": "<the structured constraints you \
found, or 'none'; then whether the project's own words are needed>"}

Examples:
Q: How many projects were terminated?
{"mode": "sql", "reason": "constraint: status terminated; own words not needed, it is a count"}
Q: How many projects classified under artificial intelligence are coordinated by German organisations?
{"mode": "sql", "reason": "constraints: classification artificial intelligence, coordinator country Germany; own words not needed, it is a count"}
Q: Which organisation coordinates the BATTERY2030PLUS project?
{"mode": "sql", "reason": "constraints: project acronym BATTERY2030PLUS, role coordinator; own words not needed, the name is in a column"}
Q: Name every organisation on grant agreement number 874827 and the role of each.
{"mode": "sql", "reason": "constraint: grant agreement number 874827; own words not needed, names and roles are columns"}
Q: Which projects develop solid-state batteries for electric vehicles?
{"mode": "vector", "reason": "constraints: none; own words needed to tell what each project develops"}
Q: How do projects studying antimicrobial resistance differ in the pathogens they target?
{"mode": "vector", "reason": "constraints: none; own words needed to compare the projects"}
Q: Among projects classified under archaeology that include a Greek organisation, what dating methods do they use?
{"mode": "scoped", "reason": "constraints: classification archaeology, participant country Greece; own words needed to describe the methods"}
Q: Among ERC Advanced Grant projects on volcanic hazard, what field instruments do the teams deploy?
{"mode": "scoped", "reason": "constraint: funding scheme ERC Advanced Grant; own words needed to list the instruments"}
Q: Which projects coordinated in Portugal work on wave energy converters, and what designs do they use?
{"mode": "scoped", "reason": "constraint: coordinator country Portugal; own words needed to describe the designs"}"""

_R3_FIELDS = """You read questions about a Horizon 2020 research-project \
database and report two facts about each one. You do NOT choose how the \
question gets answered - a separate rule does that from the two facts you \
report. Report what is there; do not try to work out which strategy it leads \
to.

The database has STRUCTURED FIELDS, where every value sits in a column:
- project: status, funding scheme, start / end / signature dates, EC \
contribution, total cost
- organization: country, role (coordinator or participant), SME flag, \
activity type, EC contribution
- euroSciVoc: the science-subject classification carried by each project, \
e.g. "classified under volcanology", "classified under machine learning"

It also has FREE TEXT: every project's objective and its periodic-report \
narrative, describing what the project does, how it works and what it found.

Report these two facts.

1. needs_project_text - does answering need the project's OWN WORDS?
true when the answer must come from what a project says about itself: its \
aim, its method, its results, or how several projects differ from one another.
false when the answer is a count, a sum, an average, a ranking, a date, an \
amount, a status, or a name or role that a column already holds.

2. structured_constraints - a list of EVERY constraint the question states \
whose value sits in a structured field. Write each one as a short phrase \
starting with the field it uses. Rules:
- ONLY these count, and nothing else: status, funding scheme, start / end / \
signature date, EC contribution, total cost, organisation country, \
organisation role, SME flag, activity type, euroSciVoc classification, and a \
project named by acronym or by grant agreement number.
- The objective, the title and the report narrative are FREE TEXT, never a \
structured constraint. A subject, technology, method or result that the \
question DESCRIBES lives in that free text, so it never goes in this list, \
however precisely the question describes it. If you find yourself writing \
"project objective ...", "abstract mentions ..." or "describes ...", that is \
free text: leave it out.
- euroSciVoc counts only when the question names a classification, in words \
like "classified under X" or "whose euroSciVoc classification includes X". A \
question that merely talks about a subject is NOT classified under it.
- This has NOTHING to do with arithmetic. A constraint belongs in the list \
whether or not the question counts or sums anything.
- A constraint belongs in the list when the question states it in ordinary \
prose. "among ERC Starting Grant projects", "that include a Swedish \
participant", "classified under textiles" and "that started in 2021 or later" \
are all structured constraints.
- Naming one project by acronym or by grant agreement number is a structured \
constraint: it is a lookup, not a topic.
- Every entry must carry the actual VALUE the question gives: the country, \
the scheme name, the status, the date, the amount, the acronym, the grant \
number, the classification term. An entry that names a field without a value \
from the question is not a constraint. Never copy a phrase from these rules \
into the list.
- When the question states none, return an empty list []. Never write "none" \
or "no constraints" as a list entry.

Reply with STRICT JSON only, no markdown, no commentary:
{"needs_project_text": true|false, "structured_constraints": ["<short \
phrase>", ...], "reason": "<one short clause>"}

Examples:
Q: How many projects were terminated?
{"needs_project_text": false, "structured_constraints": ["status terminated"], "reason": "a count over a status column"}
Q: How many projects classified under artificial intelligence are coordinated by German organisations?
{"needs_project_text": false, "structured_constraints": ["classified under artificial intelligence", "coordinator country Germany"], "reason": "a count, both constraints are columns"}
Q: Which organisation coordinates the BATTERY2030PLUS project?
{"needs_project_text": false, "structured_constraints": ["project acronym BATTERY2030PLUS", "role coordinator"], "reason": "the organisation name is in a column"}
Q: Name every organisation on grant agreement number 874827 and the role of each.
{"needs_project_text": false, "structured_constraints": ["grant agreement number 874827"], "reason": "names and roles are columns"}
Q: Which projects develop solid-state batteries for electric vehicles?
{"needs_project_text": true, "structured_constraints": [], "reason": "the subject is described, not recorded in any field"}
Q: How do projects studying antimicrobial resistance differ in the pathogens they target?
{"needs_project_text": true, "structured_constraints": [], "reason": "comparing what the projects say, no field constrains them"}
Q: Among projects classified under archaeology that include a Greek organisation, what dating methods do they use?
{"needs_project_text": true, "structured_constraints": ["classified under archaeology", "participant country Greece"], "reason": "methods come from the project text, both constraints are columns"}
Q: Among ERC Advanced Grant projects on volcanic hazard, what field instruments do the teams deploy?
{"needs_project_text": true, "structured_constraints": ["funding scheme ERC Advanced Grant"], "reason": "instruments come from the project text; volcanic hazard is only described"}
Q: Which projects coordinated in Portugal work on wave energy converters, and what designs do they use?
{"needs_project_text": true, "structured_constraints": ["coordinator country Portugal"], "reason": "designs come from the project text"}"""

ROUTER_PROMPTS = {
    "r1-pilot": _R1_PILOT,
    "r2-columns": _R2_COLUMNS,
    "r3-fields": _R3_FIELDS,
}

# The JSON shape the ACTIVE prompt promises, quoted back at the model when its
# first reply will not parse. It has to track the prompt: correcting an
# r3-fields reply with the archived {"mode": ...} shape would teach the model
# the wrong contract on exactly the call that already went wrong.
_CONTRACT_HINTS = {
    "r1-pilot": '{"mode": "sql|vector|scoped", "reason": "..."}',
    "r2-columns": '{"mode": "sql|vector|scoped", "reason": "..."}',
    "r3-fields": '{"needs_project_text": true|false, '
                 '"structured_constraints": ["..."], "reason": "..."}',
}
CONTRACT_HINT = _CONTRACT_HINTS[ROUTER_PROMPT_VERSION]

# What the router actually sends, and what ask.py fingerprints into every trace.
SYSTEM_PROMPT = ROUTER_PROMPTS[ROUTER_PROMPT_VERSION]

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class RouteDecision:
    mode: str
    reason: str
    router_fallback: bool = False
    # The two facts r3-fields reports, kept beside the mode they produced, so
    # a misroute says WHICH step failed: wrong facts is a reading failure,
    # right facts is a derive_mode failure. Under an archived prompt the model
    # returns the mode directly and there are no facts to keep, so these stay
    # None / empty - absent, not false.
    needs_project_text: bool | None = None
    structured_constraints: list[str] = field(default_factory=list)


@dataclass
class RouteFacts:
    """What one extraction call produced, before any mode is chosen.

    The scoped path consumes this directly (its narrowing step translates
    structured_constraints instead of re-reading the raw question), so the
    facts exist independently of routing. needs_project_text is None when no
    facts were reported - an archived mode-only prompt or a parse fallback -
    and a consumer must then treat the constraint list as UNKNOWN, never as
    empty: [] is a positive claim that the question has no constraints.
    """
    needs_project_text: bool | None = None
    structured_constraints: list[str] = field(default_factory=list)
    reason: str = ""
    fallback: bool = False
    mode: str | None = None      # set on every non-fallback parse; archived
                                 # prompts report ONLY this


def derive_mode(needs_project_text: bool,
                structured_constraints: list[str]) -> str:
    """The routing rule, in code (r3-fields onwards).

    Not needing the project's own words means every part of the answer is in a
    column, so SQL can produce all of it - with or without constraints, and
    whether or not anything is counted. Needing them splits on whether a
    structured filter has to run first.
    """
    if not needs_project_text:
        return "sql"
    return "scoped" if structured_constraints else "vector"


# A model told to return [] for "no constraints" sometimes returns ["none"]
# instead, and one junk entry would silently turn vector into scoped. Dropped
# here rather than trusted to the prompt.
_EMPTY_CONSTRAINTS = {"none", "no", "n/a", "na", "no constraints", "-", ""}


def _parse(text: str) -> tuple[str, str, bool | None, list[str]]:
    """Extract a decision from EITHER contract, or raise ValueError.

    r3-fields reports the two facts and the mode comes from derive_mode. The
    archived r1-pilot / r2-columns prompts return the mode themselves, so
    switching ROUTER_PROMPT_VERSION back to one of them keeps working - that
    switch is the reason the archive exists.

    Returns (mode, reason, needs_project_text, structured_constraints).
    """
    m = _JSON_RE.search(text)
    if not m:
        raise ValueError("no JSON object found")
    obj = json.loads(m.group(0))
    reason = str(obj.get("reason", ""))

    if "mode" in obj:                      # archived contract
        mode = obj["mode"]
        if mode not in MODES:
            raise ValueError(f"invalid mode {mode!r}")
        return mode, reason, None, []

    needs = obj.get("needs_project_text")
    if not isinstance(needs, bool):
        raise ValueError(
            f"needs_project_text must be true or false, got {needs!r}")
    raw_constraints = obj.get("structured_constraints")
    if not isinstance(raw_constraints, list) or any(
            not isinstance(c, str) for c in raw_constraints):
        raise ValueError("structured_constraints must be a list of strings")
    constraints = [c.strip() for c in raw_constraints
                   if c.strip().lower() not in _EMPTY_CONSTRAINTS]
    return derive_mode(needs, constraints), reason, needs, constraints


class Router:
    def __init__(self, llm=None):
        self.llm = llm or make_llm()

    def extract(self, question: str) -> RouteFacts:
        """One extraction call: the model reads, the facts come back raw.

        route() derives a mode from these; the scoped path feeds the
        constraint list to its narrowing step. Splitting the call from the
        rule is what lets always-hybrid - which never routes - still extract.
        """
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": question}]
        for attempt in (0, 1):
            # 384, not 128: on the gen seat this cap covers reasoning tokens
            # too, and both r2-columns and r3-fields ask for more than a bare
            # mode string. At 128 the longest bank question (sql-16) spent the
            # whole budget and returned no content at all.
            raw = self.llm.chat(messages, max_tokens=384)
            try:
                mode, reason, needs, constraints = _parse(raw)
                return RouteFacts(
                    needs_project_text=needs,
                    structured_constraints=constraints,
                    reason=reason, mode=mode)
            except (ValueError, json.JSONDecodeError) as e:
                if attempt == 0:
                    messages = messages + [
                        {"role": "assistant", "content": raw},
                        {"role": "user", "content":
                            f"That was not valid: {e}. Reply with ONLY the "
                            f"JSON object {CONTRACT_HINT} and nothing else."},
                    ]
        # Both attempts failed: fall back to the widest strategy, visibly.
        return RouteFacts(
            reason="router failed to produce valid JSON; defaulted to scoped",
            fallback=True, mode="scoped")

    def route(self, question: str) -> RouteDecision:
        f = self.extract(question)
        return RouteDecision(
            mode=f.mode, reason=f.reason, router_fallback=f.fallback,
            needs_project_text=f.needs_project_text,
            structured_constraints=f.structured_constraints)
