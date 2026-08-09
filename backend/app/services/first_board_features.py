"""Feature builders for local first-board similar-case retrieval."""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone

from app.models import (
    FirstBoardFeature,
    FirstBoardOutcome,
    LimitUpEvent,
    MarketSummary,
    StockDailyBar,
)
from app.services.analysis import events_for_date, summarize_market


FEATURE_VERSION = "first-board-feature-v1"
OUTCOME_VERSION = "first-board-outcome-v1"


def build_first_board_features(
    events: list[LimitUpEvent],
    trade_date=None,
) -> list[FirstBoardFeature]:
    """Build persisted first-board feature rows for one trading date."""

    target_events = events_for_date(events, trade_date)
    if not target_events:
        return []

    summary = summarize_market(target_events)
    return [
        build_first_board_feature(
            event=event,
            same_day_events=target_events,
            summary=summary,
        )
        for event in target_events
        if event.board_height == 1 and event.closed_limit
    ]


def build_first_board_feature(
    event: LimitUpEvent,
    same_day_events: list[LimitUpEvent],
    summary: MarketSummary,
) -> FirstBoardFeature:
    """Build one first-board feature row from event and market context."""

    closed_events = [item for item in same_day_events if item.closed_limit]
    industry_counts = Counter(item.industry for item in closed_events)
    concept_counts = Counter(item.concept for item in closed_events)
    first_limit_minutes = event.first_limit_time.hour * 60 + event.first_limit_time.minute

    return FirstBoardFeature(
        trade_date=event.trade_date,
        symbol=event.symbol,
        name=event.name,
        first_limit_minutes=first_limit_minutes,
        first_limit_bucket=first_limit_bucket(first_limit_minutes),
        break_count=event.break_count,
        seal_count=event.seal_count,
        turnover_rate=event.turnover_rate,
        turnover_bucket=turnover_bucket(event.turnover_rate),
        amount=event.amount,
        amount_log=round(math.log10(max(event.amount, 1)), 4),
        amount_bucket=amount_bucket(event.amount),
        industry=event.industry,
        concept=event.concept,
        same_industry_limit_up_count=industry_counts[event.industry],
        same_concept_limit_up_count=concept_counts[event.concept],
        market_limit_up_count=summary.limit_up_count,
        market_first_board_count=summary.first_board_count,
        market_failed_limit_up_rate=summary.failed_limit_up_rate,
        market_failed_rate_bucket=failed_rate_bucket(summary.failed_limit_up_rate),
        market_max_board_height=summary.max_board_height,
        market_sentiment=summary.sentiment,
        closed_limit=event.closed_limit,
        feature_version=FEATURE_VERSION,
        created_at=_now_utc(),
    )


def build_first_board_outcome(
    event: LimitUpEvent,
    bars: list[StockDailyBar],
    future_events: list[LimitUpEvent],
) -> FirstBoardOutcome:
    """Build a post-first-board outcome from local daily bars and future events."""

    ordered_bars = sorted(
        [bar for bar in bars if bar.symbol == event.symbol and bar.trade_date >= event.trade_date],
        key=lambda bar: bar.trade_date,
    )
    base_bar = ordered_bars[0] if ordered_bars else None
    post_bars = [bar for bar in ordered_bars if bar.trade_date > event.trade_date][:3]
    next_bar = post_bars[0] if post_bars else None
    outcome_ready = base_bar is not None and len(post_bars) >= 3
    promoted = event.continued_next_day or any(
        item.symbol == event.symbol
        and item.trade_date > event.trade_date
        and item.board_height >= 2
        and item.closed_limit
        for item in future_events
    )

    return FirstBoardOutcome(
        base_trade_date=event.trade_date,
        symbol=event.symbol,
        next_trade_date=next_bar.trade_date if next_bar else None,
        next_open_pct=_pct_change(next_bar.open, base_bar.close) if next_bar and base_bar else None,
        next_high_pct=_pct_change(next_bar.high, base_bar.close) if next_bar and base_bar else None,
        next_close_pct=_pct_change(next_bar.close, base_bar.close) if next_bar and base_bar else None,
        three_day_high_pct=_pct_change(max(bar.high for bar in post_bars), base_bar.close)
        if outcome_ready and base_bar
        else None,
        three_day_close_pct=_pct_change(post_bars[-1].close, base_bar.close)
        if outcome_ready and base_bar
        else None,
        max_drawdown_3d=_pct_change(min(bar.low for bar in post_bars), base_bar.close)
        if outcome_ready and base_bar
        else None,
        promoted_to_second_board=promoted,
        outcome_ready=outcome_ready,
        outcome_version=OUTCOME_VERSION,
        created_at=_now_utc(),
    )


def first_limit_bucket(minutes: int) -> str:
    """Bucket first seal time for coarse SQL recall."""

    if minutes <= 9 * 60 + 45:
        return "early_strong"
    if minutes <= 10 * 60 + 30:
        return "early"
    if minutes <= 11 * 60 + 30:
        return "late_morning"
    if minutes <= 13 * 60 + 30:
        return "early_afternoon"
    return "late"


def turnover_bucket(turnover_rate: float) -> str:
    """Bucket turnover rate into coarse similarity bands."""

    if turnover_rate < 3:
        return "low"
    if turnover_rate <= 8:
        return "normal"
    if turnover_rate <= 15:
        return "high"
    return "extreme"


def amount_bucket(amount: float) -> str:
    """Bucket trading amount into CNY liquidity bands."""

    if amount < 300_000_000:
        return "small"
    if amount <= 1_500_000_000:
        return "medium"
    if amount <= 5_000_000_000:
        return "large"
    return "huge"


def failed_rate_bucket(rate: float) -> str:
    """Bucket market failed-limit pressure for coarse recall."""

    if rate < 0.25:
        return "stable"
    if rate < 0.45:
        return "mixed"
    if rate < 0.6:
        return "weak"
    return "fragile"


def _pct_change(value: float, base: float) -> float | None:
    """Return percentage change from base, guarding zero or missing values."""

    if base == 0:
        return None
    return round((value - base) / base * 100, 2)


def _now_utc() -> datetime:
    """Return timezone-aware UTC timestamp for derived rows."""

    return datetime.now(timezone.utc)

