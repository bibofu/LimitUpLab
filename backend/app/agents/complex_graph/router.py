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
    """Route only the supported observation-dependent flagship to LangGraph."""

    compact = "".join(message.lower().split())
    has_hot = any(term in compact for term in ("热股", "人气榜", "人气排名"))
    has_limit_up = "涨停" in compact
    has_intersection = any(term in compact for term in ("交集", "同时", "里面"))
    asks_rating = any(term in compact for term in ("首板评分", "首板评级", "评分", "评级"))
    if has_hot and has_limit_up and has_intersection and asks_rating:
        return ComplexityDecision(
            route="complex",
            reason_codes=("dynamic_entity_set", "dependent_tool_arguments"),
            supported_scenario="hot_limit_up_rating_intersection_v1",
        )
    return ComplexityDecision(route="fast", reason_codes=("fast_path_default",))
