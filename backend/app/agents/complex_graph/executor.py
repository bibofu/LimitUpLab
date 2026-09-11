"""Policy-gated execution of one bounded graph step."""

from __future__ import annotations

from typing import Any

from app.agents.tool_execution import execute_tool_calls
from app.agents.tool_policy import AgentToolPolicyEngine, ToolExecution
from app.agents.tools import AgentToolRegistry
from app.models import AgentChatRequest, AgentToolTrace

from .models import ComplexPlanStep, EntityRef


def _resolve_argument_sets(step: ComplexPlanStep, entity_sets: dict[str, list[EntityRef]]) -> list[dict[str, Any]]:
    """Resolve bindings after observation and optionally fan out scalar calls."""

    calls = [dict(step.arguments)]
    for binding in step.argument_bindings:
        reference = binding.reference
        if reference.source_step not in step.depends_on:
            raise ValueError(f"binding source is not a dependency: {reference.source_step}")
        entities = entity_sets.get(reference.entity_set)
        if entities is None:
            raise ValueError(f"missing entity set: {reference.entity_set}")
        symbols = [item["symbol"] for item in entities[: reference.max_items]]
        if binding.fan_out:
            calls = [{**arguments, binding.target_argument: symbol} for arguments in calls for symbol in symbols]
        else:
            for arguments in calls:
                arguments[binding.target_argument] = (
                    symbols if binding.batch or binding.target_argument == "symbols" else (symbols[0] if symbols else None)
                )
    return calls


def resolve_dynamic_arguments(step: ComplexPlanStep, entity_sets: dict[str, list[EntityRef]]) -> dict[str, Any]:
    """Backward-compatible single-call resolver used by Phase 1 callers."""

    calls = _resolve_argument_sets(step, entity_sets)
    if len(calls) != 1:
        raise ValueError("fan-out binding resolves to multiple argument sets")
    return calls[0]


def execute_step(
    step: ComplexPlanStep,
    *,
    request: AgentChatRequest,
    tools: AgentToolRegistry,
    context_symbol: str | None,
    entity_sets: dict[str, list[EntityRef]],
    remaining_tool_calls: int,
) -> tuple[ToolExecution, list[dict[str, Any]], list[str]]:
    """Resolve, validate with Tool Policy, budget, then execute the ready step."""

    empty: ToolExecution = {"facts": {}, "tool_results": [], "tool_call_names": [], "references": []}
    if not step.tool_name or not step.capability:
        return empty, [], ["tool step is missing capability or tool"]
    try:
        argument_sets = _resolve_argument_sets(step, entity_sets)
    except ValueError as error:
        return empty, [], [str(error)]
    calls = [{"name": step.tool_name, "arguments": arguments} for arguments in argument_sets]
    if not calls:
        return empty, [], ["resolved dependency produced no executable calls"]
    if len(calls) > remaining_tool_calls:
        return empty, calls, [f"tool budget exceeded: need {len(calls)}, remaining {remaining_tool_calls}"]
    errors = AgentToolPolicyEngine(tools).validate_calls(calls, capability=step.capability)
    if errors:
        return empty, calls, errors
    return execute_tool_calls(calls, tools, request=request, context_symbol=context_symbol), calls, []


def graph_step_trace(
    *,
    graph_run_id: str,
    step: ComplexPlanStep,
    step_index: int,
    calls: list[dict[str, Any]],
    observations: list[AgentToolTrace],
    status: str,
    completion_check: dict[str, Any] | None = None,
    replan_payload: dict[str, Any] | None = None,
    tool_call_count: int = 0,
    llm_call_count: int = 0,
    latency_ms: int = 0,
) -> AgentToolTrace:
    return AgentToolTrace(
        name="complex_graph_step",
        input={
            "graph_run_id": graph_run_id,
            "graph_step_id": step.step_id,
            "step_index": step_index,
            "step_type": step.step_type,
            "capability": step.capability,
            "tool": step.tool_name,
            "tool_args": step.arguments,
            "dependency_sources": list(step.depends_on),
            "resolved_dynamic_args": [call["arguments"] for call in calls],
            "step_status": status,
            "completion_check": completion_check,
            "replan_triggered": bool(replan_payload),
            "replan_reason": (replan_payload or {}).get("reason"),
            "replan_index": step.replan_index,
            "replan_output": replan_payload,
            "tool_call_count": tool_call_count,
            "llm_call_count": llm_call_count,
            "token_usage": None,
            "latency_ms": latency_ms,
        },
        output={"observation": [item.model_dump(mode="json") for item in observations]},
        summary=f"Complex Graph {step.step_id}: {status}",
        status="error" if status == "failed" else "success",
    )
