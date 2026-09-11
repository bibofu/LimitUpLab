"""Deterministic routing for the small Phase 1 complex-query allowlist."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ComplexityDecision(BaseModel):
    """Explain whether the existing fast path or Phase 1 graph should run."""

    model_config = ConfigDict(frozen=True)

    route: Literal["fast", "complex"]
    reason_codes: tuple[str, ...]
    supported_scenario: str | None = None


def route_complexity(message: str) -> ComplexityDecision:
    """Route only explicitly supported observation-dependent patterns."""

    compact = "".join(message.lower().split())
    has_hot = any(term in compact for term in ("热股", "人气榜", "人气排名"))
    has_limit_up = "涨停" in compact
    has_intersection = any(term in compact for term in ("交集", "同时", "里面"))
    asks_rating = any(term in compact for term in ("首板评分", "首板评级", "评分", "评级"))
    if has_hot and has_limit_up and "交集" in compact and "龙虎榜" in compact and "证据" in compact:
        return ComplexityDecision(
            route="complex",
            reason_codes=("multi_source_dynamic_set", "conditional_fallback", "bounded_replan_v2"),
            supported_scenario="intersection_risk_v2",
        )
    if has_hot and has_limit_up and has_intersection and asks_rating:
        return ComplexityDecision(
            route="complex",
            reason_codes=("dynamic_entity_set", "dependent_tool_arguments"),
            supported_scenario="hot_limit_up_rating_intersection_v1",
        )
    rules = (
        ("top_ratings_then_kline_v2", "评分最高" in compact and "逐只" in compact and "k线" in compact),
        ("rating_dragon_tiger_branch_v2", "评分最高" in compact and "龙虎榜" in compact and "没有龙虎榜" in compact),
        ("empty_news_fallback_v2", "新闻" in compact and "没有结果" in compact and "k线" in compact),
        ("rating_evidence_v2", "最高分首板" in compact and "证据" in compact and "补查" in compact),
        ("highest_board_risk_v2", "连板高度最高" in compact and "龙虎榜" in compact and "没有龙虎榜" in compact),
        ("partial_stock_comparison_v2", "即使一只失败" in compact and compact.count("0") >= 2 and "k线" in compact),
    )
    for scenario, matched in rules:
        if matched:
            return ComplexityDecision(
                route="complex",
                reason_codes=("observation_dependent", "bounded_replan_v2"),
                supported_scenario=scenario,
            )
    return ComplexityDecision(route="fast", reason_codes=("fast_path_default",))
