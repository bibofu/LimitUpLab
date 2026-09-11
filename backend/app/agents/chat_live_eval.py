"""Contracts and trace-based evaluators for the Live Behavioral Eval suite."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.capability_contract import available_capability_names
from app.agents.tools import V1_CLOSED_MARKET_TOOL_NAMES
from app.models import AgentChatResponse, AgentToolTrace


LIVE_EVAL_VERSION = "agent-chat-live-eval-v1"
LIVE_EVAL_ENVIRONMENT_ID = "sample-events-plus-current-registry-v1"
LIVE_CATEGORIES = (
    "simple",
    "multi_tool",
    "replan",
    "multi_turn",
    "recovery",
    "boundary",
    "stress",
)
EXPECTED_CATEGORY_COUNTS = {
    "simple": 6,
    "multi_tool": 6,
    "replan": 8,
    "multi_turn": 6,
    "recovery": 4,
    "boundary": 4,
    "stress": 2,
}
INTERNAL_TRACES = {
    "agent_plan", "query_understanding", "llm_tool_planner",
    "llm_tool_answer", "template_general_answer", "tool_policy",
}
REFUSAL_MARKERS = ("不能", "无法", "不提供", "不会", "不支持")
CLARIFY_MARKERS = ("请明确", "请补充", "哪只", "哪个", "具体指")
PARTIAL_MARKERS = ("部分", "缺失", "不可用", "失败", "无法")


class LiveTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user: str = Field(min_length=1)


class ObservationCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    result_state: Literal["ok", "empty", "partial", "error"]


class ConditionalTools(BaseModel):
    model_config = ConfigDict(extra="forbid")
    when: ObservationCondition
    required_tools: list[str] = Field(min_length=1)


class ToolDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_tool: str
    target_tool: str


class ToolArgDependency(ToolDependency):
    source_path: str
    target_arg: str
    relation: Literal["same_set", "subset", "member", "equals"] = "same_set"


class AnswerFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_tool: str
    source_path: str
    relation: Literal["mentions_all", "mentions_any", "mentions_value"] = "mentions_value"


class FailureInjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    result_state: Literal["empty", "partial", "error"]
    match_args: dict[str, Any] = Field(default_factory=dict)


class LiveExpected(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required_capabilities: list[str] = Field(default_factory=list)
    optional_capabilities: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    optional_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    conditional_tools: list[ConditionalTools] = Field(default_factory=list)
    tool_dependencies: list[ToolDependency] = Field(default_factory=list)
    tool_arg_dependencies: list[ToolArgDependency] = Field(default_factory=list)
    expected_result_states: dict[str, str] = Field(default_factory=dict)
    required_answer_facts: list[AnswerFact] = Field(default_factory=list)
    required_answer_terms: list[str] = Field(default_factory=list)
    forbidden_answer_claims: list[str] = Field(default_factory=list)
    response_behavior: Literal["answer", "clarify", "refuse", "partial"] = "answer"
    max_tool_calls: int = Field(default=8, ge=0, le=30)
    max_llm_calls: int = Field(default=2, ge=0, le=10)
    max_replans: int = Field(default=0, ge=0, le=5)

    @model_validator(mode="after")
    def validate_contract(self) -> "LiveExpected":
        groups = [set(self.required_tools), set(self.optional_tools), set(self.forbidden_tools)]
        if any(groups[i] & groups[j] for i in range(3) for j in range(i + 1, 3)):
            raise ValueError("required, optional and forbidden tools must be disjoint")
        known_tools = set(V1_CLOSED_MARKET_TOOL_NAMES)
        mentioned = set().union(*groups)
        mentioned.update(self.expected_result_states)
        for item in self.conditional_tools:
            mentioned.add(item.when.tool)
            mentioned.update(item.required_tools)
        for item in [*self.tool_dependencies, *self.tool_arg_dependencies]:
            mentioned.update((item.source_tool, item.target_tool))
        if mentioned - known_tools:
            raise ValueError(f"unknown tools: {sorted(mentioned - known_tools)}")
        known_capabilities = set(available_capability_names(V1_CLOSED_MARKET_TOOL_NAMES))
        capabilities = set(self.required_capabilities + self.optional_capabilities)
        if capabilities - known_capabilities:
            raise ValueError(f"unknown capabilities: {sorted(capabilities - known_capabilities)}")
        return self


class LiveEvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    category: Literal["simple", "multi_tool", "replan", "multi_turn", "recovery", "boundary", "stress"]
    severity: Literal["critical", "high", "normal"] = "normal"
    tags: list[str] = Field(default_factory=list)
    anchor_datetime: str
    turns: list[LiveTurn] = Field(min_length=1, max_length=3)
    failure_injections: list[FailureInjection] = Field(default_factory=list)
    expected: LiveExpected

    @model_validator(mode="after")
    def validate_shape(self) -> "LiveEvalCase":
        if not re.fullmatch(r"LIVE-[A-Z]+-\d{3}", self.case_id):
            raise ValueError("invalid live case id")
        if self.category == "multi_turn" and len(self.turns) < 2:
            raise ValueError("multi-turn cases require at least two real turns")
        if self.category == "replan" and not self.expected.tool_arg_dependencies and not self.expected.conditional_tools:
            raise ValueError("replan cases require an observation-dependent assertion")
        return self


class LiveEvalDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["agent-chat-live-eval-v1"]
    environment_id: Literal["sample-events-plus-current-registry-v1"]
    description: str
    cases: list[LiveEvalCase]

    @model_validator(mode="after")
    def validate_suite(self) -> "LiveEvalDataset":
        counts = defaultdict(int)
        ids = set()
        for case in self.cases:
            counts[case.category] += 1
            if case.case_id in ids:
                raise ValueError(f"duplicate case id: {case.case_id}")
            ids.add(case.case_id)
        if dict(counts) != EXPECTED_CATEGORY_COUNTS:
            raise ValueError(f"expected category counts {EXPECTED_CATEGORY_COUNTS}, got {dict(counts)}")
        return self


def evaluate_live_trial(
    case: LiveEvalCase,
    responses: list[AgentChatResponse],
    *,
    llm_usage: dict[str, Any],
    latency_ms: int,
    judge: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate actual production response traces; no expected plan is injected."""

    traces = [trace for response in responses for trace in response.tool_results]
    planner_traces = [trace for trace in traces if trace.name == "llm_tool_planner"]
    capabilities = {
        str(value)
        for trace in planner_traces
        for value in trace.input.get("capabilities", [])
    }
    tool_traces = [trace for trace in traces if trace.name not in INTERNAL_TRACES]
    tools = [trace.name for trace in tool_traces]
    failures: list[str] = []

    missing_capabilities = set(case.expected.required_capabilities) - capabilities
    if missing_capabilities:
        failures.append(f"missing capabilities: {sorted(missing_capabilities)}")
    missing_tools = set(case.expected.required_tools) - set(tools)
    if missing_tools:
        failures.append(f"missing required tools: {sorted(missing_tools)}")
    forbidden = set(case.expected.forbidden_tools) & set(tools)
    if forbidden:
        failures.append(f"forbidden tools called: {sorted(forbidden)}")

    for dependency in case.expected.tool_dependencies:
        if not _ordered(tools, dependency.source_tool, dependency.target_tool):
            failures.append(f"dependency failed: {dependency.source_tool} -> {dependency.target_tool}")
    for dependency in case.expected.tool_arg_dependencies:
        source = _first_trace(tool_traces, dependency.source_tool)
        targets = _traces_after(tool_traces, dependency.target_tool, source)
        source_values = _path_values(source.output if source else {}, dependency.source_path)
        target_values = [value for trace in targets for value in _as_values(trace.input.get(dependency.target_arg))]
        if not _relation_holds(source_values, target_values, dependency.relation):
            failures.append(
                f"argument dependency failed: {dependency.source_tool}.{dependency.source_path} "
                f"-> {dependency.target_tool}.{dependency.target_arg}"
            )
    for conditional in case.expected.conditional_tools:
        observed = [trace for trace in tool_traces if trace.name == conditional.when.tool]
        if any(_result_state(trace) == conditional.when.result_state for trace in observed):
            missing = set(conditional.required_tools) - set(tools)
            if missing:
                failures.append(f"conditional tools missing: {sorted(missing)}")
    for tool, expected_state in case.expected.expected_result_states.items():
        observed = [_result_state(trace) for trace in tool_traces if trace.name == tool]
        if expected_state not in observed:
            failures.append(f"{tool} result state expected {expected_state}, got {observed}")

    answer = responses[-1].answer if responses else ""
    behavior_markers = {
        "refuse": REFUSAL_MARKERS,
        "clarify": CLARIFY_MARKERS,
        "partial": PARTIAL_MARKERS,
    }
    markers = behavior_markers.get(case.expected.response_behavior)
    if markers and not any(marker in answer for marker in markers):
        failures.append(f"answer does not satisfy {case.expected.response_behavior} behavior")
    fact_assertions = 0
    fact_assertions_passed = 0
    for term in case.expected.required_answer_terms:
        if term not in answer:
            failures.append(f"answer missing required term: {term}")
    for claim in case.expected.forbidden_answer_claims:
        if re.search(claim, answer, re.IGNORECASE):
            failures.append(f"forbidden answer claim matched: {claim}")
    for fact in case.expected.required_answer_facts:
        fact_assertions += 1
        trace = _first_trace(tool_traces, fact.source_tool)
        values = _path_values(trace.output if trace else {}, fact.source_path)
        hits = [str(value) in answer for value in values if value is not None]
        failed_fact = not hits or (fact.relation == "mentions_all" and not all(hits)) or (
            fact.relation in {"mentions_any", "mentions_value"} and not any(hits)
        )
        if failed_fact:
            failures.append(f"answer fact missing: {fact.source_tool}.{fact.source_path}")
        else:
            fact_assertions_passed += 1

    policy_repairs = sum(len(response.tool_policy.backend_repaired_tools) for response in responses)
    llm_calls = int(llm_usage.get("call_count") or 0)
    tool_calls = len(tools)
    replan_count = 0  # The current production runtime has no Observation -> Planner loop.
    if tool_calls > case.expected.max_tool_calls:
        failures.append(f"tool budget exceeded: {tool_calls}/{case.expected.max_tool_calls}")
    if llm_calls > case.expected.max_llm_calls:
        failures.append(f"LLM budget exceeded: {llm_calls}/{case.expected.max_llm_calls}")
    if replan_count > case.expected.max_replans:
        failures.append(f"replan budget exceeded: {replan_count}/{case.expected.max_replans}")
    if judge is not None and not judge.get("passed", False):
        failures.append("LLM Judge semantic quality gate failed")

    return {
        "case_id": case.case_id,
        "category": case.category,
        "severity": case.severity,
        "passed": not failures,
        "failure_reasons": failures,
        "capabilities": sorted(capabilities),
        "tool_calls": tools,
        "planner_output": [trace.input for trace in planner_traces],
        "tool_trace": [trace.model_dump(mode="json") for trace in tool_traces],
        "policy_repair": [response.tool_policy.model_dump(mode="json") for response in responses],
        "answer": answer,
        "turn_answers": [response.answer for response in responses],
        "llm_call_count": llm_calls,
        "tool_call_count": tool_calls,
        "replan_count": replan_count,
        "backend_repair_count": policy_repairs,
        "token_usage": llm_usage,
        "latency_ms": latency_ms,
        "judge_result": judge,
        "grounding_assertions": fact_assertions,
        "grounding_assertions_passed": fact_assertions_passed,
    }


def aggregate_live_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate trials into behavioral and efficiency metrics."""

    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        by_case[result["case_id"]].append(result)
    first = [trials[0] for trials in by_case.values()]
    latencies = sorted(int(item["latency_ms"]) for item in results)
    passed = sum(bool(item["passed"]) for item in results)
    metric = lambda numerator, denominator: round(numerator / denominator, 4) if denominator else None
    category_rates = {
        category: metric(sum(item["passed"] for item in first if item["category"] == category),
                         sum(item["category"] == category for item in first))
        for category in LIVE_CATEGORIES
    }
    required_tool_checks = sum("missing required tools" not in " ".join(item["failure_reasons"]) for item in results)
    dependency_trials = [item for item in results if item["category"] in {"replan", "stress"}]
    recovery_trials = [item for item in results if item["category"] == "recovery"]
    multi_turn = [item for item in results if item["category"] == "multi_turn"]
    grounding_total = sum(item["grounding_assertions"] for item in results)
    grounding_passed = sum(item["grounding_assertions_passed"] for item in results)
    return {
        "case_count": len(by_case),
        "trial_count": len(results),
        "task_success_rate": metric(passed, len(results)),
        "stable_rate": metric(sum(all(t["passed"] for t in trials) for trials in by_case.values()), len(by_case)),
        "provider_failure_rate": metric(
            sum(bool(item["token_usage"].get("failed_call_count")) for item in results),
            len(results),
        ),
        "planner_accuracy": metric(sum(not any("missing capabilities" in f for f in item["failure_reasons"]) for item in results), len(results)),
        "required_tool_recall": metric(required_tool_checks, len(results)),
        "grounding_accuracy": metric(grounding_passed, grounding_total),
        "unsupported_claim_rate": None,
        "unsupported_claim_rate_reason": "current runtime has no sentence-level claim ledger",
        "multi_turn_success_rate": metric(sum(item["passed"] for item in multi_turn), len(multi_turn)),
        "failure_recovery_rate": metric(sum(item["passed"] for item in recovery_trials), len(recovery_trials)),
        "replan_success_rate": metric(sum(item["passed"] for item in dependency_trials), len(dependency_trials)),
        "unnecessary_replan_rate": 0.0,
        "avg_tool_calls": round(sum(item["tool_call_count"] for item in results) / len(results), 2),
        "avg_llm_calls": round(sum(item["llm_call_count"] for item in results) / len(results), 2),
        "avg_tokens": round(sum(item["token_usage"].get("total_tokens", 0) for item in results) / len(results), 2),
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "category_success_rate": category_rates,
        "case_trial_success": {
            case_id: {
                "passed_trials": sum(item["passed"] for item in trials),
                "trials": len(trials),
                "trial_success_rate": metric(sum(item["passed"] for item in trials), len(trials)),
            }
            for case_id, trials in by_case.items()
        },
    }


def _first_trace(traces: list[AgentToolTrace], name: str) -> AgentToolTrace | None:
    return next((trace for trace in traces if trace.name == name), None)


def _traces_after(traces: list[AgentToolTrace], name: str, source: AgentToolTrace | None) -> list[AgentToolTrace]:
    start = traces.index(source) + 1 if source in traces else 0
    return [trace for trace in traces[start:] if trace.name == name]


def _ordered(names: list[str], source: str, target: str) -> bool:
    return source in names and target in names and names.index(source) < names.index(target)


def _result_state(trace: AgentToolTrace) -> str:
    return trace.result.status if trace.result else ("error" if trace.status == "error" else "ok")


def _path_values(payload: Any, path: str) -> list[Any]:
    """Resolve the small $.a[*].b JSONPath subset used by the committed suite."""

    nodes = [payload]
    for raw in path.removeprefix("$.").split("."):
        many = raw.endswith("[*]")
        key = raw[:-3] if many else raw
        next_nodes: list[Any] = []
        for node in nodes:
            value = node.get(key) if isinstance(node, dict) else None
            if many and isinstance(value, list):
                next_nodes.extend(value)
            elif value is not None:
                next_nodes.append(value)
        nodes = next_nodes
    return [value for node in nodes for value in _as_values(node)]


def _as_values(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple, set)) else [value]


def _relation_holds(source: list[Any], target: list[Any], relation: str) -> bool:
    left, right = set(map(str, source)), set(map(str, target))
    if not left or not right:
        return False
    if relation in {"member", "equals"}:
        return bool(left & right) if relation == "member" else left == right
    return right == left if relation == "same_set" else right <= left


def _percentile(values: list[int], quantile: float) -> int | None:
    if not values:
        return None
    return values[round((len(values) - 1) * quantile)]
