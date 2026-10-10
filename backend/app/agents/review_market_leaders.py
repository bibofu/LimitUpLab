"""Local, calendar-aligned first-board cohorts and later three-board episodes."""

from collections import Counter, defaultdict
from datetime import date, time
import math
from typing import Any, Iterable

from app.models import LimitUpEvent
from app.services.stock_position import classify_stock_position


def build_market_leader_profiles(
    *,
    events: list[LimitUpEvent],
    repository,
    start_date: date,
    end_date: date,
    trade_dates: Iterable[date],
) -> dict[str, Any]:
    """Compare all observed first boards in the supplied window, never only winners.

    The caller supplies verified exchange dates. No collector, model, prediction
    mutation or outcome backfill is invoked here.
    """
    calendar = sorted({day for day in trade_dates if day <= end_date})
    indices = {day: index for index, day in enumerate(calendar)}
    by_case = {
        (event.trade_date, event.symbol): event
        for event in events if event.trade_date <= end_date
    }
    covered_dates = {day for day, _ in by_case}
    cohort = sorted(
        (event for event in by_case.values()
         if start_date <= event.trade_date <= end_date
         and event.closed_limit and event.board_height == 1),
        key=lambda event: (event.trade_date, event.symbol),
    )
    outcomes: dict[tuple[date, str], str] = {}
    unknown_reasons: Counter[str] = Counter()
    for event in cohort:
        outcome, reason = _cohort_outcome(event, calendar, indices, covered_dates, by_case)
        outcomes[(event.trade_date, event.symbol)] = outcome
        if reason:
            unknown_reasons[reason] += 1

    leaders = _leader_episodes(by_case, calendar, indices, start_date, end_date)
    feature_events = {
        (event.trade_date, event.symbol): event for event in cohort
        if outcomes[(event.trade_date, event.symbol)] != "unknown"
    }
    for leader in leaders:
        if (anchor := leader["first_board_date"]) is not None:
            feature_events[(anchor, leader["symbol"])] = by_case[(anchor, leader["symbol"])]
    profiles = _build_profiles(feature_events, repository, end_date)
    for leader in leaders:
        profile = profiles.get((leader["first_board_date"], leader["symbol"]))
        if profile:
            leader.update({key: profile[key] for key in (
                "position_label", "position_source", "float_market_cap",
                "float_market_cap_source", "first_limit_time", "break_count", "turnover_rate",
            )})
            leader["data_missing"] = sorted(set(leader["data_missing"] + profile["data_missing"]))

    notes = [
        "对照覆盖观察期内本地全部收盘首板，按同一轮D+1二板、D+2三板分组；不是涨跌收益分组。",
        "有事件记录的交易日仍不保证全市场采集完整；完整观察日缺个股封板记录按未连续晋级处理。",
        "仅使用截至首板日的特征；缺少首板精确日期市值快照不以当前市值或估算值补齐。",
        "这是事后描述性对比，不能由赢家特征推出选股规律；不同板块的涨跌幅限制可能不同。",
    ]
    if unknown_reasons:
        labels = {
            "calendar_missing": "基准交易日历缺失",
            "followup_unavailable": "D+2尚未到达或日历未覆盖",
            "event_date_missing": "后续整日事件缺失",
            "height_inconsistent": "连板高度记录矛盾",
        }
        notes.append("未判定样本：" + "；".join(f"{labels[key]} {count} 个" for key, count in sorted(unknown_reasons.items())) + "。")
    recomputed = sum(profile["position_source"] == "recomputed_local_daily_bars" for profile in profiles.values())
    if recomputed:
        notes.append(f"{recomputed} 个首板位置由截至首板日的本地K线重算，并非当时保存的分类；至少21根，较短历史与未复权行情仍有限制。")
    estimated_caps = sum(profile["float_market_cap"] is not None
                         and profile["float_market_cap_source"] == "derived_from_amount_and_turnover"
                         for profile in profiles.values())
    if estimated_caps:
        notes.append(f"{estimated_caps} 个首板市值快照原本由成交额/换手率推算，属于当日估计值而非实测流通市值；本次仅沿用快照，未新增估算。")
    unsourced_caps = sum(profile["float_market_cap"] is not None
                        and not profile["float_market_cap_source"] for profile in profiles.values())
    if unsourced_caps:
        notes.append(f"{unsourced_caps} 个首板市值快照缺少来源标识，不能默认视为实测值。")
    if any(leader["status"] != "matched" for leader in leaders):
        notes.append("窗外首板与无法回溯的高标不并入本期对照；已证实三板但后续缺日的轮次保留正组，最高板仅计连续核验部分。")
    if any(leader["status"] == "unresolved" for leader in leaders):
        notes.append("无法回溯项的板高仅为来源报数，不表示已核验的连续板数。")
    return {
        "positive_profiles": [profiles[key] for key, outcome in outcomes.items() if outcome == "positive"],
        "negative_profiles": [profiles[key] for key, outcome in outcomes.items() if outcome == "negative"],
        "unknown_count": sum(outcome == "unknown" for outcome in outcomes.values()),
        "leaders": leaders,
        "detected_count": len(leaders),
        "matched_count": sum(leader["first_board_date"] is not None
                             and start_date <= leader["first_board_date"] <= end_date for leader in leaders),
        "notes": notes,
    }


def _cohort_outcome(event, calendar, indices, covered_dates, by_case):
    index = indices.get(event.trade_date)
    if index is None:
        return "unknown", "calendar_missing"
    following = calendar[index + 1:index + 3]
    if len(following) < 2:
        return "unknown", "followup_unavailable"
    if any(day not in covered_dates for day in following):
        return "unknown", "event_date_missing"
    for expected_height, day in enumerate(following, start=2):
        observed = by_case.get((day, event.symbol))
        if observed is None or not observed.closed_limit:
            return "negative", None
        if observed.board_height != expected_height:
            return "unknown", "height_inconsistent"
    return "positive", None


def _leader_episodes(by_case, calendar, indices, start_date, end_date):
    episodes: dict[tuple[str, Any], dict] = {}
    covered_dates = {day for day, _ in by_case}
    observed = sorted(
        (event for event in by_case.values() if start_date <= event.trade_date <= end_date
         and event.closed_limit and event.board_height >= 3),
        key=lambda event: (event.trade_date, event.symbol),
    )
    for event in observed:
        index = indices.get(event.trade_date)
        offset = index - event.board_height + 1 if index is not None else None
        key = (event.symbol, offset if offset is not None else event.trade_date)
        anchor = calendar[offset] if offset is not None and offset >= 0 else None
        missing = []
        if anchor is None:
            missing.append("首板回溯所需交易日历缺失")
        else:
            for height, day in enumerate(calendar[offset:index + 1], start=1):
                previous = by_case.get((day, event.symbol))
                if day not in covered_dates:
                    missing.append(f"{day} 整日事件缺失")
                elif previous is None or not previous.closed_limit:
                    missing.append(f"{day} 缺少该股封板事件")
                elif previous.board_height != height:
                    missing.append(f"{day} 连板高度不符")
        verified_anchor = anchor if not missing else None
        status = "unresolved" if missing else "outside_window" if anchor < start_date else "matched"
        if status == "outside_window":
            missing.append("首板日在本期观察窗外")
        entry = episodes.setdefault(key, {
            "symbol": event.symbol, "name": event.name, "first_board_date": verified_anchor,
            "max_board_height": event.board_height, "latest_date": event.trade_date,
            "status": status, "data_missing": [], "position_label": None,
            "position_source": None, "float_market_cap": None, "float_market_cap_source": None,
            "first_limit_time": None, "break_count": None, "turnover_rate": None,
        })
        if verified_anchor is None and entry["first_board_date"] is not None:
            entry["status"] = "incomplete_chain"
            missing.append(f"来源记录{event.board_height}板，后续连续链未核实")
        else:
            entry["max_board_height"] = max(entry["max_board_height"], event.board_height)
        entry["latest_date"] = event.trade_date
        entry["data_missing"] = sorted(set(entry["data_missing"] + missing))
    return sorted(episodes.values(), key=lambda item: (
        -item["max_board_height"], -item["latest_date"].toordinal(), item["symbol"],
    ))


def _field(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _position(snapshot):
    primary = _field(_field(snapshot, "position"), "primary")
    if _field(primary, "regime") == "unclassified":
        return None
    label = _field(primary, "label")
    return label.strip() if isinstance(label, str) and label.strip() not in {"", "结构不明", "未知", "数据不足"} else None


def _finite(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value) if (value > 0 if positive else value >= 0) else None


def _build_profiles(feature_events, repository, end_date):
    snapshots = {}
    for day in sorted({day for day, _ in feature_events}):
        for snapshot in repository.list_enrichment_for_date(day):
            if _field(snapshot, "trade_date") == day:
                snapshots[(day, _field(snapshot, "symbol"))] = snapshot
    need_bars = sorted({symbol for (day, symbol) in feature_events if not _position(snapshots.get((day, symbol)))})
    bars_by_symbol = defaultdict(list)
    if need_bars:
        for bar in repository.list_daily_bars_for_symbols(need_bars, end_date=end_date):
            bars_by_symbol[bar.symbol].append(bar)
    profiles = {}
    for key, event in feature_events.items():
        snapshot = snapshots.get(key)
        position = _position(snapshot)
        position_source = "first_board_snapshot" if position else None
        missing = []
        if not position:
            bars = {
                bar.trade_date: bar for bar in bars_by_symbol[event.symbol]
                if bar.trade_date <= event.trade_date and all(
                    _finite(value, positive=True) is not None for value in (bar.open, bar.high, bar.low, bar.close)
                )
            }
            if event.trade_date in bars and len(bars) >= 21:
                assessment = classify_stock_position(list(bars.values()), event.trade_date)
                position = _position({"position": assessment})
                if position:
                    position_source = "recomputed_local_daily_bars"
            if not position:
                missing.append("首板位置数据不足")
        cap = _finite(_field(snapshot, "float_market_cap"), positive=True)
        if cap is None:
            missing.append("首板当日流通市值快照缺失")
        first_time = event.first_limit_time if event.first_limit_time != time(0) else None
        if first_time is None:
            missing.append("首次封板时间缺失")
        breaks = _finite(event.break_count)
        turnover = _finite(event.turnover_rate, positive=True)
        if breaks is None:
            missing.append("炸板次数缺失")
        if turnover is None:
            missing.append("换手率缺失")
        profiles[key] = {
            "symbol": event.symbol, "first_board_date": event.trade_date,
            "position_label": position, "position_source": position_source,
            "float_market_cap": cap,
            "float_market_cap_source": _field(snapshot, "float_market_cap_source") if cap is not None else None,
            "first_limit_minutes": first_time.hour * 60 + first_time.minute if first_time else None,
            "first_limit_time": first_time.strftime("%H:%M") if first_time else None,
            "break_count": int(breaks) if breaks is not None else None,
            "turnover_rate": turnover, "industry": event.industry, "data_missing": missing,
        }
    return profiles
