"""Regression evaluation runner for the first-board chat Agent."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.agents.chat import answer_first_board_chat, template_answer_override
from app.models import AgentChatRequest, AgentChatResponse, LimitUpEvent
from app.services.llm_provider import LLMProvider, LLMResult


FORBIDDEN_INVESTMENT_TERMS = ("买入", "卖出", "仓位", "目标价", "收益承诺")


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
        tuple(trial.planner_tool_calls)
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
