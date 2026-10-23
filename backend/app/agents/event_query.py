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

    result = summarize_event_groups(events, group_by=group_by, limit=limit)
    return result["groups"], result["unclassified_event_count"]


def summarize_event_groups(events, *, group_by, limit):
    """A bounded group collection carries its population and ordering contract."""

    if group_by not in {"industry", "concept"}:
        return {"groups": [], "group_count": 0, "returned_group_count": 0,
                "groups_truncated": False, "unclassified_event_count": 0}
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
    selected = sector_summary[: max(1, min(limit, 100))]
    return {
        "groups": selected, "group_by": group_by,
        "group_count": len(sector_summary), "returned_group_count": len(selected),
        "groups_truncated": len(selected) < len(sector_summary),
        "unclassified_event_count": unclassified_event_count,
        "population_event_count": len(events),
        "population_unique_stock_count": len({event.symbol for event in events}),
        "sort_order": ["unique_stock_count desc", "limit_up_event_count desc", "sector_name asc"],
        "tie_policy": "Order by unique_stock_count descending, then limit_up_event_count descending, then sector_name ascending; a requested top N contains exactly N groups.",
        "classification": "one whole industry label per event" if group_by == "industry" else "distinct concept labels per event; groups can overlap",
    }


def summarize_event_states(events):
    """Count intersecting event states without assuming failed rows have a break."""
    closed = [event for event in events if event.closed_limit]
    opened = [event for event in events if event.break_count > 0]
    resealed = sum(event.closed_limit for event in opened)
    opened_unsealed = len(opened) - resealed
    unsealed = len(events) - len(closed)
    first = sum(event.board_height == 1 for event in closed)
    continued = sum(event.board_height >= 2 for event in closed)
    return {
        "population_unit": "events", "population_event_count": len(events),
        "unique_stock_count": len({event.symbol for event in events}),
        "closed_count": len(closed), "first_board_count": first,
        "continued_board_count": continued,
        "closed_unknown_board_count": len(closed) - first - continued,
        "intraday_opened_count": len(opened), "resealed_count": resealed,
        "opened_unsealed_count": opened_unsealed, "unsealed_count": unsealed,
        "unsealed_without_recorded_open_count": unsealed - opened_unsealed,
        "relations": {
            "closed_equals_first_plus_continued": len(closed) == first + continued,
            "opened_equals_resealed_plus_opened_unsealed": len(opened) == resealed + opened_unsealed,
            "all_unsealed_have_recorded_open": unsealed == opened_unsealed,
        },
        "definitions": {
            "closed_count": "closed_limit=true; includes resealed events",
            "intraday_opened_count": "break_count>0, regardless of close state",
            "resealed_count": "break_count>0 AND closed_limit=true; included in closed_count",
            "unsealed_count": "closed_limit=false; never presume break_count>0",
        },
    }
