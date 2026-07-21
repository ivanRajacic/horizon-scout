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


GOOD = json.dumps({"coverage": "full", "missing_facts": [],
                   "unsupported_claims": [], "reasoning": "covers the fact"})


def mk_judge(tmp_path, transport):
    return Judge(model_key="haiku", log_path=tmp_path / "judge.jsonl",
                 transport=transport)


# --- pure logic ---

def test_derive_pass_rule():
    assert derive_pass("full", [])
    assert not derive_pass("partial", [])
    assert not derive_pass("none", [])
    assert not derive_pass("full", ["invented claim"])


def test_parse_rubric_extracts_from_noise():
    obj = _parse_rubric(f"Here is my grading:\n{GOOD}\nDone.")
    assert obj["coverage"] == "full"


@pytest.mark.parametrize("bad", [
    "no json here",
    '{"coverage": "excellent", "missing_facts": [], "unsupported_claims": []}',
    '{"coverage": "full", "missing_facts": "none", "unsupported_claims": []}',
])
def test_parse_rubric_rejects_invalid(bad):
    with pytest.raises(ValueError):
        _parse_rubric(bad)


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
    assert v.passed and v.coverage == "full"
    assert v.model == "claude-haiku-4-5-20251001"
    assert v.prompt_version.startswith(JUDGE_PROMPT_VERSION + ":")
    logged = json.loads((tmp_path / "judge.jsonl").read_text(encoding="utf-8"))
    assert logged["question_id"] == "js-01" and logged["passed"] is True
    assert logged["model"] == v.model and logged["prompt"] == v.prompt_version


def test_unsupported_claim_fails(tmp_path):
    bad = json.dumps({"coverage": "full", "missing_facts": [],
                      "unsupported_claims": ["invented wind turbines"],
                      "reasoning": "extra claim"})
    v = mk_judge(tmp_path, FakeTransport([bad])).judge("q?", "ref", "ans")
    assert not v.passed and v.unsupported_claims


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
