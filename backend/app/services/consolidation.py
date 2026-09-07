"""Deterministic post-limit consolidation screening, without future outcomes."""
from collections import Counter
from datetime import date, datetime, time, timedelta
import math
from statistics import mean
from zoneinfo import ZoneInfo

from app.consolidation_models import ConsolidationCandidate, ConsolidationPool

SHANGHAI = ZoneInfo("Asia/Shanghai")
RULES = [
    "沪深主板；按涨停事件名称排除 ST 与退市标记。",
    "最近 5 个交易日有收盘涨停，以最近一次为锚点；整理 2–4 个交易日。",
    "涨停后最高价 ÷ 最低价 − 1 ≤ 8%。",
    "最新收盘相对涨停日收盘在 −10% 至 +8% 之间。",
    "整理期日均成交量 ÷ 涨停日成交量 ≤ 0.75，且量价来源标签一致。",
    "连续 20 个市场交易日量价历史中的 OHLC 完整，剔除明显价格断点及锚点不一致。",
]
WARNINGS = [
    "仅使用已结束交易日的数据重算观察池，不代表当时发布过盘前终选，也不进入一进二前向统计。",
    "交易日来自本地日K日期并集，涨停事件缺席不等于已独立核验全市场无涨停。",
    "来源标签一致不保证成交量单位一致；未复权价格与证券状态仍需独立核验。",
    "符合形态不代表后续表现为正；当前盈利优势尚未稳定验证。",
]


def completed_date_limit(now: datetime) -> date:
    local = now.astimezone(SHANGHAI)
    return local.date() if local.time() >= time(15, 30) else local.date() - timedelta(days=1)


def valid_bar(bar: dict | None) -> bool:
    return bool(bar and all(isinstance(bar.get(k), (int, float)) and
                           math.isfinite(bar[k]) and bar[k] > 0
                           for k in ("open", "high", "low", "close")) and
                bar["low"] <= min(bar["open"], bar["close"]) <=
                max(bar["open"], bar["close"]) <= bar["high"])


def assess(symbol: str, anchor: str, end: str, calendar: list[str], bars: dict):
    """Return one exclusion or quantitative evidence for an observed window."""
    a, t = calendar.index(anchor), calendar.index(end)
    if not 2 <= t - a <= 4:
        return "age_outside_2_4", None
    history = [bars.get((symbol, d)) for d in calendar[max(0, t-19):t+1]]
    if len(history) != 20 or not all(valid_bar(b) for b in history):
        return "missing_history20", None
    if any(abs(b["close"]/p["close"]-1) > .111 or b["high"]/p["close"]-1 > .112
           or b["low"]/p["close"]-1 < -.112 for p, b in zip(history, history[1:])):
        return "price_discontinuity", None
    window = [bars[(symbol, d)] for d in calendar[a:t+1]]
    previous = bars.get((symbol, calendar[a-1])) if a else None
    if not valid_bar(previous) or not .085 <= window[0]["close"]/previous["close"]-1 <= .111:
        return "anchor_price_mismatch", None
    if not all(isinstance(b.get("volume"), (int, float)) and math.isfinite(b["volume"])
               and b["volume"] > 0 for b in window):
        return "invalid_volume", None
    sources = {b.get("source") for b in window}
    if len(sources) != 1 or not next(iter(sources)):
        return "mixed_or_missing_source", None
    low, high = min(b["low"] for b in window[1:]), max(b["high"] for b in window[1:])
    relative = window[-1]["close"]/window[0]["close"]-1
    ratio = mean(b["volume"] for b in window[1:])/window[0]["volume"]
    if high/low-1 > .08 + 1e-12:
        return "range_above_8pct", None
    if not -.10 - 1e-12 <= relative <= .08 + 1e-12:
        return "close_outside_band", None
    if ratio > .75 + 1e-12:
        return "volume_above_075", None
    return None, dict(consolidation_days=t-a, anchor_close=window[0]["close"],
                      close=window[-1]["close"], range_low=low, range_high=high,
                      range_pct=round((high/low-1)*100, 4),
                      anchor_change_pct=round(relative*100, 4),
                      volume_ratio=round(ratio, 6), source=window[0]["source"])


def screen_consolidation(events: list[dict], rows: list[dict], calendar: list[str],
                         as_of: date, now: datetime) -> ConsolidationPool:
    end = as_of.isoformat()
    # Do not rely on callers to remove intraday/future features.
    dates = sorted({d for d in calendar if d <= end})
    result = ConsolidationPool(generated_at=now, data_as_of=as_of, rules=RULES, warnings=WARNINGS)
    if as_of > completed_date_limit(now):
        raise ValueError("只能使用已完成收盘的数据，当前交易日须在 15:30 后查询。")
    if end not in dates or len(dates) < 20:
        result.data_missing = ["market_history20"]
        return result
    recent_dates = set(dates[-5:])
    visible_events = [e for e in events if e["trade_date"] <= end]
    if recent_dates - {e["trade_date"] for e in visible_events}:
        result.data_missing = ["recent_event_dates"]
        return result
    latest = {}
    for event in sorted(visible_events, key=lambda e:e["trade_date"]):
        if event["trade_date"] in recent_dates and event["closed_limit"]:
            latest[event["symbol"]] = event
    bars = {(r["symbol"], r["trade_date"]):r for r in rows if r["trade_date"] <= end}
    result.pool_count = len(latest)
    exclusions = Counter()
    for symbol, event in sorted(latest.items()):
        if not symbol.startswith(("000", "001", "002", "003", "600", "601", "603", "605")) or "ST" in event["name"].upper() or "退" in event["name"]:
            exclusions["unsupported_security"] += 1
            continue
        reason, facts = assess(symbol, event["trade_date"], end, dates, bars)
        if reason:
            exclusions[reason] += 1
            continue
        first = end
        a = dates.index(event["trade_date"])
        for earlier in dates[a+2:dates.index(end)]:
            if assess(symbol, event["trade_date"], earlier, dates, bars)[0] is None:
                first = earlier
                break
        result.candidates.append(ConsolidationCandidate(
            symbol=symbol, name=event["name"], anchor_date=event["trade_date"],
            confirmed_date=first, state="new" if first == end else "watching", **facts,
            reasons=[f"涨停后整理 {facts['consolidation_days']} 日，区间幅度 {facts['range_pct']:.2f}%",
                     f"相对涨停收盘 {facts['anchor_change_pct']:+.2f}%，整理期量比 {facts['volume_ratio']:.3f}"],
            risks=["整理形态可能失效；缩量不能直接证明抛压消失。",
                   "行情为本地未复权缓存，成交量单位及当前证券状态尚未独立核验。"],
        ))
    result.candidates.sort(key=lambda c:(-c.confirmed_date.toordinal(), c.symbol))
    result.exclusions = dict(sorted(exclusions.items()))
    quality_keys = {"missing_history20", "price_discontinuity", "anchor_price_mismatch", "invalid_volume", "mixed_or_missing_source"}
    result.evaluated_count = result.pool_count - sum(v for k,v in exclusions.items() if k in quality_keys | {"unsupported_security", "age_outside_2_4"})
    result.data_missing = sorted(k for k in quality_keys if exclusions[k])
    result.status = "ready" if result.candidates else "data_missing" if result.data_missing else "empty"
    return result
