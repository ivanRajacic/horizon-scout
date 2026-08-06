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
from dataclasses import dataclass, field
from pathlib import Path

from src.claude_cli import call_claude, shared_semaphore
from src.config import (CLAUDE_CONCURRENCY, CLAUDE_MAX_CONCURRENCY,
                        JUDGE_BACKENDS, JUDGE_DEFAULT, JUDGE_LOG_PATH,
                        JUDGE_MODELS, JUDGE_PASS_FACTUAL,
                        JUDGE_PASS_FAITHFULNESS)
from src.eval import usage
from src.judge.judge import Judge
from src.judge.ragas_backend import (ClaudeCliLLM,  # installs the ragas shim
                                     OpenAICompatLLM)
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

# FactualCorrectness mode (2026-08-06, user decision). ragas defaults to "f1".
#
# READ THE COUNTERS BEFORE CHANGING THIS - ragas' mode names are inverted
# relative to what they measure. It runs two claim decompositions in opposite
# directions (`_factual_correctness.py`), and
# `decompose_and_verify_claims(a, b)` decomposes a and verifies each claim
# against b:
#   tp = reference claims the ANSWER supports      (covered)
#   fp = reference claims the answer does NOT      (omitted)
#   fn = answer claims the REFERENCE does not      (extra content)
# so "precision" = tp/(tp+fp) = the fraction of the reference the answer
# covered, with extra content absent from the formula; "recall" = tp/(tp+fn) =
# a penalty on saying anything the reference does not contain; "f1" is the
# harmonic mean and always sits between them.
#
# We want the first: the generator's answers run ~2,082 chars against
# references of ~1,149, and on full-2026-08-06 the shorter half scored 0.497
# mean factual against 0.282 for the longer half - length, not correctness, was
# moving the number. "precision" makes length free. The tradeoff, disclosed in
# the write-up: it does not charge for invented content. Faithfulness still
# does, against the retrieved context, and so does the adversarial rubric's
# invented_results.
#
# Measured on the same 33 answers, judged three times: f1 0.374 mean, recall
# 0.341 (the trial that exposed the inverted naming), precision below.
# EVERY number recorded before 2026-08-06 used f1 and is not comparable.
FACTUAL_MODE = "precision"
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
    # Overlay only. refusal is what the ADV grade IS ("explicit" passes);
    # coverage rides along as the bonus it became in j0.3, so a run can show
    # how much forensic detail correct refusals happened to carry.
    refusal: str | None = None
    invented_results: list[str] = field(default_factory=list)
    coverage: str | None = None


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
    semaphore caps this pool's concurrent judge calls across BOTH paths -
    the judge seat's own semaphore on the api backend (v5 default), the
    process-wide `claude -p` gate on the legacy claude backend. Which
    backend a model_key selects is pinned in config.JUDGE_BACKENDS."""

    def __init__(self, model_key: str = JUDGE_DEFAULT,
                 concurrency: int | None = None,
                 log_path: Path = JUDGE_LOG_PATH,
                 transport=None):
        self.model_key = model_key
        self.model = JUDGE_MODELS[model_key]
        self.log_path = log_path
        self.backend_kind = JUDGE_BACKENDS.get(model_key, "claude")

        if self.backend_kind == "api":
            from src.openai_compat import (JUDGE_SEAT, call_api,
                                           call_api_gated)
            api_transport = transport or call_api
            if concurrency is None:
                self.concurrency = JUDGE_SEAT.concurrency
                self._sem = JUDGE_SEAT.semaphore
            else:
                self.concurrency = max(1, int(concurrency))
                self._sem = threading.Semaphore(self.concurrency)
            self.backend = OpenAICompatLLM(JUDGE_SEAT, self._sem,
                                           transport=api_transport)

            def rubric_transport(prompt, model, **kw):
                # The rubric judge speaks (prompt, model) -> envelope; the
                # seat pins the model, and going through call_api_gated
                # keeps backoff and usage recording identical to the RAGAS
                # path - the overlay's spend is on the record too.
                return call_api_gated([{"role": "user", "content": prompt}],
                                      JUDGE_SEAT, transport=api_transport,
                                      semaphore=self._sem)

            self.rubric = Judge(model_key=model_key, log_path=log_path,
                                transport=rubric_transport)
        else:
            transport = transport or call_claude
            if concurrency is None:
                # Default: the process-wide `claude -p` gate, shared with the
                # generation clients - the global cap holds across all paths.
                self.concurrency = min(CLAUDE_CONCURRENCY,
                                       CLAUDE_MAX_CONCURRENCY)
                self._sem = shared_semaphore()
            else:
                self.concurrency = max(1, min(int(concurrency),
                                              CLAUDE_MAX_CONCURRENCY))
                self._sem = threading.Semaphore(self.concurrency)

            def gated(prompt, model, **kw):
                with self._sem:
                    return transport(prompt, model, **kw)

            self.backend = ClaudeCliLLM(self.model, self._sem,
                                        transport=transport)
            self.rubric = Judge(model_key=model_key, log_path=log_path,
                                transport=gated)

        self.faithfulness = Faithfulness(llm=self.backend)
        self.factual = FactualCorrectness(llm=self.backend, mode=FACTUAL_MODE)
        self.faithfulness.nli_statements_prompt.instruction += NLI_LENIENCY
        self.factual.nli_prompt.instruction += NLI_LENIENCY

    def stats(self) -> dict:
        """The backend's parse-health counters (api backend only): DeepSeek's
        loose JSON mode can fail silently inside ragas - see ragas_backend -
        so completions and unparseable-JSON counts are surfaced per run."""
        stats = getattr(self.backend, "stats", None)
        return stats() if callable(stats) else {}

    async def judge_case(self, case: dict) -> PoolVerdict:
        qid = case.get("question_id")
        q = case["question"]
        ref = case["reference_answer"]
        ans = case["answer"]

        # Label this case's `claude -p` calls so a concurrent batch's cost
        # splits back out per question. asyncio.gather gives every case its own
        # context copy and asyncio.to_thread carries it into the worker, so
        # neither the metric fan-out below nor the overlay can cross-label.
        with usage.stage(qid or usage.UNATTRIBUTED, "judge"):
            if case.get("adversarial"):
                v = await asyncio.to_thread(self.rubric.judge, q, ref, ans, qid)
                return PoolVerdict(question_id=qid, path="overlay",
                                   passed=v.passed, model=self.model,
                                   detail=v.reasoning, refusal=v.refusal,
                                   invented_results=v.invented_results,
                                   coverage=v.coverage)

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

    async def judge_batch(self, cases: list[dict], on_verdict=None) -> list:
        """One verdict (or exception) per case, order-preserving. Exceptions
        are returned, not raised, so one bad case never sinks a batch - the
        caller must check for and report them (loud failure stays loud).

        on_verdict(case, verdict_or_exception) fires as each case LANDS rather
        than when the batch does. A batch of 11 takes minutes; without this the
        caller has nothing to show for it until the last one returns. It runs
        on the event loop thread, one case at a time, so a callback that writes
        needs no lock of its own on this account.

        A callback that raises is contained the same way a bad case is: the
        other ten judges are already mid-flight and their cost is already
        spent, so one broken callback must not throw that away. The raised
        exception REPLACES that case's verdict, which is how it stays visible
        instead of being swallowed.
        """
        async def one(case):
            try:
                verdict = await self.judge_case(case)
            except Exception as e:                           # noqa: BLE001
                verdict = e
            if on_verdict is not None:
                try:
                    on_verdict(case, verdict)
                except Exception as e:                       # noqa: BLE001
                    return e
            return verdict

        return await asyncio.gather(*(one(c) for c in cases))

    def judge_all(self, cases: list[dict], on_verdict=None) -> list:
        return asyncio.run(self.judge_batch(cases, on_verdict))

    def _log(self, question: str, v: PoolVerdict):
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "question_id": v.question_id, "question": question,
            "model": v.model, "path": v.path,
            "ragas_version": ragas.__version__,
            "nli_leniency": f"{NLI_LENIENCY_VERSION}:"
                            f"{fingerprint(NLI_LENIENCY)}",
            # Load-bearing: f1 and recall are different instruments and a run
            # judged under one cannot be read against the other.
            "factual_mode": FACTUAL_MODE,
            "faithfulness": v.faithfulness,
            "factual_correctness": v.factual_correctness,
            "thresholds": {"factual": JUDGE_PASS_FACTUAL,
                           "faithfulness": JUDGE_PASS_FAITHFULNESS},
            "passed": v.passed, "detail": v.detail,
            **({"refusal": v.refusal, "invented_results": v.invented_results,
                "coverage": v.coverage} if v.path == "overlay" else {}),
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
