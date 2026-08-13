"""Regression evaluation runner for the first-board chat Agent."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from app.agents.chat import answer_first_board_chat
from app.models import AgentChatRequest, AgentChatResponse, LimitUpEvent
from app.services.llm_provider import LLMProvider, LLMResult


FORBIDDEN_INVESTMENT_TERMS = ("买入", "卖出", "仓位", "目标价", "收益承诺")


class OfflineEvalLLMProvider(LLMProvider):
    """Provider that forces the deterministic fallback path for stable evals."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        """Always behave as if the LLM is unavailable."""

        raise RuntimeError("LLM disabled for deterministic eval")


@dataclass(frozen=True)
class AgentEvalCase:
    """One chat Agent regression case loaded from JSON."""

    case_id: str
    message: str
    expected_intent: str | None = None
    intent_hint: str | None = None
    trade_date: str | None = None
    symbol: str | None = None
    required_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    answer_contains: list[str] = field(default_factory=list)
    answer_not_contains: list[str] = field(default_factory=list)
    require_warning: bool = False
    forbid_investment_terms: bool = True


@dataclass(frozen=True)
class AgentEvalCaseResult:
    """Result for one Agent regression case."""

    case_id: str
    passed: bool
    failures: list[str]
    intent: str
    tool_calls: list[str]
    planner_tool_calls: list[str]
    trace_names: list[str]
    warnings: list[str]
    backend_repaired_tools: list[str]
    repair_reasons: list[str]
    answer_preview: str


@dataclass(frozen=True)
class AgentEvalSuiteResult:
    """Aggregated Agent regression suite result."""

    total: int
    passed: int
    failed: int
    results: list[AgentEvalCaseResult]

    @property
    def ok(self) -> bool:
        """Return whether every case passed."""

        return self.failed == 0


def load_eval_cases(path: Path) -> list[AgentEvalCase]:
    """Load Agent eval cases from a JSON fixture file."""

    data = json.loads(path.read_text(encoding="utf-8"))
    return [AgentEvalCase(**item) for item in data["cases"]]


def run_agent_eval_suite(
    *,
    cases: list[AgentEvalCase],
    events: list[LimitUpEvent],
    llm_provider: LLMProvider | None = None,
    check_intent: bool = True,
) -> AgentEvalSuiteResult:
    """Run all eval cases and return pass/fail details."""

    provider = llm_provider or OfflineEvalLLMProvider()
    results = [
        run_agent_eval_case(
            case=case,
            events=events,
            llm_provider=provider,
            check_intent=check_intent,
        )
        for case in cases
    ]
    passed = sum(1 for result in results if result.passed)
    return AgentEvalSuiteResult(
        total=len(results),
        passed=passed,
        failed=len(results) - passed,
        results=results,
    )


def run_agent_eval_case(
    *,
    case: AgentEvalCase,
    events: list[LimitUpEvent],
    llm_provider: LLMProvider,
    check_intent: bool = True,
) -> AgentEvalCaseResult:
    """Run one eval case and check intent, tools, answer facts, and safety."""

    request = AgentChatRequest(
        session_id=f"eval-{case.case_id}",
        message=case.message,
        intent_hint=case.intent_hint,
        trade_date=case.trade_date,
        symbol=case.symbol,
    )
    previous_force_template = os.environ.get("LIMITUPLAB_FORCE_TEMPLATE_ANSWER")
    os.environ["LIMITUPLAB_FORCE_TEMPLATE_ANSWER"] = "true"
    try:
        response = answer_first_board_chat(
            request=request,
            events=events,
            llm_provider=llm_provider,
        )
    finally:
        if previous_force_template is None:
            os.environ.pop("LIMITUPLAB_FORCE_TEMPLATE_ANSWER", None)
        else:
            os.environ["LIMITUPLAB_FORCE_TEMPLATE_ANSWER"] = previous_force_template
    failures = _check_response(case, response, check_intent=check_intent)
    return AgentEvalCaseResult(
        case_id=case.case_id,
        passed=not failures,
        failures=failures,
        intent=response.intent,
        tool_calls=response.tool_calls,
        planner_tool_calls=_planner_tool_calls(response),
        trace_names=[trace.name for trace in response.tool_results],
        warnings=response.warnings,
        backend_repaired_tools=_backend_repaired_tools(response),
        repair_reasons=response.tool_policy.repair_reasons,
        answer_preview=response.answer[:180],
    )


def eval_suite_report(suite: AgentEvalSuiteResult) -> dict:
    """Serialize a suite result for CLI output and failure reports."""

    return {
        "total": suite.total,
        "passed": suite.passed,
        "failed": suite.failed,
        "results": [
            {
                "case_id": result.case_id,
                "passed": result.passed,
                "failures": result.failures,
                "intent": result.intent,
                "planner_tool_calls": result.planner_tool_calls,
                "final_tool_calls": result.tool_calls,
                "backend_repaired_tools": result.backend_repaired_tools,
                "repair_reasons": result.repair_reasons,
                "trace_names": result.trace_names,
                "warnings": result.warnings,
                "answer_preview": result.answer_preview,
            }
            for result in suite.results
        ],
    }


def eval_failure_report(suite: AgentEvalSuiteResult) -> dict:
    """Serialize failed cases and repaired cases for model-quality review."""

    interesting = [
        result
        for result in suite.results
        if (not result.passed) or result.backend_repaired_tools
    ]
    return {
        "total": suite.total,
        "passed": suite.passed,
        "failed": suite.failed,
        "interesting_count": len(interesting),
        "results": [
            {
                "case_id": result.case_id,
                "passed": result.passed,
                "failures": result.failures,
                "intent": result.intent,
                "planner_tool_calls": result.planner_tool_calls,
                "final_tool_calls": result.tool_calls,
                "backend_repaired_tools": result.backend_repaired_tools,
                "repair_reasons": result.repair_reasons,
                "trace_names": result.trace_names,
                "warnings": result.warnings,
                "answer_preview": result.answer_preview,
            }
            for result in interesting
        ],
    }


def _check_response(
    case: AgentEvalCase,
    response: AgentChatResponse,
    *,
    check_intent: bool,
) -> list[str]:
    failures: list[str] = []
    trace_names = [trace.name for trace in response.tool_results]
    if check_intent and case.expected_intent and response.intent != case.expected_intent:
        failures.append(
            f"intent expected {case.expected_intent}, got {response.intent}"
        )

    for tool in case.required_tools:
        if tool not in response.tool_calls and tool not in trace_names:
            failures.append(f"required tool missing: {tool}")

    for tool in case.forbidden_tools:
        if tool in response.tool_calls:
            failures.append(f"forbidden tool was called: {tool}")

    for text in case.answer_contains:
        if _normalize_answer_text(text) not in _normalize_answer_text(response.answer):
            failures.append(f"answer missing text: {text}")

    for text in case.answer_not_contains:
        if _normalize_answer_text(text) in _normalize_answer_text(response.answer):
            failures.append(f"answer contains forbidden text: {text}")

    if case.require_warning and not response.warnings:
        failures.append("expected at least one warning")

    if case.forbid_investment_terms:
        rendered = response.model_dump_json()
        for term in FORBIDDEN_INVESTMENT_TERMS:
            if term in rendered:
                failures.append(f"investment term leaked: {term}")

    return failures


def _normalize_answer_text(value: str) -> str:
    """Normalize answer text for robust eval comparisons."""

    return (
        value.replace("09:", "9:")
        .replace("，", ",")
        .replace("（", "(")
        .replace("）", ")")
        .replace("三连板", "3板")
        .replace("二连板", "2板")
    )


def _planner_tool_calls(response: AgentChatResponse) -> list[str]:
    for trace in response.tool_results:
        if trace.name != "llm_tool_planner":
            continue
        raw_calls = trace.input.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            return []
        names: list[str] = []
        for raw_call in raw_calls:
            if isinstance(raw_call, dict) and raw_call.get("name"):
                names.append(str(raw_call["name"]))
        return names
    return []


def _backend_repaired_tools(response: AgentChatResponse) -> list[str]:
    planner_calls = set(_planner_tool_calls(response))
    if not planner_calls:
        return []
    repair_ignored = {"llm_tool_planner", "llm_tool_answer", "template_general_answer"}
    return [
        tool
        for tool in response.tool_calls
        if tool not in planner_calls and tool not in repair_ignored
    ]
