"""LangGraph Phase 1 execution for the hot-stock/limit-up rating join."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from app.agents.capability_contract import CAPABILITY_BY_NAME
from app.agents.chat_answer_validation import _add_composed_tool_facts
from app.agents.tool_execution import execute_tool_calls
from app.agents.tool_policy import AgentToolPolicyEngine, ToolExecution
from app.agents.tools import AgentToolRegistry
from app.models import AgentChatRequest, AgentToolTrace

from .models import (
    ArgumentBinding,
    ComplexAgentState,
    ComplexPlanStep,
    EntityRef,
    ResultReference,
)

MAX_DYNAMIC_ENTITIES = 20


@dataclass(frozen=True)
class ComplexGraphResult:
    """State returned to the existing chat response adapter."""

    execution: ToolExecution
    graph_traces: list[AgentToolTrace]
    final_answer: str
    answer_meta: dict[str, Any]
    completion_status: str
    failed_steps: list[str]
    plan_steps: list[dict[str, Any]]


def build_flagship_plan(limit_up_arguments: dict[str, Any]) -> list[ComplexPlanStep]:
    """Build the only dependency-aware plan supported in Phase 1."""

    return [
        ComplexPlanStep(
            step_id="S1",
            capability="popularity",
            tool_name="hot_stock_ranking",
            arguments={"period": "day", "limit": 10, "source": "auto"},
        ),
        ComplexPlanStep(
            step_id="S2",
            capability="limit_up_pool",
            tool_name="limit_up_events",
            arguments=limit_up_arguments,
        ),
        ComplexPlanStep(
            step_id="S3",
            depends_on=("S1", "S2"),
            operation="intersection",
            output_entity_set="hot_limit_up_intersection",
        ),
        ComplexPlanStep(
            step_id="S4",
            capability="first_board_rating",
            tool_name="first_board_ratings",
            depends_on=("S3",),
            argument_bindings=(
                ArgumentBinding(
                    target_argument="symbols",
                    reference=ResultReference(
                        source_step="S3",
                        entity_set="hot_limit_up_intersection",
                    ),
                ),
            ),
        ),
    ]


def resolve_dynamic_arguments(
    step: ComplexPlanStep,
    entity_sets: dict[str, list[EntityRef]],
) -> dict[str, Any]:
    """Resolve validated entity-set references without asking an LLM to guess."""

    arguments = dict(step.arguments)
    for binding in step.argument_bindings:
        reference = binding.reference
        if reference.source_step not in step.depends_on:
            raise ValueError(f"binding source is not a dependency: {reference.source_step}")
        entities = entity_sets.get(reference.entity_set)
        if entities is None:
            raise ValueError(f"missing entity set: {reference.entity_set}")
        symbols = [item["symbol"] for item in entities[: reference.max_items]]
        arguments[binding.target_argument] = symbols
    return arguments


def run_hot_limit_up_rating_graph(
    *,
    request: AgentChatRequest,
    tools: AgentToolRegistry,
    limit_up_arguments: dict[str, Any],
    capabilities: tuple[str, ...],
    context_symbol: str | None,
    answer_builder: Callable[[ToolExecution], dict[str, Any]],
) -> ComplexGraphResult:
    """Execute the bounded Phase 1 graph; no replan or retry edge exists."""

    steps = build_flagship_plan(limit_up_arguments)
    step_by_id = {step.step_id: step for step in steps}

    def trace(step: ComplexPlanStep, **payload: Any) -> AgentToolTrace:
        status = str(payload.pop("step_status", "completed"))
        return AgentToolTrace(
            name="complex_graph_step",
            input={
                "graph_step_id": step.step_id,
                "step_capability": step.capability,
                "step_tool": step.tool_name,
                "dependency_sources": list(step.depends_on),
                "step_status": status,
                **payload,
            },
            summary=f"Complex Graph {step.step_id}: {status}",
            status="error" if status == "failed" else "success",
        )

    def validate_plan(state: ComplexAgentState) -> dict[str, Any]:
        errors: list[str] = []
        known_ids = set(step_by_id)
        for step in steps:
            if any(item not in known_ids for item in step.depends_on):
                errors.append(f"{step.step_id}: unknown dependency")
            if step.tool_name:
                capability = CAPABILITY_BY_NAME.get(step.capability or "")
                allowed = {
                    item.name for item in capability.required_tools
                } if capability else set()
                if step.tool_name not in allowed or not tools.is_enabled(step.tool_name):
                    errors.append(f"{step.step_id}: tool is not capability-authorized")
        if errors:
            return {
                "failed_steps": ["validate_plan"],
                "failure_reason": "; ".join(errors),
                "completion_status": "failed",
            }
        return {"current_step": "S1"}

    def execute_sources(state: ComplexAgentState) -> dict[str, Any]:
        if state.get("failure_reason"):
            return {}
        calls = [
            {"name": step_by_id[step_id].tool_name, "arguments": step_by_id[step_id].arguments}
            for step_id in ("S1", "S2")
        ]
        execution = execute_tool_calls(
            calls, tools, request=request, context_symbol=context_symbol
        )
        traces = [
            trace(
                step_by_id[step_id],
                resolved_dynamic_args=calls[index]["arguments"],
                observation_summary=execution["tool_results"][index].summary,
            )
            for index, step_id in enumerate(("S1", "S2"))
        ]
        return {
            "facts": execution["facts"],
            "tool_results": execution["tool_results"],
            "tool_call_names": execution["tool_call_names"],
            "references": execution["references"],
            "graph_traces": traces,
            "completed_steps": ["S1", "S2"],
            "tool_call_count": len(execution["tool_call_names"]),
            "current_step": "S3",
        }

    def observe_intersection(state: ComplexAgentState) -> dict[str, Any]:
        if state.get("failure_reason"):
            return {}
        facts = dict(state["facts"])
        _add_composed_tool_facts(request.message, facts)
        payload = facts.get("hot_stock_limit_up_intersection")
        if not isinstance(payload, dict):
            return {
                "failed_steps": [*state["failed_steps"], "S3"],
                "failure_reason": "intersection sources were unavailable",
                "completion_status": "failed",
            }
        entities: list[EntityRef] = [
            {
                "symbol": str(item["symbol"]),
                "name": item.get("name"),
                "source_steps": ["S1", "S2"],
            }
            for item in payload.get("items", [])[:MAX_DYNAMIC_ENTITIES]
            if isinstance(item, dict) and item.get("symbol")
        ]
        return {
            "facts": facts,
            "entity_sets": {"hot_limit_up_intersection": entities},
            "graph_traces": [
                *state["graph_traces"],
                trace(
                    step_by_id["S3"],
                    resolved_dynamic_args={},
                    observation_summary=f"intersection produced {len(entities)} symbols",
                ),
            ],
            "completed_steps": [*state["completed_steps"], "S3"],
            "current_step": "S4",
        }

    def execute_rating(state: ComplexAgentState) -> dict[str, Any]:
        if state.get("failure_reason"):
            return {}
        step = step_by_id["S4"]
        try:
            arguments = resolve_dynamic_arguments(step, state["entity_sets"])
        except ValueError as error:
            return {
                "failed_steps": [*state["failed_steps"], "S4"],
                "failure_reason": str(error),
                "completion_status": "failed",
            }
        symbols = arguments.get("symbols") or []
        if not symbols:
            return {
                "failed_steps": [*state["failed_steps"], "S4"],
                "failure_reason": "dynamic symbol set is empty",
                "completion_status": "failed",
                "graph_traces": [
                    *state["graph_traces"],
                    trace(
                        step,
                        resolved_dynamic_args=arguments,
                        observation_summary="downstream call skipped for empty entity set",
                        step_status="failed",
                    ),
                ],
            }
        part = execute_tool_calls(
            [{"name": step.tool_name, "arguments": arguments}],
            tools,
            request=request,
            context_symbol=context_symbol,
        )
        facts = {**state["facts"], **part["facts"]}
        return {
            "facts": facts,
            "tool_results": [*state["tool_results"], *part["tool_results"]],
            "tool_call_names": [*state["tool_call_names"], *part["tool_call_names"]],
            "references": list(dict.fromkeys([*state["references"], *part["references"]])),
            "graph_traces": [
                *state["graph_traces"],
                trace(
                    step,
                    resolved_dynamic_args=arguments,
                    observation_summary=part["tool_results"][0].summary,
                ),
            ],
            "completed_steps": [*state["completed_steps"], "S4"],
            "tool_call_count": state["tool_call_count"] + len(part["tool_call_names"]),
        }

    def apply_policy(state: ComplexAgentState) -> dict[str, Any]:
        if state.get("failure_reason"):
            return {}
        execution: ToolExecution = {
            "facts": state["facts"],
            "tool_results": state["tool_results"],
            "tool_call_names": state["tool_call_names"],
            "references": state["references"],
        }
        AgentToolPolicyEngine(tools).reconcile(
            request=request,
            execution=execution,
            context_symbol=context_symbol,
            capabilities=capabilities,
        )
        return {**execution, "tool_call_count": len(execution["tool_call_names"])}

    def complete(state: ComplexAgentState) -> dict[str, Any]:
        completed = "S4" in state["completed_steps"] and not state["failed_steps"]
        return {"completion_status": "complete" if completed else "failed"}

    def answer(state: ComplexAgentState) -> dict[str, Any]:
        execution: ToolExecution = {
            "facts": state["facts"],
            "tool_results": state["tool_results"],
            "tool_call_names": state["tool_call_names"],
            "references": state["references"],
        }
        answer_meta = answer_builder(execution)
        return {
            "final_answer": str(answer_meta["answer"]),
            "answer_meta": answer_meta,
        }

    builder = StateGraph(ComplexAgentState)
    builder.add_node("validate_plan", validate_plan)
    builder.add_node("execute_sources", execute_sources)
    builder.add_node("observe_intersection", observe_intersection)
    builder.add_node("execute_rating", execute_rating)
    builder.add_node("apply_policy", apply_policy)
    builder.add_node("complete", complete)
    builder.add_node("answer", answer)
    builder.add_edge(START, "validate_plan")
    builder.add_edge("validate_plan", "execute_sources")
    builder.add_edge("execute_sources", "observe_intersection")
    builder.add_edge("observe_intersection", "execute_rating")
    builder.add_edge("execute_rating", "apply_policy")
    builder.add_edge("apply_policy", "complete")
    builder.add_edge("complete", "answer")
    builder.add_edge("answer", END)

    initial: ComplexAgentState = {
        "user_query": request.message,
        "messages": [{"role": "user", "content": request.message}],
        "plan_steps": [step.model_dump(mode="json") for step in steps],
        "current_step": None,
        "entity_sets": {},
        "facts": {},
        "tool_results": [],
        "graph_traces": [],
        "completed_steps": [],
        "failed_steps": [],
        "tool_call_names": [],
        "references": [],
        "tool_call_count": 0,
        "llm_call_count": 1,
        "final_answer": "",
        "answer_meta": {},
        "completion_status": "pending",
        "failure_reason": None,
    }
    state = builder.compile().invoke(initial)
    execution: ToolExecution = {
        "facts": state["facts"],
        "tool_results": state["tool_results"],
        "tool_call_names": state["tool_call_names"],
        "references": state["references"],
    }
    return ComplexGraphResult(
        execution=execution,
        graph_traces=state["graph_traces"],
        final_answer=state["final_answer"],
        answer_meta=state["answer_meta"],
        completion_status=state["completion_status"],
        failed_steps=state["failed_steps"],
        plan_steps=state["plan_steps"],
    )
