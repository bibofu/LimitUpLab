"""Exact-session performance and denominators for a fixed prediction cohort."""

from datetime import date
from statistics import mean, median
from typing import Iterable

from app.models import ReviewAgentPick


def build_horizon_coverage(picks: list[ReviewAgentPick], *, trade_dates: Iterable[date],
                           as_of_date: date, follow_days: int = 5) -> dict:
    calendar = sorted({day for day in trade_dates if day <= as_of_date})
    offsets = {day: index for index, day in enumerate(calendar)}
    horizons = [horizon for horizon in (1, 3, 5) if horizon <= follow_days]

    def summarize(selected, horizon):
        values = []
        mature = missing = immature = unknown = 0
        for pick in selected:
            if pick.trade_date not in offsets:
                unknown += 1
                continue
            target_index = offsets[pick.trade_date] + horizon
            if target_index >= len(calendar):
                immature += 1
                continue
            mature += 1
            target = calendar[target_index]
            observed = [bar.return_from_base_pct for bar in pick.post_bars
                        if bar.trade_date == target and bar.trading_day_offset == horizon
                        and bar.return_from_base_pct is not None]
            if len(observed) != 1:
                missing += 1
            else:
                values.append(observed[0])
        return {
            "sample_size": len(selected), "mature_count": mature,
            "available_count": len(values), "missing_count": missing,
            "not_mature_count": immature, "calendar_unknown_count": unknown,
            "mean_close_return_pct": mean(values) if values else None,
            "median_close_return_pct": median(values) if values else None,
            "positive_count": sum(value > 0 for value in values),
            "flat_count": sum(value == 0 for value in values),
            "negative_count": sum(value < 0 for value in values),
        }

    daily = [{
        "trade_date": day.isoformat(),
        "horizons": {f"D+{horizon}": summarize(
            [pick for pick in picks if pick.trade_date == day], horizon,
        ) for horizon in horizons},
    } for day in sorted({pick.trade_date for pick in picks})]
    cohort_summary = {}
    for horizon in horizons:
        key = f"D+{horizon}"
        groups = {"positive_mean_dates": [], "flat_mean_dates": [],
                  "negative_mean_dates": [], "unavailable_mean_dates": []}
        for batch in daily:
            value = batch["horizons"][key]["mean_close_return_pct"]
            group = ("unavailable_mean_dates" if value is None else "positive_mean_dates" if value > 0
                     else "negative_mean_dates" if value < 0 else "flat_mean_dates")
            groups[group].append(batch["trade_date"])
        cohort_summary[key] = {"cohort_count": len(daily), **groups}
    return {
        "as_of_date": as_of_date.isoformat(),
        "requested_follow_days": follow_days,
        "definition": "D+N指首板后第N个已核验交易日；收益=(该日收盘/首板收盘-1)×100，单位%。",
        "denominator": "均值、中位数及正平负计数只使用精确D+N及首板收盘均有效的available_count；成熟缺价与未成熟分开。",
        "horizons": {f"D+{horizon}": summarize(picks, horizon) for horizon in horizons},
        "daily": daily,
        "daily_mean_groups": cohort_summary,
    }
