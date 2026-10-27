"""Bounded LLM judge for otherwise-structured borderline candidates."""

import json

import pytest

from app.services.llm_provider import LLMResult
from app.services.memory_extraction import MemoryCandidateDraft
from app.services.memory_judge import (
    JudgeUnavailable,
    judge_candidate,
    judgeable_candidate,
)


class JudgeProvider:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def generate_function_call(self, *args, **kwargs):
        self.calls.append(kwargs.get("function_name"))
        return LLMResult(content=json.dumps(self.payload, ensure_ascii=False),
                         model="fixture", provider="fixture")


def condition_candidate(**changes):
    fields = {
        "memory_type": "preference",
        "candidate_action": "upsert",
        "content_text": "detailed",
        "canonical_key": "response_style",
        "scope_type": "condition",
        "scope": {"condition": "市场大跌时"},
        "structured": {},
        "confidence": 0.75,
        "stability": "conditional_scope",
        "source_quote": "以后市场大跌时详细一点",
        "reason": "条件性偏好",
    }
    fields.update(changes)
    return MemoryCandidateDraft.model_validate(fields)


def add_decision():
    return {
        "decision": "add",
        "target_memory_id": None,
        "superseded_candidate_id": None,
    }


def test_only_scoped_borderline_confidence_is_judgeable():
    candidate = condition_candidate()
    judgeable, reasons = judgeable_candidate(
        candidate,
        key="response_style",
        message="以后市场大跌时详细一点",
        consolidation=add_decision(),
    )
    assert judgeable is True
    assert "low_confidence" in reasons

    global_candidate = condition_candidate(
        scope_type="global", scope={},
        source_quote="以后回答简洁一点", content_text="concise",
    )
    judgeable, _ = judgeable_candidate(
        global_candidate,
        key="response_style",
        message="以后回答简洁一点",
        consolidation=add_decision(),
    )
    assert judgeable is False

    assert judgeable_candidate(
        condition_candidate(confidence=0.95, stability="explicit_future"),
        key="response_style",
        message="以后市场大跌时详细一点",
        consolidation=add_decision(),
    )[0] is False


def test_judge_accepts_only_closed_decisions():
    provider = JudgeProvider({"decision": "approve", "risk": "low", "reason": "明确条件偏好"})
    result = judge_candidate(
        provider, message="以后市场大跌时详细一点", candidate=condition_candidate(),
    )
    assert result.decision == "approve"
    assert result.risk == "low"
    assert provider.calls == ["submit_memory_judgement"]


def test_judge_errors_fail_closed():
    class Broken:
        def generate_function_call(self, *args, **kwargs):
            raise RuntimeError("network")

        def generate(self, *args, **kwargs):
            raise RuntimeError("network")

    with pytest.raises(JudgeUnavailable):
        judge_candidate(Broken(), message="以后市场大跌时详细一点",
                        candidate=condition_candidate())

    provider = JudgeProvider({"decision": "approve", "risk": "low"})
    with pytest.raises(JudgeUnavailable):
        judge_candidate(provider, message="以后市场大跌时详细一点",
                        candidate=condition_candidate())
