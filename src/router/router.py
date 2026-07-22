"""Question router: one LLM call classifies a question as sql | vector | hybrid.

Strict JSON contract, one retry on malformed output, then a visible fallback
to hybrid (router_fallback=true) - M5 counts fallbacks as router failures, so
they must never be silent.
"""

import json
import re
from dataclasses import dataclass

from src.llm import make_llm

MODES = ("sql", "vector", "scoped")

# Frozen at end of pilot (M5 freeze table); bump on ANY edit. Traces log
# "label:content-hash" so a silent edit without a bump is still visible.
ROUTER_PROMPT_VERSION = "r1-pilot"

SYSTEM_PROMPT = """You route natural-language questions about a Horizon 2020 \
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

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class RouteDecision:
    mode: str
    reason: str
    router_fallback: bool = False


def _parse(text: str) -> tuple[str, str]:
    """Extract {mode, reason} or raise ValueError."""
    m = _JSON_RE.search(text)
    if not m:
        raise ValueError("no JSON object found")
    obj = json.loads(m.group(0))
    mode = obj.get("mode")
    if mode not in MODES:
        raise ValueError(f"invalid mode {mode!r}")
    return mode, str(obj.get("reason", ""))


class Router:
    def __init__(self, llm=None):
        self.llm = llm or make_llm()

    def route(self, question: str) -> RouteDecision:
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": question}]
        for attempt in (0, 1):
            raw = self.llm.chat(messages, max_tokens=128)
            try:
                mode, reason = _parse(raw)
                return RouteDecision(mode=mode, reason=reason)
            except (ValueError, json.JSONDecodeError) as e:
                if attempt == 0:
                    messages = messages + [
                        {"role": "assistant", "content": raw},
                        {"role": "user", "content":
                            f"That was not valid: {e}. Reply with ONLY the JSON "
                            'object {"mode": "sql|vector|scoped", "reason": '
                            '"..."} and nothing else.'},
                    ]
        # Both attempts failed: fall back to the widest strategy, visibly.
        return RouteDecision(
            mode="scoped",
            reason="router failed to produce valid JSON; defaulted to scoped",
            router_fallback=True)
