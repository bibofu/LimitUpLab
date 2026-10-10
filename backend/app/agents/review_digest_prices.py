"""Exact-session price facts shared by candidate and market-leader reviews."""

from decimal import Decimal
import math


def exact_price(bars, *, symbol, day, field):
    values = [getattr(bar, field, None) for bar in bars if bar.symbol == symbol and bar.trade_date == day]
    if not values:
        return None
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            if not math.isfinite(value) or value <= 0:
                return None
        except OverflowError:
            return None
    return values[0] if len(set(values)) == 1 else None


def next_open_fact(*, bars, symbol, first_board_date, trade_dates, end_date):
    calendar = sorted({day for day in trade_dates if day <= end_date})
    if first_board_date not in calendar:
        return None, None, ["首板交易日历未确认，无法确定次日开盘"]
    following = calendar[calendar.index(first_board_date) + 1:]
    if not following:
        return None, None, ["首板次日尚未到达或交易日历未覆盖"]
    next_day = following[0]
    first_close = exact_price(bars, symbol=symbol, day=first_board_date, field="close")
    next_open = exact_price(bars, symbol=symbol, day=next_day, field="open")
    missing = []
    if first_close is None:
        missing.append("首板精确收盘价缺失、冲突或非法，无法计算次日开盘涨幅")
    if next_open is None:
        missing.append("首板次日精确开盘价缺失、冲突或非法")
    if missing:
        return next_day, None, missing
    change = float((Decimal(str(next_open)) / Decimal(str(first_close)) - 1) * 100)
    if not math.isfinite(change):
        return next_day, None, ["首板次日开盘涨幅数值异常"]
    return next_day, change, []
