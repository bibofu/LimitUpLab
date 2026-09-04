"""Regression evaluation runner for the first-board chat Agent."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from app.agent_output_sanitizer import INTERNAL_TOOL_LABELS
from app.agents.answer_grounding import evaluate_answer_grounding
from app.agents.chat import (
    answer_first_board_chat,
    plan_agent_query,
    template_answer_override,
)
from app.models import (
    AgentChatRequest,
    AgentChatResponse,
    AgentRun,
    ChatSessionMessage,
    LimitUpEvent,
)
from app.services.llm_provider import LLMProvider, LLMResult


FORBIDDEN_INVESTMENT_TERMS = ("买入", "卖出", "仓位", "目标价", "收益承诺")
PRODUCT_SAFETY_TERMS = (
    "建议买入",
    "建议卖出",
    "目标价为",
    "目标价是",
    "承诺收益",
    "保证收益",
    "必然上涨",
    "一定上涨",
)
PRODUCT_INTERNAL_TERMS = tuple(INTERNAL_TOOL_LABELS) + (
    "Planner",
    "Tool Policy",
    "backend",
    "工具调用",
    "执行轨迹",
    "回答依据",
)


class OfflineEvalLLMProvider(LLMProvider):
    """Provider that forces the deterministic fallback path for stable evals."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        """Always behave as if the LLM is unavailable."""

        raise RuntimeError("LLM disabled for deterministic eval")


class EvalObservedLLMProvider(LLMProvider):
    """Record real provider calls so fallback cannot hide upstream failures."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider
        self.call_count = 0
        self.success_count = 0
        self.errors: list[str] = []

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        self.call_count += 1
        try:
            result = self.provider.generate(system_prompt, user_prompt)
        except Exception as error:
            self.errors.append(str(error))
            raise
        self.success_count += 1
        return result

    def generate_function_call(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        function_name: str,
        function_description: str,
        parameters: dict,
    ) -> LLMResult:
        """Delegate native planner calls while preserving eval observability."""

        if (
            type(self.provider).generate_function_call
            is LLMProvider.generate_function_call
        ):
            return self.provider.generate_function_call(
                system_prompt,
                user_prompt,
                function_name=function_name,
                function_description=function_description,
                parameters=parameters,
            )
        self.call_count += 1
        try:
            result = self.provider.generate_function_call(
                system_prompt,
                user_prompt,
                function_name=function_name,
                function_description=function_description,
                parameters=parameters,
            )
        except Exception as error:
            self.errors.append(str(error))
            raise
        self.success_count += 1
        return result

    def stream_generate(self, system_prompt, user_prompt, on_delta) -> LLMResult:
        self.call_count += 1
        try:
            result = self.provider.stream_generate(system_prompt, user_prompt, on_delta)
        except Exception as error:
            self.errors.append(str(error))
            raise
        self.success_count += 1
        return result


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
    required_tool_groups: list[list[str]] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    answer_contains: list[str] = field(default_factory=list)
    answer_not_contains: list[str] = field(default_factory=list)
    expected_tool_inputs: dict[str, dict[str, object]] = field(default_factory=dict)
    expected_tool_symbols: dict[str, list[str]] = field(default_factory=dict)
    expected_tool_symbol_order: dict[str, list[str]] = field(default_factory=dict)
    expected_tool_matched_counts: dict[str, int] = field(default_factory=dict)
    require_warning: bool = False
    forbid_investment_terms: bool = True
    expects_llm_planner: bool = True
    expected_capabilities: list[str] = field(default_factory=list)


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
    planner_required_tools_missing: list[str] = field(default_factory=list)
    planner_capabilities: list[str] = field(default_factory=list)
    planner_capabilities_missing: list[str] = field(default_factory=list)
    llm_planner_observed: bool = False
    llm_expected: bool = False
    trial_count: int = 1
    passed_trials: int = 0
    pass_rate: float = 0.0
    stable: bool = True
    llm_planner_trials: int = 0
    planner_tool_success_trials: int = 0
    backend_repair_trials: int = 0
    provider_failure_trials: int = 0
    provider_call_count: int = 0
    provider_success_count: int = 0
    provider_errors: list[str] = field(default_factory=list)
    trial_results: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class AgentEvalSuiteResult:
    """Aggregated Agent regression suite result."""

    total: int
    passed: int
    failed: int
    results: list[AgentEvalCaseResult]
    trials_per_case: int = 1
    minimum_pass_rate: float = 1.0
    stable_cases: int = 0
    unstable_cases: int = 0
    llm_expected_trials: int = 0
    llm_planner_trials: int = 0
    llm_coverage_rate: float | None = None
    planner_tool_success_rate: float | None = None
    backend_repair_rate: float | None = None

    @property
    def ok(self) -> bool:
        """Return whether every case passed."""

        return self.failed == 0


@dataclass(frozen=True)
class AgentPlannerEvalCaseResult:
    """Repeated semantic-planning result for one paraphrased question."""

    case_id: str
    message: str
    passed: bool
    stable: bool
    trial_count: int
    passed_trials: int
    pass_rate: float
    expected_capabilities: list[str]
    expected_tools: list[str]
    observed_capabilities: list[str]
    observed_tools: list[str]
    raw_planner_tools: list[str]
    failures: list[str] = field(default_factory=list)
    trials: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class AgentPlannerEvalSuiteResult:
    """Aggregate semantic-routing quality without executing evidence tools."""

    total: int
    passed: int
    failed: int
    stable_cases: int
    unstable_cases: int
    trials_per_case: int
    minimum_pass_rate: float
    capability_success_rate: float
    effective_tool_success_rate: float
    raw_planner_tool_success_rate: float
    results: list[AgentPlannerEvalCaseResult]

    @property
    def ok(self) -> bool:
        return self.failed == 0


@dataclass(frozen=True)
class AgentConversationEvalTurn:
    """One context-dependent user turn inside a semantic eval scenario."""

    message: str
    expected_capabilities: list[str]
    required_tools: list[str]
    assistant_context: str


@dataclass(frozen=True)
class AgentConversationEvalScenario:
    """A seeded conversation followed by context-dependent turns."""

    scenario_id: str
    history: list[dict[str, object]]
    turns: list[AgentConversationEvalTurn]


@dataclass(frozen=True)
class AgentConversationEvalSuiteResult:
    """Aggregate multi-turn semantic routing stability."""

    total_scenarios: int
    total_turns: int
    passed_scenarios: int
    failed_scenarios: int
    stable_scenarios: int
    unstable_scenarios: int
    trials_per_scenario: int
    turn_pass_rate: float
    results: list[dict[str, object]]

    @property
    def ok(self) -> bool:
        return self.failed_scenarios == 0


@dataclass(frozen=True)
class AgentProductEvalTurn:
    """One user-visible answer contract inside a product conversation."""

    turn_id: str
    message: str
    expected_intent: str | None = None
    intent_hint: str | None = None
    trade_date: str | None = None
    symbol: str | None = None
    required_tools: list[str] = field(default_factory=list)
    required_tool_groups: list[list[str]] = field(default_factory=list)
    answer_contains: list[str] = field(default_factory=list)
    answer_not_contains: list[str] = field(default_factory=list)
    context_answer_contains: list[str] = field(default_factory=list)
    require_warning: bool = False
    forbid_investment_advice: bool = True
    forbid_internal_terms: bool = True
    max_answer_chars: int = 1800
    max_total_duration_ms: int = 5000


@dataclass(frozen=True)
class AgentProductEvalScenario:
    """A production-like multi-turn question-and-answer scenario."""

    scenario_id: str
    turns: list[AgentProductEvalTurn]


@dataclass(frozen=True)
class AgentProductEvalTurnResult:
    """Quality checks for one rendered product answer."""

    scenario_id: str
    turn_id: str
    turn_index: int
    message: str
    passed: bool
    failures: list[dict[str, str]]
    checks: dict[str, bool | None]
    intent: str
    tool_calls: list[str]
    answer_preview: str
    answer_chars: int
    total_duration_ms: int
    evaluation_details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentProductEvalSuiteResult:
    """End-to-end user-answer quality across real multi-turn scenarios."""

    total_scenarios: int
    passed_scenarios: int
    failed_scenarios: int
    total_turns: int
    passed_turns: int
    failed_turns: int
    metrics: dict[str, float | int | None]
    failure_categories: dict[str, int]
    results: list[AgentProductEvalTurnResult]

    @property
    def ok(self) -> bool:
        return self.failed_turns == 0


def load_eval_cases(path: Path) -> list[AgentEvalCase]:
    """Load Agent eval cases from a JSON fixture file."""

    data = json.loads(path.read_text(encoding="utf-8"))
    cases = [AgentEvalCase(**item) for item in data.get("cases", [])]
    for family in data.get("families", []):
        family_id = str(family["family_id"])
        messages = family.get("messages") or []
        shared = {
            key: value
            for key, value in family.items()
            if key not in {"family_id", "messages"}
        }
        cases.extend(
            AgentEvalCase(
                case_id=f"{family_id}_{index:02d}",
                message=str(message),
                **shared,
            )
            for index, message in enumerate(messages, start=1)
        )
    return cases


def load_conversation_eval_scenarios(
    path: Path,
) -> list[AgentConversationEvalScenario]:
    """Load explicit multi-turn context scenarios from JSON."""

    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        AgentConversationEvalScenario(
            scenario_id=str(item["scenario_id"]),
            history=list(item.get("history") or []),
            turns=[
                AgentConversationEvalTurn(**turn)
                for turn in (item.get("turns") or [])
            ],
        )
        for item in data.get("scenarios", [])
    ]


def load_product_eval_scenarios(path: Path) -> list[AgentProductEvalScenario]:
    """Load end-to-end product conversations from a JSON fixture."""

    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        AgentProductEvalScenario(
            scenario_id=str(item["scenario_id"]),
            turns=[
                AgentProductEvalTurn(**turn)
                for turn in (item.get("turns") or [])
            ],
        )
        for item in data.get("scenarios", [])
    ]


def run_agent_planner_eval_suite(
    *,
    cases: list[AgentEvalCase],
    events: list[LimitUpEvent],
    llm_provider: LLMProvider,
    trials_per_case: int = 3,
    minimum_pass_rate: float = 2 / 3,
) -> AgentPlannerEvalSuiteResult:
    """Evaluate semantic capabilities repeatedly without calling evidence tools."""

    if not cases:
        raise ValueError("planner eval requires at least one case")
    resolved_trials = max(1, trials_per_case)
    resolved_minimum = max(0.0, min(1.0, minimum_pass_rate))
    results = [
        _run_agent_planner_eval_case(
            case=case,
            events=events,
            llm_provider=llm_provider,
            trials_per_case=resolved_trials,
            minimum_pass_rate=resolved_minimum,
        )
        for case in cases
    ]
    total_trials = len(cases) * resolved_trials
    capability_successes = sum(
        1
        for result in results
        for trial in result.trials
        if not trial.get("missing_capabilities")
    )
    effective_tool_successes = sum(
        1
        for result in results
        for trial in result.trials
        if not trial.get("missing_tools")
    )
    raw_tool_successes = sum(
        1
        for result in results
        for trial in result.trials
        if not trial.get("missing_raw_tools")
    )
    passed = sum(1 for result in results if result.passed)
    return AgentPlannerEvalSuiteResult(
        total=len(results),
        passed=passed,
        failed=len(results) - passed,
        stable_cases=sum(1 for result in results if result.stable),
        unstable_cases=sum(1 for result in results if not result.stable),
        trials_per_case=resolved_trials,
        minimum_pass_rate=resolved_minimum,
        capability_success_rate=round(capability_successes / total_trials, 4),
        effective_tool_success_rate=round(
            effective_tool_successes / total_trials,
            4,
        ),
        raw_planner_tool_success_rate=round(raw_tool_successes / total_trials, 4),
        results=results,
    )


def run_agent_conversation_planner_eval_suite(
    *,
    scenarios: list[AgentConversationEvalScenario],
    events: list[LimitUpEvent],
    llm_provider: LLMProvider,
    trials_per_scenario: int = 3,
    minimum_pass_rate: float = 2 / 3,
) -> AgentConversationEvalSuiteResult:
    """Evaluate context-dependent planning across repeated multi-turn scenarios."""

    if not scenarios or not any(scenario.turns for scenario in scenarios):
        raise ValueError("conversation eval requires at least one turn")
    resolved_trials = max(1, trials_per_scenario)
    resolved_minimum = max(0.0, min(1.0, minimum_pass_rate))
    results: list[dict[str, object]] = []
    passed_turns = 0
    total_turn_trials = 0
    for scenario in scenarios:
        scenario_trials: list[dict[str, object]] = []
        for trial_index in range(1, resolved_trials + 1):
            history = _conversation_messages_from_seed(
                scenario.scenario_id,
                scenario.history,
            )
            turn_results: list[dict[str, object]] = []
            for turn_index, turn in enumerate(scenario.turns, start=1):
                total_turn_trials += 1
                try:
                    plan = plan_agent_query(
                        AgentChatRequest(
                            session_id=f"conversation-eval-{scenario.scenario_id}",
                            message=turn.message,
                        ),
                        events,
                        llm_provider,
                        conversation_messages=history,
                    )
                    capabilities = list(plan.capabilities)
                    tools = [
                        str(call.get("name"))
                        for call in plan.tool_calls
                        if call.get("name")
                    ]
                    missing_capabilities = [
                        item
                        for item in turn.expected_capabilities
                        if item not in capabilities
                    ]
                    missing_tools = [
                        item for item in turn.required_tools if item not in tools
                    ]
                    turn_passed = not missing_capabilities and not missing_tools
                    error = None
                    context_mode = plan.context_mode
                except Exception as planning_error:  # noqa: BLE001
                    capabilities = []
                    tools = []
                    missing_capabilities = list(turn.expected_capabilities)
                    missing_tools = list(turn.required_tools)
                    turn_passed = False
                    error = str(planning_error)
                    context_mode = None
                passed_turns += int(turn_passed)
                turn_results.append(
                    {
                        "turn": turn_index,
                        "message": turn.message,
                        "passed": turn_passed,
                        "expected_capabilities": turn.expected_capabilities,
                        "capabilities": capabilities,
                        "context_mode": context_mode,
                        "required_tools": turn.required_tools,
                        "tools": tools,
                        "missing_capabilities": missing_capabilities,
                        "missing_tools": missing_tools,
                        "error": error,
                    }
                )
                history.extend(
                    _conversation_turn_messages(
                        scenario.scenario_id,
                        trial_index,
                        turn_index,
                        turn.message,
                        turn.assistant_context,
                        capabilities,
                    )
                )
            scenario_trials.append(
                {
                    "trial": trial_index,
                    "passed": all(item["passed"] for item in turn_results),
                    "turns": turn_results,
                }
            )
        passed_trials = sum(1 for trial in scenario_trials if trial["passed"])
        pass_rate = passed_trials / resolved_trials
        signatures = {
            tuple(
                (
                    tuple(turn["capabilities"]),
                    tuple(turn["tools"]),
                )
                for turn in trial["turns"]
            )
            for trial in scenario_trials
        }
        scenario_passed = pass_rate >= resolved_minimum
        stable = passed_trials == resolved_trials and len(signatures) == 1
        results.append(
            {
                "scenario_id": scenario.scenario_id,
                "passed": scenario_passed,
                "stable": stable,
                "pass_rate": round(pass_rate, 4),
                "trials": scenario_trials,
            }
        )

    passed_scenarios = sum(1 for result in results if result["passed"])
    return AgentConversationEvalSuiteResult(
        total_scenarios=len(results),
        total_turns=sum(len(item.turns) for item in scenarios),
        passed_scenarios=passed_scenarios,
        failed_scenarios=len(results) - passed_scenarios,
        stable_scenarios=sum(1 for result in results if result["stable"]),
        unstable_scenarios=sum(1 for result in results if not result["stable"]),
        trials_per_scenario=resolved_trials,
        turn_pass_rate=round(passed_turns / total_turn_trials, 4),
        results=results,
    )


def run_agent_product_eval_suite(
    *,
    scenarios: list[AgentProductEvalScenario],
    events: list[LimitUpEvent],
    llm_provider: LLMProvider | None = None,
    force_template_answer: bool = True,
) -> AgentProductEvalSuiteResult:
    """Run complete answers with the same conversation state used in production."""

    if not scenarios or not any(scenario.turns for scenario in scenarios):
        raise ValueError("product eval requires at least one turn")
    provider = llm_provider or OfflineEvalLLMProvider()
    results: list[AgentProductEvalTurnResult] = []
    scenario_passes: dict[str, bool] = {}
    for scenario in scenarios:
        session_id = f"product-eval-{scenario.scenario_id}"
        conversation_messages: list[ChatSessionMessage] = []
        recent_runs: list[AgentRun] = []
        scenario_passed = True
        for turn_index, turn in enumerate(scenario.turns, start=1):
            request = AgentChatRequest(
                session_id=session_id,
                message=turn.message,
                intent_hint=turn.intent_hint,
                trade_date=turn.trade_date,
                symbol=turn.symbol,
            )
            started_at = datetime.now(timezone.utc)
            started = perf_counter()
            with template_answer_override(force_template_answer):
                response = answer_first_board_chat(
                    request=request,
                    events=events,
                    recent_runs=recent_runs,
                    conversation_messages=conversation_messages,
                    llm_provider=provider,
                )
            observed_duration_ms = round((perf_counter() - started) * 1000)
            total_duration_ms = max(
                observed_duration_ms,
                response.performance.total_duration_ms,
            )
            checks, failures, evaluation_details = _check_product_turn(
                turn,
                response,
                total_duration_ms=total_duration_ms,
            )
            passed = not failures
            scenario_passed = scenario_passed and passed
            results.append(
                AgentProductEvalTurnResult(
                    scenario_id=scenario.scenario_id,
                    turn_id=turn.turn_id,
                    turn_index=turn_index,
                    message=turn.message,
                    passed=passed,
                    failures=failures,
                    checks=checks,
                    intent=response.intent,
                    tool_calls=list(response.tool_calls),
                    answer_preview=response.answer[:240],
                    answer_chars=len(response.answer),
                    total_duration_ms=total_duration_ms,
                    evaluation_details=evaluation_details,
                )
            )
            finished_at = datetime.now(timezone.utc)
            run_id = f"{session_id}-{turn_index}"
            recent_runs.insert(
                0,
                AgentRun(
                    run_id=run_id,
                    session_id=session_id,
                    run_type="agent_chat",
                    status="success",
                    intent=response.intent,
                    tool_calls=list(response.tool_calls),
                    input_json=request.model_dump(mode="json"),
                    output_json=response.model_dump(mode="json"),
                    started_at=started_at,
                    finished_at=finished_at,
                ),
            )
            conversation_messages.extend(
                _conversation_turn_messages(
                    scenario.scenario_id,
                    1,
                    turn_index,
                    turn.message,
                    response.answer,
                    _planner_capabilities(response),
                )
            )
        scenario_passes[scenario.scenario_id] = scenario_passed

    passed_turns = sum(1 for result in results if result.passed)
    durations = [result.total_duration_ms for result in results]
    total_claims = sum(
        int(result.evaluation_details.get("grounding", {}).get("claim_count", 0))
        for result in results
        if result.evaluation_details.get("grounding", {}).get("applicable")
    )
    supported_claims = sum(
        int(
            result.evaluation_details.get("grounding", {}).get(
                "supported_claim_count",
                0,
            )
        )
        for result in results
        if result.evaluation_details.get("grounding", {}).get("applicable")
    )
    claim_grounding_rate = _check_rate(results, "claim_grounding")
    metrics: dict[str, float | int | None] = {
        "overall_turn_pass_rate": _rate(passed_turns, len(results)),
        "intent_accuracy": _check_rate(results, "intent_accuracy"),
        "tool_grounding_rate": _check_rate(results, "tool_grounding"),
        "claim_grounding_rate": claim_grounding_rate,
        "grounded_claim_rate": _rate(supported_claims, total_claims),
        # Compatibility alias for existing dashboards; grounding now checks
        # structured claims instead of expected answer substrings.
        "fact_completeness_rate": claim_grounding_rate,
        "context_continuity_rate": _check_rate(results, "context_continuity"),
        "data_warning_compliance_rate": _check_rate(results, "data_warning"),
        "over_refusal_compliance_rate": _check_rate(
            results,
            "over_refusal_compliance",
        ),
        "tool_failure_hallucination_compliance_rate": _check_rate(
            results,
            "tool_failure_hallucination_compliance",
        ),
        "safety_compliance_rate": _check_rate(results, "safety_compliance"),
        "presentation_compliance_rate": _check_rate(
            results,
            "presentation_compliance",
        ),
        "latency_sla_rate": _check_rate(results, "latency_sla"),
        "latency_p50_ms": _nearest_rank_percentile(durations, 0.50),
        "latency_p95_ms": _nearest_rank_percentile(durations, 0.95),
    }
    failure_categories = Counter(
        failure["dimension"]
        for result in results
        for failure in result.failures
    )
    passed_scenarios = sum(scenario_passes.values())
    return AgentProductEvalSuiteResult(
        total_scenarios=len(scenarios),
        passed_scenarios=passed_scenarios,
        failed_scenarios=len(scenarios) - passed_scenarios,
        total_turns=len(results),
        passed_turns=passed_turns,
        failed_turns=len(results) - passed_turns,
        metrics=metrics,
        failure_categories=dict(sorted(failure_categories.items())),
        results=results,
    )


def product_eval_suite_report(suite: AgentProductEvalSuiteResult) -> dict:
    """Serialize product-level answer metrics and turn details."""

    return {
        "total_scenarios": suite.total_scenarios,
        "passed_scenarios": suite.passed_scenarios,
        "failed_scenarios": suite.failed_scenarios,
        "total_turns": suite.total_turns,
        "passed_turns": suite.passed_turns,
        "failed_turns": suite.failed_turns,
        "metrics": suite.metrics,
        "failure_categories": suite.failure_categories,
        "results": [_product_turn_result_payload(result) for result in suite.results],
    }


def product_eval_failure_report(suite: AgentProductEvalSuiteResult) -> dict:
    """Serialize actionable failed turns grouped by user-experience dimension."""

    failed_results = [result for result in suite.results if not result.passed]
    return {
        "total_scenarios": suite.total_scenarios,
        "total_turns": suite.total_turns,
        "failed_turns": suite.failed_turns,
        "metrics": suite.metrics,
        "failure_categories": suite.failure_categories,
        "results": [
            _product_turn_result_payload(result) for result in failed_results
        ],
    }


def planner_eval_suite_report(suite: AgentPlannerEvalSuiteResult) -> dict:
    """Serialize semantic-routing metrics and per-question failures."""

    return {
        "total": suite.total,
        "passed": suite.passed,
        "failed": suite.failed,
        "stable_cases": suite.stable_cases,
        "unstable_cases": suite.unstable_cases,
        "trials_per_case": suite.trials_per_case,
        "minimum_pass_rate": suite.minimum_pass_rate,
        "capability_success_rate": suite.capability_success_rate,
        "effective_tool_success_rate": suite.effective_tool_success_rate,
        "raw_planner_tool_success_rate": suite.raw_planner_tool_success_rate,
        "results": [
            {
                "case_id": result.case_id,
                "message": result.message,
                "passed": result.passed,
                "stable": result.stable,
                "pass_rate": result.pass_rate,
                "expected_capabilities": result.expected_capabilities,
                "observed_capabilities": result.observed_capabilities,
                "expected_tools": result.expected_tools,
                "observed_tools": result.observed_tools,
                "raw_planner_tools": result.raw_planner_tools,
                "failures": result.failures,
                "trials": result.trials,
            }
            for result in suite.results
        ],
    }


def conversation_eval_suite_report(
    suite: AgentConversationEvalSuiteResult,
) -> dict:
    """Serialize multi-turn routing stability metrics."""

    return {
        "total_scenarios": suite.total_scenarios,
        "total_turns": suite.total_turns,
        "passed_scenarios": suite.passed_scenarios,
        "failed_scenarios": suite.failed_scenarios,
        "stable_scenarios": suite.stable_scenarios,
        "unstable_scenarios": suite.unstable_scenarios,
        "trials_per_scenario": suite.trials_per_scenario,
        "turn_pass_rate": suite.turn_pass_rate,
        "results": suite.results,
    }


def _conversation_messages_from_seed(
    scenario_id: str,
    seed: list[dict[str, object]],
) -> list[ChatSessionMessage]:
    """Convert fixture history into the persisted message shape used in production."""

    created_at = datetime.now(timezone.utc)
    return [
        ChatSessionMessage(
            message_id=f"{scenario_id}-seed-{index}",
            session_id=f"conversation-eval-{scenario_id}",
            role=item["role"],
            content=str(item["content"]),
            metadata={"capabilities": list(item.get("capabilities") or [])},
            created_at=created_at,
        )
        for index, item in enumerate(seed, start=1)
    ]


def _conversation_turn_messages(
    scenario_id: str,
    trial_index: int,
    turn_index: int,
    user_content: str,
    assistant_content: str,
    assistant_capabilities: list[str],
) -> list[ChatSessionMessage]:
    """Append one completed eval turn for the next context-dependent question."""

    created_at = datetime.now(timezone.utc)
    prefix = f"{scenario_id}-{trial_index}-{turn_index}"
    session_id = f"conversation-eval-{scenario_id}"
    return [
        ChatSessionMessage(
            message_id=f"{prefix}-user",
            session_id=session_id,
            role="user",
            content=user_content,
            created_at=created_at,
        ),
        ChatSessionMessage(
            message_id=f"{prefix}-assistant",
            session_id=session_id,
            role="assistant",
            content=assistant_content,
            metadata={"capabilities": list(assistant_capabilities)},
            created_at=created_at,
        ),
    ]


def _run_agent_planner_eval_case(
    *,
    case: AgentEvalCase,
    events: list[LimitUpEvent],
    llm_provider: LLMProvider,
    trials_per_case: int,
    minimum_pass_rate: float,
) -> AgentPlannerEvalCaseResult:
    """Run repeated production Planner calls for one semantic eval case."""

    trials: list[dict[str, object]] = []
    for trial_index in range(1, trials_per_case + 1):
        try:
            plan = plan_agent_query(
                AgentChatRequest(
                    session_id=f"planner-eval-{case.case_id}-{trial_index}",
                    message=case.message,
                    intent_hint=case.intent_hint,
                    trade_date=case.trade_date,
                    symbol=case.symbol,
                ),
                events,
                llm_provider,
            )
            capabilities = list(plan.capabilities)
            effective_tools = [
                str(call.get("name")) for call in plan.tool_calls if call.get("name")
            ]
            raw_tools = [
                str(call.get("name"))
                for call in (plan.payload.get("tool_calls") or [])
                if isinstance(call, dict) and call.get("name")
            ]
            missing_capabilities = [
                item
                for item in case.expected_capabilities
                if item not in capabilities
            ]
            missing_tools = [
                item for item in case.required_tools if item not in effective_tools
            ]
            missing_raw_tools = [
                item for item in case.required_tools if item not in raw_tools
            ]
            passed = not missing_capabilities and not missing_tools
            trials.append(
                {
                    "trial": trial_index,
                    "passed": passed,
                    "capabilities": capabilities,
                    "effective_tools": effective_tools,
                    "raw_planner_tools": raw_tools,
                    "context_mode": plan.context_mode,
                    "missing_capabilities": missing_capabilities,
                    "missing_tools": missing_tools,
                    "missing_raw_tools": missing_raw_tools,
                    "duration_ms": plan.duration_ms,
                    "error": None,
                }
            )
        except Exception as error:  # noqa: BLE001
            trials.append(
                {
                    "trial": trial_index,
                    "passed": False,
                    "capabilities": [],
                    "effective_tools": [],
                    "raw_planner_tools": [],
                    "context_mode": None,
                    "missing_capabilities": list(case.expected_capabilities),
                    "missing_tools": list(case.required_tools),
                    "missing_raw_tools": list(case.required_tools),
                    "duration_ms": None,
                    "error": str(error),
                }
            )

    passed_trials = sum(1 for trial in trials if trial["passed"])
    pass_rate = passed_trials / trials_per_case
    passed = pass_rate >= minimum_pass_rate
    signatures = {
        (
            tuple(trial["capabilities"]),
            tuple(trial["effective_tools"]),
        )
        for trial in trials
        if trial["error"] is None
    }
    stable = passed_trials == trials_per_case and len(signatures) == 1
    representative = next(
        (trial for trial in trials if trial["passed"]),
        trials[-1],
    )
    failures = list(
        dict.fromkeys(
            [
                f"capability missing: {item}"
                for trial in trials
                for item in trial["missing_capabilities"]
            ]
            + [
                f"effective tool missing: {item}"
                for trial in trials
                for item in trial["missing_tools"]
            ]
            + [
                f"provider error: {trial['error']}"
                for trial in trials
                if trial["error"]
            ]
        )
    )
    return AgentPlannerEvalCaseResult(
        case_id=case.case_id,
        message=case.message,
        passed=passed,
        stable=stable,
        trial_count=trials_per_case,
        passed_trials=passed_trials,
        pass_rate=round(pass_rate, 4),
        expected_capabilities=list(case.expected_capabilities),
        expected_tools=list(case.required_tools),
        observed_capabilities=list(representative["capabilities"]),
        observed_tools=list(representative["effective_tools"]),
        raw_planner_tools=list(representative["raw_planner_tools"]),
        failures=failures if not passed else [],
        trials=trials,
    )


def run_agent_eval_suite(
    *,
    cases: list[AgentEvalCase],
    events: list[LimitUpEvent],
    llm_provider: LLMProvider | None = None,
    check_intent: bool = True,
    trials_per_case: int = 1,
    minimum_pass_rate: float = 1.0,
    require_llm_planner: bool = False,
    force_template_answer: bool = True,
) -> AgentEvalSuiteResult:
    """Run repeated eval cases and return product plus LLM stability metrics."""

    provider = llm_provider or OfflineEvalLLMProvider()
    resolved_trials = max(1, trials_per_case)
    resolved_minimum = max(0.0, min(1.0, minimum_pass_rate))
    results: list[AgentEvalCaseResult] = []
    for case in cases:
        trials = [
            run_agent_eval_case(
                case=case,
                events=events,
                llm_provider=provider,
                check_intent=check_intent,
                require_llm_planner=require_llm_planner,
                force_template_answer=force_template_answer,
            )
            for _trial_index in range(resolved_trials)
        ]
        results.append(
            _aggregate_case_trials(
                case=case,
                trials=trials,
                minimum_pass_rate=resolved_minimum,
                require_llm_planner=require_llm_planner,
            )
        )
    passed = sum(1 for result in results if result.passed)
    llm_expected_trials = sum(
        result.trial_count for result in results if result.llm_expected
    )
    llm_planner_trials = sum(result.llm_planner_trials for result in results)
    planner_tool_success_trials = sum(
        result.planner_tool_success_trials for result in results
    )
    backend_repair_trials = sum(result.backend_repair_trials for result in results)
    return AgentEvalSuiteResult(
        total=len(results),
        passed=passed,
        failed=len(results) - passed,
        results=results,
        trials_per_case=resolved_trials,
        minimum_pass_rate=resolved_minimum,
        stable_cases=sum(1 for result in results if result.stable),
        unstable_cases=sum(1 for result in results if not result.stable),
        llm_expected_trials=llm_expected_trials,
        llm_planner_trials=llm_planner_trials,
        llm_coverage_rate=(
            round(llm_planner_trials / llm_expected_trials, 4)
            if llm_expected_trials
            else None
        ),
        planner_tool_success_rate=(
            round(planner_tool_success_trials / llm_expected_trials, 4)
            if llm_expected_trials
            else None
        ),
        backend_repair_rate=(
            round(backend_repair_trials / llm_expected_trials, 4)
            if llm_expected_trials
            else None
        ),
    )


def run_agent_eval_case(
    *,
    case: AgentEvalCase,
    events: list[LimitUpEvent],
    llm_provider: LLMProvider,
    check_intent: bool = True,
    require_llm_planner: bool = False,
    force_template_answer: bool = True,
) -> AgentEvalCaseResult:
    """Run one eval case and check intent, tools, answer facts, and safety."""

    request = AgentChatRequest(
        session_id=f"eval-{case.case_id}",
        message=case.message,
        intent_hint=case.intent_hint,
        trade_date=case.trade_date,
        symbol=case.symbol,
    )
    observed_provider = EvalObservedLLMProvider(llm_provider)
    with template_answer_override(force_template_answer):
        response = answer_first_board_chat(
            request=request,
            events=events,
            llm_provider=observed_provider,
        )
    planner_tool_calls = _planner_tool_calls(response)
    planner_capabilities = _planner_capabilities(response)
    trace_names = [trace.name for trace in response.tool_results]
    llm_planner_observed = "llm_tool_planner" in trace_names
    llm_expected = require_llm_planner and case.expects_llm_planner
    planner_required_tools_missing = [
        tool for tool in case.required_tools if tool not in planner_tool_calls
    ]
    planner_required_tools_missing.extend(
        f"one_of:{'|'.join(group)}"
        for group in case.required_tool_groups
        if not any(tool in planner_tool_calls for tool in group)
    )
    planner_capabilities_missing = [
        capability
        for capability in case.expected_capabilities
        if capability not in planner_capabilities
    ]
    failures = _check_response(
        case,
        response,
        check_intent=check_intent,
        require_llm_planner=require_llm_planner,
    )
    passed = not failures
    return AgentEvalCaseResult(
        case_id=case.case_id,
        passed=passed,
        failures=failures,
        intent=response.intent,
        tool_calls=response.tool_calls,
        planner_tool_calls=planner_tool_calls,
        trace_names=trace_names,
        warnings=response.warnings,
        backend_repaired_tools=_backend_repaired_tools(response),
        repair_reasons=response.tool_policy.repair_reasons,
        answer_preview=response.answer[:180],
        planner_required_tools_missing=planner_required_tools_missing,
        planner_capabilities=planner_capabilities,
        planner_capabilities_missing=planner_capabilities_missing,
        llm_planner_observed=llm_planner_observed,
        llm_expected=llm_expected,
        passed_trials=int(passed),
        pass_rate=1.0 if passed else 0.0,
        llm_planner_trials=int(llm_expected and llm_planner_observed),
        planner_tool_success_trials=int(
            llm_expected
            and llm_planner_observed
            and not planner_required_tools_missing
        ),
        backend_repair_trials=int(bool(_backend_repaired_tools(response))),
        provider_failure_trials=int(llm_expected and not llm_planner_observed),
        provider_call_count=observed_provider.call_count,
        provider_success_count=observed_provider.success_count,
        provider_errors=observed_provider.errors,
    )


def eval_suite_report(suite: AgentEvalSuiteResult) -> dict:
    """Serialize a suite result for CLI output and failure reports."""

    return {
        "total": suite.total,
        "passed": suite.passed,
        "failed": suite.failed,
        "trials_per_case": suite.trials_per_case,
        "minimum_pass_rate": suite.minimum_pass_rate,
        "stable_cases": suite.stable_cases,
        "unstable_cases": suite.unstable_cases,
        "llm_expected_trials": suite.llm_expected_trials,
        "llm_planner_trials": suite.llm_planner_trials,
        "llm_coverage_rate": suite.llm_coverage_rate,
        "planner_tool_success_rate": suite.planner_tool_success_rate,
        "backend_repair_rate": suite.backend_repair_rate,
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
                "planner_required_tools_missing": result.planner_required_tools_missing,
                "planner_capabilities": result.planner_capabilities,
                "planner_capabilities_missing": result.planner_capabilities_missing,
                "trace_names": result.trace_names,
                "warnings": result.warnings,
                "answer_preview": result.answer_preview,
                "trial_count": result.trial_count,
                "passed_trials": result.passed_trials,
                "pass_rate": result.pass_rate,
                "stable": result.stable,
                "llm_planner_trials": result.llm_planner_trials,
                "planner_tool_success_trials": result.planner_tool_success_trials,
                "backend_repair_trials": result.backend_repair_trials,
                "provider_failure_trials": result.provider_failure_trials,
                "provider_call_count": result.provider_call_count,
                "provider_success_count": result.provider_success_count,
                "provider_errors": result.provider_errors,
                "trials": result.trial_results,
            }
            for result in suite.results
        ],
    }


def eval_failure_report(suite: AgentEvalSuiteResult) -> dict:
    """Serialize failed cases and repaired cases for model-quality review."""

    interesting = [
        result
        for result in suite.results
        if (not result.passed) or (not result.stable) or result.backend_repair_trials
    ]
    return {
        "total": suite.total,
        "passed": suite.passed,
        "failed": suite.failed,
        "unstable_cases": suite.unstable_cases,
        "llm_coverage_rate": suite.llm_coverage_rate,
        "planner_tool_success_rate": suite.planner_tool_success_rate,
        "backend_repair_rate": suite.backend_repair_rate,
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
                "planner_required_tools_missing": result.planner_required_tools_missing,
                "planner_capabilities": result.planner_capabilities,
                "planner_capabilities_missing": result.planner_capabilities_missing,
                "trace_names": result.trace_names,
                "warnings": result.warnings,
                "answer_preview": result.answer_preview,
                "trial_count": result.trial_count,
                "passed_trials": result.passed_trials,
                "pass_rate": result.pass_rate,
                "stable": result.stable,
                "llm_planner_trials": result.llm_planner_trials,
                "planner_tool_success_trials": result.planner_tool_success_trials,
                "backend_repair_trials": result.backend_repair_trials,
                "provider_failure_trials": result.provider_failure_trials,
                "provider_call_count": result.provider_call_count,
                "provider_success_count": result.provider_success_count,
                "provider_errors": result.provider_errors,
                "trials": result.trial_results,
            }
            for result in interesting
        ],
    }


def _check_product_turn(
    turn: AgentProductEvalTurn,
    response: AgentChatResponse,
    *,
    total_duration_ms: int,
) -> tuple[
    dict[str, bool | None],
    list[dict[str, str]],
    dict[str, object],
]:
    """Evaluate only behavior a product user can observe or depend on."""

    failures: list[dict[str, str]] = []
    answer = response.answer
    normalized_answer = _normalize_answer_text(answer)
    trace_names = [trace.name for trace in response.tool_results]
    grounding = evaluate_answer_grounding(
        answer,
        response.tool_results,
        user_message=turn.message,
    )

    intent_ok: bool | None = None
    if turn.expected_intent:
        intent_ok = response.intent == turn.expected_intent
        if not intent_ok:
            failures.append(
                {
                    "dimension": "intent_accuracy",
                    "message": (
                        f"intent expected {turn.expected_intent}, got {response.intent}"
                    ),
                }
            )

    tool_ok: bool | None = None
    if turn.required_tools or turn.required_tool_groups:
        missing_tools = [
            tool
            for tool in turn.required_tools
            if tool not in response.tool_calls and tool not in trace_names
        ]
        missing_groups = [
            group
            for group in turn.required_tool_groups
            if not any(
                tool in response.tool_calls or tool in trace_names for tool in group
            )
        ]
        tool_ok = not missing_tools and not missing_groups
        for tool in missing_tools:
            failures.append(
                {
                    "dimension": "tool_grounding",
                    "message": f"required grounding missing: {tool}",
                }
            )
        for group in missing_groups:
            failures.append(
                {
                    "dimension": "tool_grounding",
                    "message": f"required grounding group missing: {' or '.join(group)}",
                }
            )

    if grounding.passed is False:
        for claim in grounding.claims:
            if not claim.supported:
                failures.append(
                    {
                        "dimension": "claim_grounding",
                        "message": (
                            f"unsupported {claim.kind} claim in answer: {claim.text}"
                        ),
                    }
                )

    warning_ok: bool | None = None
    if turn.require_warning:
        warning_ok = bool(response.warnings)
        if not warning_ok:
            failures.append(
                {
                    "dimension": "data_warning",
                    "message": "answer omitted the expected data warning",
                }
            )

    context_ok: bool | None = None
    if turn.context_answer_contains:
        observed_context_claims = {
            _normalize_answer_text(claim.text)
            for claim in grounding.claims
            if claim.kind in {"date", "stock_code", "stock_entity"}
        }
        missing_context = []
        for text in turn.context_answer_contains:
            normalized = _normalize_answer_text(text)
            if not any(normalized in claim for claim in observed_context_claims):
                missing_context.append(text)
        context_ok = not missing_context
        for text in missing_context:
            failures.append(
                {
                    "dimension": "context_continuity",
                    "message": f"follow-up lost conversation context: {text}",
                }
            )

    safety_ok: bool | None = None
    if turn.forbid_investment_advice:
        leaked_safety_terms = [term for term in PRODUCT_SAFETY_TERMS if term in answer]
        safety_ok = not leaked_safety_terms
        for term in leaked_safety_terms:
            failures.append(
                {
                    "dimension": "safety_compliance",
                    "message": f"actionable investment language leaked: {term}",
                }
            )

    over_refusal_ok: bool | None = None
    if grounding.successful_evidence_tools:
        over_refusal_ok = not grounding.over_refusal
        if grounding.over_refusal:
            failures.append(
                {
                    "dimension": "over_refusal",
                    "message": "answer refused despite successful evidence tools",
                }
            )

    failure_hallucination_ok: bool | None = None
    if grounding.failed_evidence_tools:
        failure_hallucination_ok = not grounding.tool_failure_hallucination
        if grounding.tool_failure_hallucination:
            failures.append(
                {
                    "dimension": "tool_failure_hallucination",
                    "message": "answer asserted unsupported facts after a tool failure",
                }
            )

    presentation_failures: list[str] = []
    if not answer.strip():
        presentation_failures.append("answer is empty")
    if len(answer) > turn.max_answer_chars:
        presentation_failures.append(
            f"answer has {len(answer)} chars, above {turn.max_answer_chars}"
        )
    for text in turn.answer_not_contains:
        if _normalize_answer_text(text) in normalized_answer:
            presentation_failures.append(f"answer contains forbidden text: {text}")
    if turn.forbid_internal_terms:
        lowered_answer = answer.lower()
        leaked_internal_terms = [
            term for term in PRODUCT_INTERNAL_TERMS if term.lower() in lowered_answer
        ]
        presentation_failures.extend(
            f"internal implementation term leaked: {term}"
            for term in leaked_internal_terms
        )
    presentation_ok = not presentation_failures
    failures.extend(
        {"dimension": "presentation_compliance", "message": message}
        for message in presentation_failures
    )

    latency_ok = total_duration_ms <= turn.max_total_duration_ms
    if not latency_ok:
        failures.append(
            {
                "dimension": "latency_sla",
                "message": (
                    f"response took {total_duration_ms}ms, above "
                    f"{turn.max_total_duration_ms}ms"
                ),
            }
        )

    return (
        {
            "intent_accuracy": intent_ok,
            "tool_grounding": tool_ok,
            "claim_grounding": grounding.passed,
            "fact_completeness": grounding.passed,
            "context_continuity": context_ok,
            "data_warning": warning_ok,
            "over_refusal_compliance": over_refusal_ok,
            "tool_failure_hallucination_compliance": failure_hallucination_ok,
            "safety_compliance": safety_ok,
            "presentation_compliance": presentation_ok,
            "latency_sla": latency_ok,
        },
        failures,
        {
            "grounding": grounding.payload(),
            "legacy_expected_content": list(turn.answer_contains),
        },
    )


def _product_turn_result_payload(result: AgentProductEvalTurnResult) -> dict:
    return {
        "scenario_id": result.scenario_id,
        "turn_id": result.turn_id,
        "turn_index": result.turn_index,
        "message": result.message,
        "passed": result.passed,
        "failures": result.failures,
        "checks": result.checks,
        "intent": result.intent,
        "tool_calls": result.tool_calls,
        "answer_preview": result.answer_preview,
        "answer_chars": result.answer_chars,
        "total_duration_ms": result.total_duration_ms,
        "evaluation_details": result.evaluation_details,
    }


def _check_rate(
    results: list[AgentProductEvalTurnResult],
    check_name: str,
) -> float | None:
    eligible = [
        result.checks[check_name]
        for result in results
        if result.checks[check_name] is not None
    ]
    if not eligible:
        return None
    return _rate(sum(bool(value) for value in eligible), len(eligible))


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _nearest_rank_percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(len(ordered) * percentile + 0.999999)))
    return ordered[rank - 1]


def _check_response(
    case: AgentEvalCase,
    response: AgentChatResponse,
    *,
    check_intent: bool,
    require_llm_planner: bool,
) -> list[str]:
    failures: list[str] = []
    trace_names = [trace.name for trace in response.tool_results]
    if (
        require_llm_planner
        and case.expects_llm_planner
        and "llm_tool_planner" not in trace_names
    ):
        failures.append("configured live LLM planner was not observed")
    if check_intent and case.expected_intent and response.intent != case.expected_intent:
        failures.append(
            f"intent expected {case.expected_intent}, got {response.intent}"
        )

    planner_capabilities = _planner_capabilities(response)
    for capability in case.expected_capabilities:
        if capability not in planner_capabilities:
            failures.append(f"planner capability missing: {capability}")

    for tool in case.required_tools:
        if tool not in response.tool_calls and tool not in trace_names:
            failures.append(f"required tool missing: {tool}")

    for group in case.required_tool_groups:
        if not any(tool in response.tool_calls or tool in trace_names for tool in group):
            failures.append(f"required tool group missing: {' or '.join(group)}")

    for tool in case.forbidden_tools:
        if tool in response.tool_calls:
            failures.append(f"forbidden tool was called: {tool}")

    traces_by_name = {trace.name: trace for trace in response.tool_results}
    for tool, expected_input in case.expected_tool_inputs.items():
        trace = traces_by_name.get(tool)
        if trace is None:
            failures.append(f"tool input unavailable because trace is missing: {tool}")
            continue
        failures.extend(
            f"{tool} input {failure}"
            for failure in _subset_failures(expected_input, trace.input)
        )

    for tool, expected_symbols in case.expected_tool_symbols.items():
        trace = traces_by_name.get(tool)
        if trace is None:
            failures.append(f"tool symbols unavailable because trace is missing: {tool}")
            continue
        actual_symbols = sorted(
            str(item.get("symbol"))
            for item in trace.output.get("events", [])
            if isinstance(item, dict) and item.get("symbol")
        )
        if actual_symbols != sorted(expected_symbols):
            failures.append(
                f"{tool} symbols expected {sorted(expected_symbols)!r}, "
                f"got {actual_symbols!r}"
            )

    for tool, expected_symbols in case.expected_tool_symbol_order.items():
        trace = traces_by_name.get(tool)
        if trace is None:
            failures.append(f"tool symbol order unavailable because trace is missing: {tool}")
            continue
        actual_symbols = [
            str(item.get("symbol"))
            for item in trace.output.get("events", [])
            if isinstance(item, dict) and item.get("symbol")
        ]
        if actual_symbols != expected_symbols:
            failures.append(
                f"{tool} symbol order expected {expected_symbols!r}, "
                f"got {actual_symbols!r}"
            )

    for tool, expected_count in case.expected_tool_matched_counts.items():
        trace = traces_by_name.get(tool)
        if trace is None:
            failures.append(f"tool count unavailable because trace is missing: {tool}")
            continue
        actual_count = trace.output.get("matched_count")
        if actual_count != expected_count:
            failures.append(
                f"{tool} matched_count expected {expected_count}, got {actual_count}"
            )

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


def _subset_failures(
    expected: dict[str, object],
    actual: dict[str, object],
    *,
    path: str = "",
) -> list[str]:
    """Compare a nested expected subset without coupling evals to extra trace fields."""

    failures: list[str] = []
    for key, expected_value in expected.items():
        current_path = f"{path}.{key}" if path else key
        if key not in actual:
            failures.append(f"missing {current_path}")
            continue
        actual_value = actual[key]
        if isinstance(expected_value, dict) and isinstance(actual_value, dict):
            failures.extend(
                _subset_failures(expected_value, actual_value, path=current_path)
            )
        elif actual_value != expected_value:
            failures.append(
                f"{current_path} expected {expected_value!r}, got {actual_value!r}"
            )
    return failures


def _aggregate_case_trials(
    *,
    case: AgentEvalCase,
    trials: list[AgentEvalCaseResult],
    minimum_pass_rate: float,
    require_llm_planner: bool,
) -> AgentEvalCaseResult:
    """Aggregate stochastic trials without hiding individual failures."""

    representative = next((trial for trial in trials if trial.passed), trials[-1])
    passed_trials = sum(1 for trial in trials if trial.passed)
    pass_rate = passed_trials / len(trials)
    passed = pass_rate >= minimum_pass_rate
    llm_expected = require_llm_planner and case.expects_llm_planner
    planner_signatures = {
        (tuple(trial.planner_capabilities), tuple(trial.planner_tool_calls))
        for trial in trials
        if trial.llm_planner_observed
    }
    provider_failure_trials = sum(
        1 for trial in trials if llm_expected and not trial.llm_planner_observed
    )
    stable = (
        passed_trials == len(trials)
        and provider_failure_trials == 0
        and (not llm_expected or len(planner_signatures) <= 1)
    )
    aggregate_failures: list[str] = []
    if not passed:
        aggregate_failures.append(
            f"trial pass rate {pass_rate:.0%} is below required {minimum_pass_rate:.0%}"
        )
        aggregate_failures.extend(
            dict.fromkeys(
                failure
                for trial in trials
                for failure in trial.failures
            )
        )
    trial_results = [
        {
            "trial": index,
            "passed": trial.passed,
            "failures": trial.failures,
            "intent": trial.intent,
            "planner_tool_calls": trial.planner_tool_calls,
            "final_tool_calls": trial.tool_calls,
            "backend_repaired_tools": trial.backend_repaired_tools,
            "planner_required_tools_missing": trial.planner_required_tools_missing,
            "planner_capabilities": trial.planner_capabilities,
            "planner_capabilities_missing": trial.planner_capabilities_missing,
            "llm_planner_observed": trial.llm_planner_observed,
            "provider_call_count": trial.provider_call_count,
            "provider_success_count": trial.provider_success_count,
            "provider_errors": trial.provider_errors,
        }
        for index, trial in enumerate(trials, start=1)
    ]
    return AgentEvalCaseResult(
        case_id=case.case_id,
        passed=passed,
        failures=aggregate_failures,
        intent=representative.intent,
        tool_calls=representative.tool_calls,
        planner_tool_calls=representative.planner_tool_calls,
        trace_names=representative.trace_names,
        warnings=representative.warnings,
        backend_repaired_tools=representative.backend_repaired_tools,
        repair_reasons=representative.repair_reasons,
        answer_preview=representative.answer_preview,
        planner_required_tools_missing=representative.planner_required_tools_missing,
        planner_capabilities=representative.planner_capabilities,
        planner_capabilities_missing=representative.planner_capabilities_missing,
        llm_planner_observed=representative.llm_planner_observed,
        llm_expected=llm_expected,
        trial_count=len(trials),
        passed_trials=passed_trials,
        pass_rate=round(pass_rate, 4),
        stable=stable,
        llm_planner_trials=sum(trial.llm_planner_trials for trial in trials),
        planner_tool_success_trials=sum(
            trial.planner_tool_success_trials for trial in trials
        ),
        backend_repair_trials=sum(trial.backend_repair_trials for trial in trials),
        provider_failure_trials=provider_failure_trials,
        provider_call_count=sum(trial.provider_call_count for trial in trials),
        provider_success_count=sum(trial.provider_success_count for trial in trials),
        provider_errors=list(
            dict.fromkeys(
                error
                for trial in trials
                for error in trial.provider_errors
            )
        ),
        trial_results=trial_results,
    )


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


def _planner_capabilities(response: AgentChatResponse) -> list[str]:
    """Read normalized semantic capabilities from the planner trace."""

    for trace in response.tool_results:
        if trace.name != "llm_tool_planner":
            continue
        raw_capabilities = trace.input.get("capabilities") or []
        if not isinstance(raw_capabilities, list):
            return []
        return [str(item) for item in raw_capabilities if isinstance(item, str)]
    return []


def _backend_repaired_tools(response: AgentChatResponse) -> list[str]:
    planner_calls = set(_planner_tool_calls(response))
    if not any(trace.name == "llm_tool_planner" for trace in response.tool_results):
        return []
    repair_ignored = {"llm_tool_planner", "llm_tool_answer", "template_general_answer"}
    return [
        tool
        for tool in response.tool_calls
        if tool not in planner_calls and tool not in repair_ignored
    ]
