"""On-demand industry-sector market data collectors backed by AKShare."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import akshare as ak

from app.collectors.network import without_proxy


@dataclass(frozen=True)
class SectorSpotRow:
    """Normalized latest industry-sector ranking row."""

    sector_name: str
    rank: int
    change_pct: float
    amount_yi: float | None
    net_inflow_yi: float | None
    up_count: int | None
    down_count: int | None
    leader_name: str | None
    leader_price: float | None
    leader_change_pct: float | None
    source: str


@dataclass(frozen=True)
class SectorDailyRow:
    """Normalized completed daily sector-index row."""

    trade_date: date
    close: float
    change_pct: float | None
    source: str


_SPOT_CACHE_TTL = timedelta(minutes=5)
_HISTORY_CACHE_TTL = timedelta(minutes=15)
_spot_cache: tuple[datetime, list[SectorSpotRow]] | None = None
_history_cache: dict[
    tuple[str, date, date], tuple[datetime, list[SectorDailyRow]]
] = {}
_cache_lock = threading.Lock()


def collect_sector_spot() -> list[SectorSpotRow]:
    """Collect the latest industry ranking with THS and Eastmoney fallback."""

    global _spot_cache
    now = datetime.now()
    with _cache_lock:
        if _spot_cache and now - _spot_cache[0] < _SPOT_CACHE_TTL:
            return list(_spot_cache[1])

    errors: list[str] = []
    for source, loader, normalizer in (
        ("akshare-ths-industry-summary", _load_ths_spot, _normalize_ths_spot),
        ("akshare-eastmoney-industry-spot", _load_eastmoney_spot, _normalize_em_spot),
    ):
        try:
            rows = normalizer(loader(), source)
            if not rows:
                raise ValueError("provider returned no sector rows")
            with _cache_lock:
                _spot_cache = (now, rows)
            return list(rows)
        except Exception as error:  # noqa: BLE001
            errors.append(f"{source}: {error}")
    raise RuntimeError("unable to collect sector spot data: " + "; ".join(errors))


def collect_sector_history(
    sector_name: str,
    start_date: date,
    end_date: date,
) -> list[SectorDailyRow]:
    """Collect completed sector daily history with a short in-process cache."""

    cache_key = (sector_name, start_date, end_date)
    now = datetime.now()
    with _cache_lock:
        cached = _history_cache.get(cache_key)
        if cached and now - cached[0] < _HISTORY_CACHE_TTL:
            return list(cached[1])

    errors: list[str] = []
    providers = (
        (
            "akshare-ths-industry-history",
            _load_ths_history,
            _normalize_ths_history,
        ),
        (
            "akshare-eastmoney-industry-history",
            _load_eastmoney_history,
            _normalize_em_history,
        ),
    )
    for source, loader, normalizer in providers:
        try:
            frame = loader(sector_name, start_date, end_date)
            rows = normalizer(frame, source)
            if not rows:
                raise ValueError("provider returned no history rows")
            with _cache_lock:
                _history_cache[cache_key] = (now, rows)
            return list(rows)
        except Exception as error:  # noqa: BLE001
            errors.append(f"{source}: {error}")
    raise RuntimeError(
        f"unable to collect sector history for {sector_name}: " + "; ".join(errors)
    )


def _load_ths_spot():
    with without_proxy():
        return ak.stock_board_industry_summary_ths()


def _load_eastmoney_spot():
    with without_proxy():
        return ak.stock_board_industry_name_em()


def _load_ths_history(sector_name: str, start_date: date, end_date: date):
    with without_proxy():
        return ak.stock_board_industry_index_ths(
            symbol=sector_name,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
        )


def _load_eastmoney_history(sector_name: str, start_date: date, end_date: date):
    with without_proxy():
        return ak.stock_board_industry_hist_em(
            symbol=sector_name,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            period="日k",
            adjust="",
        )


def _normalize_ths_spot(frame, source: str) -> list[SectorSpotRow]:
    return [
        SectorSpotRow(
            sector_name=str(row.get("板块") or "").strip(),
            rank=_integer(row.get("序号")) or index + 1,
            change_pct=_number(row.get("涨跌幅")) or 0.0,
            amount_yi=_number(row.get("总成交额")),
            net_inflow_yi=_number(row.get("净流入")),
            up_count=_integer(row.get("上涨家数")),
            down_count=_integer(row.get("下跌家数")),
            leader_name=_text(row.get("领涨股")),
            leader_price=_number(row.get("领涨股-最新价")),
            leader_change_pct=_number(row.get("领涨股-涨跌幅")),
            source=source,
        )
        for index, row in enumerate(frame.to_dict("records"))
        if str(row.get("板块") or "").strip()
    ]


def _normalize_em_spot(frame, source: str) -> list[SectorSpotRow]:
    return [
        SectorSpotRow(
            sector_name=str(row.get("板块名称") or "").strip(),
            rank=_integer(row.get("排名")) or index + 1,
            change_pct=_number(row.get("涨跌幅")) or 0.0,
            amount_yi=None,
            net_inflow_yi=None,
            up_count=_integer(row.get("上涨家数")),
            down_count=_integer(row.get("下跌家数")),
            leader_name=_text(row.get("领涨股票")),
            leader_price=None,
            leader_change_pct=_number(row.get("领涨股票-涨跌幅")),
            source=source,
        )
        for index, row in enumerate(frame.to_dict("records"))
        if str(row.get("板块名称") or "").strip()
    ]


def _normalize_ths_history(frame, source: str) -> list[SectorDailyRow]:
    records = frame.to_dict("records")
    closes = [_number(row.get("收盘价")) for row in records]
    rows: list[SectorDailyRow] = []
    for index, row in enumerate(records):
        close = closes[index]
        if close is None:
            continue
        previous = closes[index - 1] if index > 0 else None
        rows.append(
            SectorDailyRow(
                trade_date=_date_value(row.get("日期")),
                close=close,
                change_pct=_change_pct(close, previous),
                source=source,
            )
        )
    return sorted(rows, key=lambda item: item.trade_date)


def _normalize_em_history(frame, source: str) -> list[SectorDailyRow]:
    rows = [
        SectorDailyRow(
            trade_date=_date_value(row.get("日期")),
            close=_required_number(row.get("收盘")),
            change_pct=_number(row.get("涨跌幅")),
            source=source,
        )
        for row in frame.to_dict("records")
        if _number(row.get("收盘")) is not None
    ]
    return sorted(rows, key=lambda item: item.trade_date)


def _change_pct(current: float, previous: float | None) -> float | None:
    if previous is None or previous == 0:
        return None
    return round((current - previous) / previous * 100, 2)


def _date_value(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _number(value: Any) -> float | None:
    if value is None or str(value).strip() in {"", "-", "nan", "None"}:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _required_number(value: Any) -> float:
    number = _number(value)
    if number is None:
        raise ValueError(f"expected numeric value, got {value!r}")
    return number


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text and text != "-" else None
