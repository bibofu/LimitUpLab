import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from typing import Any

import akshare as ak

from app.models import StockIntradayKLineBar, StockKLineBar


def collect_stock_kline(
    symbol: str,
    days: int = 5,
    end_date: date | None = None,
) -> list[StockKLineBar]:
    normalized_symbol = _normalize_stock_symbol(symbol)
    target_end_date = end_date or date.today()
    start_date = target_end_date - timedelta(days=max(days * 3, 15))

    frame = ak.stock_zh_a_hist_tx(
        symbol=normalized_symbol,
        start_date=start_date.strftime("%Y%m%d"),
        end_date=target_end_date.strftime("%Y%m%d"),
        adjust="",
    )

    rows = frame.to_dict("records")[-days:]
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


def collect_stock_intraday_kline(
    symbol: str,
    trade_date: date,
    period: int = 5,
) -> list[StockIntradayKLineBar]:
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
    value = symbol.strip().lower()
    if value.startswith(("sh", "sz")):
        return value
    if not (len(value) == 6 and value.isdigit()):
        raise ValueError("stock symbol must be a 6-digit A-share code")
    market = "sh" if value.startswith(("5", "6", "9")) else "sz"
    return f"{market}{value}"


def _parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


@contextmanager
def _without_proxy() -> Iterator[None]:
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
