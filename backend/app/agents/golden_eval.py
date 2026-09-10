"""Four-layer end-to-end evaluation for the versioned Agent golden dataset."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter

from app.agents.answer_grounding import evaluate_answer_grounding
from app.agents.capability_contract import TOOL_CAPABILITIES
from app.agents.chat import answer_first_board_chat, template_answer_override
from app.agents.eval_runner import OfflineEvalLLMProvider
from app.agents.golden_dataset import GOLDEN_DATASET_VERSION, GoldenEvalCase
from app.agents.tools import AgentToolRegistry
from app.models import (
    AgentChatRequest,
    AgentRun,
    AgentToolTrace,
    ChatSessionMessage,
    LimitUpEvent,
)
from app.repositories import SQLiteFirstBoardRepository
from app.services.llm_provider import LLMProvider


INTERNAL_TOOL_NAMES = {
    "agent_plan",
    "llm_tool_planner",
    "llm_tool_answer",
    "template_general_answer",
}
REFUSAL_MARKERS = ("无法", "不能", "不提供", "没有可用", "未找到", "不支持")
LAYER_QUESTIONS = {
    "planner": "Planner 是否理解了用户意图并选择了正确能力？",
    "tool_execution": "必需工具是否执行，并使用了正确参数？",
    "grounding": "所需事实是否来自工具证据，回答是否存在无依据断言？",
    "answer": "最终回答是否完整、克制，并满足拒答与安全约束？",
}


@dataclass(frozen=True)
class GoldenLayerResult:
    """Pass/fail details for one stage of an Agent run."""

    passed: bool
    failures: list[str]
    observed: dict[str, object]


@dataclass(frozen=True)
class GoldenCaseResult:
    """One Agent run evaluated independently at all four layers."""

    case_id: str
    category: str
    question: str
    passed: bool
    duration_ms: int
    planner: GoldenLayerResult
    tool_execution: GoldenLayerResult
    grounding: GoldenLayerResult
    answer: GoldenLayerResult


@dataclass(frozen=True)
class GoldenSuiteResult:
    """Aggregate golden-dataset results without collapsing layer failures."""

    dataset_version: str
    total: int
    passed: int
    failed: int
    layer_pass_rates: dict[str, float]
    category_pass_rates: dict[str, float]
    results: list[GoldenCaseResult]

    @property
    def ok(self) -> bool:
        return self.failed == 0


def run_golden_eval_suite(
    *,
    cases: list[GoldenEvalCase],
    events: list[LimitUpEvent],
    dataset_version: str = GOLDEN_DATASET_VERSION,
    llm_provider: LLMProvider | None = None,
    force_template_answer: bool = True,
    repository: SQLiteFirstBoardRepository | None = None,
) -> GoldenSuiteResult:
    """Run production orchestration once per case and score its four layers."""

    if not cases:
        raise ValueError("golden eval requires at least one case")
    provider = llm_provider or OfflineEvalLLMProvider()
    active_repository = repository or SQLiteFirstBoardRepository()
    histories: dict[str, list[ChatSessionMessage]] = {}
    recent_runs: dict[str, list[AgentRun]] = {}
    results: list[GoldenCaseResult] = []
    for case in cases:
        conversation_id = case.conversation_id or case.case_id
        session_id = f"golden-eval-{conversation_id}"
        request = AgentChatRequest(
            session_id=session_id,
            message=case.question,
            intent_hint=case.intent_hint,
            trade_date=case.trade_date,
            symbol=case.symbol,
        )
        registry = AgentToolRegistry(
            events=events,
            first_board_repository=active_repository,
        )
        _inject_tool_failures(registry, case.simulate_tool_failure)
        started_at = datetime.now(timezone.utc)
        started = perf_counter()
        with template_answer_override(force_template_answer):
            response = answer_first_board_chat(
                request=request,
                events=events,
                repository=active_repository,
                recent_runs=recent_runs.get(conversation_id, []),
                conversation_messages=histories.get(conversation_id, []),
                llm_provider=provider,
                tool_registry=registry,
            )
        duration_ms = round((perf_counter() - started) * 1000)
        planner = _evaluate_planner(case, response.tool_results, response.tool_calls)
        execution = _evaluate_tool_execution(case, response.tool_results)
        grounding = _evaluate_grounding(case, response.answer, response.tool_results)
        answer = _evaluate_answer(case, response.answer, grounding)
        result = GoldenCaseResult(
            case_id=case.case_id,
            category=case.category,
            question=case.question,
            passed=all(
                layer.passed for layer in (planner, execution, grounding, answer)
            ),
            duration_ms=duration_ms,
            planner=planner,
            tool_execution=execution,
            grounding=grounding,
            answer=answer,
        )
        results.append(result)
        finished_at = datetime.now(timezone.utc)
        run = AgentRun(
            run_id=f"{session_id}-{case.case_id}",
            session_id=session_id,
            run_type="agent_chat",
            status="success",
            intent=response.intent,
            tool_calls=list(response.tool_calls),
            input_json=request.model_dump(mode="json"),
            output_json=response.model_dump(mode="json"),
            started_at=started_at,
            finished_at=finished_at,
        )
        recent_runs.setdefault(conversation_id, []).insert(0, run)
        histories.setdefault(conversation_id, []).extend(
            _conversation_messages(
                session_id,
                case.case_id,
                case.question,
                response.answer,
            )
        )

    passed = sum(result.passed for result in results)
    layer_pass_rates = {
        name: _rate(
            sum(getattr(result, name).passed for result in results),
            len(results),
        )
        for name in ("planner", "tool_execution", "grounding", "answer")
    }
    category_totals = Counter(result.category for result in results)
    category_passes = Counter(
        result.category for result in results if result.passed
    )
    return GoldenSuiteResult(
        dataset_version=dataset_version,
        total=len(results),
        passed=passed,
        failed=len(results) - passed,
        layer_pass_rates=layer_pass_rates,
        category_pass_rates={
            category: _rate(category_passes[category], total)
            for category, total in sorted(category_totals.items())
        },
        results=results,
    )


def golden_suite_report(
    suite: GoldenSuiteResult,
    *,
    failures_only: bool = False,
) -> dict:
    """Serialize layer and category metrics plus actionable case diagnostics."""

    selected = (
        [result for result in suite.results if not result.passed]
        if failures_only
        else suite.results
    )
    return {
        "dataset_version": suite.dataset_version,
        "total": suite.total,
        "passed": suite.passed,
        "failed": suite.failed,
        "layer_pass_rates": suite.layer_pass_rates,
        "layer_questions": LAYER_QUESTIONS,
        "category_pass_rates": suite.category_pass_rates,
        "results": [_case_payload(result) for result in selected],
    }


def _evaluate_planner(
    case: GoldenEvalCase,
    traces: list[AgentToolTrace],
    final_tool_calls: list[str],
) -> GoldenLayerResult:
    capabilities, planned_tools = _planner_observations(traces, final_tool_calls)
    failures = [
        f"missing capability: {item}"
        for item in case.expected_capabilities
        if item not in capabilities
    ]
    return GoldenLayerResult(
        passed=not failures,
        failures=failures,
        observed={"capabilities": capabilities, "planned_tools": planned_tools},
    )


def _evaluate_tool_execution(
    case: GoldenEvalCase,
    traces: list[AgentToolTrace],
) -> GoldenLayerResult:
    evidence = {
        trace.name: trace
        for trace in traces
        if trace.name not in INTERNAL_TOOL_NAMES
    }
    failures: list[str] = []
    for tool in case.required_tools:
        trace = evidence.get(tool)
        if trace is None:
            failures.append(f"required tool was not executed: {tool}")
            continue
        if trace.status != "success" and not case.allow_refusal:
            failures.append(f"tool {tool} failed: {trace.error or trace.status}")
    for tool, expected in case.expected_parameters.items():
        trace = evidence.get(tool)
        if trace is None:
            failures.append(f"parameters unavailable because tool is missing: {tool}")
            continue
        failures.extend(
            f"{tool} parameter {item}"
            for item in _subset_failures(expected, trace.input)
        )
    return GoldenLayerResult(
        passed=not failures,
        failures=failures,
        observed={
            "tools": {
                name: {
                    "status": trace.status,
                    "input": trace.input,
                    "error": trace.error,
                }
                for name, trace in evidence.items()
            }
        },
    )


def _evaluate_grounding(
    case: GoldenEvalCase,
    answer: str,
    traces: list[AgentToolTrace],
) -> GoldenLayerResult:
    evidence_text = json.dumps(
        {
            trace.name: trace.output
            for trace in traces
            if trace.name not in INTERNAL_TOOL_NAMES and trace.status == "success"
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    missing_facts = [fact for fact in case.required_facts if fact not in evidence_text]
    grounding = evaluate_answer_grounding(answer, traces, user_message=case.question)
    failures = [
        f"required fact missing from tool evidence: {fact}"
        for fact in missing_facts
    ]
    if grounding.passed is False:
        failures.extend(
            f"unsupported {claim.kind} claim: {claim.text}"
            for claim in grounding.claims
            if not claim.supported
        )
    if grounding.tool_failure_hallucination:
        failures.append("answer invented facts after tool failure")
    return GoldenLayerResult(
        passed=not failures,
        failures=failures,
        observed=grounding.payload(),
    )


def _evaluate_answer(
    case: GoldenEvalCase,
    answer: str,
    grounding: GoldenLayerResult,
) -> GoldenLayerResult:
    normalized = _normalize(answer)
    failures = [
        f"answer missing required text: {item}"
        for item in case.must_include
        if _normalize(item) not in normalized
    ]
    failures.extend(
        f"answer contains forbidden text: {item}"
        for item in case.must_not_include
        if _normalize(item) in normalized
    )
    refused = any(marker in answer for marker in REFUSAL_MARKERS)
    if (
        refused
        and not case.allow_refusal
        and grounding.observed.get("successful_evidence_tools")
    ):
        failures.append("answer refused despite successful evidence")
    return GoldenLayerResult(
        passed=not failures,
        failures=failures,
        observed={"refused": refused, "answer_preview": answer[:300]},
    )


def _planner_observations(
    traces: list[AgentToolTrace],
    final_tool_calls: list[str],
) -> tuple[list[str], list[str]]:
    for trace in traces:
        if trace.name == "llm_tool_planner":
            capabilities = [str(item) for item in trace.input.get("capabilities") or []]
            planned = [
                str(item["name"])
                for item in trace.input.get("tool_calls") or []
                if isinstance(item, dict) and item.get("name")
            ]
            return capabilities, planned
        if trace.name == "agent_plan":
            planned = [
                str(item["name"])
                for item in trace.input.get("tool_steps") or []
                if isinstance(item, dict) and item.get("name")
            ]
            return _capabilities_for_tools(planned), planned
    planned = [name for name in final_tool_calls if name not in INTERNAL_TOOL_NAMES]
    return _capabilities_for_tools(planned), planned


def _capabilities_for_tools(tools: list[str]) -> list[str]:
    return list(
        dict.fromkeys(
            TOOL_CAPABILITIES[name]
            for name in tools
            if name in TOOL_CAPABILITIES
        )
    )


def _inject_tool_failures(registry: AgentToolRegistry, tool_names: list[str]) -> None:
    for tool_name in tool_names:
        if not hasattr(registry, tool_name):
            raise ValueError(f"cannot inject unknown tool failure: {tool_name}")

        def fail(*_args, _tool_name: str = tool_name, **_kwargs):
            raise RuntimeError(f"golden eval injected {_tool_name} source failure")

        setattr(registry, tool_name, fail)


def _subset_failures(
    expected: dict[str, object],
    actual: dict[str, object],
    *,
    path: str = "",
) -> list[str]:
    failures: list[str] = []
    for key, expected_value in expected.items():
        current = f"{path}.{key}" if path else key
        if key not in actual:
            failures.append(f"missing {current}")
        elif isinstance(expected_value, dict) and isinstance(actual[key], dict):
            failures.extend(_subset_failures(expected_value, actual[key], path=current))
        elif actual[key] != expected_value:
            failures.append(f"{current} expected {expected_value!r}, got {actual[key]!r}")
    return failures


def _conversation_messages(
    session_id: str,
    case_id: str,
    question: str,
    answer: str,
) -> list[ChatSessionMessage]:
    created_at = datetime.now(timezone.utc)
    return [
        ChatSessionMessage(
            message_id=f"{case_id}-user",
            session_id=session_id,
            role="user",
            content=question,
            created_at=created_at,
        ),
        ChatSessionMessage(
            message_id=f"{case_id}-assistant",
            session_id=session_id,
            role="assistant",
            content=answer,
            created_at=created_at,
        ),
    ]


def _case_payload(result: GoldenCaseResult) -> dict[str, object]:
    return {
        "case_id": result.case_id,
        "category": result.category,
        "question": result.question,
        "passed": result.passed,
        "duration_ms": result.duration_ms,
        "layers": {
            "planner": result.planner.__dict__,
            "tool_execution": result.tool_execution.__dict__,
            "grounding": result.grounding.__dict__,
            "answer": result.answer.__dict__,
        },
    }


def _normalize(value: str) -> str:
    return "".join(value.lower().split())


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0
