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
    dates = [str(row.get("trade_date") or "") for row in rows]
    if (any(type(value) not in (int, float) or not isfinite(value) or value <= 0 for value in closes)
            or any(not day for day in dates) or dates != sorted(set(dates))):
        return None
    return {"start_date": dates[0], "end_date": dates[-1],
            "start_close": closes[0], "end_close": closes[-1],
            "observations": len(rows), "observed_intervals": len(rows) - 1,
            "return_pct": round((closes[-1] / closes[0] - 1) * 100, 3),
            "sources": sorted(sources), "price_basis": "as_returned_close_prices"}


def _drawdown(rows, *, peak_field, trough_field):
    """Keep the running peak in time order, never pair a later peak with an earlier low."""
    if any(type(row.get(field)) not in (int, float)
           or not isfinite(row[field]) or row[field] <= 0
           for row in rows for field in (peak_field, trough_field)):
        return None
    peak = rows[0]
    peak_at_loss = peak
    trough = peak
    worst = 0.0
    for row in rows:
        if row[peak_field] > peak[peak_field]:
            peak = row
        loss = (row[trough_field] / peak[peak_field] - 1) * 100
        if loss < worst:
            worst, peak_at_loss, trough = loss, peak, row
    return {
        "start_date": str(rows[0]["trade_date"]), "end_date": str(rows[-1]["trade_date"]),
        "observations": len(rows), "drawdown_pct": round(worst, 3),
        "peak_date": str(peak_at_loss["trade_date"]), "peak_price": peak_at_loss[peak_field],
        "trough_date": str(trough["trade_date"]), "trough_price": trough[trough_field],
        "peak_field": peak_field, "trough_field": trough_field,
        "price_basis": "as_returned_prices",
    }


def kline_agent_view(payload):
    """Only expose a return with the actual visible observations proving its scope."""
    bars = payload.get("bars", [])
    windows = []
    for intervals in (5, 10, 20):
        if len(bars) >= intervals + 1:
            window = _window(bars[-intervals - 1:])
            if window is not None:
                windows.append(window)
    window = _window(bars)
    close_drawdown = _drawdown(bars, peak_field="close", trough_field="close") if window else None
    high_low_drawdown = _drawdown(bars, peak_field="high", trough_field="low") if window else None
    missing = list(payload.get("data_missing") or [])
    for key, value in (("close_max_drawdown", close_drawdown), ("high_low_max_drawdown", high_low_drawdown)):
        if value is None and key not in missing:
            missing.append(key)
    return {**{key: value for key, value in payload.items() if key not in PERIOD_FIELDS},
            "display_window_return": window, "return_windows": windows,
            "close_max_drawdown": close_drawdown, "high_low_max_drawdown": high_low_drawdown,
            # This view validates the supplied observations, not the exchange's
            # trading calendar. Row counts and freshness never prove no gaps.
            "calendar_coverage_status": "unverified",
            "data_missing": missing}
