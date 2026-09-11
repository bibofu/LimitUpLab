"""Deterministic completion rules for supported Phase 2 scenarios."""

from __future__ import annotations

from app.models import AgentToolTrace

from .models import CompletionCheck
from .observer import result_state


def check_completion(scenario: str, traces: list[AgentToolTrace], replan_count: int) -> CompletionCheck:
    by_name: dict[str, list[AgentToolTrace]] = {}
    for trace in traces:
        by_name.setdefault(trace.name, []).append(trace)

    def has(tool: str, states: set[str] = {"ok", "partial"}) -> bool:
        return any(result_state(item) in states for item in by_name.get(tool, []))

    if scenario == "hot_limit_up_rating_intersection_v1":
        complete = has("hot_stock_ranking") and has("limit_up_events") and has("first_board_ratings")
        return CompletionCheck(complete=complete, missing_requirements=[] if complete else ["intersection_rating"], reason="deterministic Phase 1 dependency binding")
    if scenario == "intersection_risk_v2":
        dragon = by_name.get("dragon_tiger_list", [])
        fallback_needed = bool(dragon) and any(result_state(item) in {"empty", "partial", "error"} for item in dragon)
        complete = all(has(tool) for tool in ("hot_stock_ranking", "limit_up_events", "first_board_ratings", "stock_kline")) and bool(dragon) and (not fallback_needed or has("stock_news"))
        missing = [] if complete else (["intersection_news_fallback"] if dragon else ["intersection_risk_evidence"])
        return CompletionCheck(complete=complete, missing_requirements=missing, reason="intersection members require trend and conditional risk evidence")
    if scenario == "top_ratings_then_kline_v2":
        complete = has("first_board_ratings") and has("stock_kline")
        return CompletionCheck(complete=complete, missing_requirements=[] if complete else ["top_candidate_kline"], reason="top candidates require downstream K-line evidence")
    if scenario == "rating_dragon_tiger_branch_v2":
        dragon = by_name.get("dragon_tiger_list", [])
        fallback_needed = bool(dragon) and any(result_state(item) in {"empty", "partial", "error"} for item in dragon)
        complete = has("first_board_ratings") and bool(dragon) and (not fallback_needed or (has("stock_kline") and has("stock_news")))
        missing = [] if complete else (["dragon_tiger_fallback_evidence"] if dragon else ["candidate_dragon_tiger"])
        return CompletionCheck(complete=complete, missing_requirements=missing, reason="conditional branch follows observed Dragon-Tiger outcome")
    if scenario == "empty_news_fallback_v2":
        news = by_name.get("stock_news", [])
        fallback_needed = bool(news) and any(result_state(item) in {"empty", "error"} for item in news)
        complete = bool(news) and (not fallback_needed or (has("stock_kline") and has("stock_activity")))
        return CompletionCheck(complete=complete, missing_requirements=[] if complete else ["news_fallback_evidence"], reason="empty news requires K-line and activity fallback")
    if scenario == "rating_evidence_v2":
        complete = has("first_board_ratings") and has("stock_kline") and has("first_board_critic")
        return CompletionCheck(complete=complete, missing_requirements=[] if complete else ["rating_supporting_and_risk_evidence"], reason="rating alone is insufficient explanation evidence")
    if scenario == "highest_board_risk_v2":
        dragon = by_name.get("dragon_tiger_list", [])
        fallback_needed = bool(dragon) and any(result_state(item) in {"empty", "partial", "error"} for item in dragon)
        complete = has("limit_up_events") and bool(dragon) and (not fallback_needed or has("stock_kline"))
        return CompletionCheck(complete=complete, missing_requirements=[] if complete else ["highest_board_risk_evidence"], reason="highest-board observations drive risk evidence branch")
    if scenario == "partial_stock_comparison_v2":
        successful = sum(result_state(item) in {"ok", "partial"} for item in by_name.get("stock_kline", []))
        complete = successful >= 1 and len(by_name.get("stock_kline", [])) >= 2
        return CompletionCheck(complete=complete, missing_requirements=[] if complete else ["at_least_one_comparable_candidate"], reason="single-candidate failure must not discard successful evidence")
    return CompletionCheck(complete=False, missing_requirements=["unsupported_scenario"], reason="no deterministic completion rule")
