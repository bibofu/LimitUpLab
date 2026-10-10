"""Derive review-only outcomes from observations available at the report cutoff."""

from datetime import date
from typing import Iterable

from app.models import (
    AgentEvaluationItem,
    AgentPrediction,
    LimitUpEvent,
    ReviewAgentPostBar,
    StockDailyBar,
)
from app.services.evaluation_agent import _evaluate_prediction
from app.services.first_board_features import build_first_board_outcome


def evaluate_review_prediction_as_of(
    prediction: AgentPrediction,
    *,
    event: LimitUpEvent | None,
    bars: list[StockDailyBar],
    events: list[LimitUpEvent],
    trade_dates: Iterable[date],
    as_of_date: date,
) -> AgentEvaluationItem:
    """Re-evaluate one saved prediction without reading or replacing saved outcomes."""

    calendar = sorted({day for day in trade_dates if day <= as_of_date})
    observed_bars = _bars_in_period(
        bars, prediction.symbol, prediction.trade_date, as_of_date,
    )
    if (
        event is None
        or event.symbol != prediction.symbol
        or event.trade_date != prediction.trade_date
        or prediction.trade_date not in calendar
        or not any(bar.trade_date == prediction.trade_date for bar in observed_bars)
    ):
        return _evaluate_prediction(prediction, None)

    # The event's persisted continuation flag may have been backfilled after the
    # cutoff. Only the exact next-day event within this report may prove promotion.
    outcome = build_first_board_outcome(
        event=event.model_copy(update={"continued_next_day": False}),
        bars=observed_bars,
        future_events=[
            item for item in events
            if item.symbol == prediction.symbol
            and prediction.trade_date < item.trade_date <= as_of_date
        ],
        trading_dates=calendar,
    )
    return _evaluate_prediction(prediction, outcome)


def build_review_post_bars(
    bars: list[StockDailyBar],
    *,
    symbol: str,
    base_date: date,
    as_of_date: date,
    follow_days: int,
    trade_dates: Iterable[date],
) -> list[ReviewAgentPostBar]:
    """Keep known bars in their actual calendar slots without shifting missing days."""

    window = review_window_dates(
        trade_dates, base_date=base_date, as_of_date=as_of_date, follow_days=follow_days,
    )
    # An unknown calendar cannot establish a follow-up window. The base-day
    # observation itself can still be shown without inventing a D+N label.
    offsets = {day: index for index, day in enumerate(window)} or {base_date: 0}
    observed_bars = [
        bar for bar in _bars_in_period(bars, symbol, base_date, as_of_date)
        if bar.trade_date in offsets
    ]
    base_bar = next((bar for bar in observed_bars if bar.trade_date == base_date), None)
    base_close = base_bar.close if base_bar else None
    return [
        ReviewAgentPostBar(
            trade_date=bar.trade_date,
            trading_day_offset=offsets[bar.trade_date],
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            change_pct=bar.change_pct,
            return_from_base_pct=(
                ((bar.close - base_close) / base_close) * 100
                if base_close else None
            ),
        )
        for bar in observed_bars
    ]


def review_window_dates(
    trade_dates: Iterable[date],
    *,
    base_date: date,
    as_of_date: date,
    follow_days: int,
) -> tuple[date, ...]:
    """Return the elapsed portion of a confirmed base-day plus D+N window."""

    calendar = sorted(set(trade_dates))
    if base_date not in calendar:
        return ()
    base_index = calendar.index(base_date)
    return tuple(
        day for day in calendar[base_index:base_index + max(follow_days, 0) + 1]
        if day <= as_of_date
    )


def _bars_in_period(
    bars: list[StockDailyBar],
    symbol: str,
    base_date: date,
    as_of_date: date,
) -> list[StockDailyBar]:
    return sorted(
        (bar for bar in bars if bar.symbol == symbol and base_date <= bar.trade_date <= as_of_date),
        key=lambda bar: bar.trade_date,
    )
