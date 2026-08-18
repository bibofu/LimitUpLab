"""AKShare-backed stock K-line collectors for after-close review pages."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from typing import Any

import akshare as ak
import requests

from app.models import StockCloseSnapshot, StockIntradayKLineBar, StockKLineBar


def collect_stock_kline(
    symbol: str,
    days: int = 5,
    end_date: date | None = None,
) -> list[StockKLineBar]:
    """Collect recent daily K-line bars ending at the latest persisted trade date."""

    normalized_symbol = _normalize_stock_symbol(symbol)
    target_end_date = end_date or date.today()
    start_date = target_end_date - timedelta(days=max(days * 3, 15))

    with _without_proxy():
        frame = ak.stock_zh_a_hist_tx(
            symbol=normalized_symbol,
            start_date=start_date.strftime("%Y%m%d"),
            # Tencent treats end_date as an exclusive boundary.
            end_date=(target_end_date + timedelta(days=1)).strftime("%Y%m%d"),
            adjust="",
        )

    rows = [
        row
        for row in frame.to_dict("records")
        if _parse_date(row["date"]) <= target_end_date
    ][-days:]
    return [
        StockKLineBar(
            trade_date=_parse_date(row["date"]),
            open=round(float(row["open"]), 2),
            close=round(float(row["close"]), 2),
            high=round(float(row["high"]), 2),
            low=round(float(row["low"]), 2),
            volume=float(row["amount"]),
        )
        for row in rows
    ]


def collect_stock_spot_klines(
    symbols: list[str],
    trade_date: date,
) -> dict[str, StockKLineBar]:
    """Collect one completed trading day's OHLC snapshot for several stocks."""

    normalized = sorted({_normalize_stock_symbol(symbol) for symbol in symbols})
    if not normalized:
        return {}

    with _without_proxy():
        response = requests.get(
            f"https://qt.gtimg.cn/q={','.join(normalized)}",
            headers={"Referer": "https://gu.qq.com/"},
            timeout=10,
        )
        response.raise_for_status()

    result: dict[str, StockKLineBar] = {}
    payload = response.content.decode("gbk", errors="replace")
    for line in payload.splitlines():
        parsed = _parse_tencent_spot_line(line, trade_date)
        if parsed is None:
            continue
        symbol, bar = parsed
        result[symbol] = bar
    return result



def collect_stock_close_snapshot(
    symbol: str,
    end_date: date | None = None,
) -> StockCloseSnapshot:
    """Collect the latest available close snapshot from recent daily K-line bars."""

    bars = collect_stock_kline(symbol=symbol, days=2, end_date=end_date)
    return build_stock_close_snapshot(
        symbol=symbol,
        bars=bars,
        source="akshare.stock_zh_a_hist_tx",
    )

def build_stock_close_snapshot(
    symbol: str,
    bars: list[StockKLineBar],
    source: str,
) -> StockCloseSnapshot:
    """Build a latest close snapshot from ordered daily K-line bars."""

    if not bars:
        raise ValueError("stock close data is empty")

    latest_bar = bars[-1]
    previous_bar = bars[-2] if len(bars) >= 2 else None
    previous_close = previous_bar.close if previous_bar else None
    change = (
        round(latest_bar.close - previous_close, 2)
        if previous_close is not None
        else None
    )
    change_pct = (
        round((latest_bar.close - previous_close) / previous_close * 100, 2)
        if previous_close not in (None, 0)
        else None
    )

    return StockCloseSnapshot(
        symbol=symbol,
        trade_date=latest_bar.trade_date,
        close=latest_bar.close,
        previous_close=previous_close,
        change=change,
        change_pct=change_pct,
        volume=latest_bar.volume,
        source=source,
    )
def collect_stock_intraday_kline(
    symbol: str,
    trade_date: date,
    period: int = 5,
) -> list[StockIntradayKLineBar]:
    """Collect intraday K-line bars for reviewing one completed trading day.

    Sina is tried first because it exposes period-based minute bars directly.
    Eastmoney is used as a fallback and then aggregated to the requested period.
    """

    normalized_symbol = _normalize_stock_symbol(symbol)
    rows = _collect_intraday_rows_from_sina(normalized_symbol, trade_date, period)
    if rows:
        return rows

    eastmoney_symbol = normalized_symbol.removeprefix("sh").removeprefix("sz")
    with _without_proxy():
        frame = ak.stock_zh_a_hist_pre_min_em(
            symbol=eastmoney_symbol,
            start_time="09:30:00",
            end_time="15:00:00",
        )

    rows = [
        row
        for row in _intraday_rows_from_frame(frame)
        if row.timestamp.date() == trade_date
    ]
    return _aggregate_intraday_rows(rows, period=period)


def _collect_intraday_rows_from_sina(
    symbol: str,
    trade_date: date,
    period: int,
) -> list[StockIntradayKLineBar]:
    """Read minute bars from Sina and keep only the requested trading date."""

    with _without_proxy():
        frame = ak.stock_zh_a_minute(
            symbol=symbol,
            period=str(period),
            adjust="",
        )

    return [
        StockIntradayKLineBar(
            timestamp=_parse_datetime(row["day"]),
            open=round(float(row["open"]), 2),
            close=round(float(row["close"]), 2),
            high=round(float(row["high"]), 2),
            low=round(float(row["low"]), 2),
            volume=float(row["volume"]),
            amount=float(row["amount"]),
        )
        for row in frame.to_dict("records")
        if _parse_datetime(row["day"]).date() == trade_date
    ]


def _intraday_rows_from_frame(frame: Any) -> list[StockIntradayKLineBar]:
    """Convert Eastmoney minute K-line rows into internal intraday bar models."""

    return [
        StockIntradayKLineBar(
            timestamp=_parse_datetime(row["时间"]),
            open=round(float(row["开盘"]), 2),
            close=round(float(row["收盘"]), 2),
            high=round(float(row["最高"]), 2),
            low=round(float(row["最低"]), 2),
            volume=float(row["成交量"]),
            amount=float(row["成交额"]),
        )
        for row in frame.to_dict("records")
    ]


def _aggregate_intraday_rows(
    rows: list[StockIntradayKLineBar],
    period: int,
) -> list[StockIntradayKLineBar]:
    """Aggregate 1-minute rows into N-minute OHLCV bars."""

    if period <= 1:
        return rows

    aggregated: list[StockIntradayKLineBar] = []
    for start in range(0, len(rows), period):
        group = rows[start:start + period]
        if len(group) == 0:
            continue
        aggregated.append(
            StockIntradayKLineBar(
                timestamp=group[-1].timestamp,
                open=group[0].open,
                close=group[-1].close,
                high=max(row.high for row in group),
                low=min(row.low for row in group),
                volume=sum(row.volume for row in group),
                amount=sum(row.amount for row in group),
            )
        )
    return aggregated


def _normalize_stock_symbol(symbol: str) -> str:
    """Normalize a 6-digit A-share code to AKShare's `sh`/`sz` symbol format."""

    value = symbol.strip().lower()
    if value.startswith(("sh", "sz")):
        return value
    if not (len(value) == 6 and value.isdigit()):
        raise ValueError("stock symbol must be a 6-digit A-share code")
    market = "sh" if value.startswith(("5", "6", "9")) else "sz"
    return f"{market}{value}"


def _parse_tencent_spot_line(
    line: str,
    expected_date: date,
) -> tuple[str, StockKLineBar] | None:
    """Parse one Tencent quote line when it belongs to the expected trade date."""

    quote_start = line.find('"')
    quote_end = line.rfind('"')
    if quote_start < 0 or quote_end <= quote_start:
        return None
    fields = line[quote_start + 1:quote_end].split("~")
    if len(fields) <= 34:
        return None

    symbol = fields[2].strip().zfill(6)
    timestamp = fields[30].strip()
    if len(symbol) != 6 or len(timestamp) < 8:
        return None
    try:
        quote_date = datetime.strptime(timestamp[:8], "%Y%m%d").date()
        close = float(fields[3])
        open_price = float(fields[5])
        high = float(fields[33])
        low = float(fields[34])
        volume = float(fields[6])
    except (TypeError, ValueError):
        return None
    if quote_date != expected_date or min(open_price, high, low, close) <= 0:
        return None
    return (
        symbol,
        StockKLineBar(
            trade_date=quote_date,
            open=round(open_price, 2),
            high=round(high, 2),
            low=round(low, 2),
            close=round(close, 2),
            volume=volume,
        ),
    )


def _parse_date(value: Any) -> date:
    """Parse collector date values into `date`."""

    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _parse_datetime(value: Any) -> datetime:
    """Parse collector timestamp values into `datetime`."""

    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


@contextmanager
def _without_proxy() -> Iterator[None]:
    """Temporarily clear proxy env vars for data sources that reject the proxy."""

    proxy_names = (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    )
    previous = {name: os.environ.get(name) for name in proxy_names}
    try:
        for name in proxy_names:
            os.environ.pop(name, None)
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value



