"""A-share trading calendar collector used by scheduled after-close jobs."""

from __future__ import annotations

from concurrent.futures import Future
from datetime import date
from math import isfinite
import os
from threading import Lock
from time import monotonic

import akshare as ak

from app.collectors.network import without_proxy
from app.collectors.process_timeout import run_in_killable_process


_CACHE_TTL_SECONDS = 3600.0
_cache: tuple[float, tuple[date, ...]] | None = None
_inflight: Future[tuple[date, ...]] | None = None
_lock = Lock()


def calendar_timeout_seconds() -> float:
    """Keep calendar deadlines explicit and impossible to disable accidentally."""
    timeout = float(os.getenv("LIMITUPLAB_TRADING_CALENDAR_TIMEOUT_SECONDS", "8"))
    if not isfinite(timeout) or timeout <= 0:
        raise ValueError("Trading calendar timeout must be finite and greater than zero")
    return timeout


def collect_a_share_trade_dates(start_date: date, end_date: date) -> list[date]:
    """Return exchange trading dates in the inclusive date range."""
    global _cache, _inflight
    timeout = calendar_timeout_seconds()
    with _lock:
        if (_cache is not None and monotonic() < _cache[0]
                and _cache[1][0] <= start_date <= end_date <= _cache[1][-1]):
            return [day for day in _cache[1] if start_date <= day <= end_date]
        _cache = None
        pending = _inflight
        owner = pending is None
        if pending is None:
            pending = Future()
            _inflight = pending
    if not owner:
        days = pending.result(timeout=timeout + 5)
        return [day for day in days if start_date <= day <= end_date]
    try:
        days = run_in_killable_process(_fetch_trade_dates, timeout_seconds=timeout)
        with _lock:
            if days and days[0] <= start_date <= end_date <= days[-1]:
                _cache = (monotonic() + _CACHE_TTL_SECONDS, days)
        pending.set_result(days)
        return [day for day in days if start_date <= day <= end_date]
    except BaseException as error:
        pending.set_exception(error)
        raise
    finally:
        with _lock:
            _inflight = None


def _fetch_trade_dates() -> tuple[date, ...]:
    """Fetch full exchange history inside the isolated provider process."""
    with without_proxy():
        frame = ak.tool_trade_date_hist_sina()
    if frame is None or frame.empty or "trade_date" not in frame.columns:
        raise RuntimeError("A-share trading calendar returned no rows")

    dates = {
        _parse_trade_date(value)
        for value in frame["trade_date"].tolist()
    }
    return tuple(sorted(dates))


# Normalize a trading-calendar date from its provider representation.
def _parse_trade_date(value: object) -> date:
    if isinstance(value, date):
        return value
    normalized = str(value).strip()[:10].replace("/", "-")
    return date.fromisoformat(normalized)
