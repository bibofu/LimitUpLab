"""Collect completed A-share limit-down pools from Eastmoney through AKShare."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import akshare as ak

from app.collectors.network import without_proxy


@dataclass(frozen=True)
class LimitDownItem:
    """One stock that closed at its down-limit price."""

    symbol: str
    name: str
    change_pct: float | None
    industry: str | None


@dataclass(frozen=True)
class LimitDownSnapshot:
    """One completed trading day's limit-down pool."""

    trade_date: date
    items: list[LimitDownItem]
    source: str = "akshare-eastmoney-limit-down-pool"


_CACHE_TTL = timedelta(minutes=10)
_cache: dict[date, tuple[datetime, LimitDownSnapshot]] = {}
_cache_lock = threading.Lock()


def collect_limit_down_pool(trade_date: date) -> LimitDownSnapshot:
    """Return the requested date's completed limit-down pool with a short cache."""

    now = datetime.now()
    with _cache_lock:
        cached = _cache.get(trade_date)
        if cached and now - cached[0] < _CACHE_TTL:
            return cached[1]

    with without_proxy():
        frame = ak.stock_zt_pool_dtgc_em(date=trade_date.strftime("%Y%m%d"))
    items = [
        LimitDownItem(
            symbol=str(row.get("代码") or "").strip(),
            name=str(row.get("名称") or "").strip(),
            change_pct=_number(row.get("涨跌幅")),
            industry=_text(row.get("所属行业")),
        )
        for row in frame.to_dict("records")
        if _valid_symbol(row.get("代码")) and str(row.get("名称") or "").strip()
    ]
    snapshot = LimitDownSnapshot(trade_date=trade_date, items=items)
    with _cache_lock:
        _cache[trade_date] = (now, snapshot)
    return snapshot


def _valid_symbol(value: object) -> bool:
    symbol = str(value or "").strip()
    return len(symbol) == 6 and symbol.isdigit()


def _number(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
