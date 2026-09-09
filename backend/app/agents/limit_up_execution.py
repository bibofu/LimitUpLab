"""Shared event-query execution and evidence projection, independent of policy."""
from typing import Any

from app.agents.query_contract import LimitUpQueryContract
from app.agents.tools import AgentToolRegistry, ToolResult
from app.models import LimitUpEvent


def execute_limit_up_query(
    tools: AgentToolRegistry,
    contract: LimitUpQueryContract,
) -> tuple[ToolResult, dict[str, Any]]:
    """Execute the canonical argument set and retain complete event evidence."""
    arguments = contract.to_tool_arguments()
    # Planner arguments use JSON dates; the Python registry accepts date objects.
    arguments["trade_date"] = contract.trade_date
    result = tools.limit_up_events(**arguments)
    query_contract = contract.to_dict()
    result.input["query_contract"] = query_contract
    result.trace_output["query_contract"] = query_contract
    payload = result.trace_output
    facts = {
        "trade_date": payload.get("trade_date"),
        "market": payload.get("market"),
        "market_label": payload.get("market_label"),
        "matched_count": payload.get("matched_count"),
        "unique_stock_count": payload.get("unique_stock_count"),
        "returned_count": payload.get("returned_count"),
        "start_trade_date": payload.get("start_trade_date"),
        "recent_trade_days": payload.get("recent_trade_days"),
        "selected_trade_day_count": payload.get("selected_trade_day_count"),
        "group_by": payload.get("group_by"),
        "sector_summary": payload.get("sector_summary", []),
        "unclassified_event_count": payload.get("unclassified_event_count", 0),
        "query_contract": query_contract,
        "events": [_event_fact(event) for event in result.output],
    }
    return result, facts


def _event_fact(event: LimitUpEvent) -> dict[str, Any]:
    """Serialize one limit-up event into compact Agent facts."""

    return {
        "symbol": event.symbol,
        "name": event.name,
        "trade_date": event.trade_date.isoformat(),
        "board_height": event.board_height,
        "industry": event.industry,
        "concept": event.concept,
        "first_limit_time": event.first_limit_time.strftime("%H:%M"),
        "last_limit_time": event.last_limit_time.strftime("%H:%M"),
        "break_count": event.break_count,
        "closed_limit": event.closed_limit,
        "amount": event.amount,
        "turnover_rate": event.turnover_rate,
    }
