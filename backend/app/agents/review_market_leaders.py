"""Shared first-board profiles and calendar-aligned leader episode facts."""

from collections import defaultdict
from datetime import time
import math
from typing import Any

from app.services.stock_position import classify_stock_position


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
