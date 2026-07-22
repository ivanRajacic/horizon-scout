"""RAGAS-based judge (M5, RQ5): faithfulness + factual correctness for
ordinary questions; the rubric judge (judge.py) stays as the refusal overlay
for adversarial questions.

Why the split: claim-decomposition metrics are structurally blind to correct
refusals - "no projects match" decomposes into ~zero claims (score undefined)
while a fabricated answer full of invented projects produces well-formed
claims and can score misleadingly well. Those are exactly H5c's cells, so the
bank's `adversarial` flag routes them to the explicit rubric rule instead:
pass iff the answer states that nothing matches and invents nothing.

Pass rule for the RAGAS path lives in derive_ragas_pass, in code, with
thresholds in config - pilot drafts until the d10 freeze:
  factual_correctness (claim-level F1 vs the reference) must clear its
  threshold, and faithfulness (claims grounded in retrieved contexts) must
  clear its own whenever contexts exist to check against. Questions judged
  without retrieved contexts (e.g. SQL-route answers) skip faithfulness.

Every verdict is logged to judge.jsonl: path, scores, thresholds, pinned
model, ragas version. The rubric overlay logs through judge.py as before.
"""

import asyncio
import json
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from src.claude_cli import call_claude, shared_semaphore
from src.config import (CLAUDE_CONCURRENCY, CLAUDE_MAX_CONCURRENCY,
                        JUDGE_DEFAULT, JUDGE_LOG_PATH, JUDGE_MODELS,
                        JUDGE_PASS_FACTUAL, JUDGE_PASS_FAITHFULNESS)
from src.judge.judge import Judge
from src.judge.ragas_backend import ClaudeCliLLM  # installs the ragas shim
from src.llm import fingerprint

import ragas  # noqa: E402  (after backend import: shim must run first)
from ragas.dataset_schema import SingleTurnSample  # noqa: E402
from ragas.metrics import FactualCorrectness, Faithfulness  # noqa: E402

# Appended to both metrics' NLI instructions. Without it the claim check is
# hyper-literal: "2,127 projects in the database were terminated" was judged
# UNSUPPORTED by the reference "2127 projects have status TERMINATED" because
# "in the database" is not verbatim in the reference - framing counted as an
# invented fact, F1 went to 0 on a correct answer (js-01). This note is part
# of the versioned judge rubric: bump the version on ANY edit; frozen at d10.
NLI_LENIENCY_VERSION = "n1-pilot"
NLI_LENIENCY = (
    "\nJudge semantic support, not verbatim wording: a statement is supported "
    "when its factual content follows from the context. Trivial framing about "
    "the data source (e.g. 'in the database', 'according to the data') and "
    "number, date, or currency reformatting do NOT make a statement "
    "unsupported. Only genuinely new factual content (entities, numbers, "
    "events, properties absent from the context) makes a statement "
    "unsupported.")


@dataclass
class PoolVerdict:
    question_id: str | None
    path: str                                # "ragas" | "overlay"
    passed: bool
    model: str
    faithfulness: float | None = None        # None: not measurable / overlay
    factual_correctness: float | None = None
    detail: str = ""                         # overlay reasoning; ragas notes


def _score(value) -> float | None:
    """ragas returns NaN when a metric is undefined (e.g. zero claims
    extracted); normalize that to None so it can never satisfy a >= test."""
    f = float(value)
    return None if math.isnan(f) else f


def derive_ragas_pass(faithfulness: float | None,
                      factual: float | None) -> bool:
    """The RAGAS-path pass rule, in code (thresholds in config, frozen d10)."""
    if factual is None or factual < JUDGE_PASS_FACTUAL:
        return False
    if faithfulness is not None and faithfulness < JUDGE_PASS_FAITHFULNESS:
        return False
    return True


class JudgePool:
    """Judges a batch of cases concurrently, dispatching each to the RAGAS
    metrics or the rubric overlay by the case's `adversarial` flag. One
    semaphore caps concurrent `claude -p` processes across BOTH paths."""

    def __init__(self, model_key: str = JUDGE_DEFAULT,
                 concurrency: int | None = None,
                 log_path: Path = JUDGE_LOG_PATH,
                 transport=call_claude):
        self.model_key = model_key
        self.model = JUDGE_MODELS[model_key]
        self.log_path = log_path
        if concurrency is None:
            # Default: the process-wide `claude -p` gate, shared with the
            # generation clients - the global cap holds across all paths.
            self.concurrency = min(CLAUDE_CONCURRENCY, CLAUDE_MAX_CONCURRENCY)
            self._sem = shared_semaphore()
        else:
            self.concurrency = max(1, min(int(concurrency),
                                          CLAUDE_MAX_CONCURRENCY))
            self._sem = threading.Semaphore(self.concurrency)

        def gated(prompt, model, **kw):
            with self._sem:
                return transport(prompt, model, **kw)

        self.backend = ClaudeCliLLM(self.model, self._sem, transport=transport)
        self.rubric = Judge(model_key=model_key, log_path=log_path,
                            transport=gated)
        self.faithfulness = Faithfulness(llm=self.backend)
        self.factual = FactualCorrectness(llm=self.backend)
        self.faithfulness.nli_statements_prompt.instruction += NLI_LENIENCY
        self.factual.nli_prompt.instruction += NLI_LENIENCY

    async def judge_case(self, case: dict) -> PoolVerdict:
        qid = case.get("question_id")
        q = case["question"]
        ref = case["reference_answer"]
        ans = case["answer"]

        if case.get("adversarial"):
            v = await asyncio.to_thread(self.rubric.judge, q, ref, ans, qid)
            return PoolVerdict(question_id=qid, path="overlay",
                               passed=v.passed, model=self.model,
                               detail=v.reasoning)

        contexts = case.get("contexts") or []
        sample = SingleTurnSample(user_input=q, response=ans, reference=ref,
                                  retrieved_contexts=contexts)
        tasks = [self.factual.single_turn_ascore(sample)]
        if contexts:
            tasks.append(self.faithfulness.single_turn_ascore(sample))
        scores = await asyncio.gather(*tasks)
        factual = _score(scores[0])
        faith = _score(scores[1]) if contexts else None

        notes = []
        if not contexts:
            notes.append("faithfulness skipped: no retrieved contexts")
        elif faith is None:
            notes.append("faithfulness undefined (NaN): no claims extracted")
        if factual is None:
            notes.append("factual_correctness undefined (NaN)")

        verdict = PoolVerdict(
            question_id=qid, path="ragas",
            passed=derive_ragas_pass(faith, factual), model=self.model,
            faithfulness=faith, factual_correctness=factual,
            detail="; ".join(notes))
        self._log(q, verdict)
        return verdict

    async def judge_batch(self, cases: list[dict]) -> list:
        """One verdict (or exception) per case, order-preserving. Exceptions
        are returned, not raised, so one bad case never sinks a batch - the
        caller must check for and report them (loud failure stays loud)."""
        return await asyncio.gather(*(self.judge_case(c) for c in cases),
                                    return_exceptions=True)

    def judge_all(self, cases: list[dict]) -> list:
        return asyncio.run(self.judge_batch(cases))

    def _log(self, question: str, v: PoolVerdict):
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "question_id": v.question_id, "question": question,
            "model": v.model, "path": v.path,
            "ragas_version": ragas.__version__,
            "nli_leniency": f"{NLI_LENIENCY_VERSION}:"
                            f"{fingerprint(NLI_LENIENCY)}",
            "faithfulness": v.faithfulness,
            "factual_correctness": v.factual_correctness,
            "thresholds": {"factual": JUDGE_PASS_FACTUAL,
                           "faithfulness": JUDGE_PASS_FAITHFULNESS},
            "passed": v.passed, "detail": v.detail,
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
