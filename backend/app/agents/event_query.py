"""Deterministic filtering, ordering and grouping of already selected events."""

import re
from typing import Any

from app.agents.query_contract import MARKET_SEGMENT_PREFIXES
from app.models import LimitUpEvent


def filter_limit_up_events(
    events: list[LimitUpEvent],
    *,
    board_height: int | None,
    min_board_height: int | None,
    market: str | None,
    event_status: str,
    query: str | None,
    highest_only: bool,
) -> list[LimitUpEvent]:
    """Apply normalized conditions before selecting the highest matching board."""

    if board_height is not None:
        events = [event for event in events if event.board_height == board_height]
    if min_board_height is not None:
        events = [event for event in events if event.board_height >= min_board_height]
    if market is not None:
        prefixes = MARKET_SEGMENT_PREFIXES[market]
        events = [event for event in events if event.symbol.startswith(prefixes)]
    if event_status == "failed":
        events = [event for event in events if not event.closed_limit]
    elif event_status == "broken_intraday":
        events = [event for event in events if event.break_count > 0]
    elif event_status == "closed":
        events = [event for event in events if event.closed_limit]
    if query:
        normalized_query = query.strip().lower()
        events = [
            event
            for event in events
            if normalized_query in event.symbol.lower()
            or normalized_query in event.name.lower()
            or normalized_query in event.industry.lower()
            or normalized_query in event.concept.lower()
        ]
    if highest_only and events:
        max_height = max(event.board_height for event in events)
        events = [event for event in events if event.board_height == max_height]
    return events


def sort_limit_up_events(
    events: list[LimitUpEvent],
    *,
    sort_by: str,
    sort_order: str,
) -> list[LimitUpEvent]:
    """Sort event rows deterministically while preserving a symbol tie-breaker."""

    key_getters = {
        "board_height": lambda event: event.board_height,
        "first_limit_time": lambda event: event.first_limit_time,
        "amount": lambda event: event.amount,
        "turnover_rate": lambda event: event.turnover_rate,
        "break_count": lambda event: event.break_count,
    }
    key_getter = key_getters.get(sort_by, key_getters["board_height"])
    ordered = sorted(events, key=lambda event: event.symbol)
    return sorted(ordered, key=key_getter, reverse=sort_order == "desc")


def group_limit_up_events(
    events: list[LimitUpEvent],
    *,
    group_by: str | None,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    """Count every matching event before limiting the displayed sector groups."""

    if group_by not in {"industry", "concept"}:
        return [], 0
    grouped: dict[str, list[LimitUpEvent]] = {}
    unclassified_event_count = 0
    for event in events:
        raw_label = str(getattr(event, group_by) or "").strip()
        labels = (
            [raw_label]
            if group_by == "industry" and raw_label
            else [
                item.strip()
                for item in re.split(r"[+＋、,，;/；|]", raw_label)
                if item.strip()
            ]
        )
        if not labels:
            unclassified_event_count += 1
            continue
        for label in dict.fromkeys(labels):
            grouped.setdefault(label, []).append(event)
    sector_summary: list[dict[str, Any]] = []
    for label, group_events in grouped.items():
        stocks_by_symbol = {event.symbol: event.name for event in group_events}
        sector_summary.append(
            {
                "sector_name": label,
                "unique_stock_count": len(stocks_by_symbol),
                "limit_up_event_count": len(group_events),
                "trade_day_count": len({event.trade_date for event in group_events}),
                "stocks": [
                    {"symbol": symbol, "name": name}
                    for symbol, name in sorted(stocks_by_symbol.items())
                ],
            }
        )
    sector_summary.sort(
        key=lambda item: (
            -item["unique_stock_count"],
            -item["limit_up_event_count"],
            item["sector_name"],
        )
    )
    return sector_summary[: max(1, min(limit, 100))], unclassified_event_count
