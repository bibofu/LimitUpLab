"""Canonical source families for daily bars collected through equivalent endpoints."""


def daily_bar_source_family(source: str | None) -> str | None:
    """Group labels that originate from the same underlying market-data provider."""

    normalized = (source or "").strip().lower()
    if not normalized:
        return None
    if "stock_zh_a_hist_tx" in normalized or "qt.gtimg.cn" in normalized or "tencent" in normalized:
        return "tencent"
    return normalized
