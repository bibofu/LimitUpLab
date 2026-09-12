"""Deterministic graph compilation for the Phase 1/2 scenario allowlist."""

from __future__ import annotations

import re
from typing import Any

from app.models import AgentChatRequest

from .models import ArgumentBinding, ComplexPlanStep, ResultReference
from .router import extract_explicit_stock_target


SCENARIO_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "hot_limit_up_rating_intersection_v1": ("popularity", "limit_up_pool", "first_board_rating"),
    "top_ratings_then_kline_v2": ("first_board_rating", "stock_trend"),
    "rating_dragon_tiger_branch_v2": ("first_board_rating", "dragon_tiger"),
    "empty_news_fallback_v2": ("stock_news",),
    "rating_evidence_v2": ("first_board_rating",),
    "highest_board_risk_v2": ("limit_up_pool", "dragon_tiger"),
    "partial_stock_comparison_v2": ("stock_trend",),
    "intersection_risk_v2": ("popularity", "limit_up_pool", "first_board_rating", "stock_trend", "dragon_tiger"),
}


def scenario_capabilities(scenario: str) -> tuple[str, ...]:
    return SCENARIO_CAPABILITIES.get(scenario, ())


def _binding(source_step: str, entity_set: str, target: str, *, fan_out: bool = False) -> tuple[ArgumentBinding, ...]:
    return (ArgumentBinding(
        target_argument=target,
        reference=ResultReference(source_step=source_step, entity_set=entity_set),
        fan_out=fan_out,
    ),)


def build_initial_plan(
    scenario: str,
    request: AgentChatRequest,
    limit_up_arguments: dict[str, Any],
) -> list[ComplexPlanStep]:
    """Compile only the first observation boundary; later work belongs to replan."""

    trade_date = request.trade_date.isoformat() if request.trade_date else None
    if scenario in {"hot_limit_up_rating_intersection_v1", "intersection_risk_v2"}:
        limit_args = {
            **limit_up_arguments,
            "board_height": None, "min_board_height": None, "highest_only": False,
            "query": None, "limit": 100,
        }
        return [
            ComplexPlanStep(step_id="S1", capability="popularity", tool_name="hot_stock_ranking", arguments={"period": "day", "limit": 10, "source": "auto"}),
            ComplexPlanStep(step_id="S2", capability="limit_up_pool", tool_name="limit_up_events", arguments=limit_args),
            ComplexPlanStep(step_id="S3", step_type="operation", depends_on=("S1", "S2"), operation="intersection", output_entity_set="hot_limit_up_intersection"),
            ComplexPlanStep(step_id="S4", capability="first_board_rating", tool_name="first_board_ratings", depends_on=("S3",), argument_bindings=_binding("S3", "hot_limit_up_intersection", "symbols")),
        ]
    if scenario in {"top_ratings_then_kline_v2", "rating_dragon_tiger_branch_v2", "rating_evidence_v2"}:
        return [ComplexPlanStep(
            step_id="S1", capability="first_board_rating", tool_name="first_board_ratings",
            arguments={"trade_date": trade_date}, output_entity_set="top_ratings",
        )]
    if scenario == "empty_news_fallback_v2":
        symbol = extract_explicit_stock_target(request.message, request.symbol)
        if not symbol:
            raise ValueError("missing reliable stock target for empty-news fallback")
        return [ComplexPlanStep(step_id="S1", capability="stock_news", tool_name="stock_news", arguments={"symbol": symbol, "days": 7, "limit": 10})]
    if scenario == "highest_board_risk_v2":
        return [ComplexPlanStep(step_id="S1", capability="limit_up_pool", tool_name="limit_up_events", arguments={**limit_up_arguments, "highest_only": True, "limit": 20}, output_entity_set="highest_board")]
    if scenario == "partial_stock_comparison_v2":
        symbols = list(dict.fromkeys(re.findall(r"(?<!\d)\d{6}(?!\d)", request.message)))[:2]
        return [ComplexPlanStep(step_id=f"S{index + 1}", capability="stock_trend", tool_name="stock_kline", arguments={"symbol": symbol, "days": 20}) for index, symbol in enumerate(symbols)]
    raise ValueError(f"unsupported complex scenario: {scenario}")


def build_flagship_plan(limit_up_arguments: dict[str, Any]) -> list[ComplexPlanStep]:
    """Backward-compatible Phase 1 plan builder used by focused tests."""

    request = AgentChatRequest(session_id="phase-one-plan", message="热股涨停交集评分")
    return build_initial_plan("hot_limit_up_rating_intersection_v1", request, limit_up_arguments)
