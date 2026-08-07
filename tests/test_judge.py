"""Judge unit tests: rubric parsing, pass derivation, retry-then-loud-failure,
and verdict logging. Transport is faked - no `claude` CLI or network needed."""

import json

import pytest

from src.judge.judge import (Judge, JudgeError, JUDGE_PROMPT_VERSION,
                             _parse_rubric, build_prompt, derive_pass)


def envelope(result: str, **extra) -> dict:
    return {"type": "result", "result": result, "is_error": False,
            "duration_ms": 1200, "num_turns": 1, "total_cost_usd": 0.0,
            **extra}


class FakeTransport:
    def __init__(self, results):
        self.results = list(results)
        self.prompts = []

    def __call__(self, prompt, model, timeout_s=None):
        self.prompts.append(prompt)
        return envelope(self.results.pop(0))


def rubric(refusal="explicit", invented=(), coverage="none",
           missing=(), reasoning="graded"):
    return json.dumps({"refusal": refusal,
                       "invented_results": list(invented),
                       "coverage": coverage, "missing_facts": list(missing),
                       "reasoning": reasoning})


GOOD = rubric()


def mk_judge(tmp_path, transport):
    return Judge(model_key="haiku", log_path=tmp_path / "judge.jsonl",
                 transport=transport)


# --- default transport resolution ---

def test_default_transport_follows_judge_backends(tmp_path):
    """Judge() used to default to model_key=deepseek WITH transport=
    call_claude - an API model on the `claude -p` shell-out. None now
    resolves from config.JUDGE_BACKENDS; nothing is called, only resolved."""
    from src.claude_cli import call_claude
    api_judge = Judge(log_path=tmp_path / "judge.jsonl")   # deepseek default
    assert api_judge.transport is not call_claude
    assert callable(api_judge.transport)
    legacy = Judge(model_key="haiku", log_path=tmp_path / "judge.jsonl")
    assert legacy.transport is call_claude
    # explicit injection still wins
    fake = FakeTransport([])
    assert mk_judge(tmp_path, fake).transport is fake


# --- pure logic ---

def test_derive_pass_is_the_refusal_and_nothing_else():
    """j0.3: pass iff the answer said outright that the thing is not there
    and did not supply it anyway."""
    assert derive_pass("explicit", [])
    # A search that came up empty is not a statement that the thing is absent.
    assert not derive_pass("hedged", [])
    assert not derive_pass("none", [])
    # Refusing and then supplying the missing thing is not a refusal.
    assert not derive_pass("explicit", ["the committed amount is EUR 1.2bn"])


def test_coverage_never_decides_an_adversarial_verdict():
    """The whole point of j0.3. A clean refusal that adds none of the
    reference's forensics passes; adv-06 failed exactly this way under j0.2,
    and every future correct refusal would have too."""
    assert derive_pass("explicit", [])          # coverage is not an argument
    for level in ("full", "partial", "none"):
        obj = _parse_rubric(rubric(coverage=level))
        assert derive_pass(obj["refusal"], obj["invented_results"])


def test_parse_rubric_extracts_from_noise():
    obj = _parse_rubric(f"Here is my grading:\n{GOOD}\nDone.")
    assert obj["refusal"] == "explicit"


@pytest.mark.parametrize("bad", [
    "no json here",
    '{"refusal": "maybe", "invented_results": [], "coverage": "none", '
    '"missing_facts": []}',
    '{"refusal": "explicit", "invented_results": [], '
    '"coverage": "excellent", "missing_facts": []}',
    '{"refusal": "explicit", "invented_results": [], "coverage": "none", '
    '"missing_facts": "none"}',
    '{"refusal": "explicit", "invented_results": "a claim", '
    '"coverage": "none", "missing_facts": []}',
    # The pre-j0.3 shape must not parse as something the new rule can grade.
    '{"coverage": "full", "missing_facts": [], "unsupported_claims": []}',
])
def test_parse_rubric_rejects_invalid(bad):
    with pytest.raises(ValueError):
        _parse_rubric(bad)


def test_rubric_tells_the_judge_a_bare_refusal_is_a_good_answer():
    p = build_prompt("q?", "ref", "ans")
    assert "NOT a list of things the answer has to repeat" in p
    assert "never as a requirement" in p


def test_prompt_is_condition_blind():
    # The judge must never see which experimental condition produced the
    # answer - the prompt has no slot for it and no such vocabulary.
    p = build_prompt("q?", "ref", "ans").lower()
    for leak in ("router", "hybrid", "scoped", "condition", "sql"):
        assert leak not in p


# --- judge flow ---

def test_verdict_and_log(tmp_path):
    j = mk_judge(tmp_path, FakeTransport([GOOD]))
    v = j.judge("q?", "ref", "ans", question_id="js-01")
    assert v.passed and v.refusal == "explicit"
    assert v.model == "claude-haiku-4-5-20251001"
    assert v.prompt_version.startswith(JUDGE_PROMPT_VERSION + ":")
    logged = json.loads((tmp_path / "judge.jsonl").read_text(encoding="utf-8"))
    assert logged["question_id"] == "js-01" and logged["passed"] is True
    assert logged["refusal"] == "explicit" and logged["coverage"] == "none"
    assert logged["model"] == v.model and logged["prompt"] == v.prompt_version


def test_missed_forensics_are_recorded_not_penalized(tmp_path):
    """adv-06's shape: refused correctly, named none of the 62 volcanology
    projects. Recorded as missing, still a pass."""
    v = mk_judge(tmp_path, FakeTransport([
        rubric(coverage="none",
               missing=["the 62 volcanology projects", "EPOS IP"])
    ])).judge("q?", "ref", "ans")
    assert v.passed and v.coverage == "none"
    assert v.missing_facts == ["the 62 volcanology projects", "EPOS IP"]


def test_supplying_the_missing_thing_fails_even_after_a_refusal(tmp_path):
    """adv-04's shape: no paid-out data exists, so a committed figure offered
    as the answer is fabricated no matter how the sentence opens."""
    v = mk_judge(tmp_path, FakeTransport([
        rubric(invented=["EUR 1.2bn was paid out"])
    ])).judge("q?", "ref", "ans")
    assert not v.passed
    assert v.invented_results == ["EUR 1.2bn was paid out"]


def test_retry_then_success(tmp_path):
    t = FakeTransport(["garbage", GOOD])
    v = mk_judge(tmp_path, t).judge("q?", "ref", "ans")
    assert v.passed and v.meta["retried"] is True
    assert "ONLY the JSON" in t.prompts[1]


def test_two_failures_raise_loudly(tmp_path):
    j = mk_judge(tmp_path, FakeTransport(["garbage", "still garbage"]))
    with pytest.raises(JudgeError):
        j.judge("q?", "ref", "ans")
    assert not (tmp_path / "judge.jsonl").exists()  # no verdict, no log line


def test_unknown_model_key_rejected(tmp_path):
    with pytest.raises(ValueError):
        Judge(model_key="opus", log_path=tmp_path / "j.jsonl",
              transport=FakeTransport([]))
