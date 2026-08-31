"""Shared next-trading-day promotion labels for first-board research."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from app.models import FirstBoardOutcome, LimitUpEvent


def resolve_first_board_promotion_labels(
    events: list[LimitUpEvent],
    outcomes: dict[tuple[date, str], FirstBoardOutcome] | None = None,
) -> dict[tuple[date, str], bool]:
    """Build complete 1-to-2 labels from events, with cached outcomes as fallback."""

    labels = build_event_promotion_labels(events)
    for key, outcome in (outcomes or {}).items():
        if outcome.next_day_ready:
            labels.setdefault(key, outcome.promoted_to_second_board)
    return labels


def build_event_promotion_labels(
    events: list[LimitUpEvent],
) -> dict[tuple[date, str], bool]:
    """Label each closed first board after the next local trading date is known."""

    grouped: dict[date, dict[str, LimitUpEvent]] = defaultdict(dict)
    for event in events:
        if event.closed_limit:
            grouped[event.trade_date][event.symbol] = event

    labels: dict[tuple[date, str], bool] = {}
    trade_dates = sorted(grouped)
    for base_date, next_date in zip(trade_dates, trade_dates[1:]):
        calendar_gap = (next_date - base_date).days
        if calendar_gap < 1 or calendar_gap > 4:
            continue
        next_events = grouped[next_date]
        for event in grouped[base_date].values():
            if event.board_height != 1:
                continue
            next_event = next_events.get(event.symbol)
            labels[(base_date, event.symbol)] = bool(
                next_event is not None and next_event.board_height == 2
            )
    return labels
