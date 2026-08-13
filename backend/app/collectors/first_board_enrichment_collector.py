"""External collectors used by the first-board enrichment pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import akshare as ak
import pandas as pd
import requests

from app.collectors.stock_kline_collector import _without_proxy


@dataclass(frozen=True)
class DragonTigerFact:
    """One stock's representative daily Dragon-Tiger List record."""

    symbol: str
    buy_amount: float | None
    sell_amount: float | None
    net_buy_amount: float | None
    float_market_cap: float | None
    reason: str | None


@dataclass(frozen=True)
class PopularityFact:
    """One Eastmoney popularity rank captured at a point in time."""

    symbol: str
    rank: int
    rank_change: int | None
    captured_at: datetime


def collect_dragon_tiger_facts(trade_date: date) -> dict[str, DragonTigerFact]:
    """Collect representative Dragon-Tiger List rows for one trading day."""

    value = trade_date.strftime("%Y%m%d")
    with _without_proxy():
        frame = ak.stock_lhb_detail_em(start_date=value, end_date=value)
    if frame.empty:
        return {}

    facts: dict[str, DragonTigerFact] = {}
    for symbol, rows in frame.groupby("代码"):
        representative = rows.loc[
            pd.to_numeric(rows["龙虎榜成交额"], errors="coerce").fillna(0).idxmax()
        ]
        facts[str(symbol).zfill(6)] = DragonTigerFact(
            symbol=str(symbol).zfill(6),
            buy_amount=_optional_float(representative.get("龙虎榜买入额")),
            sell_amount=_optional_float(representative.get("龙虎榜卖出额")),
            net_buy_amount=_optional_float(representative.get("龙虎榜净买额")),
            float_market_cap=_optional_float(representative.get("流通市值")),
            reason=_optional_text(representative.get("上榜原因")),
        )
    return facts


def collect_eastmoney_popularity() -> dict[str, PopularityFact]:
    """Collect the current Eastmoney A-share popularity Top100 snapshot."""

    captured_at = datetime.now(timezone.utc)
    payload = {
        "appId": "appId01",
        "globalId": "786e4c21-70dc-435a-93bb-38",
        "marketType": "",
        "pageNo": 1,
        "pageSize": 100,
    }
    with _without_proxy():
        response = requests.post(
            "https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
    rows = response.json().get("data") or []
    facts: dict[str, PopularityFact] = {}
    for row in rows:
        raw_symbol = str(row.get("sc") or "")
        symbol = raw_symbol[-6:]
        rank = _optional_int(row.get("rk"))
        if len(symbol) != 6 or rank is None:
            continue
        previous_rank = _optional_int(row.get("hisRc"))
        facts[symbol] = PopularityFact(
            symbol=symbol,
            rank=rank,
            rank_change=(previous_rank - rank) if previous_rank else None,
            captured_at=captured_at,
        )
    return facts


def collect_recent_listing_dates() -> dict[str, date]:
    """Collect listing dates available from Eastmoney's IPO history table."""

    with _without_proxy():
        frame = ak.stock_xgsglb_em(symbol="全部股票")
    if frame.empty:
        return {}
    result: dict[str, date] = {}
    for row in frame.to_dict("records"):
        symbol = str(row.get("股票代码") or "").zfill(6)
        listing_date = row.get("上市日期")
        if isinstance(listing_date, datetime):
            listing_date = listing_date.date()
        if isinstance(listing_date, date) and len(symbol) == 6:
            result[symbol] = listing_date
    return result


def collect_listing_date(symbol: str) -> date | None:
    """Collect one stock's authoritative listing date from CNInfo."""

    with _without_proxy():
        frame = ak.stock_profile_cninfo(symbol=symbol)
    if frame.empty:
        return None
    value = frame.iloc[0].get("上市日期")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def _optional_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else number


def _optional_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None
