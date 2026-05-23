"""AKShare-backed market index snapshots for dashboard context."""

from datetime import date, datetime, timedelta
from typing import Any

import akshare as ak

from app.models import MarketIndexSnapshot


INDEX_SPECS = (
    ("上证指数", "000001.SH", "sh000001"),
    ("深证成指", "399001.SZ", "sz399001"),
    ("创业板指", "399006.SZ", "sz399006"),
)

_CACHE_TTL = timedelta(minutes=15)
_cache: dict[date | None, tuple[datetime, list[MarketIndexSnapshot]]] = {}


def collect_market_indices(trade_date: date | None = None) -> list[MarketIndexSnapshot]:
    """Collect major index snapshots, cached briefly by requested trade date."""

    cached = _cache.get(trade_date)
    now = datetime.now()
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    indices = [
        _collect_market_index(
            name=name,
            display_symbol=display_symbol,
            akshare_symbol=akshare_symbol,
            trade_date=trade_date,
        )
        for name, display_symbol, akshare_symbol in INDEX_SPECS
    ]
    _cache[trade_date] = (now, indices)
    return indices


def _collect_market_index(
    name: str,
    display_symbol: str,
    akshare_symbol: str,
    trade_date: date | None,
) -> MarketIndexSnapshot:
    """Collect and normalize one index's latest close, change, and five-day trend."""

    frame = ak.stock_zh_index_daily(symbol=akshare_symbol)
    rows = frame.to_dict("records")
    if trade_date:
        rows = [
            _normalize_row(row)
            for row in rows
            if _parse_date(row["date"]) <= trade_date
        ]
    else:
        rows = [_normalize_row(row) for row in rows]

    if len(rows) < 2:
        raise ValueError(f"not enough index data for {display_symbol}")

    latest = rows[-1]
    previous = rows[-2]
    latest_close = float(latest["close"])
    previous_close = float(previous["close"])
    change_pct = round((latest_close - previous_close) / previous_close * 100, 2)

    return MarketIndexSnapshot(
        name=name,
        symbol=display_symbol,
        close=round(latest_close, 2),
        change_pct=change_pct,
        trend=[round(float(row["close"]), 2) for row in rows[-5:]],
    )


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize AKShare index rows so `date` is always a `date` object."""

    return {**row, "date": _parse_date(row["date"])}


def _parse_date(value: Any) -> date:
    """Parse AKShare date values into `date`."""

    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
