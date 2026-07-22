"""Reference-based LLM judge (M5, v4): Sonnet via the shared `claude -p`
transport.

Design constraints from the plan:
- Transport is ONE function (src/claude_cli.py: call_claude, shared with
  generation and RAGAS): subscription -> API is a one-function swap, nothing
  downstream changes.
- The judge is blind to experimental condition: it sees question, reference,
  answer - never which pipeline produced the answer.
- The judge returns SUB-SCORES (coverage, missing facts, unsupported claims);
  pass/fail is derived IN CODE, so the pass threshold lives here, versioned,
  not inside the model's head.
- Rubric v0.1 is the pilot version; it is frozen before Study 2 (d10) and any
  edit bumps JUDGE_PROMPT_VERSION. Verdict logs carry "label:content-hash".
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

JUDGE_PROMPT_VERSION = "j0.1-pilot"

COVERAGE_LEVELS = ("full", "partial", "none")

RUBRIC_PROMPT = """You are grading one answer from a question-answering system \
over a database of EU Horizon 2020 research projects. You see the question, a \
reference, and the system's answer - nothing else.

The reference lists the key facts a correct answer should contain. It is NOT \
the only acceptable wording: grade fact coverage, never textual similarity. \
Numbers count as covered when they match after rounding or reformatting.

If the reference states that no matching data exists, a correct answer must \
say so; an answer that invents or supplies results anyway has coverage "none".

Grade two things:
1. coverage - how many of the reference's key facts appear in the answer:
   "full" (all of them), "partial" (some), "none".
2. unsupported_claims - substantive factual claims in the answer that the \
reference does not support (extra specifics, invented numbers, projects or \
outcomes not in the reference). Hedged statements of absence ("the excerpts \
do not mention X") are not claims.

Reply with STRICT JSON only - no markdown fences, no commentary:
{"coverage": "full|partial|none", "missing_facts": ["<key fact absent from the answer>", ...], "unsupported_claims": ["<unsupported claim quoted or paraphrased>", ...], "reasoning": "<at most three sentences>"}"""

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
    coverage: str
    missing_facts: list[str]
    unsupported_claims: list[str]
    reasoning: str
    passed: bool
    raw: str = ""                    # judge's raw text, for disagreement audits
    meta: dict = field(default_factory=dict)  # transport envelope subset


def build_prompt(question: str, reference: str, answer: str) -> str:
    return (f"{RUBRIC_PROMPT}\n\n"
            f"Question:\n{question}\n\n"
            f"Reference (key facts that should appear):\n{reference}\n\n"
            f"System answer to grade:\n{answer}")


def _parse_rubric(text: str) -> dict:
    """Extract and validate the rubric JSON or raise ValueError."""
    m = _JSON_RE.search(text)
    if not m:
        raise ValueError("no JSON object found in judge output")
    obj = json.loads(m.group(0))
    if obj.get("coverage") not in COVERAGE_LEVELS:
        raise ValueError(f"invalid coverage {obj.get('coverage')!r}")
    for key in ("missing_facts", "unsupported_claims"):
        v = obj.get(key)
        if not isinstance(v, list) or any(not isinstance(s, str) for s in v):
            raise ValueError(f"{key} must be a list of strings")
    return obj


def derive_pass(coverage: str, unsupported_claims: list[str]) -> bool:
    """The pass rule, in code (frozen with the rubric): adequate coverage AND
    no unsupported claims. v0.1 sets adequate = full."""
    return coverage == "full" and not unsupported_claims


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
                coverage=obj["coverage"],
                missing_facts=obj["missing_facts"],
                unsupported_claims=obj["unsupported_claims"],
                reasoning=str(obj.get("reasoning", "")),
                passed=derive_pass(obj["coverage"], obj["unsupported_claims"]),
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
            "coverage": v.coverage, "missing_facts": v.missing_facts,
            "unsupported_claims": v.unsupported_claims,
            "reasoning": v.reasoning, "passed": v.passed, "meta": v.meta,
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
