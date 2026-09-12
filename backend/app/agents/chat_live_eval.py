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
LIVE_EVAL_ENVIRONMENT_ID = "chat-live-world-v2-fully-frozen-v2"
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
    "routing_decision", "complex_graph_plan", "complex_graph_step",
    "complex_graph_completion", "complex_graph_replan",
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
    result_state: Literal["ok", "empty", "partial", "error"] | None = None
    path: str | None = None
    relation: Literal["equals", "contains", "not_contains", "empty", "non_empty"] | None = None
    value: Any = None

    @model_validator(mode="after")
    def validate_condition(self) -> "ObservationCondition":
        if self.result_state is None and (self.path is None or self.relation is None):
            raise ValueError("condition requires result_state or path/relation")
        if (self.path is None) != (self.relation is None):
            raise ValueError("content conditions require both path and relation")
        return self


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


class DependencySource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    path: str


class MultiSourceToolArgDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[DependencySource] = Field(min_length=2)
    operation: Literal["intersection", "union"]
    target_tool: str
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
    multi_source_tool_arg_dependencies: list[MultiSourceToolArgDependency] = Field(default_factory=list)
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
        for item in self.multi_source_tool_arg_dependencies:
            mentioned.add(item.target_tool)
            mentioned.update(source.tool for source in item.sources)
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
        if (
            self.category == "replan"
            and not self.expected.tool_arg_dependencies
            and not self.expected.multi_source_tool_arg_dependencies
            and not self.expected.conditional_tools
        ):
            raise ValueError("replan cases require an observation-dependent assertion")
        return self


class LiveEvalDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["agent-chat-live-eval-v1"]
    environment_id: Literal["chat-live-world-v2-fully-frozen-v2"]
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
    task_plans = [trace for trace in traces if trace.name == "task_plan"]
    capabilities = {
        str(value)
        for trace in planner_traces
        for value in trace.input.get("capabilities", [])
    }
    capabilities.update(str(step["capability"]) for trace in task_plans
                        for step in trace.output.get("steps", []) if step.get("capability"))
    tool_traces = [trace for trace in traces if trace.name not in INTERNAL_TRACES and not trace.name.startswith("task_")]
    tools = [trace.name for trace in tool_traces]
    raw_tools = {
        str(call["name"])
        for trace in planner_traces
        for call in trace.input.get("tool_calls", [])
        if isinstance(call, dict) and call.get("name")
    }
    raw_tools.update(str(step["tool_name"]) for trace in task_plans
                     for step in trace.output.get("steps", []) if step.get("tool_name"))
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

    conditional_targets = {
        tool for item in case.expected.conditional_tools for tool in item.required_tools
    }
    active_conditional_targets = {
        tool
        for item in case.expected.conditional_tools
        if any(
            trace.name == item.when.tool and _condition_matches(trace, item.when)
            for trace in tool_traces
        )
        for tool in item.required_tools
    }

    for dependency in case.expected.tool_dependencies:
        if not _ordered(tools, dependency.source_tool, dependency.target_tool):
            failures.append(f"dependency failed: {dependency.source_tool} -> {dependency.target_tool}")
    for dependency in case.expected.tool_arg_dependencies:
        if (
            dependency.target_tool in conditional_targets
            and dependency.target_tool not in active_conditional_targets
        ):
            continue
        source = _first_trace(tool_traces, dependency.source_tool)
        targets = _traces_after(tool_traces, dependency.target_tool, source)
        source_values = _path_values(source.output if source else {}, dependency.source_path)
        target_values = [
            value for trace in targets
            for value in _as_values(trace.input.get(dependency.target_arg))
            if value is not None
        ]
        # An unscoped, complete empty query is stronger evidence than querying
        # each source entity separately: none of the candidates can be present.
        unscoped_empty_proof = bool(targets) and not target_values and all(
            dependency.target_arg not in trace.input and _result_state(trace) == "empty"
            for trace in targets
        )
        if not unscoped_empty_proof and not _relation_holds(source_values, target_values, dependency.relation):
            failures.append(
                f"argument dependency failed: {dependency.source_tool}.{dependency.source_path} "
                f"-> {dependency.target_tool}.{dependency.target_arg}"
            )
    for dependency in case.expected.multi_source_tool_arg_dependencies:
        source_sets: list[set[str]] = []
        latest_source_index = -1
        missing_sources: list[str] = []
        for source in dependency.sources:
            matching_sources = [
                (index, trace)
                for index, trace in enumerate(tool_traces)
                if trace.name == source.tool
            ]
            values = [
                value
                for _index, trace in matching_sources
                for value in _path_values(trace.output, source.path)
            ]
            if not matching_sources or not values:
                missing_sources.append(source.tool)
                continue
            source_sets.append(set(map(str, values)))
            latest_source_index = max(
                latest_source_index,
                max(index for index, _trace in matching_sources),
            )
        targets = [
            trace for index, trace in enumerate(tool_traces)
            if trace.name == dependency.target_tool and index > latest_source_index
        ]
        target_values = [
            value for trace in targets
            for value in _as_values(trace.input.get(dependency.target_arg))
        ]
        expected_values = _combine_source_sets(source_sets, dependency.operation)
        if missing_sources or not _relation_holds(
            list(expected_values), target_values, dependency.relation
        ):
            detail = f"; missing sources: {sorted(missing_sources)}" if missing_sources else ""
            failures.append(
                f"multi-source dependency failed: {dependency.operation} -> "
                f"{dependency.target_tool}.{dependency.target_arg}{detail}"
            )
    conditional_assertions: list[dict[str, Any]] = []
    for conditional in case.expected.conditional_tools:
        matches = [
            (index, trace) for index, trace in enumerate(tool_traces)
            if trace.name == conditional.when.tool and _condition_matches(trace, conditional.when)
        ]
        if matches:
            condition_index, _condition_trace = matches[0]
            target_indices = {
                tool: [
                    index for index, trace in enumerate(tool_traces)
                    if trace.name == tool and index > condition_index
                ]
                for tool in conditional.required_tools
            }
            missing = {tool for tool, indices in target_indices.items() if not indices}
            if missing:
                failures.append(
                    f"conditional tools missing after observation: {sorted(missing)}"
                )
            conditional_assertions.append(
                {
                    "condition_tool": conditional.when.tool,
                    "condition_tool_call_index": condition_index,
                    "condition_observation_index": condition_index,
                    "target_tool_call_indices": target_indices,
                    "matched": True,
                    "passed": not missing,
                }
            )
        else:
            conditional_assertions.append(
                {"condition_tool": conditional.when.tool, "matched": False, "passed": True}
            )
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
    required_fact_assertions = 0
    required_fact_assertions_passed = 0
    for term in case.expected.required_answer_terms:
        if term not in answer:
            failures.append(f"answer missing required term: {term}")
    for claim in case.expected.forbidden_answer_claims:
        if re.search(claim, answer, re.IGNORECASE):
            failures.append(f"forbidden answer claim matched: {claim}")
    for fact in case.expected.required_answer_facts:
        required_fact_assertions += 1
        trace = _first_trace(tool_traces, fact.source_tool)
        values = _path_values(trace.output if trace else {}, fact.source_path)
        hits = [str(value) in answer for value in values if value is not None]
        failed_fact = not hits or (fact.relation == "mentions_all" and not all(hits)) or (
            fact.relation in {"mentions_any", "mentions_value"} and not any(hits)
        )
        if failed_fact:
            failures.append(f"answer fact missing: {fact.source_tool}.{fact.source_path}")
        else:
            required_fact_assertions_passed += 1

    policy_repairs = sum(len(response.tool_policy.policy_repaired_tools) for response in responses)
    backend_repairs = sum(len(response.tool_policy.backend_repaired_tools) for response in responses)
    graph_plans = [trace for trace in traces if trace.name == "complex_graph_plan"]
    completion_traces = [trace for trace in traces if trace.name in {"complex_graph_completion", "task_completion"}]
    task_executions = [trace for trace in traces if trace.name == "task_execution"]
    required_capabilities = set(case.expected.required_capabilities)
    required_tools = set(case.expected.required_tools)
    raw_capability_hits = len(required_capabilities & capabilities)
    raw_required_tool_hits = len(required_tools & raw_tools)
    effective_required_tool_hits = len(required_tools & set(tools))
    llm_calls = int(llm_usage.get("call_count") or 0)
    tool_calls = len(tools)
    replan_count = sum(int(trace.input.get("replan_count") or 0) for trace in graph_plans)
    replan_count += sum(int(trace.output.get("replan_count") or 0) for trace in task_executions)
    graph_compilation_count = sum(int(trace.input.get("graph_compilation_count") or 0) for trace in graph_plans)
    graph_compilation_count += len(task_executions)
    backend_repair_count = backend_repairs + sum(int(trace.input.get("backend_repair_count") or 0) for trace in graph_plans)
    policy_repair_count = policy_repairs + sum(int(trace.input.get("policy_repair_count") or 0) for trace in graph_plans)
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
        "raw_tool_calls": sorted(raw_tools),
        "planner_output": [trace.input for trace in planner_traces] + [trace.output for trace in task_plans],
        "tool_trace": [trace.model_dump(mode="json") for trace in tool_traces],
        "graph_trace": [
            trace.model_dump(mode="json")
            for trace in traces
            if trace.name.startswith(("complex_graph_", "task_")) or trace.name == "routing_decision"
        ],
        "full_trace": [trace.model_dump(mode="json") for trace in traces],
        "policy_repair": [response.tool_policy.model_dump(mode="json") for response in responses],
        "answer": answer,
        "turn_answers": [response.answer for response in responses],
        "llm_call_count": llm_calls,
        "tool_call_count": tool_calls,
        "replan_count": replan_count,
        "initial_plan_complete": bool(completion_traces and completion_traces[0].output.get("complete")),
        "graph_compilation_count": graph_compilation_count,
        "backend_repair_count": backend_repair_count,
        "policy_repair_count": policy_repair_count,
        "token_usage": llm_usage,
        "latency_ms": latency_ms,
        "judge_result": judge,
        "conditional_assertions": conditional_assertions,
        "required_capability_count": len(required_capabilities),
        "raw_capability_hits": raw_capability_hits,
        "raw_capability_recall": _rate(raw_capability_hits, len(required_capabilities)),
        "required_tool_count": len(required_tools),
        "raw_required_tool_hits": raw_required_tool_hits,
        "raw_required_tool_recall": _rate(raw_required_tool_hits, len(required_tools)),
        "effective_required_tool_hits": effective_required_tool_hits,
        "effective_required_tool_recall": _rate(effective_required_tool_hits, len(required_tools)),
        "backend_repair_needed": backend_repair_count > 0,
        "required_fact_assertions": required_fact_assertions,
        "required_fact_assertions_passed": required_fact_assertions_passed,
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
    observation_dependent_trials = [item for item in results if item["category"] in {"replan", "stress"}]
    complex_trials = [item for item in results if item.get("graph_compilation_count", 0) > 0]
    triggered_replans = [item for item in complex_trials if item["replan_count"] > 0]
    recovery_trials = [item for item in results if item["category"] == "recovery"]
    multi_turn = [item for item in results if item["category"] == "multi_turn"]
    fact_total = sum(item["required_fact_assertions"] for item in results)
    fact_passed = sum(item["required_fact_assertions_passed"] for item in results)
    capability_total = sum(item["required_capability_count"] for item in results)
    tool_total = sum(item["required_tool_count"] for item in results)
    return {
        "case_count": len(by_case),
        "trial_count": len(results),
        "task_success_rate": metric(passed, len(results)),
        "stable_rate": metric(sum(all(t["passed"] for t in trials) for trials in by_case.values()), len(by_case)),
        "provider_failure_rate": metric(
            sum(bool(item["token_usage"].get("failed_call_count")) for item in results),
            len(results),
        ),
        "raw_capability_recall": metric(sum(item["raw_capability_hits"] for item in results), capability_total),
        "raw_required_tool_recall": metric(sum(item["raw_required_tool_hits"] for item in results), tool_total),
        "effective_required_tool_recall": metric(sum(item["effective_required_tool_hits"] for item in results), tool_total),
        "backend_repair_rate": metric(sum(item["backend_repair_needed"] for item in results), len(results)),
        "policy_repair_rate": metric(sum(item["policy_repair_count"] > 0 for item in results), len(results)),
        "avg_graph_compilations": round(sum(item["graph_compilation_count"] for item in results) / len(results), 2),
        "required_fact_coverage": metric(fact_passed, fact_total),
        "unsupported_claim_rate": None,
        "unsupported_claim_rate_reason": "current runtime has no sentence-level claim ledger",
        "multi_turn_success_rate": metric(sum(item["passed"] for item in multi_turn), len(multi_turn)),
        "failure_recovery_rate": metric(sum(item["passed"] for item in recovery_trials), len(recovery_trials)),
        "observation_dependent_task_success_rate": metric(
            sum(item["passed"] for item in observation_dependent_trials),
            len(observation_dependent_trials),
        ),
        "replan_trigger_rate": metric(len(triggered_replans), len(complex_trials)),
        "replan_success_rate": metric(sum(item["passed"] for item in triggered_replans), len(triggered_replans)),
        "unnecessary_replan_rate": metric(sum(item.get("initial_plan_complete", False) for item in triggered_replans), len(triggered_replans)),
        "avg_replans_per_complex_task": round(sum(item["replan_count"] for item in complex_trials) / len(complex_trials), 2) if complex_trials else None,
        "max_replans_observed": max((item["replan_count"] for item in complex_trials), default=0),
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


def _condition_matches(trace: AgentToolTrace, condition: ObservationCondition) -> bool:
    if condition.result_state is not None and _result_state(trace) != condition.result_state:
        return False
    if condition.path is None or condition.relation is None:
        return True
    values = _path_values(trace.output, condition.path)
    expected = str(condition.value)
    observed = set(map(str, values))
    if condition.relation == "equals":
        return observed == {expected}
    if condition.relation == "contains":
        return expected in observed
    if condition.relation == "not_contains":
        return expected not in observed
    if condition.relation == "empty":
        return not values
    return bool(values)


def _combine_source_sets(source_sets: list[set[str]], operation: str) -> set[str]:
    if not source_sets:
        return set()
    if operation == "intersection":
        return set.intersection(*source_sets)
    return set.union(*source_sets)


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _percentile(values: list[int], quantile: float) -> int | None:
    if not values:
        return None
    return values[round((len(values) - 1) * quantile)]
