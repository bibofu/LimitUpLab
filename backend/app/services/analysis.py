"""Pure analysis helpers for limit-up events.

This module intentionally avoids database or network access. Routers and
repositories pass in already-loaded events, which keeps the calculations easy to
test and reusable for future agent/reporting features.
"""

from collections import Counter, defaultdict
from datetime import date
from statistics import mean
from typing import Optional

from app.models import (
    BoardPromotionBucket,
    BoardPromotionStock,
    ContinuationStat,
    ConceptHeat,
    DailyBoardPromotionStat,
    FailedRateStat,
    LimitUpEvent,
    MarketIndexSnapshot,
    MarketSummary,
    PostPerformanceStat,
)


def latest_trade_date(events: list[LimitUpEvent]) -> date:
    """Return the newest trading date available in a non-empty event list."""

    return max(event.trade_date for event in events)


def events_for_date(
    events: list[LimitUpEvent],
    trade_date: Optional[date] = None,
) -> list[LimitUpEvent]:
    """Filter events to a trading date, defaulting to the latest available date."""

    target_date = trade_date or latest_trade_date(events)
    return [event for event in events if event.trade_date == target_date]


def find_stock_event(
    events: list[LimitUpEvent],
    symbol: str,
    trade_date: date | None = None,
) -> LimitUpEvent | None:
    """Return one stock's requested event, defaulting to its latest local event."""

    normalized_symbol = symbol.strip().lower()
    matches = [
        event
        for event in events
        if event.symbol.lower() == normalized_symbol
        and (trade_date is None or event.trade_date == trade_date)
    ]
    return max(matches, key=lambda event: event.trade_date, default=None)


def summarize_market(
    events: list[LimitUpEvent],
    indices: Optional[list[MarketIndexSnapshot]] = None,
) -> MarketSummary:
    """Build the dashboard market summary for the latest persisted trading day.

    `failed_count` currently means events with at least one intraday break
    (`break_count > 0`). This is a seal-quality metric, not strictly the same
    as the number of stocks that failed to close at limit-up.
    """

    latest_date = latest_trade_date(events)
    latest_events = events_for_date(events, latest_date)
    closed_events = [event for event in latest_events if event.closed_limit]
    failed_count = sum(1 for event in latest_events if event.break_count > 0)
    unsealed_count = sum(1 for event in latest_events if not event.closed_limit)
    industry_counts = Counter(event.industry for event in closed_events)
    concept_counts = Counter(event.concept for event in closed_events)
    concept_failed_counts = Counter(
        event.concept for event in latest_events if event.break_count > 0
    )
    max_board_height = max((event.board_height for event in closed_events), default=0)
    failed_rate = round(failed_count / len(latest_events), 4)
    first_board_count = sum(1 for event in closed_events if event.board_height == 1)
    continued_board_count = sum(1 for event in closed_events if event.board_height > 1)
    total_amount = sum(event.amount for event in closed_events)

    return MarketSummary(
        trade_date=latest_date,
        limit_up_count=len(closed_events),
        first_board_count=first_board_count,
        continued_board_count=continued_board_count,
        failed_count=failed_count,
        unsealed_count=unsealed_count,
        unsealed_rate=round(unsealed_count / len(latest_events), 4),
        limit_down_count=None,
        failed_limit_up_rate=failed_rate,
        max_board_height=max_board_height,
        total_amount=total_amount,
        hot_industries=[name for name, _ in industry_counts.most_common(3)],
        hot_concepts=[
            ConceptHeat(
                name=name,
                limit_up_count=count,
                failed_count=concept_failed_counts[name],
            )
            for name, count in concept_counts.most_common(5)
        ],
        indices=indices or [],
    )


def list_first_board(events: list[LimitUpEvent]) -> list[LimitUpEvent]:
    """Return latest-day first-board events sorted by first seal time."""

    return sorted(
        [
            event
            for event in events_for_date(events)
            if event.closed_limit and event.board_height == 1
        ],
        key=lambda event: event.first_limit_time,
    )


def list_continued_board(events: list[LimitUpEvent]) -> list[LimitUpEvent]:
    """Return latest-day continued-board events, highest board first."""

    return sorted(
        [
            event
            for event in events_for_date(events)
            if event.closed_limit and event.board_height > 1
        ],
        key=lambda event: (-event.board_height, event.first_limit_time),
    )


def list_failed_events(events: list[LimitUpEvent]) -> list[LimitUpEvent]:
    """Return latest-day stocks that touched limit-up but did not close there."""

    return sorted(
        [event for event in events_for_date(events) if not event.closed_limit],
        key=lambda event: (-event.break_count, event.first_limit_time),
    )


def list_recent_limit_up(events: list[LimitUpEvent], days: int = 5) -> list[LimitUpEvent]:
    """Return events from the most recent N trading dates in reverse order."""

    trade_dates = sorted({event.trade_date for event in events}, reverse=True)[:days]
    return sorted(
        [
            event
            for event in events
            if event.trade_date in trade_dates and event.closed_limit
        ],
        key=lambda event: (event.trade_date, event.board_height, event.first_limit_time),
        reverse=True,
    )


def calculate_continuation(events: list[LimitUpEvent]) -> list[ContinuationStat]:
    """Calculate next-day continuation probability grouped by board height."""

    grouped: dict[int, list[LimitUpEvent]] = defaultdict(list)
    for event in events:
        grouped[event.board_height].append(event)

    return [
        ContinuationStat(
            board_height=height,
            sample_size=len(items),
            continued_count=sum(1 for item in items if item.continued_next_day),
            probability=round(
                sum(1 for item in items if item.continued_next_day) / len(items),
                4,
            ),
        )
        for height, items in sorted(grouped.items())
    ]


def calculate_daily_board_promotion(
    events: list[LimitUpEvent],
    days: int = 5,
    end_date: date | None = None,
) -> list[DailyBoardPromotionStat]:
    """Calculate promotion rates for each adjacent pair of local trading dates.

    The previous date's stocks that closed at limit-up form the denominator. A
    stock is promoted only when it also closes at limit-up on the next observed
    trading date with its board height increased by exactly one. Pairs separated
    by more than four calendar days are skipped because local history may have a
    data gap that cannot be distinguished from a long exchange holiday.
    """

    if days <= 0:
        return []
    grouped: dict[date, dict[str, LimitUpEvent]] = defaultdict(dict)
    for event in events:
        if event.closed_limit:
            grouped[event.trade_date][event.symbol] = event

    trade_dates = sorted(grouped)
    daily_stats: list[DailyBoardPromotionStat] = []
    for previous_date, trade_date in zip(trade_dates, trade_dates[1:]):
        calendar_gap = (trade_date - previous_date).days
        if calendar_gap < 1 or calendar_gap > 4:
            continue
        previous_events = list(grouped[previous_date].values())
        current_events = grouped[trade_date]
        if not previous_events:
            continue

        promoted_symbols = {
            event.symbol
            for event in previous_events
            if (
                event.symbol in current_events
                and current_events[event.symbol].board_height
                == event.board_height + 1
            )
        }
        first_board_events = [
            event for event in previous_events if event.board_height == 1
        ]
        continued_board_events = [
            event for event in previous_events if event.board_height >= 2
        ]
        buckets = []
        for height in sorted({event.board_height for event in previous_events}):
            cohort = [
                event for event in previous_events if event.board_height == height
            ]
            promoted_count = sum(
                1 for event in cohort if event.symbol in promoted_symbols
            )
            buckets.append(
                BoardPromotionBucket(
                    from_board_height=height,
                    to_board_height=height + 1,
                    sample_size=len(cohort),
                    promoted_count=promoted_count,
                    probability=round(promoted_count / len(cohort), 4),
                )
            )

        first_promoted_count = sum(
            1 for event in first_board_events if event.symbol in promoted_symbols
        )
        continued_promoted_count = sum(
            1 for event in continued_board_events if event.symbol in promoted_symbols
        )
        promoted_stocks = sorted(
            (
                BoardPromotionStock(
                    symbol=event.symbol,
                    name=current_events[event.symbol].name,
                    industry=current_events[event.symbol].industry,
                    concept=current_events[event.symbol].concept,
                    from_board_height=event.board_height,
                    to_board_height=current_events[event.symbol].board_height,
                    first_limit_time=current_events[event.symbol].first_limit_time,
                    break_count=current_events[event.symbol].break_count,
                )
                for event in previous_events
                if event.symbol in promoted_symbols
            ),
            key=lambda item: (
                -item.to_board_height,
                item.first_limit_time,
                item.symbol,
            ),
        )
        daily_stats.append(
            DailyBoardPromotionStat(
                trade_date=trade_date,
                previous_trade_date=previous_date,
                sample_size=len(previous_events),
                promoted_count=len(promoted_symbols),
                probability=round(len(promoted_symbols) / len(previous_events), 4),
                first_board_sample_size=len(first_board_events),
                first_board_promoted_count=first_promoted_count,
                first_board_probability=_optional_rate(
                    first_promoted_count,
                    len(first_board_events),
                ),
                continued_board_sample_size=len(continued_board_events),
                continued_board_promoted_count=continued_promoted_count,
                continued_board_probability=_optional_rate(
                    continued_promoted_count,
                    len(continued_board_events),
                ),
                buckets=buckets,
                promoted_stocks=promoted_stocks,
            )
        )
    if end_date is not None:
        daily_stats = [item for item in daily_stats if item.trade_date <= end_date]
    return daily_stats[-days:]


def _optional_rate(count: int, sample_size: int) -> float | None:
    """Return a rounded empirical rate when the cohort is non-empty."""

    return round(count / sample_size, 4) if sample_size else None


def calculate_failed_rates(events: list[LimitUpEvent]) -> list[FailedRateStat]:
    """Calculate intraday break rate grouped by board height."""

    grouped: dict[int, list[LimitUpEvent]] = defaultdict(list)
    for event in events:
        grouped[event.board_height].append(event)

    return [
        FailedRateStat(
            board_height=height,
            sample_size=len(items),
            failed_count=sum(1 for item in items if item.break_count > 0),
            failed_rate=round(
                sum(1 for item in items if item.break_count > 0) / len(items),
                4,
            ),
        )
        for height, items in sorted(grouped.items())
    ]


def calculate_post_performance(events: list[LimitUpEvent]) -> list[PostPerformanceStat]:
    """Calculate average post-limit-up returns grouped by board height."""

    grouped: dict[int, list[LimitUpEvent]] = defaultdict(list)
    for event in events:
        grouped[event.board_height].append(event)

    return [
        PostPerformanceStat(
            board_height=height,
            sample_size=len(items),
            avg_next_open_pct=round(mean(item.next_open_pct for item in items), 2),
            avg_next_high_pct=round(mean(item.next_high_pct for item in items), 2),
            avg_next_close_pct=round(mean(item.next_close_pct for item in items), 2),
            avg_five_day_return_pct=round(
                mean(item.five_day_return_pct for item in items),
                2,
            ),
        )
        for height, items in sorted(grouped.items())
    ]
