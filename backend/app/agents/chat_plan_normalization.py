"""Validation and normalization for LLM-produced chat plans."""

import json
import re
from typing import Any

from app.agents.query_contract import (
    extract_market_event_type,
    looks_like_market_event_query,
)
from app.agents.tool_policy import (
    extract_promotion_days as _extract_promotion_days,
    extract_trade_date as _extract_trade_date,
    looks_like_broad_sector_ranking_question as _looks_like_broad_sector_ranking_question,
    looks_like_daily_board_promotion_question as _looks_like_daily_board_promotion_question,
    looks_like_first_board_position_question as _looks_like_first_board_position_question,
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
) -> list[dict[str, Any]]:
    """Route daily promotion questions to the adjacent-close cohort tool."""

    if not _looks_like_daily_board_promotion_question(request.message):
        return tool_calls
    normalized = [
        call for call in tool_calls if call.get("name") != "limit_up_events"
    ]
    if any(call.get("name") == "daily_board_promotion" for call in normalized):
        return normalized
    end_date = request.trade_date or _extract_trade_date(request.message)
    normalized.insert(
        0,
        {
            "name": "daily_board_promotion",
            "arguments": {
                "days": _extract_promotion_days(request.message),
                "end_date": end_date.isoformat() if end_date else None,
            },
        },
    )
    return normalized[:6]

