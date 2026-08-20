"""AKShare-backed market index snapshots for dashboard context."""

from datetime import date, datetime, timedelta
from typing import Any

import akshare as ak
import requests

from app.collectors.network import without_proxy
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
    """Collect one date-aligned index snapshot with a provider fallback."""

    errors: list[str] = []
    providers = (
        ("tencent_index_daily_spot", _tencent_rows),
        ("eastmoney_index_daily", _eastmoney_rows),
        ("sina_index_daily", _sina_rows),
    )
    for source, loader in providers:
        try:
            rows = loader(akshare_symbol, trade_date)
            return _snapshot_from_rows(
                name=name,
                display_symbol=display_symbol,
                rows=rows,
                requested_trade_date=trade_date,
                source=source,
            )
        except Exception as error:
            errors.append(f"{source}: {error}")
    raise RuntimeError(
        f"unable to collect date-aligned index data for {display_symbol}: "
        + "; ".join(errors)
    )


def _eastmoney_rows(
    akshare_symbol: str,
    trade_date: date | None,
) -> list[dict[str, Any]]:
    """Fetch a compact recent window from Eastmoney's index daily endpoint."""

    end_date = trade_date or date.today()
    start_date = end_date - timedelta(days=30)
    with without_proxy():
        frame = ak.stock_zh_index_daily_em(
            symbol=akshare_symbol,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
        )
    return frame.to_dict("records")


def _sina_rows(
    akshare_symbol: str,
    trade_date: date | None,
) -> list[dict[str, Any]]:
    """Fetch Sina history as a fallback; date alignment is checked downstream."""

    del trade_date
    with without_proxy():
        frame = ak.stock_zh_index_daily(symbol=akshare_symbol)
    return frame.to_dict("records")


def _tencent_rows(
    akshare_symbol: str,
    trade_date: date | None,
) -> list[dict[str, Any]]:
    """Combine Tencent history with its completed-day spot snapshot."""

    end_date = trade_date or date.today()
    start_date = end_date - timedelta(days=30)
    with without_proxy():
        frame = ak.stock_zh_a_hist_tx(
            symbol=akshare_symbol,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=(end_date + timedelta(days=1)).strftime("%Y%m%d"),
            adjust="",
        )
        response = requests.get(
            f"https://qt.gtimg.cn/q={akshare_symbol}",
            headers={"Referer": "https://gu.qq.com/"},
            timeout=10,
        )
        response.raise_for_status()

    rows = frame.to_dict("records")
    spot = _parse_tencent_spot(response.content, end_date)
    if spot is not None:
        rows.append(spot)
    return rows


def _parse_tencent_spot(
    content: bytes,
    requested_trade_date: date,
) -> dict[str, Any] | None:
    """Parse one Tencent index quote only when it belongs to the requested day."""

    payload = content.decode("gbk", errors="replace")
    quote_start = payload.find('"')
    quote_end = payload.rfind('"')
    if quote_start < 0 or quote_end <= quote_start:
        return None
    fields = payload[quote_start + 1:quote_end].split("~")
    if len(fields) <= 30:
        return None
    try:
        quote_date = datetime.strptime(fields[30][:8], "%Y%m%d").date()
        close = float(fields[3])
    except (TypeError, ValueError):
        return None
    if quote_date != requested_trade_date or close <= 0:
        return None
    return {"date": quote_date, "close": close}


def _snapshot_from_rows(
    *,
    name: str,
    display_symbol: str,
    rows: list[dict[str, Any]],
    requested_trade_date: date | None,
    source: str,
) -> MarketIndexSnapshot:
    """Normalize rows and reject a stale snapshot for an explicit trade date."""

    rows_by_date = {
        normalized["date"]: normalized
        for normalized in (_normalize_row(row) for row in rows)
    }
    normalized = [rows_by_date[item] for item in sorted(rows_by_date)]
    if requested_trade_date is not None:
        normalized = [
            row for row in normalized if row["date"] <= requested_trade_date
        ]

    if len(normalized) < 2:
        raise ValueError(f"not enough index data for {display_symbol}")

    latest = normalized[-1]
    previous = normalized[-2]
    if (
        requested_trade_date is not None
        and latest["date"] != requested_trade_date
    ):
        raise ValueError(
            f"stale index data for {display_symbol}: latest={latest['date']}, "
            f"requested={requested_trade_date}"
        )
    latest_close = float(latest["close"])
    previous_close = float(previous["close"])
    change_pct = round((latest_close - previous_close) / previous_close * 100, 2)

    return MarketIndexSnapshot(
        name=name,
        symbol=display_symbol,
        trade_date=latest["date"],
        close=round(latest_close, 2),
        change_pct=change_pct,
        trend=[round(float(row["close"]), 2) for row in normalized[-5:]],
        source=source,
    )


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize AKShare index rows so `date` is always a `date` object."""

    return {**row, "date": _parse_date(row["date"])}


def _parse_date(value: Any) -> date:
    """Parse AKShare date values into `date`."""

    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
