"""A-share trading calendar collector used by scheduled after-close jobs."""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, replace
from datetime import date
from math import isfinite
import os
from threading import Lock
from time import monotonic

import akshare as ak

from app.collectors.hithink_finance_collector import HithinkFinanceCollector
from app.collectors.network import without_proxy
from app.collectors.process_timeout import run_in_killable_process


_CACHE_TTL_SECONDS = 3600.0
_HITHINK_SOURCE = "hithink-finance.market.calendar"
_SINA_SOURCE = "akshare.tool_trade_date_hist_sina"
_INCOMPLETE_COVERAGE_WARNING = (
    "交易日历未覆盖查询窗口末端；窗口末段及今天之后的交易日状态可能未确认。"
)


@dataclass(frozen=True)
class TradingCalendarSnapshot:
    """Calendar dates plus the provider and the range they were verified for."""

    dates: tuple[date, ...]
    source: str
    query_start: date
    query_end: date
    coverage_verified: bool
    warnings: tuple[str, ...] = ()


_cache: tuple[float, TradingCalendarSnapshot] | None = None
_inflight: Future | None = None
_inflight_range: tuple[date, date] | None = None
_lock = Lock()


def calendar_timeout_seconds() -> float:
    """Keep calendar deadlines explicit and impossible to disable accidentally."""
    timeout = float(os.getenv("LIMITUPLAB_TRADING_CALENDAR_TIMEOUT_SECONDS", "30"))
    if not isfinite(timeout) or timeout <= 0:
        raise ValueError("Trading calendar timeout must be finite and greater than zero")
    return timeout


def collect_a_share_trade_dates(start_date: date, end_date: date) -> list[date]:
    """Return exchange trading dates in the inclusive date range."""
    return list(collect_a_share_calendar(start_date, end_date).dates)


def collect_a_share_calendar(
    start_date: date,
    end_date: date,
    *,
    timeout_seconds: float | None = None,
) -> TradingCalendarSnapshot:
    """Return verified calendar dates and their provider for one range."""
    if start_date > end_date:
        raise ValueError("Trading calendar start date must not exceed end date")
    budget = calendar_timeout_seconds() if timeout_seconds is None else timeout_seconds
    if not isfinite(budget) or budget <= 0:
        raise ValueError("Trading calendar timeout must be finite and greater than zero")
    return _collect_with_budget(start_date, end_date, budget)


def _collect_with_budget(
    start_date: date, end_date: date, timeout: float,
) -> TradingCalendarSnapshot:
    global _cache, _inflight, _inflight_range
    deadline = monotonic() + timeout
    with _lock:
        if (_cache is not None and monotonic() < _cache[0]
                and _covers(_cache[1].dates, start_date, end_date)):
            return _filter_snapshot(_cache[1], start_date, end_date)
        _cache = None
        pending = _inflight
        owner = pending is None
        if pending is None:
            pending = Future()
            _inflight = pending
            _inflight_range = (start_date, end_date)
        pending_range = _inflight_range
    if not owner:
        value = pending.result(timeout=timeout + 5)
        snapshot = _coerce_snapshot(value, start_date, end_date)
        if (not _covers(snapshot.dates, start_date, end_date) and pending_range is not None
                and not (pending_range[0] <= start_date <= end_date <= pending_range[1])):
            # The shared full-history fetch may cover the owner's recent request but
            # not this waiter's older/future dates. Fetch the missing range.
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("Trading calendar deadline expired before range fallback")
            return _collect_with_budget(start_date, end_date, remaining)
        return _filter_snapshot(snapshot, start_date, end_date)
    try:
        value = run_in_killable_process(
            _fetch_trade_calendar, start_date, end_date, timeout, timeout_seconds=timeout,
        )
        snapshot = _coerce_snapshot(value, start_date, end_date)
        with _lock:
            if _covers(snapshot.dates, start_date, end_date):
                _cache = (monotonic() + _CACHE_TTL_SECONDS, snapshot)
            _inflight = None
            _inflight_range = None
        pending.set_result(snapshot)
        return _filter_snapshot(snapshot, start_date, end_date)
    except BaseException as error:
        pending.set_exception(error)
        raise
    finally:
        with _lock:
            if _inflight is pending:
                _inflight = None
                _inflight_range = None


def _covers(days: tuple[date, ...], start_date: date, end_date: date) -> bool:
    return bool(days and days[0] <= start_date <= end_date <= days[-1])


def _coerce_snapshot(
    value: TradingCalendarSnapshot | tuple[date, ...] | list[date],
    start_date: date,
    end_date: date,
) -> TradingCalendarSnapshot:
    """Accept a dates-only worker result at the injection boundary."""
    if isinstance(value, TradingCalendarSnapshot):
        return value
    dates = tuple(value)
    return TradingCalendarSnapshot(
        dates=dates,
        source="unknown",
        query_start=start_date,
        query_end=end_date,
        coverage_verified=_covers(dates, start_date, end_date),
    )


def _filter_snapshot(
    snapshot: TradingCalendarSnapshot,
    start_date: date,
    end_date: date,
) -> TradingCalendarSnapshot:
    return replace(
        snapshot,
        dates=tuple(day for day in snapshot.dates if start_date <= day <= end_date),
    )


def _fetch_trade_calendar(
    start_date: date | None = None,
    end_date: date | None = None,
    timeout_seconds: float | None = None,
) -> TradingCalendarSnapshot:
    """Prefer CLI only for covered ranges; retain Sina for older/future dates."""
    requested_start = start_date or date.today()
    requested_end = end_date or date.today()
    budget = calendar_timeout_seconds() if timeout_seconds is None else timeout_seconds
    try:
        collector = HithinkFinanceCollector()
        collector.timeout_seconds = min(collector.timeout_seconds, budget / 2)
        days = tuple(sorted({_parse_trade_date(day) for day in collector.collect_trading_calendar()}))
        # A trading-day list cannot prove coverage beyond its first/last day,
        # including weekends at the boundary. Conservatively consult Sina.
        if _covers(days, requested_start, requested_end):
            return TradingCalendarSnapshot(
                dates=days,
                source=_HITHINK_SOURCE,
                query_start=requested_start,
                query_end=requested_end,
                coverage_verified=True,
            )
    except Exception:
        pass
    with without_proxy():
        frame = ak.tool_trade_date_hist_sina()
    if frame is None or frame.empty or "trade_date" not in frame.columns:
        raise RuntimeError("A-share trading calendar returned no rows")

    dates = tuple(sorted({_parse_trade_date(value) for value in frame["trade_date"].tolist()}))
    coverage_verified = _covers(dates, requested_start, requested_end)
    return TradingCalendarSnapshot(
        dates=dates,
        source=_SINA_SOURCE,
        query_start=requested_start,
        query_end=requested_end,
        coverage_verified=coverage_verified,
        warnings=() if coverage_verified else (_INCOMPLETE_COVERAGE_WARNING,),
    )


def _fetch_trade_dates(
    start_date: date | None = None, end_date: date | None = None,
    timeout_seconds: float | None = None,
) -> tuple[date, ...]:
    """Retain the dates-only worker shape used by existing callers."""
    return _fetch_trade_calendar(start_date, end_date, timeout_seconds).dates


# Normalize a trading-calendar date from its provider representation.
def _parse_trade_date(value: object) -> date:
    if isinstance(value, date):
        return value
    normalized = str(value).strip()[:10].replace("/", "-")
    return date.fromisoformat(normalized)
