"""Bounded Observe → Check → Replan orchestration for complex queries."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable, Literal, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from app.agents.chat_answer_validation import _add_composed_tool_facts
from app.agents.capability_contract import CAPABILITY_BY_NAME
from app.agents.tool_policy import ToolExecution
from app.agents.tools import AgentToolRegistry
from app.models import AgentChatRequest, AgentToolTrace

from .completion import check_completion
from .executor import execute_step, graph_step_trace, resolve_dynamic_arguments
from .models import ComplexPlanStep, EntityRef, ReplanRequest
from .observer import observation_payload, symbols_from_trace
from .planner import build_flagship_plan, build_initial_plan, scenario_capabilities
from .replanner import replan, validate_replan

MAX_REPLAN = 2
MAX_TOOL_CALLS = 10
MAX_LLM_CALLS = 4
MAX_DYNAMIC_ENTITIES = 20


class _GraphState(TypedDict):
    pending: list[ComplexPlanStep]
    all_steps: list[ComplexPlanStep]
    execution: ToolExecution
    graph_traces: list[AgentToolTrace]
    entity_sets: dict[str, list[EntityRef]]
    completed_steps: list[str]
    failed_steps: list[str]
    replan_count: int
    completion: dict[str, Any]
    terminal_reason: str | None
    budget_exhausted: bool
    active_step: ComplexPlanStep | None
    active_calls: list[dict[str, Any]]
    active_observations: list[AgentToolTrace]
    active_errors: list[str]
    active_latency_ms: int
    answer_meta: dict[str, Any]


@dataclass(frozen=True)
class ComplexGraphResult:
    execution: ToolExecution
    graph_traces: list[AgentToolTrace]
    final_answer: str
    answer_meta: dict[str, Any]
    completion_status: str
    failed_steps: list[str]
    plan_steps: list[dict[str, Any]]
    graph_run_id: str
    completion_check: dict[str, Any]
    replan_count: int
    graph_compilation_count: int
    backend_repair_count: int
    policy_repair_count: int


def _merge_execution(target: ToolExecution, part: ToolExecution) -> None:
    target["facts"].update(part["facts"])
    target["tool_results"].extend(part["tool_results"])
    target["tool_call_names"].extend(part["tool_call_names"])
    target["references"] = list(dict.fromkeys([*target["references"], *part["references"]]))


def _observe_entity_set(step: ComplexPlanStep, traces: list[AgentToolTrace], entity_sets: dict[str, list[EntityRef]]) -> None:
    if not step.output_entity_set or not traces:
        return
    limit = 3 if step.output_entity_set == "top_ratings" else MAX_DYNAMIC_ENTITIES
    entities = symbols_from_trace(traces[0], limit=limit)
    for entity in entities:
        entity["source_steps"] = [step.step_id]
    entity_sets[step.output_entity_set] = entities


def _execute_operation(step: ComplexPlanStep, execution: ToolExecution, entity_sets: dict[str, list[EntityRef]]) -> str | None:
    if step.operation != "intersection" or not step.output_entity_set:
        return "unsupported deterministic operation"
    _add_composed_tool_facts("热股和涨停股交集", execution["facts"])
    payload = execution["facts"].get("hot_stock_limit_up_intersection")
    if not isinstance(payload, dict):
        return "intersection sources were unavailable"
    payload["event_label"] = "涨停票"
    entity_sets[step.output_entity_set] = [
        {"symbol": str(item["symbol"]), "name": item.get("name"), "source_steps": list(step.depends_on)}
        for item in payload.get("items", [])[:MAX_DYNAMIC_ENTITIES]
        if isinstance(item, dict) and item.get("symbol")
    ]
    return None


def run_complex_graph(
    *,
    scenario: str,
    request: AgentChatRequest,
    tools: AgentToolRegistry,
    limit_up_arguments: dict[str, Any],
    context_symbol: str | None,
    answer_builder: Callable[[ToolExecution], dict[str, Any]],
    llm_call_count: int = 1,
) -> ComplexGraphResult:
    """Execute the allowlisted graph; every ready step is policy-gated."""

    graph_run_id = f"cgraph-{uuid4().hex[:12]}"
    initial_steps = build_initial_plan(scenario, request, limit_up_arguments)
    validation_errors: list[str] = []
    for step in initial_steps:
        if not step.tool_name:
            continue
        contract = CAPABILITY_BY_NAME.get(step.capability or "")
        authorized = {item.name for item in contract.required_tools} if contract else set()
        if step.tool_name not in authorized or not tools.is_enabled(step.tool_name):
            validation_errors.append(f"{step.step_id}: tool is not capability-authorized or enabled")
    def execute_node(state: _GraphState) -> dict[str, Any]:
        if not state["pending"]:
            return {"active_step": None}
        step, remaining = state["pending"][0], state["pending"][1:]
        started = perf_counter()
        if any(item not in state["completed_steps"] for item in step.depends_on):
            errors, calls, observations = ["unresolved dependency"], [], []
        elif step.step_type == "operation":
            error = _execute_operation(step, state["execution"], state["entity_sets"])
            errors, calls, observations = ([error] if error else []), [], []
        else:
            part, calls, errors = execute_step(
                step, request=request, tools=tools, context_symbol=context_symbol,
                entity_sets=state["entity_sets"],
                remaining_tool_calls=MAX_TOOL_CALLS - len(state["execution"]["tool_call_names"]),
            )
            observations = part["tool_results"]
            if not errors:
                _merge_execution(state["execution"], part)
        return {
            "pending": remaining, "active_step": step, "active_calls": calls,
            "active_observations": observations, "active_errors": [str(item) for item in errors],
            "active_latency_ms": round((perf_counter() - started) * 1000),
        }

    def observe_node(state: _GraphState) -> dict[str, Any]:
        step = state["active_step"]
        if step is None:
            return {}
        errors = state["active_errors"]
        completed, failed = list(state["completed_steps"]), list(state["failed_steps"])
        terminal_reason = state["terminal_reason"]
        if errors:
            failed.append(step.step_id)
            terminal_reason = "; ".join(errors)
        else:
            _observe_entity_set(step, state["active_observations"], state["entity_sets"])
            completed.append(step.step_id)
        trace = graph_step_trace(
            graph_run_id=graph_run_id, step=step, step_index=len(completed) + len(failed),
            calls=state["active_calls"], observations=state["active_observations"],
            status="failed" if errors else "completed",
            tool_call_count=len(state["execution"]["tool_call_names"]), llm_call_count=llm_call_count,
            latency_ms=state["active_latency_ms"],
        )
        return {"completed_steps": completed, "failed_steps": failed, "terminal_reason": terminal_reason, "graph_traces": [*state["graph_traces"], trace]}

    def completion_node(state: _GraphState) -> dict[str, Any]:
        completion = check_completion(scenario, state["execution"]["tool_results"], state["replan_count"])
        trace = AgentToolTrace(
            name="complex_graph_completion",
            input={"graph_run_id": graph_run_id, "completion_check": completion.model_dump(mode="json"), "completion_reason": completion.reason, "tool_call_count": len(state["execution"]["tool_call_names"]), "llm_call_count": llm_call_count},
            output=completion.model_dump(mode="json"), summary=f"Completion check: {completion.reason}",
            status="success" if completion.complete else "skipped",
        )
        terminal_reason = state["terminal_reason"]
        budget_exhausted = not completion.complete and (
            state["replan_count"] >= MAX_REPLAN
            or len(state["execution"]["tool_call_names"]) >= MAX_TOOL_CALLS
            or llm_call_count >= MAX_LLM_CALLS
        )
        if budget_exhausted:
            terminal_reason = terminal_reason or "bounded graph budget exhausted"
        return {"completion": completion.model_dump(mode="json"), "terminal_reason": terminal_reason, "budget_exhausted": budget_exhausted, "graph_traces": [*state["graph_traces"], trace]}

    def replan_node(state: _GraphState) -> dict[str, Any]:
        completion = state["completion"]
        payload = ReplanRequest(
            original_user_query=request.message,
            original_plan=[item.model_dump(mode="json") for item in initial_steps],
            completed_steps=state["completed_steps"], failed_steps=state["failed_steps"],
            tool_observations=observation_payload(state["execution"]["tool_results"]),
            missing_requirements=list(completion["missing_requirements"]),
            remaining_tool_calls=MAX_TOOL_CALLS - len(state["execution"]["tool_call_names"]),
            remaining_replans=MAX_REPLAN - state["replan_count"],
        )
        index = state["replan_count"] + 1
        output = replan(scenario, payload, replan_index=index)
        errors = validate_replan(output, prior_steps=[item.model_dump(mode="json") for item in state["all_steps"]], remaining_tool_calls=payload.remaining_tool_calls)
        trace = AgentToolTrace(
            name="complex_graph_replan",
            input={"graph_run_id": graph_run_id, "replan_triggered": True, "replan_reason": output.reason, "replan_index": index, **payload.model_dump(mode="json")},
            output={"replan_output": output.model_dump(mode="json"), **output.model_dump(mode="json"), "validation_errors": errors},
            summary=f"Replan {index}: {output.reason}", status="error" if errors else "success",
        )
        return {
            "replan_count": index, "pending": [] if errors else list(output.new_steps),
            "all_steps": [*state["all_steps"], *([] if errors else output.new_steps)],
            "terminal_reason": "; ".join(errors) if errors else state["terminal_reason"],
            "graph_traces": [*state["graph_traces"], trace],
        }

    def answer_node(state: _GraphState) -> dict[str, Any]:
        answer_meta = answer_builder(state["execution"])
        if not state["completion"].get("complete"):
            answer_meta["answer"] = f"当前信息不足或部分步骤未完成。\n\n{answer_meta.get('answer', '')}".strip()
            answer_meta.setdefault("warnings", []).append(state["terminal_reason"] or state["completion"].get("reason"))
        return {"answer_meta": answer_meta}

    def after_observe(state: _GraphState) -> Literal["execute", "completion"]:
        return "execute" if state["pending"] else "completion"

    def after_completion(state: _GraphState) -> Literal["replan", "answer"]:
        return "answer" if state["completion"].get("complete") or state["budget_exhausted"] else "replan"

    def after_replan(state: _GraphState) -> Literal["execute", "answer"]:
        return "execute" if state["pending"] else "answer"

    builder = StateGraph(_GraphState)
    builder.add_node("execute", execute_node)
    builder.add_node("observe", observe_node)
    builder.add_node("completion", completion_node)
    builder.add_node("replan", replan_node)
    builder.add_node("answer", answer_node)
    builder.add_edge(START, "execute")
    builder.add_edge("execute", "observe")
    builder.add_conditional_edges("observe", after_observe)
    builder.add_conditional_edges("completion", after_completion)
    builder.add_conditional_edges("replan", after_replan)
    builder.add_edge("answer", END)
    initial: _GraphState = {
        "pending": [] if validation_errors else list(initial_steps), "all_steps": list(initial_steps),
        "execution": {"facts": {}, "tool_results": [], "tool_call_names": [], "references": []},
        "graph_traces": [], "entity_sets": {}, "completed_steps": [],
        "failed_steps": ["validate_plan"] if validation_errors else [], "replan_count": 0,
        "completion": check_completion(scenario, [], 0).model_dump(mode="json"),
        "terminal_reason": "; ".join(validation_errors) if validation_errors else None,
        "budget_exhausted": False,
        "active_step": None, "active_calls": [], "active_observations": [],
        "active_errors": [], "active_latency_ms": 0, "answer_meta": {},
    }
    state = builder.compile().invoke(initial, config={"recursion_limit": 64})
    execution = state["execution"]
    graph_traces = state["graph_traces"]
    completion = state["completion"]
    answer_meta = state["answer_meta"]
    failed_steps = state["failed_steps"]
    all_steps = state["all_steps"]
    replan_count = state["replan_count"]
    return ComplexGraphResult(
        execution=execution, graph_traces=graph_traces,
        final_answer=str(answer_meta["answer"]), answer_meta=answer_meta,
        completion_status=("complete" if completion["complete"] else ("failed" if not execution["tool_results"] else "partial")),
        failed_steps=failed_steps, plan_steps=[item.model_dump(mode="json") for item in all_steps],
        graph_run_id=graph_run_id, completion_check=completion,
        replan_count=replan_count, graph_compilation_count=sum(item.tool_name is not None for item in all_steps),
        backend_repair_count=0, policy_repair_count=0,
    )


def run_hot_limit_up_rating_graph(**kwargs: Any) -> ComplexGraphResult:
    """Keep the Phase 1 entry point and deterministic intersection binding."""

    kwargs.pop("capabilities", None)
    return run_complex_graph(scenario="hot_limit_up_rating_intersection_v1", **kwargs)


__all__ = [
    "MAX_LLM_CALLS", "MAX_REPLAN", "MAX_TOOL_CALLS", "ComplexGraphResult",
    "build_flagship_plan", "resolve_dynamic_arguments", "run_complex_graph",
    "run_hot_limit_up_rating_graph", "scenario_capabilities",
]
