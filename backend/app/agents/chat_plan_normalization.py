"""Validation and normalization for LLM-produced chat plans."""

import json
import re
from typing import Any

from app.agents.query_contract import (
    extract_market_event_type,
    looks_like_market_event_query,
)
from app.agents.tool_policy import (
    extract_kline_days as _extract_kline_days,
    extract_promotion_days as _extract_promotion_days,
    extract_stock_news_days as _extract_stock_news_days,
    extract_trade_date as _extract_trade_date,
    looks_like_broad_sector_ranking_question as _looks_like_broad_sector_ranking_question,
    looks_like_daily_board_promotion_question as _looks_like_daily_board_promotion_question,
    looks_like_first_board_position_question as _looks_like_first_board_position_question,
    looks_like_stock_kline_question as _looks_like_stock_kline_question,
    looks_like_stock_news_question as _looks_like_stock_news_question,
)
from app.models import AgentChatRequest


def _parse_json_object(content: str) -> dict[str, Any]:
    """Parse a JSON object from an LLM response."""

    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("LLM planner did not return a JSON object")
    return parsed


def _normalize_tool_calls(raw_calls: object) -> list[dict[str, Any]]:
    """Normalize planner tool calls into a predictable list."""

    if not isinstance(raw_calls, list):
        return []
    calls: list[dict[str, Any]] = []
    for raw in raw_calls:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        arguments = raw.get("arguments") or {}
        if name and isinstance(arguments, dict):
            calls.append({"name": name, "arguments": arguments})
    return calls[:6]


def _normalize_market_event_plan(
    request: AgentChatRequest,
    raw_capabilities: list[object],
    tool_calls: list[dict[str, Any]],
) -> tuple[list[object], list[dict[str, Any]]]:
    """Repair explicit market-event semantics before capability tool injection."""

    requested_event_type = extract_market_event_type(request.message)
    if requested_event_type != "limit_down" or not looks_like_market_event_query(
        request.message
    ):
        return raw_capabilities, tool_calls

    capabilities = [
        item
        for item in raw_capabilities
        if (
            item.get("name") if isinstance(item, dict) else item
        ) != "limit_up_pool"
    ]
    if "market_events" not in capabilities:
        capabilities.append("market_events")
    normalized_calls = [
        call for call in tool_calls if call.get("name") != "limit_up_events"
    ]
    for call in normalized_calls:
        if call.get("name") == "market_event_pool":
            call["arguments"] = {
                **dict(call.get("arguments") or {}),
                "event_type": requested_event_type,
            }
            break
    else:
        normalized_calls.insert(
            0,
            {
                "name": "market_event_pool",
                "arguments": {"event_type": requested_event_type},
            },
        )
    return capabilities, normalized_calls[:6]


def _normalize_broad_sector_plan(
    request: AgentChatRequest,
    raw_capabilities: list[object],
    tool_calls: list[dict[str, Any]],
) -> tuple[list[object], list[dict[str, Any]]]:
    """Enforce whole-market ranking semantics for broad sector questions."""

    if not _looks_like_broad_sector_ranking_question(request.message):
        return raw_capabilities, tool_calls

    broad_capabilities = {"market_environment", "market_index_trend", "popularity"}
    capabilities = [
        item
        for item in raw_capabilities
        if (item.get("name") if isinstance(item, dict) else item)
        not in broad_capabilities
    ]
    if not any(
        (item.get("name") if isinstance(item, dict) else item)
        == "sector_performance"
        for item in capabilities
    ):
        capabilities.append("sector_performance")

    unrelated_tools = {"market_summary", "market_index_trend", "hot_stock_ranking"}
    normalized_calls = [
        call
        for call in tool_calls
        if call.get("name") not in unrelated_tools | {"sector_performance"}
    ]
    normalized_calls.insert(
        0,
        {"name": "sector_performance", "arguments": {"sector": None}},
    )
    return capabilities, normalized_calls[:6]


def _normalize_explicit_stock_evidence_plan(
    request: AgentChatRequest,
    raw_capabilities: list[object],
    tool_calls: list[dict[str, Any]],
) -> tuple[list[object], list[dict[str, Any]]]:
    """Preserve explicitly requested K-line and stock-news evidence."""

    asks_kline = _looks_like_stock_kline_question(request.message)
    asks_news = _looks_like_stock_news_question(request.message)
    # This normalizer exists to disambiguate stock-scoped news and its optional
    # K-line companion. A generic "哪些板块股票走势好" must remain the sector
    # constituent-ranking capability rather than being rewritten as stock_trend.
    if not asks_news:
        return raw_capabilities, tool_calls

    capabilities = list(raw_capabilities)
    capability_names = {
        item.get("name") if isinstance(item, dict) else item
        for item in capabilities
    }
    if asks_kline and "stock_trend" not in capability_names:
        capabilities.append("stock_trend")
    if asks_news and "stock_news" not in capability_names:
        capabilities.append("stock_news")

    # A broad stock_activity call must not replace granular evidence that the
    # user explicitly named.
    if asks_kline and asks_news:
        capabilities = [
            item
            for item in capabilities
            if (item.get("name") if isinstance(item, dict) else item)
            != "stock_activity"
        ]
        tool_calls = [
            call for call in tool_calls if call.get("name") != "stock_activity"
        ]
    return capabilities, tool_calls


def _normalize_explicit_stock_tool_calls(
    request: AgentChatRequest,
    tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Bind explicit stock symbols/windows and fan out multi-stock calls."""

    symbols = list(dict.fromkeys(re.findall(r"(?<!\d)\d{6}(?!\d)", request.message)))
    if not symbols:
        return tool_calls

    normalized: list[dict[str, Any]] = []
    for call in tool_calls:
        name = call.get("name")
        if name not in {"stock_kline", "stock_news"}:
            normalized.append(call)
            continue
        base_arguments = dict(call.get("arguments") or {})
        days = (
            _extract_kline_days(request.message)
            if name == "stock_kline"
            else _extract_stock_news_days(request.message)
        )
        for symbol in symbols:
            normalized.append(
                {
                    "name": name,
                    "arguments": {
                        **base_arguments,
                        "symbol": symbol,
                        "days": days,
                    },
                }
            )
    return normalized[:6]


def _normalize_first_board_position_tool_calls(
    request: AgentChatRequest,
    tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Route aggregate K-line position questions to the ratings fact source."""

    if not _looks_like_first_board_position_question(request.message):
        return tool_calls
    normalized = [
        call for call in tool_calls if call.get("name") != "limit_up_events"
    ]
    if any(call.get("name") == "first_board_ratings" for call in normalized):
        return normalized
    trade_date = request.trade_date or _extract_trade_date(request.message)
    normalized.insert(
        0,
        {
            "name": "first_board_ratings",
            "arguments": {
                "trade_date": trade_date.isoformat() if trade_date else None,
            },
        },
    )
    return normalized[:6]


def _normalize_daily_board_promotion_tool_calls(
    request: AgentChatRequest,
    tool_calls: list[dict[str, Any]],
    *,
    default_days: int = 5,
) -> list[dict[str, Any]]:
    """Route daily promotion questions to the adjacent-close cohort tool."""

    if not _looks_like_daily_board_promotion_question(request.message):
        return tool_calls
    requested_days = _extract_promotion_days(
        request.message,
        default_days=default_days,
    )
    normalized = [
        call for call in tool_calls if call.get("name") != "limit_up_events"
    ]
    for call in normalized:
        if call.get("name") != "daily_board_promotion":
            continue
        arguments = dict(call.get("arguments") or {})
        arguments["days"] = requested_days
        call["arguments"] = arguments
        return normalized
    end_date = request.trade_date or _extract_trade_date(request.message)
    normalized.insert(
        0,
        {
            "name": "daily_board_promotion",
            "arguments": {
                "days": requested_days,
                "end_date": end_date.isoformat() if end_date else None,
            },
        },
    )
    return normalized[:6]
