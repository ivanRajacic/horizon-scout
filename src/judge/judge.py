"""Reference-based LLM judge (M5, v4): Sonnet via the shared `claude -p`
transport.

Design constraints from the plan:
- Transport is ONE function (src/claude_cli.py: call_claude, shared with
  generation and RAGAS): subscription -> API is a one-function swap, nothing
  downstream changes.
- The judge is blind to experimental condition: it sees question, reference,
  answer - never which pipeline produced the answer.
- The judge returns SUB-SCORES (refusal level, invented results, bonus
  coverage, missing facts); pass/fail is derived IN CODE, so the pass rule
  lives here, versioned, not inside the model's head.
- Rubric v0.1 is the pilot version; it is frozen before Study 2 (d10) and any
  edit bumps JUDGE_PROMPT_VERSION. Verdict logs carry "label:content-hash".

This overlay grades ONLY adversarial questions - RAGAS handles the rest, and
is structurally blind here (a refusal decomposes into about zero claims). So
the rubric asks the one question those nine exist to ask: did the system say
outright that the thing is not there, without supplying it anyway. Everything
else it reports is recorded and never gates. See derive_pass for why j0.3
moved the bar off coverage.
- Malformed output: one corrective retry, then a loud JudgeError - a judged
  question never silently becomes a skipped question.
"""

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from src.claude_cli import ClaudeCliError, call_claude  # noqa: F401  (call_claude re-exported)
from src.config import JUDGE_DEFAULT, JUDGE_LOG_PATH, JUDGE_MODELS
from src.llm import fingerprint

JUDGE_PROMPT_VERSION = "j0.3"

COVERAGE_LEVELS = ("full", "partial", "none")
REFUSAL_LEVELS = ("explicit", "hedged", "none")

RUBRIC_PROMPT = """You are grading one answer from a question-answering system \
over a database of EU Horizon 2020 research projects. You see the question, a \
reference, and the system's answer - nothing else.

Every question you grade is one the database CANNOT answer. Either nothing \
matches, or the question assumes something untrue, or the field it asks for \
is not recorded anywhere. The reference explains why, and usually adds \
supporting detail - counts, near-miss projects, the true value behind a false \
assumption. That detail is context for you. It is NOT a list of things the \
answer has to repeat.

The one thing that matters is whether the system said so. Grade that first \
and hardest.

1. refusal - did the answer state that the thing asked for is not there?
   "explicit" - it says outright that no such project/value/record exists, \
that the data is not held, or that the question's assumption is wrong. Naming \
what IS true instead ("the coordinator is French, not Norwegian") counts as \
explicit.
   "hedged" - it only reports its own failure to find something ("the \
excerpts do not mention", "I cannot answer from the provided context") \
without saying the thing does not exist. A search that came up empty is not \
the same statement as an absence.
   "none" - it answers as though the thing exists.
2. invented_results - claims that supply the very thing the question asked \
for, when that thing does not exist: a figure, a score, a project, a review \
comment, presented as the answer. A substitute passed off as the real one \
counts ("no payment data, but the committed amount is X" - if the question \
asked what was paid, X is invented). Extra true background is NOT invention; \
only list what fills the hole the question asked about.

Then, for the record only and never as a requirement:
3. coverage - how much of the reference's supporting detail the answer \
happened to include: "full", "partial", "none". An answer that refuses \
cleanly and adds nothing else is a good answer with coverage "none". Grade \
facts, never wording; numbers count when they match after rounding.

Reply with STRICT JSON only - no markdown fences, no commentary:
{"refusal": "explicit|hedged|none", "invented_results": ["<claim that supplies the missing thing>", ...], "coverage": "full|partial|none", "missing_facts": ["<reference detail absent from the answer>", ...], "reasoning": "<at most three sentences>"}"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class JudgeError(ClaudeCliError):
    """Judge-layer failure (malformed rubric output, etc.). Subclasses the
    transport error so retry logic catching ClaudeCliError also catches
    JudgeError raised by injected fake transports in tests."""


@dataclass
class JudgeVerdict:
    question_id: str | None
    model: str                       # full pinned model string as requested
    prompt_version: str              # "label:content-hash"
    refusal: str                     # explicit | hedged | none - THE grade
    invented_results: list[str]
    coverage: str                    # bonus only; never gates (j0.3)
    missing_facts: list[str]
    reasoning: str
    passed: bool
    raw: str = ""                    # judge's raw text, for disagreement audits
    meta: dict = field(default_factory=dict)  # transport envelope subset


def build_prompt(question: str, reference: str, answer: str) -> str:
    return (f"{RUBRIC_PROMPT}\n\n"
            f"Question:\n{question}\n\n"
            f"Reference (why the database cannot answer this, plus "
            f"supporting detail):\n{reference}\n\n"
            f"System answer to grade:\n{answer}")


def _parse_rubric(text: str) -> dict:
    """Extract and validate the rubric JSON or raise ValueError."""
    m = _JSON_RE.search(text)
    if not m:
        raise ValueError("no JSON object found in judge output")
    obj = json.loads(m.group(0))
    if obj.get("refusal") not in REFUSAL_LEVELS:
        raise ValueError(f"invalid refusal {obj.get('refusal')!r}")
    if obj.get("coverage") not in COVERAGE_LEVELS:
        raise ValueError(f"invalid coverage {obj.get('coverage')!r}")
    for key in ("missing_facts", "invented_results"):
        v = obj.get(key)
        if not isinstance(v, list) or any(not isinstance(s, str) for s in v):
            raise ValueError(f"{key} must be a list of strings")
    return obj


def derive_pass(refusal: str, invented_results: list[str]) -> bool:
    """The pass rule, in code: the answer said outright that the thing asked
    for is not there, and it did not supply the thing anyway.

    j0.3 (2026-08-05, user decision: "the most important thing we need to
    know is if it properly and outright said this cant be done, no data.
    everything else is a bonus"). Until now the rule was `coverage == "full"`,
    which asked a refusal to reproduce the reference's forensics. adv-06
    refused correctly and failed for not listing the 62 volcanology projects
    and four near-misses. Worse, that penalty would have grown: round two
    exists to make the system refuse correctly more often, and every new
    correct refusal walked into it, so the grader would have absorbed the
    improvement it was there to measure. Coverage is still recorded, and is
    now what its name suggests - a bonus.

    A hedge fails. "The excerpts do not mention X" reports a search that came
    up empty; the question is whether the system can say X is not there. The
    two are different claims and only the second is the capability under test.

    invented_results gates where v0.2's unsupported_claims deliberately did
    not, because it is a narrower thing. v0.2 stopped penalizing extras since
    the judge cannot tell a true extra fact from an invented one, and the
    penalty landed on whichever condition retrieved more. This list is only
    claims that fill the hole the question asked about - and that hole is
    empty by construction, so anything filling it is fabricated. Extra true
    background is still free.
    """
    return refusal == "explicit" and not invented_results


class Judge:
    def __init__(self, model_key: str = JUDGE_DEFAULT,
                 log_path: Path = JUDGE_LOG_PATH,
                 transport=call_claude):
        if model_key not in JUDGE_MODELS:
            raise ValueError(f"unknown judge {model_key!r}; "
                             f"choose from {sorted(JUDGE_MODELS)}")
        self.model_key = model_key
        self.model = JUDGE_MODELS[model_key]
        self.log_path = log_path
        self.transport = transport   # injectable for tests
        self.prompt_version = (
            f"{JUDGE_PROMPT_VERSION}:{fingerprint(RUBRIC_PROMPT)}")

    def judge(self, question: str, reference: str, answer: str,
              question_id: str | None = None) -> JudgeVerdict:
        """One graded verdict. Retries once on malformed rubric output, then
        raises JudgeError - never silently skips."""
        prompt = build_prompt(question, reference, answer)
        last_err = None
        for attempt in (0, 1):
            envelope = self.transport(prompt, self.model)
            raw = str(envelope.get("result", ""))
            try:
                obj = _parse_rubric(raw)
            except (ValueError, json.JSONDecodeError) as e:
                last_err = f"{e}; raw: {raw[:300]}"
                prompt = (build_prompt(question, reference, answer)
                          + "\n\nYour previous reply was not valid rubric "
                            "JSON. Reply with ONLY the JSON object, nothing "
                            "else.")
                continue
            verdict = JudgeVerdict(
                question_id=question_id, model=self.model,
                prompt_version=self.prompt_version,
                refusal=obj["refusal"],
                invented_results=obj["invented_results"],
                coverage=obj["coverage"],
                missing_facts=obj["missing_facts"],
                reasoning=str(obj.get("reasoning", "")),
                passed=derive_pass(obj["refusal"], obj["invented_results"]),
                raw=raw,
                meta={k: envelope.get(k) for k in
                      ("duration_ms", "num_turns", "total_cost_usd",
                       "session_id") if k in envelope},
            )
            verdict.meta["retried"] = attempt == 1
            self._log(question, verdict)
            return verdict
        raise JudgeError(
            f"judge produced invalid rubric JSON twice ({last_err})")

    def _log(self, question: str, v: JudgeVerdict):
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "question_id": v.question_id, "question": question,
            "model": v.model, "prompt": v.prompt_version,
            "refusal": v.refusal, "invented_results": v.invented_results,
            "coverage": v.coverage, "missing_facts": v.missing_facts,
            "reasoning": v.reasoning, "passed": v.passed, "meta": v.meta,
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
