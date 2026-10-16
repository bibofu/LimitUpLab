"""Collect complete limit-down membership from Tonghuashun, with Eastmoney backup."""

from __future__ import annotations

import threading
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import akshare as ak

from app.collectors.network import without_proxy
from app.collectors.hithink_finance_collector import HithinkFinanceCollector
from app.collectors.process_timeout import run_in_killable_process


logger = logging.getLogger(__name__)


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
_SUPPLEMENT_TIMEOUT_SECONDS = 5.0
_cache: dict[date, tuple[datetime, LimitDownSnapshot]] = {}
_cache_lock = threading.Lock()


def collect_limit_down_pool(trade_date: date) -> LimitDownSnapshot:
    """Return the requested date's completed limit-down pool with a short cache."""

    now = datetime.now()
    with _cache_lock:
        cached = _cache.get(trade_date)
        if cached and now - cached[0] < _CACHE_TTL:
            return cached[1]

    try:
        preferred = HithinkFinanceCollector().collect_all_limit_down_pool(trade_date=trade_date)
    except Exception as error:  # noqa: BLE001 - provider boundary owns fallback
        logger.warning("Tonghuashun limit-down pool failed; using Eastmoney: %s", error)
        snapshot = LimitDownSnapshot(trade_date=trade_date, items=_eastmoney_items(trade_date))
    else:
        supplements: dict[str, LimitDownItem] = {}
        if preferred.items:
            try:
                supplements = {
                    item.symbol: item for item in run_in_killable_process(
                        _eastmoney_items, trade_date, timeout_seconds=_SUPPLEMENT_TIMEOUT_SECONDS,
                    )
                }
            except Exception as error:  # noqa: BLE001 - optional field supplement
                logger.warning("Eastmoney limit-down industry supplement unavailable: %s", error)
        supplemented = any(
            item.symbol in supplements and supplements[item.symbol].industry is not None
            for item in preferred.items
        )
        snapshot = LimitDownSnapshot(
            trade_date=trade_date,
            items=[LimitDownItem(
                symbol=item.symbol, name=item.name, change_pct=item.change_pct,
                industry=supplements[item.symbol].industry if item.symbol in supplements else None,
            ) for item in preferred.items],
            source="hithink-finance+eastmoney-industry" if supplemented else "hithink-finance",
        )
    with _cache_lock:
        _cache[trade_date] = (now, snapshot)
    return snapshot


def _eastmoney_items(trade_date: date) -> list[LimitDownItem]:
    with without_proxy():
        frame = ak.stock_zt_pool_dtgc_em(date=trade_date.strftime("%Y%m%d"))
    return [
        LimitDownItem(
            symbol=str(row.get("代码") or "").strip(),
            name=str(row.get("名称") or "").strip(),
            change_pct=_number(row.get("涨跌幅")),
            industry=_text(row.get("所属行业")),
        )
        for row in frame.to_dict("records")
        if _valid_symbol(row.get("代码")) and str(row.get("名称") or "").strip()
    ]


# Check that the source value is a supported stock-code representation.
def _valid_symbol(value: object) -> bool:
    symbol = str(value or "").strip()
    return len(symbol) == 6 and symbol.isdigit()


# Parse a numeric provider/report field; an unparseable value follows the explicit missing-value
# branch below.
# A None result represents the unavailable or inapplicable branch; callers must check it before
# using the value.
def _number(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# Normalize the source value into the text representation used by this boundary.
def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
