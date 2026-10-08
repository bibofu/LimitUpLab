"""Load verified calendar coverage before calculating daily promotion rates."""

from collections.abc import Iterable
from datetime import date

from app.models import DailyBoardPromotionReport, LimitUpEvent
from app.services.analysis import calculate_daily_board_promotion
from app.services.promotion_calendar import load_promotion_calendar


def build_daily_promotion_report(
    events: list[LimitUpEvent],
    *,
    days: int = 5,
    end_date: date | None = None,
    trade_dates: Iterable[date] | None = None,
) -> DailyBoardPromotionReport:
    """Keep unavailable sessions distinct from observed zero promotions."""
    observed = sorted({e.trade_date for e in events if end_date is None or e.trade_date <= end_date})
    if not observed:
        return DailyBoardPromotionReport(warnings=["暂无收盘事件数据，无法计算晋级率。"])
    warnings: list[str] = []
    if end_date is not None and end_date > observed[-1]:
        warnings.append(
            f"请求截至 {end_date.isoformat()}，本地收盘事件仅至 {observed[-1].isoformat()}；"
            "后续交易日的晋级结果尚未确认。"
        )
    calendar_warnings: tuple[str, ...] = ()
    if trade_dates is None:
        calendar = load_promotion_calendar(observed[0], observed[-1])
        dates = list(calendar.trade_dates)
        calendar_warnings = calendar.warnings
        warnings.extend(calendar_warnings)
    else:
        dates = sorted(set(trade_dates))
    dates = [d for d in dates if observed[0] <= d <= observed[-1]]
    if len(observed) > 1 and not dates and not calendar_warnings:
        warnings.append("交易日历未确认，暂不计算晋级率。")
    # Report gaps in the requested recent window, including either side of a cohort.
    missing = [d for d in dates[-(max(days, 1) + 1):] if d not in observed]
    if missing:
        warnings.append("晋级率缺少交易日收盘事件数据：" + "、".join(d.isoformat() for d in missing) + "；涉及这些日期的晋级率未计算。")
    items = calculate_daily_board_promotion(events, days=days, end_date=end_date, trade_dates=dates)
    if not items and not warnings:
        warnings.append("相邻交易日收盘数据不足，暂时无法计算晋级率。")
    return DailyBoardPromotionReport(items=items, latest_event_date=observed[-1], warnings=warnings)
