"""Deterministic composition and completeness checks for chat tool answers."""

import re
from typing import Any

from app.agents.chat_templates import (
    _looks_like_high_score_promotion_question,
)
from app.agents.query_contract import (
    extract_result_limit,
    looks_like_exhaustive_request,
)
from app.agents.tool_policy import (
    looks_like_daily_board_promotion_question as _looks_like_daily_board_promotion_question,
    looks_like_first_board_position_question as _looks_like_first_board_position_question,
)


def _add_composed_tool_facts(message: str, facts: dict[str, Any]) -> None:
    """Add deterministic joins needed by questions that combine multiple tools."""

    intersection = _build_hot_stock_event_intersection(message, facts)
    if intersection is not None:
        facts["hot_stock_limit_up_intersection"] = intersection


def _build_hot_stock_event_intersection(
    message: str,
    facts: dict[str, Any],
) -> dict[str, Any] | None:
    """Join a popularity ranking with a filtered limit-up pool by stock symbol."""

    if not _looks_like_hot_stock_event_intersection_question(message):
        return None
    hot_stocks = facts.get("hot_stock_ranking")
    limit_up_events = facts.get("limit_up_events")
    if not isinstance(hot_stocks, dict) or not isinstance(limit_up_events, dict):
        return None

    events = {
        str(item.get("symbol")): item
        for item in limit_up_events.get("events", [])
        if isinstance(item, dict) and item.get("symbol")
    }
    joined: list[dict[str, Any]] = []
    for hot_stock in hot_stocks.get("items", []):
        if not isinstance(hot_stock, dict):
            continue
        symbol = str(hot_stock.get("symbol") or "")
        event = events.get(symbol)
        if event is None:
            continue
        joined.append(
            {
                "rank": hot_stock.get("rank"),
                "symbol": symbol,
                "name": hot_stock.get("name") or event.get("name"),
                "industry": event.get("industry"),
                "board_height": event.get("board_height"),
                "board_height_text": event.get("board_height_text"),
                "first_limit_time": event.get("first_limit_time"),
                "break_count": event.get("break_count"),
                "closed_limit": event.get("closed_limit"),
            }
        )
    joined.sort(
        key=lambda item: (
            item.get("rank") if isinstance(item.get("rank"), int) else 10_000,
            item.get("symbol") or "",
        )
    )
    return {
        "source": hot_stocks.get("source"),
        "source_label": hot_stocks.get("source_label"),
        "captured_at_beijing": hot_stocks.get("captured_at_beijing"),
        "data_fresh": hot_stocks.get("data_fresh"),
        "requested_count": hot_stocks.get("requested_count"),
        "hot_stock_count": len(hot_stocks.get("items", [])),
        "trade_date": limit_up_events.get("trade_date"),
        "event_label": _hot_stock_event_label(message),
        "event_count": len(events),
        "matched_count": len(joined),
        "items": joined,
    }


def _looks_like_hot_stock_event_intersection_question(message: str) -> bool:
    """Recognize questions that filter a popularity ranking by a limit-up cohort."""

    compact = re.sub(r"\s+", "", message).lower()
    has_hot_stock_scope = any(
        term in compact
        for term in ("热股榜", "热股排行", "热门股", "人气榜", "人气排名")
    )
    has_event_filter = any(
        term in compact
        for term in ("首板", "连板", "涨停", "炸板")
    )
    asks_for_subset = any(
        term in compact
        for term in ("哪些", "有谁", "有哪", "筛出", "找出", "属于", "同时", "交集")
    )
    return has_hot_stock_scope and has_event_filter and asks_for_subset


def _hot_stock_event_label(message: str) -> str:
    """Return a user-facing label for the event-side filter."""

    if "首板" in message:
        return "首板票"
    if "连板" in message:
        return "连板票"
    if "炸板" in message:
        return "炸板票"
    return "涨停票"


def _looks_like_exhaustive_list_request(message: str) -> bool:
    """Return whether the user explicitly asks for the complete result set."""

    return looks_like_exhaustive_request(message)


def _requires_exhaustive_event_answer(
    message: str,
    facts: dict[str, Any],
) -> bool:
    """Enable full-list output only for a multi-item limit-up event result."""

    if "hot_stock_limit_up_intersection" in facts:
        return False
    events = _limit_up_items_from_facts(facts)
    return (
        _looks_like_exhaustive_list_request(message)
        and len(events) > 8
    )


def _contains_every_event_symbol(answer: str, facts: dict[str, Any]) -> bool:
    """Verify an exhaustive LLM answer did not truncate or omit event rows."""

    events = _limit_up_items_from_facts(facts)
    symbols = [
        str(item.get("symbol"))
        for item in events
        if isinstance(item, dict) and item.get("symbol")
    ]
    return bool(symbols) and all(symbol in answer for symbol in symbols)


def _requires_complete_hot_stock_answer(
    message: str,
    facts: dict[str, Any],
) -> bool:
    """Return whether a requested Top-N ranking is fully available for rendering."""

    requested = extract_result_limit(message)
    payload = facts.get("hot_stock_ranking")
    return (
        "hot_stock_limit_up_intersection" not in facts
        and requested is not None
        and requested > 20
        and isinstance(payload, dict)
        and bool(payload.get("complete"))
        and len(payload.get("items") or []) >= requested
    )


def _contains_every_hot_stock_symbol(answer: str, facts: dict[str, Any]) -> bool:
    """Verify that a full Top-N answer preserves every ranked stock."""

    payload = facts.get("hot_stock_ranking")
    if not isinstance(payload, dict):
        return False
    requested = int(payload.get("requested_count") or 0)
    symbols = [
        str(item.get("symbol"))
        for item in (payload.get("items") or [])[:requested]
        if isinstance(item, dict) and item.get("symbol")
    ]
    return bool(symbols) and all(symbol in answer for symbol in symbols)


def _requires_hot_stock_event_intersection_answer(facts: dict[str, Any]) -> bool:
    """Return whether two executed stock lists were composed as a set intersection."""

    return isinstance(facts.get("hot_stock_limit_up_intersection"), dict)


def _contains_exact_hot_stock_event_intersection(
    answer: str,
    facts: dict[str, Any],
) -> bool:
    """Reject answers that omit joined rows or leak non-matching popularity rows."""

    intersection = facts.get("hot_stock_limit_up_intersection")
    hot_stocks = facts.get("hot_stock_ranking")
    if not isinstance(intersection, dict) or not isinstance(hot_stocks, dict):
        return False
    matched_symbols = {
        str(item.get("symbol"))
        for item in intersection.get("items", [])
        if isinstance(item, dict) and item.get("symbol")
    }
    if not matched_symbols:
        return any(term in answer for term in ("没有交集", "暂无", "0只", "0 只"))
    non_matching_symbols = {
        str(item.get("symbol"))
        for item in hot_stocks.get("items", [])
        if isinstance(item, dict)
        and item.get("symbol")
        and str(item.get("symbol")) not in matched_symbols
    }
    return (
        all(symbol in answer for symbol in matched_symbols)
        and not any(symbol in answer for symbol in non_matching_symbols)
    )


def _requires_complete_position_answer(
    message: str,
    facts: dict[str, Any],
) -> bool:
    """Return whether the response must preserve the full position grouping."""

    ratings = facts.get("first_board_ratings")
    classification = (
        ratings.get("position_classification") if isinstance(ratings, dict) else None
    )
    return (
        _looks_like_first_board_position_question(message)
        and isinstance(classification, dict)
        and bool(classification.get("groups") or classification.get("missing_candidates"))
    )


def _contains_complete_position_groups(answer: str, facts: dict[str, Any]) -> bool:
    """Check that an LLM position answer retains every group and candidate."""

    ratings = facts.get("first_board_ratings")
    if not isinstance(ratings, dict):
        return False
    classification = ratings.get("position_classification")
    if not isinstance(classification, dict):
        return False
    groups = classification.get("groups") or []
    labels = [
        str(group.get("label"))
        for group in groups
        if isinstance(group, dict) and group.get("label")
    ]
    symbols = [
        str(candidate.get("symbol"))
        for group in groups
        if isinstance(group, dict)
        for candidate in group.get("candidates") or []
        if isinstance(candidate, dict) and candidate.get("symbol")
    ]
    symbols.extend(
        str(candidate.get("symbol"))
        for candidate in classification.get("missing_candidates") or []
        if isinstance(candidate, dict) and candidate.get("symbol")
    )
    return bool(labels or symbols) and all(label in answer for label in labels) and all(
        symbol in answer for symbol in symbols
    )


def _requires_daily_promotion_answer(
    message: str,
    facts: dict[str, Any],
) -> bool:
    """Return whether the answer must preserve daily promotion observations."""

    payload = facts.get("daily_board_promotion")
    return (
        _looks_like_daily_board_promotion_question(message)
        and isinstance(payload, dict)
        and bool(payload.get("items"))
    )


def _contains_daily_promotion_facts(
    answer: str,
    facts: dict[str, Any],
    message: str,
) -> bool:
    """Check that an LLM answer includes every requested day and cohort size."""

    payload = facts.get("daily_board_promotion")
    if not isinstance(payload, dict):
        return False
    items = payload.get("items") or []
    required_tokens: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        required_tokens.extend(
            (
                str(item.get("trade_date")),
                f"{item.get('promoted_count')}/{item.get('sample_size')}",
                (
                    f"{item.get('first_board_promoted_count')}"
                    f"/{item.get('first_board_sample_size')}"
                ),
            )
        )
    if _asks_for_promoted_stock_details(message):
        latest = items[-1] if items else {}
        required_tokens.extend(
            str(stock.get("symbol"))
            for stock in latest.get("promoted_stocks") or []
            if isinstance(stock, dict)
        )
    return bool(required_tokens) and all(token in answer for token in required_tokens)


def _asks_for_promoted_stock_details(message: str) -> bool:
    """Return whether the user asks for the concrete promoted-stock list."""

    return "晋级" in message and any(
        term in message
        for term in ("哪些票", "哪些股票", "哪些个股", "股票有哪些", "票有哪些")
    )


def _extract_high_score_review_days(message: str) -> int:
    """Return the requested number of mature Top10 review dates."""

    match = re.search(r"(?:最近|近|过去)?\s*(\d{1,2})\s*(?:个?交易日|天|日)", message)
    return max(1, min(int(match.group(1)), 20)) if match else 5


def _requires_high_score_promotion_answer(
    message: str,
    facts: dict[str, Any],
) -> bool:
    """Return whether a high-score promotion comparison must be preserved."""

    payload = facts.get("review_high_score_picks")
    return (
        _looks_like_high_score_promotion_question(message)
        and isinstance(payload, dict)
        and int(payload.get("promotion_ready_date_count") or 0) > 0
    )


def _contains_high_score_promotion_facts(
    answer: str,
    facts: dict[str, Any],
) -> bool:
    """Check that the answer reports score-ranked and market promotion samples."""

    payload = facts.get("review_high_score_picks")
    if not isinstance(payload, dict):
        return False
    required_tokens = (
        f"{payload.get('top_pick_promoted_count')}/{payload.get('top_pick_promotion_sample_size')}",
        f"{payload.get('market_promoted_count')}/{payload.get('market_promotion_sample_size')}",
    )
    return all(token in answer for token in required_tokens)


def _limit_up_items_from_facts(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Return limit-up rows from either local events or Tonghuashun pool facts."""

    local_payload = facts.get("limit_up_events")
    if isinstance(local_payload, dict) and isinstance(local_payload.get("events"), list):
        return [item for item in local_payload["events"] if isinstance(item, dict)]
    remote_payload = facts.get("remote_limit_up_pool")
    if isinstance(remote_payload, dict) and isinstance(remote_payload.get("items"), list):
        return [item for item in remote_payload["items"] if isinstance(item, dict)]
    return []

