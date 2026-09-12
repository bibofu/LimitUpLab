"""Bounded, deterministic replanner for the first Phase 2 recovery patterns."""

from __future__ import annotations

import json
from typing import Any

from .models import ArgumentBinding, ComplexPlanStep, ReplanOutput, ReplanRequest, ResultReference


def _bound(source: str, entity_set: str, target: str, *, fan_out: bool = False, batch: bool = False, max_items: int = 3) -> tuple[ArgumentBinding, ...]:
    return (ArgumentBinding(target_argument=target, reference=ResultReference(source_step=source, entity_set=entity_set, max_items=max_items), fan_out=fan_out, batch=batch),)


def replan(scenario: str, request: ReplanRequest, *, replan_index: int) -> ReplanOutput:
    """Return only allowlisted steps derived from observed missing requirements."""

    if request.remaining_replans <= 0 or request.remaining_tool_calls <= 0:
        return ReplanOutput(reason="bounded budget exhausted")
    suffix = f"R{replan_index}"
    steps: list[ComplexPlanStep] = []
    if scenario == "top_ratings_then_kline_v2":
        steps = [ComplexPlanStep(step_id=f"{suffix}-K", capability="stock_trend", tool_name="stock_kline", depends_on=("S1",), arguments={"days": 5}, argument_bindings=_bound("S1", "top_ratings", "symbol", fan_out=True), replan_index=replan_index)]
    elif scenario == "rating_dragon_tiger_branch_v2":
        if not any(item["tool"] == "dragon_tiger_list" for item in request.tool_observations):
            steps = [ComplexPlanStep(step_id=f"{suffix}-D", capability="dragon_tiger", tool_name="dragon_tiger_list", depends_on=("S1",), arguments={"board_type": "all", "limit": 30}, argument_bindings=_bound("S1", "top_ratings", "query"), replan_index=replan_index)]
        else:
            for key, capability, tool, arguments in (
                ("K", "stock_trend", "stock_kline", {"days": 5}),
                ("N", "stock_news", "stock_news", {"days": 7, "limit": 10}),
            ):
                steps.append(ComplexPlanStep(step_id=f"{suffix}-{key}", capability=capability, tool_name=tool, depends_on=("S1",), arguments=arguments, argument_bindings=_bound("S1", "top_ratings", "symbol"), replan_index=replan_index))
    elif scenario == "empty_news_fallback_v2":
        symbol = next(
            (
                item.get("tool_args", {}).get("symbol")
                for item in request.tool_observations
                if item["tool"] == "stock_news"
                and isinstance(item.get("tool_args"), dict)
                and item["tool_args"].get("symbol")
            ),
            None,
        )
        if not isinstance(symbol, str) or not symbol.strip():
            return ReplanOutput(
                reason="missing reliable stock target in stock_news observation; fallback rejected"
            )
        symbol = symbol.strip()
        steps = [
            ComplexPlanStep(step_id=f"{suffix}-K", capability="stock_trend", tool_name="stock_kline", arguments={"symbol": symbol, "days": 10}, replan_index=replan_index),
            ComplexPlanStep(step_id=f"{suffix}-A", capability="stock_activity", tool_name="stock_activity", arguments={"symbol": symbol, "days": 7, "news_limit": 8}, replan_index=replan_index),
        ]
    elif scenario == "rating_evidence_v2":
        steps = [
            ComplexPlanStep(step_id=f"{suffix}-K", capability="stock_trend", tool_name="stock_kline", depends_on=("S1",), arguments={"days": 20}, argument_bindings=_bound("S1", "top_ratings", "symbol"), replan_index=replan_index),
            ComplexPlanStep(step_id=f"{suffix}-C", capability="rating_critic", tool_name="first_board_critic", depends_on=("S1",), argument_bindings=_bound("S1", "top_ratings", "symbol"), replan_index=replan_index),
        ]
    elif scenario == "highest_board_risk_v2":
        if not any(item["tool"] == "dragon_tiger_list" for item in request.tool_observations):
            steps = [ComplexPlanStep(step_id=f"{suffix}-D", capability="dragon_tiger", tool_name="dragon_tiger_list", depends_on=("S1",), arguments={"board_type": "all", "limit": 30}, argument_bindings=_bound("S1", "highest_board", "query"), replan_index=replan_index)]
        else:
            steps = [ComplexPlanStep(step_id=f"{suffix}-K", capability="stock_trend", tool_name="stock_kline", depends_on=("S1",), arguments={"days": 5}, argument_bindings=_bound("S1", "highest_board", "symbol", fan_out=True), replan_index=replan_index)]
    elif scenario == "intersection_risk_v2":
        if not any(item["tool"] == "dragon_tiger_list" for item in request.tool_observations):
            steps = [
                ComplexPlanStep(step_id=f"{suffix}-K", capability="stock_trend", tool_name="stock_kline", depends_on=("S3",), arguments={"days": 20}, argument_bindings=_bound("S3", "hot_limit_up_intersection", "symbol", batch=True, max_items=20), replan_index=replan_index),
                ComplexPlanStep(step_id=f"{suffix}-D", capability="dragon_tiger", tool_name="dragon_tiger_list", depends_on=("S3",), arguments={"board_type": "all", "limit": 30}, argument_bindings=_bound("S3", "hot_limit_up_intersection", "query"), replan_index=replan_index),
            ]
        else:
            steps = [ComplexPlanStep(step_id=f"{suffix}-N", capability="stock_news", tool_name="stock_news", depends_on=("S3",), arguments={"days": 7, "limit": 10}, argument_bindings=_bound("S3", "hot_limit_up_intersection", "symbol"), replan_index=replan_index)]
    elif scenario == "stock_risk_branch_v2":
        symbol = next(
            (
                item.get("tool_args", {}).get("query")
                for item in request.tool_observations
                if item["tool"] == "dragon_tiger_list"
                and isinstance(item.get("tool_args"), dict)
                and item["tool_args"].get("query")
            ),
            None,
        )
        if isinstance(symbol, str) and symbol.strip():
            steps = [ComplexPlanStep(
                step_id=f"{suffix}-N", capability="stock_news", tool_name="stock_news",
                arguments={"symbol": symbol.strip(), "days": 7, "limit": 10},
                replan_index=replan_index,
            )]
    elif scenario == "partial_stock_comparison_v2":
        failed_args = next((item["tool_args"] for item in request.tool_observations if item["tool"] == "stock_kline" and item["result_state"] == "error"), None)
        if failed_args:
            steps = [ComplexPlanStep(step_id=f"{suffix}-K", capability="stock_trend", tool_name="stock_kline", arguments=dict(failed_args), replan_index=replan_index)]
    return ReplanOutput(new_steps=steps, reason=f"deterministic recovery for {', '.join(request.missing_requirements)}")


def _normalized(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalized(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_normalized(item) for item in value]
    return value


def plan_step_fingerprint(step: ComplexPlanStep | dict[str, Any]) -> str:
    """Fingerprint static arguments and deferred bindings without resolving them."""

    payload = step.model_dump(mode="json") if isinstance(step, ComplexPlanStep) else step
    material = {
        "tool_name": payload.get("tool_name"),
        "arguments": payload.get("arguments") or {},
        "argument_bindings": payload.get("argument_bindings") or [],
    }
    return json.dumps(_normalized(material), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_replan(output: ReplanOutput, *, prior_steps: list[dict], remaining_tool_calls: int, completed_step_ids: set[str] | None = None) -> list[str]:
    """Reject duplicates, empty plans, unresolved dependencies and over-budget fan-in."""

    if not output.new_steps:
        return ["empty replan"]
    prior_ids = {str(item.get("step_id")) for item in prior_steps}
    new_ids = [item.step_id for item in output.new_steps]
    errors: list[str] = []
    if len(new_ids) != len(set(new_ids)) or prior_ids & set(new_ids):
        errors.append("duplicate step")
    successful_ids = prior_ids if completed_step_ids is None else completed_step_ids
    prior_signatures = {
        plan_step_fingerprint(item)
        for item in prior_steps if item.get("tool_name") and str(item.get("step_id")) in successful_ids
    }
    if any(plan_step_fingerprint(step) in prior_signatures for step in output.new_steps):
        errors.append("duplicate successful tool step")
    known = prior_ids | set(new_ids)
    if any(dependency not in known for step in output.new_steps for dependency in step.depends_on):
        errors.append("unresolved dependency")
    if len(output.new_steps) > remaining_tool_calls:
        errors.append("replan exceeds remaining tool budget")
    return errors
