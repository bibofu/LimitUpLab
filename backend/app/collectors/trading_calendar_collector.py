"""A-share trading calendar collector used by scheduled after-close jobs."""

from __future__ import annotations

from datetime import date

import akshare as ak

from app.collectors.network import without_proxy


def collect_a_share_trade_dates(start_date: date, end_date: date) -> list[date]:
    """Return exchange trading dates in the inclusive date range."""

    with without_proxy():
        frame = ak.tool_trade_date_hist_sina()
    if frame is None or frame.empty or "trade_date" not in frame.columns:
        raise RuntimeError("A-share trading calendar returned no rows")

    dates = {
        _parse_trade_date(value)
        for value in frame["trade_date"].tolist()
    }
    return sorted(item for item in dates if start_date <= item <= end_date)


def _parse_trade_date(value: object) -> date:
    if isinstance(value, date):
        return value
    normalized = str(value).strip()[:10].replace("/", "-")
    return date.fromisoformat(normalized)
