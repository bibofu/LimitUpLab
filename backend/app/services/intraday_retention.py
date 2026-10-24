"""Bound persisted minute bars to the stock-detail chart's rolling window."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from app.collectors.trading_calendar_collector import collect_a_share_trade_dates
from app.repositories import FirstBoardRepository


INTRADAY_RETENTION_TRADE_DAYS = 7
MIN_RETENTION_TRADE_DAYS = 5
CALENDAR_LOOKBACK_DAYS = 45

CalendarLoader = Callable[[date, date], list[date]]
BarDeleter = Callable[[date], int]


@dataclass(frozen=True)
class IntradayRetentionResult:
    """Outcome of one rolling-window minute-bar cleanup."""

    keep_trade_days: int
    cutoff_date: date | None
    deleted_rows: int
    retained_trade_dates: tuple[date, ...] = ()
    warnings: tuple[str, ...] = ()


def prune_intraday_bars(
    *,
    repository: FirstBoardRepository,
    trade_date: date,
    keep_trade_days: int = INTRADAY_RETENTION_TRADE_DAYS,
    calendar_loader: CalendarLoader = collect_a_share_trade_dates,
    deleter: BarDeleter | None = None,
) -> IntradayRetentionResult:
    """Delete minute bars older than the last N verified trading sessions.

    The five-day chart needs five sessions; seven keeps a small repair buffer.
    The cutoff is derived from the exchange calendar, never from calendar-day
    arithmetic, and no rows are deleted when the window cannot be verified.
    """

    if keep_trade_days < MIN_RETENTION_TRADE_DAYS:
        raise ValueError(
            f"keep_trade_days must be at least {MIN_RETENTION_TRADE_DAYS}"
        )
    try:
        dates = sorted({
            day
            for day in calendar_loader(
                trade_date - timedelta(days=CALENDAR_LOOKBACK_DAYS),
                trade_date,
            )
            if day <= trade_date
        })
    except Exception as error:  # noqa: BLE001 - retention is best-effort
        return IntradayRetentionResult(
            keep_trade_days=keep_trade_days,
            cutoff_date=None,
            deleted_rows=0,
            warnings=(
                f"交易日历不可用：{type(error).__name__}；未清理分钟线。",
            ),
        )

    if trade_date not in dates:
        return IntradayRetentionResult(
            keep_trade_days=keep_trade_days,
            cutoff_date=None,
            deleted_rows=0,
            warnings=(
                "交易日历未覆盖目标交易日；未清理分钟线。",
            ),
        )
    if len(dates) < keep_trade_days:
        return IntradayRetentionResult(
            keep_trade_days=keep_trade_days,
            cutoff_date=None,
            deleted_rows=0,
            warnings=(
                f"交易日历仅覆盖 {len(dates)} 个交易日，少于保留窗口 "
                f"{keep_trade_days}；未清理分钟线。",
            ),
        )

    retained = tuple(dates[-keep_trade_days:])
    cutoff = retained[0]
    delete = deleter or repository.delete_intraday_bars_before
    try:
        deleted = delete(cutoff)
    except Exception as error:  # noqa: BLE001 - report the failed cleanup
        return IntradayRetentionResult(
            keep_trade_days=keep_trade_days,
            cutoff_date=cutoff,
            deleted_rows=0,
            retained_trade_dates=retained,
            warnings=(
                f"分钟线清理失败：{type(error).__name__}；未确认删除结果。",
            ),
        )
    return IntradayRetentionResult(
        keep_trade_days=keep_trade_days,
        cutoff_date=cutoff,
        deleted_rows=max(0, int(deleted or 0)),
        retained_trade_dates=retained,
    )
