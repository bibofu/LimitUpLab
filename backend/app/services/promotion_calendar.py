"""Verified calendar adjacency shared by board-promotion calculations."""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import date
from threading import Lock
from time import monotonic
from typing import Iterable

from app.collectors.trading_calendar_collector import (
    calendar_timeout_seconds,
    collect_a_share_trade_dates,
)


@dataclass(frozen=True)
class PromotionCalendar:
    trade_dates: tuple[date, ...]
    warnings: tuple[str, ...] = ()


_CACHE_TTL_SECONDS = 3600.0
_MAX_CACHE_ENTRIES = 128
_cache: OrderedDict[tuple[date, date], tuple[float, PromotionCalendar]] = OrderedDict()
_inflight: dict[tuple[date, date], Future[PromotionCalendar]] = {}
_lock = Lock()


def adjacent_trade_date_pairs(trade_dates: Iterable[date]) -> set[tuple[date, date]]:
    """Pair consecutive verified sessions, regardless of intervening holidays."""
    days = sorted(set(trade_dates))
    return set(zip(days, days[1:]))


def _fetch_calendar(start_date: date, end_date: date) -> PromotionCalendar:
    try:
        collected = collect_a_share_trade_dates(start_date, end_date)
        if any(type(day) is not date for day in collected):
            raise ValueError("Invalid trading-calendar date")
        days = tuple(sorted({day for day in collected if start_date <= day <= end_date}))
    except Exception:
        return PromotionCalendar((), ("交易日历读取失败，无法确认相邻交易日；相关晋级率暂不计算。",))
    if start_date not in days or end_date not in days:
        return PromotionCalendar((), ("交易日历未覆盖查询范围的起止交易日，无法确认相邻交易日；相关晋级率暂不计算。",))
    return PromotionCalendar(days)


def load_promotion_calendar(start_date: date, end_date: date) -> PromotionCalendar:
    """Cache verified calendars for one hour; coalesce concurrent identical reads."""
    if start_date > end_date:
        return PromotionCalendar((), ("交易日历查询的开始日期晚于结束日期，相关晋级率暂不计算。",))
    if start_date == end_date:
        return PromotionCalendar((start_date,))

    key = (start_date, end_date)
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            expires_at, result = cached
            if monotonic() < expires_at:
                _cache.move_to_end(key)
                return result
            del _cache[key]
        pending = _inflight.get(key)
        owner = pending is None
        if pending is None:
            pending = Future()
            _inflight[key] = pending
    if not owner:
        try:
            return pending.result(timeout=calendar_timeout_seconds() + 5)
        except (TimeoutError, ValueError):
            return PromotionCalendar((), ("交易日历读取超时，无法确认相邻交易日；相关晋级率暂不计算。",))

    try:
        result = _fetch_calendar(start_date, end_date)
        with _lock:
            if result.trade_dates:
                _cache[key] = (monotonic() + _CACHE_TTL_SECONDS, result)
                _cache.move_to_end(key)
                while len(_cache) > _MAX_CACHE_ENTRIES:
                    _cache.popitem(last=False)
            del _inflight[key]
            pending.set_result(result)
        return result
    except BaseException as error:
        # Release waiters even if the fetching thread is interrupted.
        with _lock:
            _inflight.pop(key, None)
            pending.set_exception(error)
        raise
