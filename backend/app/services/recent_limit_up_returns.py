"""Complete local candidate panels with deterministic, common-window returns."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from math import isfinite

from app.repositories.post_limit_repository import PostLimitDataset


# Same reviewed unadjusted sources as the single-stock K-line presentation.
# Keep services independent of agents, whose package initializer imports tools.
UNADJUSTED_SOURCES = {
    "hithink-finance.market.history.none", "hithink-finance.market.snapshot",
    "akshare.stock_zh_a_hist_tx", "tencent.qt.gtimg.cn",
}


@dataclass(frozen=True)
class RecentLimitUpReturn:
    """One auditable local candidate's return over the shared observation window."""

    symbol: str
    name: str
    return_pct: float
    start_date: str
    end_date: str
    start_close: float
    end_close: float
    window_start_date: str
    observed_intervals: int
    last_limit_date: str
    limit_up_count: int
    price_basis: str
    sources: list[str]


def build_recent_limit_up_returns(
    dataset: PostLimitDataset,
    *,
    end_date: date | None = None,
    recent_trade_days: int = 7,
    return_trade_days: int = 5,
) -> dict:
    """Return every calculable candidate; subsequent selection belongs to compute.

    The calendar is the union of local event and daily-bar dates, not proof of
    exchange-calendar completeness. Every stock uses the same required dates;
    missing observations never move its base date backwards.
    """
    for name, value, maximum in (("recent_trade_days", recent_trade_days, 20),
                                 ("return_trade_days", return_trade_days, 60)):
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"{name} must be an integer between 1 and {maximum}")
    if end_date is not None and type(end_date) is not date:
        raise ValueError("end_date must be a date")

    calendar = sorted(set(dataset.calendar) | set(dataset.event_dates))
    cutoff = end_date.isoformat() if end_date else (calendar[-1] if calendar else None)
    observed_days = [day for day in calendar if cutoff is not None and day <= cutoff]
    candidate_days = observed_days[-recent_trade_days:]
    return_days = observed_days[-return_trade_days - 1:]
    candidate_day_set = set(candidate_days)
    event_days = set(dataset.event_dates)
    missing = []
    if cutoff is None or cutoff not in calendar:
        missing.append(f"截止日 {cutoff or '未确定'} 没有本地日K观测，未回退到其他日期。")
    if len(candidate_days) < recent_trade_days:
        missing.append(f"候选期需要 {recent_trade_days} 个本地交易日，实际仅有 {len(candidate_days)} 个。")
    uncovered = [day for day in candidate_days if day not in event_days]
    if uncovered:
        missing.append("候选期涨停事件覆盖缺失：" + "、".join(uncovered))
    if len(return_days) < return_trade_days + 1:
        missing.append(f"收益计算需要 {return_trade_days + 1} 个共同收盘观测，实际仅有 {len(return_days)} 个。")

    # Count each stock/date once even if the supplied event snapshot has repeats.
    candidates: dict[str, dict] = {}
    for event in sorted(dataset.events, key=lambda row: str(row.get("trade_date", ""))):
        if event.get("trade_date") not in candidate_day_set or not event.get("closed_limit"):
            continue
        symbol = str(event.get("symbol") or "")
        if not symbol:
            missing.append("候选期有缺少股票代码的涨停事件，无法纳入计算。")
            continue
        candidate = candidates.setdefault(symbol, {"name": "", "dates": set()})
        candidate["name"] = event.get("name") or candidate["name"]
        candidate["dates"].add(event["trade_date"])

    bar_index = defaultdict(list)
    required_days = set(return_days)
    for bar in dataset.bars:
        if bar.get("symbol") in candidates and bar.get("trade_date") in required_days:
            bar_index[(bar["symbol"], bar["trade_date"])].append(bar)

    items, missing_items = [], []
    window_ready = cutoff in calendar and len(return_days) == return_trade_days + 1
    for symbol, candidate in sorted(candidates.items()):
        reasons = []
        window = []
        if not window_ready:
            reasons.append("共同收益计算窗口不足或截止日缺失。")
        else:
            for day in return_days:
                bars = bar_index[(symbol, day)]
                if not bars:
                    reasons.append(f"{day} 日K缺失。")
                elif len(bars) != 1:
                    reasons.append(f"{day} 日K重复，无法确定唯一收盘价。")
                else:
                    window.append(bars[0])
            for bar in window:
                close = bar.get("close")
                if type(close) not in (int, float) or not isfinite(close) or close <= 0:
                    reasons.append(f"{bar['trade_date']} 收盘价无效。")
                if bar.get("source") not in UNADJUSTED_SOURCES:
                    reasons.append(f"{bar['trade_date']} 价格来源未知或不属于已确认的未复权口径。")
        if reasons:
            missing_items.append({"symbol": symbol, "name": candidate["name"], "reasons": reasons})
            continue

        start_close, end_close = window[0]["close"], window[-1]["close"]
        # Decimal avoids admitting exactly 20% into a strict >20 filter because
        # of binary subtraction; retain the precision needed for close thresholds.
        change = (Decimal(str(end_close)) / Decimal(str(start_close)) - 1) * 100
        return_pct = float(change)
        if not isfinite(return_pct):
            missing_items.append({"symbol": symbol, "name": candidate["name"],
                                  "reasons": ["区间收益计算结果不是有限数值。"]})
            continue
        items.append(asdict(RecentLimitUpReturn(
            symbol=symbol, name=candidate["name"], return_pct=return_pct,
            start_date=return_days[0], end_date=cutoff,
            start_close=float(start_close), end_close=float(end_close),
            window_start_date=return_days[1], observed_intervals=return_trade_days,
            last_limit_date=max(candidate["dates"]),
            limit_up_count=len(candidate["dates"]),
            price_basis="unadjusted_close_prices",
            sources=sorted({bar["source"] for bar in window}),
        )))

    if missing_items:
        missing.append(f"{len(missing_items)} 只候选缺少完整、有效且口径一致的区间日K，未参与收益筛选；详情见逐股数据缺口。")
    return {
        "status": "partial" if missing else ("ok" if items else "empty"),
        "source": "derived.local_recent_limit_up_returns",
        "sources": sorted({source for item in items for source in item["sources"]}),
        "scope": "本地已记录的近期收盘涨停股票，按股票去重；不代表完整市场涨停池或全市场股票。",
        "end_date": cutoff,
        "available_data_as_of": observed_days[-1] if observed_days else None,
        "recent_trade_days": recent_trade_days,
        "return_trade_days": return_trade_days,
        "candidate_start_date": candidate_days[0] if candidate_days else None,
        "candidate_end_date": candidate_days[-1] if candidate_days else None,
        "candidate_trade_dates": candidate_days,
        "selected_trade_day_count": len(candidate_days),
        "return_trade_dates": return_days,
        "start_date": return_days[0] if window_ready else None,
        "window_start_date": return_days[1] if window_ready else None,
        "calendar_source": "local_observed_dates",
        "calendar_basis": "local_observed_dates",
        "calendar_verified": False,
        "calendar_note": "交易日历由本地日K与涨停事件的观测日期构成，未核验交易所日历的完整性。",
        "warnings": ["仅覆盖本地已记录的近期收盘涨停股票，不保证完整市场涨停池覆盖。",
                     "未独立核验交易所日历；收益窗口按本地共同观测日期计算。"],
        "price_basis": "unadjusted_close_prices",
        "return_definition": "(截止日未复权收盘价 / 基准日未复权收盘价 - 1) × 100；N日需要N+1个共同收盘观测，不是复权收益。",
        "candidate_count": len(candidates), "analyzed_count": len(items),
        "missing_count": len(missing_items), "matched_count": len(items),
        "returned_count": len(items), "source_truncated": False,
        "items": items, "missing_items": missing_items, "data_missing": missing,
    }
