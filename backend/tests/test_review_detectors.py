"""Deterministic checks and judge input/output validation."""

import json

import pytest

from app.services.llm_provider import (
    LLMResult,
    NativeFunctionCallingUnavailable,
)
from app.services.review_checks import run_deterministic_checks
from app.services.review_judge import (
    DIMENSIONS,
    JudgeUnavailable,
    estimate_judge_cost,
    run_judge,
)
from app.services.review_snapshot import RunSnapshot


def snapshot(
    answer: str,
    *,
    task_status: str = "complete",
    tool_calls: list[str] | None = None,
    warnings: list[str] | None = None,
    intent: str = "market_summary",
    token_complete: bool = True,
    tool_evidence: list[dict] | None = None,
) -> RunSnapshot:
    evidence = {
        "task_status": task_status,
        "intent": intent,
        "tool_calls": tool_calls if tool_calls is not None else ["market_summary"],
        "warnings": warnings or [],
        "tool_result_count": 1,
        "token_usage_complete": token_complete,
    }
    return RunSnapshot(
        run_id="run_snapshot",
        session_id="chat_snapshot",
        owner_id="visitor_" + "1" * 32,
        question="市场最高标是哪个",
        answer=answer,
        intent=intent,
        task_status=task_status,
        trace_url=None,
        evidence=evidence,
        tool_evidence=tool_evidence or [],
    )


class FakeProvider:
    def __init__(self, content: str, *, fallback: bool = False):
        self.content = content
        self.fallback = fallback
        self.calls = 0

    def generate_function_call(self, *args, **kwargs):
        self.calls += 1
        if self.fallback:
            raise NativeFunctionCallingUnavailable("not supported")
        return self._result()

    def generate(self, *args, **kwargs):
        self.calls += 1
        return self._result()

    def _result(self) -> LLMResult:
        return LLMResult(
            content=self.content,
            model="fake-judge",
            provider="fake",
            duration_ms=12,
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            response_mode="function_call",
        )


class CapturingProvider(FakeProvider):
    def __init__(self, content: str):
        super().__init__(content)
        self.prompt = ""

    def generate_function_call(self, system_prompt, user_prompt, **kwargs):
        self.prompt = user_prompt
        return super().generate_function_call(system_prompt, user_prompt, **kwargs)


def judge_json(fail_dimension: str | None = None, refs: list[str] | None = None) -> str:
    dimensions = []
    for dimension in DIMENSIONS:
        failed = dimension == fail_dimension
        dimensions.append({
            "dimension": dimension,
            "verdict": "fail" if failed else "pass",
            "reason": "证据显示字段不一致" if failed else "符合要求",
            "evidence_refs": (refs if refs is not None else ["answer"]) if failed else [],
        })
    return json.dumps({"dimensions": dimensions, "notes": ""}, ensure_ascii=False)


def test_deterministic_checks_pass_grounded_answer():
    report = run_deterministic_checks(snapshot(
        "市场最高标是测试股份（000001），收盘 10.20 元，涨幅 10%。",
        tool_calls=["market_summary"],
    ))
    assert report.overall == "pass"
    assert {item.check for item in report.checks} >= {"delivery_status", "tool_evidence"}


def test_deterministic_checks_flag_numbers_without_tool_evidence():
    report = run_deterministic_checks(snapshot(
        "收盘 10.20 元，涨幅 10%。",
        tool_calls=[],
    ))
    assert report.overall == "fail"
    failure = next(item for item in report.checks if item.check == "tool_evidence")
    assert failure.verdict == "fail"
    assert report.failures() == [failure]


def test_deterministic_checks_require_partial_disclosure():
    report = run_deterministic_checks(snapshot(
        "只有部分数据。",
        task_status="partial",
        tool_calls=["dragon_tiger_list"],
    ))
    assert report.overall == "fail"
    assert any(
        item.check == "missing_data_disclosure" and item.verdict == "fail"
        for item in report.checks
    )


def test_deterministic_checks_keep_unknown_separate_from_fail():
    report = run_deterministic_checks(snapshot(
        "研究结论仅供参考，不建议买入。",
        warnings=["tool fallback used"],
        token_complete=False,
    ))
    assert report.overall == "unknown"
    assert not report.failures()
    verdicts = {item.check: item.verdict for item in report.checks}
    assert verdicts["compliance_language"] == "unknown"
    assert verdicts["tool_warnings"] == "unknown"
    assert verdicts["token_measurement"] == "unknown"


def test_judge_accepts_evidence_backed_fail():
    outcome = run_judge(FakeProvider(judge_json("factual_grounding")), snapshot("回答"))
    assert outcome.report.overall == "fail"
    assert outcome.report.dimensions[1].verdict == "fail"
    assert outcome.prompt_tokens == 100
    assert outcome.total_tokens == 150


def test_judge_payload_includes_bounded_tool_excerpts():
    provider = CapturingProvider(judge_json())
    run_judge(
        provider,
        snapshot("最高标为测试股份。", tool_evidence=[{
            "tool": "market_summary",
            "status": "success",
            "excerpt": "最高标=测试股份 000678，5 连板",
            "truncated": False,
        }]),
    )
    assert "最高标=测试股份 000678" in provider.prompt
    assert "market_summary" in provider.prompt


def test_judge_rejects_invented_or_missing_evidence():
    with pytest.raises(JudgeUnavailable, match="unknown_evidence_reference"):
        run_judge(
            FakeProvider(judge_json("factual_grounding", ["invented"])),
            snapshot("回答"),
        )
    with pytest.raises(JudgeUnavailable, match="fail_without_evidence"):
        run_judge(
            FakeProvider(judge_json("factual_grounding", [])),
            snapshot("回答"),
        )


def test_judge_falls_back_to_plain_json_generation():
    provider = FakeProvider(judge_json(), fallback=True)
    outcome = run_judge(provider, snapshot("回答"))
    assert outcome.report.overall == "pass"
    assert provider.calls == 2


def test_judge_cost_requires_explicit_prices(monkeypatch):
    assert estimate_judge_cost(100, 50) == (0.0, False)
    monkeypatch.setenv("LIMITUPLAB_REVIEW_JUDGE_INPUT_COST_PER_MILLION", "1")
    monkeypatch.setenv("LIMITUPLAB_REVIEW_JUDGE_OUTPUT_COST_PER_MILLION", "2")
    cost, priced = estimate_judge_cost(1_000_000, 500_000)
    assert priced is True
    assert cost == 2.0
