"""Deterministic, event-relative screening, path and historical statistics."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
from statistics import mean, median
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from app.post_limit_query_contract import PostLimitQueryContract, PostLimitShape
from app.repositories.post_limit_repository import PostLimitDataset


POST_LIMIT_RULE_VERSION = "post_limit_research_v2"
MAIN_BOARD_PREFIXES = ("000", "001", "002", "003", "600", "601", "603", "605")
SHAPE_LABELS: dict[PostLimitShape, str] = {
    "high_drawdown": "高位大幅回撤",
    "volume_consolidation": "横盘缩量",
    "pullback_stabilizing": "回撤企稳",
    "strong_nonconsecutive": "强势不连板",
    "broken_board_repair": "断板修复",
    "second_to_third": "2进3观察",
}
ALL_SHAPES = tuple(SHAPE_LABELS)
RULES = {
    "high_drawdown": "距最近涨停1–4日；最新收盘较涨停日至观察日前一日的最高价回撤至少10%。",
    "volume_consolidation": "距最近涨停2–4日；区间幅度不超过8%；收盘较涨停收盘−10%至+8%；量比不超过0.75。",
    "pullback_stabilizing": "距最近涨停2–4日；收盘较涨停收盘回撤3%至25%；收盘高于前收且位于当日振幅上方40%区域。",
    "strong_nonconsecutive": "距最近涨停2–4日；收盘高于涨停收盘；连续两日收盘抬升；期间最低价不低于涨停收盘95%。",
    "broken_board_repair": "最近涨停板高至少2；距涨停2–4日；收盘突破前日最高价且不低于涨停收盘88%。",
    "second_to_third": "截止日收盘为二板，且前一交易日为封住的首板。",
}


def build_post_limit_screen(
    dataset: PostLimitDataset,
    contract: PostLimitQueryContract,
) -> dict[str, Any]:
    """Screen the latest eligible anchor for each stock as of one close."""

    if not dataset.calendar or not dataset.latest_data_date:
        return _empty_result(contract, "data_missing", ["daily_bars"])
    end = _resolve_completed_end(dataset, contract)
    if end > dataset.latest_data_date or end.isoformat() not in dataset.calendar:
        raise ValueError("所选日期没有完整的本地日K数据。")
    return _screen_loaded(dataset, contract, end)


def build_post_limit_path(
    dataset: PostLimitDataset,
    contract: PostLimitQueryContract,
    *,
    symbol: str,
) -> dict[str, Any]:
    """Return an annotated path from one closed limit-up anchor through the cutoff."""

    if not dataset.calendar or not dataset.latest_data_date:
        return _empty_result(contract, "data_missing", ["daily_bars"]) | {"symbol": symbol, "path": []}
    end = _resolve_completed_end(dataset, contract)
    end_text = end.isoformat()
    if end_text not in dataset.calendar:
        raise ValueError("所选日期没有完整的本地日K数据。")
    end_index = dataset.calendar.index(end_text)
    earliest_index = max(0, end_index - max(contract.recent_limit_days, 20) + 1)
    candidates = [
        event for event in dataset.events
        if event["symbol"] == symbol and event["closed_limit"]
        and dataset.calendar[earliest_index] <= event["trade_date"] <= end_text
        and (contract.anchor_date is None or event["trade_date"] == contract.anchor_date.isoformat())
    ]
    if not candidates:
        return {
            **_base_metadata(dataset, contract, end),
            "status": "empty",
            "symbol": symbol,
            "anchor": None,
            "metrics": None,
            "path": [],
            "data_missing": [],
            "warnings": ["指定范围内没有找到该股票的收盘涨停锚点。"],
        }
    anchor = max(candidates, key=lambda item: item["trade_date"])
    anchor = _with_previous_board_state(anchor, dataset)
    bars = _bar_map(dataset)
    metrics, issue = build_post_limit_metrics(anchor, end_text, dataset.calendar, bars)
    if issue or metrics is None:
        return {
            **_base_metadata(dataset, contract, end),
            "status": "data_missing",
            "symbol": symbol,
            "anchor": _anchor_fact(anchor),
            "metrics": None,
            "path": [],
            "data_missing": [issue or "unknown"],
            "warnings": ["涨停后路径所需日K不完整。"],
        }
    anchor_index = dataset.calendar.index(anchor["trade_date"])
    window = [bars[(symbol, day)] for day in dataset.calendar[anchor_index:end_index + 1]]
    running_peak = 0.0
    path = []
    for offset, bar in enumerate(window):
        previous_peak = running_peak
        running_peak = max(running_peak, float(bar["high"]))
        anchor_change_pct = _pct(float(bar["close"]) / float(window[0]["close"]) - 1)
        running_drawdown_pct = _pct(float(bar["close"]) / running_peak - 1)
        volume_vs_anchor = round(float(bar["volume"]) / float(window[0]["volume"]), 4)
        states = ["涨停锚点"] if offset == 0 else []
        if offset > 0 and float(bar["high"]) > previous_peak:
            states.append("运行高点刷新")
        if offset > 0 and float(bar["close"]) > float(window[offset - 1]["high"]):
            states.append("突破前日高点")
        if anchor_change_pct < 0:
            states.append("收盘低于涨停收盘")
        if running_drawdown_pct <= -10:
            states.append("较运行高点回撤至少10%")
        if offset > 0 and volume_vs_anchor <= .75:
            states.append("相对涨停日缩量")
        path.append({
            "trade_date": bar["trade_date"],
            "day": f"T+{offset}",
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
            "volume": bar["volume"],
            "change_from_anchor_close_pct": anchor_change_pct,
            "drawdown_from_running_peak_pct": running_drawdown_pct,
            "volume_vs_anchor": volume_vs_anchor,
            "states": states,
        })
    matched_shapes = _matched_shapes(metrics, anchor, contract)
    return {
        **_base_metadata(dataset, contract, end),
        "status": "ready",
        "symbol": symbol,
        "name": anchor["name"],
        "anchor": _anchor_fact(anchor),
        "metrics": metrics,
        "matched_shapes": matched_shapes,
        "matched_shape_labels": [SHAPE_LABELS[shape] for shape in matched_shapes],
        "rules": {shape: _effective_rule(shape, contract) for shape in matched_shapes},
        "path": path,
        "data_missing": [],
        "warnings": ["走势仅基于已完成交易日的未复权日K。"],
    }


def build_post_limit_statistics(
    dataset: PostLimitDataset,
    contract: PostLimitQueryContract,
) -> dict[str, Any]:
    """Recompute versioned historical cohorts without treating them as live signals."""

    if not dataset.calendar or not dataset.latest_data_date:
        return _empty_result(contract, "data_missing", ["daily_bars"])
    end = _resolve_completed_end(dataset, contract)
    end_text = end.isoformat()
    if end_text not in dataset.calendar:
        raise ValueError("所选日期没有完整的本地日K数据。")
    shapes = contract.shapes or (contract.shape,)
    event_date_set = {event["trade_date"] for event in dataset.events if event["trade_date"] <= end_text}
    end_index = dataset.calendar.index(end_text)
    mature_dates = dataset.calendar[: max(0, end_index - 4)]
    eligible_signal_dates = []
    missing_event_dates = set()
    for index, day in enumerate(mature_dates):
        window = dataset.calendar[max(0, index - contract.recent_limit_days + 1):index + 1]
        missing = set(window) - event_date_set
        if missing:
            missing_event_dates.update(missing)
        else:
            eligible_signal_dates.append(day)
    if not eligible_signal_dates:
        return _empty_result(contract, "data_missing", [
            "recent_event_dates" if missing_event_dates else "mature_signal_dates"
        ], dataset=dataset, end=end)

    raw_signals: list[dict[str, Any]] = []
    for signal_day in eligible_signal_dates:
        daily_contract = PostLimitQueryContract(
            **{
                **contract.__dict__,
                "mode": "screen",
                "data_as_of": date.fromisoformat(signal_day),
                "shapes": tuple(shapes),
                "limit": 100,
                "exhaustive": True,
            }
        )
        daily = _screen_loaded(dataset, daily_contract, date.fromisoformat(signal_day), include_all=True)
        for item in daily["candidates"]:
            for shape in item["matched_shapes"]:
                if shape in shapes:
                    raw_signals.append({**item, "shape": shape, "signal_date": signal_day})

    signals = _first_triggers(raw_signals)
    available_signal_dates = sorted({item["signal_date"] for item in signals})
    selected_dates = available_signal_dates[-contract.statistics_days:]
    selected = [item for item in signals if item["signal_date"] in selected_dates]
    bars = _bar_map(dataset)
    events = {(item["symbol"], item["trade_date"]): item for item in dataset.events}
    complete: list[dict[str, Any]] = []
    outcome_missing = Counter()
    for signal in selected:
        outcome, issue = _attach_outcome(signal, bars, dataset.calendar, events)
        if issue:
            outcome_missing[issue] += 1
        elif outcome:
            complete.append({**signal, **outcome})

    summaries = [_summarize_shape(shape, complete) for shape in shapes]
    groups = _group_statistics(complete, contract.group_by) if contract.group_by else []
    complete_dates = len({item["signal_date"] for item in complete})
    quality = "sufficient" if complete_dates >= 5 and len(complete) >= 30 and not missing_event_dates else "insufficient"
    comparison_allowed = not missing_event_dates and len(summaries) > 1 and all(
        item.get("sample_quality") == "sufficient" for item in summaries
    )
    digest_payload = [
        [item["symbol"], item["anchor_date"], item["signal_date"], item["shape"], item["d5_close_pct"]]
        for item in complete
    ]
    return {
        **_base_metadata(dataset, contract, end),
        "status": "ready" if complete else "data_missing" if outcome_missing or missing_event_dates else "empty",
        "snapshot_kind": "recomputed_historical_research",
        "shapes": list(shapes),
        "shape_labels": {shape: SHAPE_LABELS[shape] for shape in shapes},
        "rules": {shape: _effective_rule(shape, contract) for shape in shapes},
        "signal_dates": selected_dates,
        "requested_signal_days": contract.statistics_days,
        "signal_count": len(selected),
        "complete_sample_count": len(complete),
        "complete_signal_date_count": complete_dates,
        "sample_quality": quality,
        "comparison_allowed": comparison_allowed,
        "summaries": summaries,
        "groups": groups,
        "outcome_missing": dict(sorted(outcome_missing.items())),
        "data_missing": sorted({*outcome_missing, *(["recent_event_dates"] if missing_event_dates else [])}),
        "input_fingerprint": hashlib.sha256(
            json.dumps(digest_payload, ensure_ascii=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "warnings": [
            *(["事件覆盖不完整，以下日期缺失，统计仅包含已覆盖窗口：" + "、".join(sorted(missing_event_dates))] if missing_event_dates else []),
            "这是按当前本地数据重算的历史描述性研究，不是当时发布的预测。",
            "不同形态可能包含同一股票，样本数不能直接相加。",
            *( ["至少一个形态的完整样本少于30个或不足5个信号日，不据此判断形态优劣。"] if not comparison_allowed and len(shapes) > 1 else []),
        ],
    }


def _screen_loaded(
    dataset: PostLimitDataset,
    contract: PostLimitQueryContract,
    end: date,
    *,
    include_all: bool = False,
) -> dict[str, Any]:
    end_text = end.isoformat()
    end_index = dataset.calendar.index(end_text)
    recent_dates = set(dataset.calendar[max(0, end_index-contract.recent_limit_days+1):end_index+1])
    visible_events = [event for event in dataset.events if event["trade_date"] <= end_text]
    present_event_dates = {event["trade_date"] for event in visible_events}
    if recent_dates - present_event_dates:
        return _empty_result(contract, "data_missing", ["recent_event_dates"], dataset=dataset, end=end)
    latest: dict[str, dict] = {}
    for event in sorted(visible_events, key=lambda item: item["trade_date"]):
        if event["trade_date"] in recent_dates and event["closed_limit"] and _supported(event):
            latest[event["symbol"]] = event
    bars = _bar_map(dataset)
    exclusions = Counter()
    candidates = []
    evaluated = 0
    shapes = contract.shapes or (contract.shape,)
    for symbol, anchor in sorted(latest.items()):
        anchor = _with_previous_board_state(anchor, dataset)
        if contract.board_height and anchor["board_height"] != contract.board_height:
            continue
        if contract.query and not _matches_query(anchor, contract.query):
            continue
        age = end_index - dataset.calendar.index(anchor["trade_date"])
        if not any(_shape_age_eligible(shape, age) for shape in shapes):
            exclusions["age_outside_shape_window"] += 1
            continue
        metrics, issue = build_post_limit_metrics(anchor, end_text, dataset.calendar, bars)
        if issue or metrics is None:
            exclusions[issue or "unknown"] += 1
            continue
        evaluated += 1
        matched = _matched_shapes(metrics, anchor, contract)
        selected_matches = [shape for shape in matched if shape in shapes]
        if not selected_matches:
            exclusions["shape_not_matched"] += 1
            continue
        candidates.append({
            **_anchor_fact(anchor),
            **metrics,
            "matched_shapes": selected_matches,
            "matched_shape_labels": [SHAPE_LABELS[shape] for shape in selected_matches],
            "reasons": _reasons(selected_matches, metrics),
            "risks": _risks(selected_matches),
        })
    sort_by = contract.sort_by or _default_sort(contract.shape)
    candidates = _sort_candidates(candidates, sort_by, contract.sort_order)
    matched_count = len(candidates)
    displayed = candidates if include_all or contract.exhaustive else candidates[: contract.limit]
    quality_keys = {
        "missing_calendar_date", "missing_history20", "price_discontinuity",
        "missing_path_bar", "anchor_price_mismatch", "invalid_volume",
        "mixed_or_missing_source",
    }
    quality_missing = {key: value for key, value in exclusions.items() if key in quality_keys}
    coverage = round(evaluated / len(latest), 4) if latest else 1.0
    return {
        **_base_metadata(dataset, contract, end),
        "status": "ready" if matched_count else "data_missing" if quality_missing else "empty",
        "snapshot_kind": "recomputed_observation",
        "shape": contract.shape,
        "shapes": list(shapes),
        "shape_labels": {shape: SHAPE_LABELS[shape] for shape in shapes},
        "rules": {shape: _effective_rule(shape, contract) for shape in shapes},
        "pool_count": len(latest),
        "evaluable_count": evaluated,
        "coverage_ratio": coverage,
        "pending_data_count": sum(quality_missing.values()),
        "matched_count": matched_count,
        "returned_count": len(displayed),
        "candidates": displayed,
        "exclusions": dict(sorted(exclusions.items())),
        "data_missing": sorted(quality_missing),
        "warnings": [
            "仅使用已完成交易日的本地数据重算观察结果，不代表历史上发布过该名单。",
            *( [f"可评价覆盖率为 {coverage:.1%}，结果不能代表完整涨停池。"] if coverage < 1 else []),
        ],
    }


def build_post_limit_metrics(
    anchor: dict,
    end: str,
    calendar: list[str],
    bars: dict[tuple[str, str], dict],
) -> tuple[dict[str, Any] | None, str | None]:
    """Build the canonical event-relative facts shared by page and Agent tools."""

    if anchor["trade_date"] not in calendar or end not in calendar:
        return None, "missing_calendar_date"
    anchor_index, end_index = calendar.index(anchor["trade_date"]), calendar.index(end)
    age = end_index - anchor_index
    if age < 0 or age > 20:
        return None, "age_outside_supported_window"
    history_days = calendar[max(0, end_index-19):end_index+1]
    history = [bars.get((anchor["symbol"], day)) for day in history_days]
    if len(history) != 20 or not all(_valid_bar(bar) for bar in history):
        return None, "missing_history20"
    if _price_break(history):
        return None, "price_discontinuity"
    window_days = calendar[anchor_index:end_index+1]
    window = [bars.get((anchor["symbol"], day)) for day in window_days]
    if not all(_valid_bar(bar) for bar in window):
        return None, "missing_path_bar"
    previous = bars.get((anchor["symbol"], calendar[anchor_index-1])) if anchor_index else None
    if not _valid_bar(previous) or not .085 <= float(window[0]["close"]) / float(previous["close"]) - 1 <= .111:
        return None, "anchor_price_mismatch"
    if not all(_valid_volume(bar) for bar in window):
        return None, "invalid_volume"
    sources = {bar.get("source") for bar in window}
    if len(sources) != 1 or not next(iter(sources)):
        return None, "mixed_or_missing_source"

    anchor_close = float(window[0]["close"])
    latest = window[-1]
    prior = window[-2] if age >= 1 else previous
    post = window[1:]
    peak_window = window[:-1] if age >= 1 else window
    peak_index = max(range(len(peak_window)), key=lambda index: (float(peak_window[index]["high"]), index))
    peak = float(peak_window[peak_index]["high"])
    low = min(float(bar["low"]) for bar in post) if post else float(window[0]["low"])
    high = max(float(bar["high"]) for bar in post) if post else float(window[0]["high"])
    latest_range = float(latest["high"]) - float(latest["low"])
    close_position = (
        (float(latest["close"]) - float(latest["low"])) / latest_range
        if latest_range > 0 else .5
    )
    return {
        "signal_date": end,
        "anchor_age": age,
        "anchor_close": anchor_close,
        "close": float(latest["close"]),
        "anchor_change_pct": _pct(float(latest["close"]) / anchor_close - 1),
        "peak_date": peak_window[peak_index]["trade_date"],
        "peak_price": peak,
        "peak_drawdown_pct": _pct(1 - float(latest["close"]) / peak),
        "range_low": low,
        "range_high": high,
        "range_pct": _pct(high / low - 1),
        "volume_ratio": round(mean(float(bar["volume"]) for bar in post) / float(window[0]["volume"]), 6) if post else 1.0,
        "latest_change_pct": _pct(float(latest["close"]) / float(prior["close"]) - 1),
        "close_position_pct": _pct(close_position),
        "source": window[0]["source"],
        "latest_close_above_previous": float(latest["close"]) > float(prior["close"]),
        "latest_close_above_previous_high": float(latest["close"]) > float(prior["high"]),
        "two_closes_rising": age >= 2 and float(window[-1]["close"]) > float(window[-2]["close"]) > float(window[-3]["close"]),
    }, None


def _matched_shapes(metrics: dict[str, Any], anchor: dict, contract: PostLimitQueryContract) -> list[PostLimitShape]:
    age = metrics["anchor_age"]
    anchor_change = metrics["anchor_change_pct"]
    peak_threshold = contract.min_peak_drawdown_pct if contract.min_peak_drawdown_pct is not None else 10.0
    volume_threshold = contract.max_volume_ratio if contract.max_volume_ratio is not None else .75
    range_threshold = contract.max_range_pct if contract.max_range_pct is not None else 8.0
    anchor_min = contract.min_anchor_change_pct
    anchor_max = contract.max_anchor_change_pct
    matched: list[PostLimitShape] = []
    if matches_high_drawdown(age, metrics["peak_drawdown_pct"], peak_threshold):
        matched.append("high_drawdown")
    consolidation_min = -10.0 if anchor_min is None else anchor_min
    consolidation_max = 8.0 if anchor_max is None else anchor_max
    if matches_volume_consolidation(
        age, metrics["range_pct"], anchor_change, metrics["volume_ratio"],
        max_range_pct=range_threshold, min_anchor_change_pct=consolidation_min,
        max_anchor_change_pct=consolidation_max, max_volume_ratio=volume_threshold,
    ):
        matched.append("volume_consolidation")
    pullback_min = -25.0 if anchor_min is None else anchor_min
    pullback_max = -3.0 if anchor_max is None else anchor_max
    if 2 <= age <= 4 and pullback_min <= anchor_change <= pullback_max and metrics["latest_close_above_previous"] and metrics["close_position_pct"] >= 60:
        matched.append("pullback_stabilizing")
    if 2 <= age <= 4 and anchor_change > 0 and metrics["two_closes_rising"] and metrics["range_low"] >= metrics["anchor_close"] * .95:
        matched.append("strong_nonconsecutive")
    if 2 <= age <= 4 and int(anchor.get("board_height") or 0) >= 2 and metrics["latest_close_above_previous_high"] and anchor_change >= -12:
        matched.append("broken_board_repair")
    if age == 0 and int(anchor.get("board_height") or 0) == 2 and anchor.get("_previous_closed_first"):
        matched.append("second_to_third")
    return matched


def matches_high_drawdown(age: int, peak_drawdown_pct: float, minimum_pct: float = 10.0) -> bool:
    """Shared versioned predicate used by the page and Agent research tools."""

    return 1 <= age <= 4 and peak_drawdown_pct >= minimum_pct - 1e-12


def matches_volume_consolidation(
    age: int,
    range_pct: float,
    anchor_change_pct: float,
    volume_ratio: float,
    *,
    max_range_pct: float = 8.0,
    min_anchor_change_pct: float = -10.0,
    max_anchor_change_pct: float = 8.0,
    max_volume_ratio: float = .75,
) -> bool:
    """Shared consolidation predicate with inclusive boundary semantics."""

    return (
        2 <= age <= 4
        and range_pct <= max_range_pct + 1e-12
        and min_anchor_change_pct - 1e-12 <= anchor_change_pct <= max_anchor_change_pct + 1e-12
        and volume_ratio <= max_volume_ratio + 1e-12
    )


def _attach_outcome(signal: dict, bars: dict, calendar: list[str], events: dict) -> tuple[dict[str, Any] | None, str | None]:
    index = calendar.index(signal["signal_date"])
    horizon = calendar[index+1:index+6]
    if len(horizon) < 5:
        return None, "immature"
    future = [bars.get((signal["symbol"], day)) for day in horizon]
    if not all(_valid_bar(bar) for bar in future):
        return None, "missing_outcome_bar"
    signal_bar = bars.get((signal["symbol"], signal["signal_date"]))
    if not _valid_bar(signal_bar) or _price_break([signal_bar, *future]):
        return None, "outcome_price_discontinuity"
    base = float(future[0]["open"])
    next_event = events.get((signal["symbol"], horizon[0]))
    return {
        "outcome_start": horizon[0],
        "outcome_end": horizon[-1],
        "d1_close_pct": _pct(float(future[0]["close"]) / base - 1),
        "d3_close_pct": _pct(float(future[2]["close"]) / base - 1),
        "d5_close_pct": _pct(float(future[4]["close"]) / base - 1),
        "mae5_pct": _pct(min(float(bar["low"]) for bar in future) / base - 1),
        "mfe5_pct": _pct(max(float(bar["high"]) for bar in future) / base - 1),
        "next_day_closed_limit": bool(next_event and next_event["closed_limit"]),
    }, None


def _summarize_shape(shape: PostLimitShape, rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [row for row in rows if row["shape"] == shape]
    if not selected:
        return {"shape": shape, "label": SHAPE_LABELS[shape], "sample_count": 0, "sample_quality": "insufficient"}
    signal_date_count = len({row["signal_date"] for row in selected})
    return {
        "shape": shape,
        "label": SHAPE_LABELS[shape],
        "sample_count": len(selected),
        "signal_date_count": signal_date_count,
        "sample_quality": "sufficient" if signal_date_count >= 5 and len(selected) >= 30 else "insufficient",
        "d1_mean_pct": _avg(selected, "d1_close_pct"),
        "d1_median_pct": round(median(row["d1_close_pct"] for row in selected), 4),
        "d3_mean_pct": _avg(selected, "d3_close_pct"),
        "d3_median_pct": round(median(row["d3_close_pct"] for row in selected), 4),
        "d5_mean_pct": _avg(selected, "d5_close_pct"),
        "d5_median_pct": round(median(row["d5_close_pct"] for row in selected), 4),
        "d5_positive_pct": _pct(mean(row["d5_close_pct"] > 0 for row in selected)),
        "d5_below_minus5_pct": _pct(mean(row["d5_close_pct"] < -5 for row in selected)),
        "mae5_mean_pct": _avg(selected, "mae5_pct"),
        "mae5_median_pct": round(median(row["mae5_pct"] for row in selected), 4),
        "mfe5_mean_pct": _avg(selected, "mfe5_pct"),
        "mfe5_median_pct": round(median(row["mfe5_pct"] for row in selected), 4),
        "next_day_closed_limit_pct": _pct(mean(row["next_day_closed_limit"] for row in selected)),
    }


def _group_statistics(rows: list[dict[str, Any]], group_by: str) -> list[dict[str, Any]]:
    field = {"anchor_age": "anchor_age", "signal_date": "signal_date"}.get(group_by, group_by)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if group_by == "shape":
            key = row["shape"]
        else:
            key = str(row.get(field) or "未知")
        groups[key].append(row)
    result = []
    for key, selected in groups.items():
        signal_date_count = len({row["signal_date"] for row in selected})
        result.append({
            "group": key,
            "sample_count": len(selected),
            "signal_date_count": signal_date_count,
            "sample_quality": (
                "sufficient"
                if len(selected) >= 30 and signal_date_count >= 5
                else "insufficient"
            ),
            "d5_mean_pct": _avg(selected, "d5_close_pct"),
            "d5_median_pct": round(median(row["d5_close_pct"] for row in selected), 4),
            "d5_positive_pct": _pct(mean(row["d5_close_pct"] > 0 for row in selected)),
            "mae5_median_pct": round(median(row["mae5_pct"] for row in selected), 4),
        })
    return sorted(result, key=lambda item: (-item["sample_count"], item["group"]))[:20]


def _first_triggers(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    result = []
    for row in sorted(rows, key=lambda item: (item["signal_date"], item["symbol"], item["shape"])):
        key = row["symbol"], row["anchor_date"], row["shape"]
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


def _base_metadata(dataset: PostLimitDataset, contract: PostLimitQueryContract, end: date) -> dict[str, Any]:
    return {
        "rule_version": POST_LIMIT_RULE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_as_of": end.isoformat(),
        "latest_data_date": dataset.latest_data_date.isoformat() if dataset.latest_data_date else None,
        "market": "main_board",
        "query_contract": contract.to_dict(),
    }


def _resolve_completed_end(dataset: PostLimitDataset, contract: PostLimitQueryContract) -> date:
    """Pin undated queries to the latest locally available completed close."""

    shanghai_now = datetime.now(ZoneInfo("Asia/Shanghai"))
    completed_limit = shanghai_now.date() if shanghai_now.time() >= time(15, 30) else shanghai_now.date() - timedelta(days=1)
    if contract.data_as_of and contract.data_as_of > completed_limit:
        raise ValueError("只能查询已结束交易日，当前交易日须在15:30后查询。")
    eligible = [day for day in dataset.calendar if day <= completed_limit.isoformat()]
    if not eligible:
        raise ValueError("本地没有已结束交易日的日K数据。")
    return contract.data_as_of or date.fromisoformat(eligible[-1])


def _empty_result(contract: PostLimitQueryContract, status: str, missing: list[str], *, dataset: PostLimitDataset | None = None, end: date | None = None) -> dict[str, Any]:
    metadata = _base_metadata(dataset, contract, end) if dataset and end else {
        "rule_version": POST_LIMIT_RULE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_as_of": end.isoformat() if end else None,
        "latest_data_date": dataset.latest_data_date.isoformat() if dataset and dataset.latest_data_date else None,
        "market": "main_board",
        "query_contract": contract.to_dict(),
    }
    return {**metadata, "status": status, "candidates": [], "data_missing": missing, "warnings": []}


def _anchor_fact(anchor: dict) -> dict[str, Any]:
    return {
        "symbol": anchor["symbol"], "name": anchor["name"],
        "anchor_date": anchor["trade_date"], "board_height": anchor.get("board_height"),
        "industry": anchor.get("industry") or "", "concept": anchor.get("concept") or "",
    }


def _with_previous_board_state(anchor: dict, dataset: PostLimitDataset) -> dict:
    """Attach exact prior-session first-board evidence for the 2-to-3 preset."""

    if anchor["trade_date"] not in dataset.calendar:
        return anchor
    index = dataset.calendar.index(anchor["trade_date"])
    previous_date = dataset.calendar[index - 1] if index else None
    previous = next(
        (
            event for event in dataset.events
            if previous_date and event["symbol"] == anchor["symbol"]
            and event["trade_date"] == previous_date
        ),
        None,
    )
    return {
        **anchor,
        "_previous_closed_first": bool(
            previous and previous["closed_limit"] and int(previous.get("board_height") or 0) == 1
        ),
    }


def _supported(event: dict) -> bool:
    return event["symbol"].startswith(MAIN_BOARD_PREFIXES) and "ST" not in event["name"].upper() and "退" not in event["name"]


def _matches_query(event: dict, query: str) -> bool:
    needle = query.strip().lower()
    return any(needle in str(event.get(field) or "").lower() for field in ("symbol", "name", "industry", "concept"))


def _bar_map(dataset: PostLimitDataset) -> dict[tuple[str, str], dict]:
    return {(row["symbol"], row["trade_date"]): row for row in dataset.bars}


def _valid_bar(bar: dict | None) -> bool:
    return bool(bar and all(isinstance(bar.get(key), (int, float)) and math.isfinite(bar[key]) and bar[key] > 0 for key in ("open", "high", "low", "close")) and bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"])


def _valid_volume(bar: dict | None) -> bool:
    return bool(bar and isinstance(bar.get("volume"), (int, float)) and math.isfinite(bar["volume"]) and bar["volume"] > 0)


def _price_break(bars: list[dict]) -> bool:
    return any(abs(float(current["close"]) / float(previous["close"]) - 1) > .111 or float(current["high"]) / float(previous["close"]) - 1 > .112 or float(current["low"]) / float(previous["close"]) - 1 < -.112 for previous, current in zip(bars, bars[1:]))


def _pct(value: float) -> float:
    return round(value * 100, 4)


def _avg(rows: list[dict[str, Any]], field: str) -> float:
    return round(mean(float(row[field]) for row in rows), 4)


def _default_sort(shape: PostLimitShape) -> str:
    return {
        "high_drawdown": "peak_drawdown_pct",
        "volume_consolidation": "volume_ratio",
        "pullback_stabilizing": "anchor_change_pct",
        "strong_nonconsecutive": "anchor_change_pct",
        "broken_board_repair": "anchor_change_pct",
        "second_to_third": "board_height",
    }[shape]


def _shape_age_eligible(shape: PostLimitShape, age: int) -> bool:
    return age == 0 if shape == "second_to_third" else 1 <= age <= 4 if shape == "high_drawdown" else 2 <= age <= 4


def _sort_candidates(rows: list[dict[str, Any]], field: str, order: str) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: row["symbol"])
    return sorted(ordered, key=lambda row: (row.get(field) is None, row.get(field) or 0), reverse=order == "desc")


def _effective_rule(shape: PostLimitShape, contract: PostLimitQueryContract) -> str:
    overrides = []
    if shape == "high_drawdown" and contract.min_peak_drawdown_pct is not None:
        overrides.append(f"峰值回撤≥{contract.min_peak_drawdown_pct:g}%")
    if shape == "volume_consolidation" and contract.max_volume_ratio is not None:
        overrides.append(f"量比≤{contract.max_volume_ratio:g}")
    if shape == "volume_consolidation" and contract.max_range_pct is not None:
        overrides.append(f"区间幅度≤{contract.max_range_pct:g}%")
    if shape in {"volume_consolidation", "pullback_stabilizing"}:
        if contract.min_anchor_change_pct is not None:
            overrides.append(f"相对涨停收盘≥{contract.min_anchor_change_pct:g}%")
        if contract.max_anchor_change_pct is not None:
            overrides.append(f"相对涨停收盘≤{contract.max_anchor_change_pct:g}%")
    return RULES[shape] + (" 用户覆盖：" + "、".join(overrides) + "。" if overrides else "")


def _reasons(shapes: list[PostLimitShape], metrics: dict[str, Any]) -> list[str]:
    reasons = [f"符合{SHAPE_LABELS[shape]}" for shape in shapes]
    reasons.append(f"涨停后{metrics['anchor_age']}个交易日，相对涨停收盘{metrics['anchor_change_pct']:+.2f}%")
    reasons.append(f"较{metrics['peak_date']}参考高点回撤{metrics['peak_drawdown_pct']:.2f}%，量比{metrics['volume_ratio']:.3f}")
    return reasons


def _risks(shapes: list[PostLimitShape]) -> list[str]:
    result = ["形态只描述已发生的量价路径，不能证明后续方向。"]
    if "high_drawdown" in shapes:
        result.append("大幅回撤可能继续扩大，当前条件不要求反弹确认。")
    return result
