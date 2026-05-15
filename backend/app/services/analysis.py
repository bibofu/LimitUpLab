from collections import Counter, defaultdict
from datetime import date
from typing import Optional
from statistics import mean

from app.models import (
    ContinuationStat,
    ConceptHeat,
    FailedRateStat,
    LimitUpEvent,
    MarketSummary,
    PostPerformanceStat,
)
from app.services.sample_data import SAMPLE_INDICES


def latest_trade_date(events: list[LimitUpEvent]) -> date:
    return max(event.trade_date for event in events)


def events_for_date(
    events: list[LimitUpEvent],
    trade_date: Optional[date] = None,
) -> list[LimitUpEvent]:
    target_date = trade_date or latest_trade_date(events)
    return [event for event in events if event.trade_date == target_date]


def summarize_market(events: list[LimitUpEvent]) -> MarketSummary:
    latest_date = latest_trade_date(events)
    latest_events = events_for_date(events, latest_date)
    failed_count = sum(1 for event in latest_events if event.break_count > 0)
    industry_counts = Counter(event.industry for event in latest_events)
    concept_counts = Counter(event.concept for event in latest_events)
    concept_failed_counts = Counter(
        event.concept for event in latest_events if event.break_count > 0
    )
    max_board_height = max(event.board_height for event in latest_events)
    failed_rate = round(failed_count / len(latest_events), 4)
    first_board_count = sum(1 for event in latest_events if event.board_height == 1)
    continued_board_count = sum(1 for event in latest_events if event.board_height > 1)
    total_amount = sum(event.amount for event in latest_events)

    if continued_board_count >= 3 and failed_rate < 0.35:
        sentiment = "heating"
    elif failed_rate > 0.55:
        sentiment = "cooling"
    else:
        sentiment = "diverging"

    return MarketSummary(
        trade_date=latest_date,
        limit_up_count=len(latest_events),
        first_board_count=first_board_count,
        continued_board_count=continued_board_count,
        failed_count=failed_count,
        limit_down_count=7,
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
        indices=SAMPLE_INDICES,
        sentiment=sentiment,
    )


def list_first_board(events: list[LimitUpEvent]) -> list[LimitUpEvent]:
    return sorted(
        [event for event in events_for_date(events) if event.board_height == 1],
        key=lambda event: event.first_limit_time,
    )


def list_continued_board(events: list[LimitUpEvent]) -> list[LimitUpEvent]:
    return sorted(
        [event for event in events_for_date(events) if event.board_height > 1],
        key=lambda event: (-event.board_height, event.first_limit_time),
    )


def list_failed_events(events: list[LimitUpEvent]) -> list[LimitUpEvent]:
    return sorted(
        [event for event in events_for_date(events) if event.break_count > 0],
        key=lambda event: (-event.break_count, event.first_limit_time),
    )


def list_recent_limit_up(events: list[LimitUpEvent], days: int = 3) -> list[LimitUpEvent]:
    trade_dates = sorted({event.trade_date for event in events}, reverse=True)[:days]
    return sorted(
        [event for event in events if event.trade_date in trade_dates],
        key=lambda event: (event.trade_date, event.board_height, event.first_limit_time),
        reverse=True,
    )


def calculate_continuation(events: list[LimitUpEvent]) -> list[ContinuationStat]:
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


def calculate_failed_rates(events: list[LimitUpEvent]) -> list[FailedRateStat]:
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
