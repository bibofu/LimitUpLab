"""Explicit price windows for Agent observations, separate from public metrics."""

from math import isfinite


PERIOD_FIELDS = ("return_5d_pct", "return_10d_pct", "return_20d_pct")
UNADJUSTED_SOURCES = {
    "hithink-finance.market.history.none", "hithink-finance.market.snapshot",
    "akshare.stock_zh_a_hist_tx", "tencent.qt.gtimg.cn",
}


def _window(rows):
    if len(rows) < 2:
        return None
    sources = {row.get("source") for row in rows}
    if (None in sources or "unknown" in sources or "" in sources
            or (len(sources) > 1 and not sources <= UNADJUSTED_SOURCES)):
        return None
    closes = [row.get("close") for row in rows]
    dates = [str(row.get("trade_date", "")) for row in rows]
    if (any(type(value) not in (int, float) or not isfinite(value) or value <= 0 for value in closes)
            or any(not day for day in dates) or dates != sorted(set(dates))):
        return None
    return {"start_date": dates[0], "end_date": dates[-1],
            "start_close": closes[0], "end_close": closes[-1],
            "observations": len(rows), "observed_intervals": len(rows) - 1,
            "return_pct": round((closes[-1] / closes[0] - 1) * 100, 3),
            "sources": sorted(sources), "price_basis": "as_returned_close_prices"}


def kline_agent_view(payload):
    """Only expose a return with the actual visible observations proving its scope."""
    bars = payload.get("bars", [])
    windows = []
    for intervals in (5, 10, 20):
        if len(bars) >= intervals + 1:
            window = _window(bars[-intervals - 1:])
            if window is not None:
                windows.append(window)
    return {**{key: value for key, value in payload.items() if key not in PERIOD_FIELDS},
            "display_window_return": _window(bars), "return_windows": windows}
