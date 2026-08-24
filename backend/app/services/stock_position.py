"""Explainable point-in-time position classification for first-board stocks."""

from __future__ import annotations

from datetime import date
from statistics import mean

from app.models import (
    StockDailyBar,
    StockPositionAssessment,
    StockPositionMatch,
    StockPositionRegime,
)


POSITION_CLASSIFIER_VERSION = "stock-position-rule-v1"

POSITION_LABELS: dict[StockPositionRegime, str] = {
    "oversold_rebound": "超跌反弹首板",
    "v_reversal": "V形反转首板",
    "low_base_breakout": "低位启动首板",
    "mid_base_breakout": "中位平台突破首板",
    "trend_acceleration": "上升趋势加速首板",
    "high_consolidation": "高位震荡首板",
    "high_breakout": "高位突破首板",
    "second_wave": "二波启动首板",
    "unclassified": "结构不明",
}


def classify_stock_position(
    bars: list[StockDailyBar],
    trade_date: date,
) -> StockPositionAssessment:
    """Classify position using only bars available by the first-board close."""

    ordered = _ordered_bars(bars, trade_date)
    if len(ordered) < 21:
        return _unclassified_assessment(
            bar_count=len(ordered),
            evidence=[f"仅有 {len(ordered)} 根日 K，至少需要 21 根才能判断位置"],
        )

    metrics = _build_metrics(ordered)
    scores = _score_regimes(metrics)
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    best_regime, best_score = ranked[0]
    alternatives = [
        _match(regime, score)
        for regime, score in ranked[1:3]
        if score >= 35 and best_score - score <= 20
    ]
    if best_score < 45:
        alternatives = [_match(best_regime, best_score), *alternatives][:2]
        primary = _match("unclassified", round(100 - best_score, 1))
    else:
        primary = _match(best_regime, best_score)

    runner_up_score = ranked[1][1] if len(ranked) > 1 else 0.0
    coverage = min(1.0, max(0.0, (len(ordered) - 1) / 120))
    separation = max(0.0, best_score - runner_up_score)
    confidence = min(
        0.95,
        (0.45 + best_score / 200 + min(separation, 30) / 150)
        * (0.72 + coverage * 0.28),
    )
    if primary.regime == "unclassified":
        confidence = min(confidence, 0.45)

    return StockPositionAssessment(
        primary=primary,
        alternatives=alternatives,
        confidence=round(confidence, 3),
        tags=_build_tags(metrics, len(ordered)),
        evidence=_build_evidence(primary.regime, metrics),
        metrics={key: _rounded(value) for key, value in metrics.items()},
        bar_count=len(ordered),
        classifier_version=POSITION_CLASSIFIER_VERSION,
    )


def _ordered_bars(bars: list[StockDailyBar], trade_date: date) -> list[StockDailyBar]:
    by_date = {
        item.trade_date: item
        for item in bars
        if item.trade_date <= trade_date and min(item.open, item.high, item.low, item.close) > 0
    }
    return [by_date[value] for value in sorted(by_date)][-125:]


def _build_metrics(ordered: list[StockDailyBar]) -> dict[str, float | None]:
    current = ordered[-1]
    prior = ordered[:-1]
    prior_close = prior[-1].close
    window_120 = prior[-120:]
    window_60 = prior[-60:]
    window_20 = prior[-20:]
    high_120 = max(item.high for item in window_120)
    low_120 = min(item.low for item in window_120)
    high_60 = max(item.high for item in window_60)
    low_60 = min(item.low for item in window_60)
    high_20 = max(item.high for item in window_20)
    low_20 = min(item.low for item in window_20)
    ma5 = mean(item.close for item in prior[-5:])
    ma10 = mean(item.close for item in prior[-10:])
    ma20 = mean(item.close for item in prior[-20:])
    previous_ma20 = (
        mean(item.close for item in prior[-25:-5])
        if len(prior) >= 25
        else None
    )
    volumes = [item.volume for item in prior[-5:] if item.volume > 0]
    wave = _wave_metrics(window_60, current.close)
    return {
        "position_120_pct": _range_position(prior_close, low_120, high_120),
        "position_60_pct": _range_position(prior_close, low_60, high_60),
        "return_20d_pct": _period_return(prior, 20),
        "return_60d_pct": _period_return(prior, 60),
        "drawdown_from_60d_high_pct": _pct(prior_close, high_60),
        "distance_to_120d_high_after_board_pct": _pct(current.close, high_120),
        "breakout_20d_pct": _pct(current.close, high_20),
        "range_width_20d_pct": (
            ((high_20 - low_20) / mean(item.close for item in window_20)) * 100
            if window_20
            else None
        ),
        "ma20_slope_pct": _pct(ma20, previous_ma20) if previous_ma20 else None,
        "bullish_alignment": 1.0 if ma5 > ma10 > ma20 else 0.0,
        "close_above_ma20": 1.0 if current.close > ma20 else 0.0,
        "board_day_volume_ratio_5d": (
            current.volume / mean(volumes)
            if current.volume > 0 and volumes and mean(volumes) > 0
            else None
        ),
        "board_day_return_pct": _pct(current.close, prior_close),
        **wave,
    }


def _wave_metrics(
    bars: list[StockDailyBar],
    current_close: float,
) -> dict[str, float | None]:
    if len(bars) < 15:
        return {
            "prior_wave_gain_pct": None,
            "pullback_from_wave_peak_pct": None,
            "wave_pullback_days": None,
            "wave_peak_age_days": None,
            "wave_trough_age_days": None,
            "rebound_from_wave_trough_pct": None,
            "wave_low_retained_pct": None,
        }

    peak_search = bars[:-3]
    peak_index = max(range(len(peak_search)), key=lambda index: peak_search[index].high)
    peak_price = bars[peak_index].high
    before_peak = bars[: peak_index + 1]
    wave_start_price = min(item.low for item in before_peak)
    after_peak = bars[peak_index + 1:]
    if not after_peak:
        return {
            "prior_wave_gain_pct": _pct(peak_price, wave_start_price),
            "pullback_from_wave_peak_pct": None,
            "wave_pullback_days": None,
            "wave_peak_age_days": None,
            "wave_trough_age_days": None,
            "rebound_from_wave_trough_pct": None,
            "wave_low_retained_pct": None,
        }
    trough_offset = min(range(len(after_peak)), key=lambda index: after_peak[index].low)
    trough_index = peak_index + 1 + trough_offset
    trough_price = bars[trough_index].low
    return {
        "prior_wave_gain_pct": _pct(peak_price, wave_start_price),
        "pullback_from_wave_peak_pct": _pct(trough_price, peak_price),
        "wave_pullback_days": float(trough_index - peak_index),
        "wave_peak_age_days": float(len(bars) - 1 - peak_index),
        "wave_trough_age_days": float(len(bars) - 1 - trough_index),
        "rebound_from_wave_trough_pct": _pct(current_close, trough_price),
        "wave_low_retained_pct": _pct(trough_price, wave_start_price),
    }


def _score_regimes(metrics: dict[str, float | None]) -> dict[StockPositionRegime, float]:
    value = lambda key: metrics.get(key)  # noqa: E731
    position_120 = value("position_120_pct")
    position_60 = value("position_60_pct")
    return_20 = value("return_20d_pct")
    drawdown = value("drawdown_from_60d_high_pct")
    slope = value("ma20_slope_pct")
    width = value("range_width_20d_pct")
    breakout = value("breakout_20d_pct")
    distance_high = value("distance_to_120d_high_after_board_pct")
    wave_gain = value("prior_wave_gain_pct")
    pullback = value("pullback_from_wave_peak_pct")
    pullback_days = value("wave_pullback_days")
    peak_age = value("wave_peak_age_days")
    trough_age = value("wave_trough_age_days")
    rebound = value("rebound_from_wave_trough_pct")
    retained = value("wave_low_retained_pct")
    bullish = value("bullish_alignment") == 1.0
    above_ma20 = value("close_above_ma20") == 1.0
    volume_ratio = value("board_day_volume_ratio_5d")

    scores: dict[StockPositionRegime, float] = {
        "oversold_rebound": _sum_points(
            (position_60 is not None and position_60 <= 25, 30),
            (return_20 is not None and return_20 <= -12, 25),
            (drawdown is not None and drawdown <= -20, 20),
            (slope is not None and slope < 0, 15),
            (not bullish, 10),
        ),
        "v_reversal": _sum_points(
            (wave_gain is not None and wave_gain >= 25, 20),
            (pullback is not None and pullback <= -18, 25),
            (pullback_days is not None and pullback_days <= 15, 15),
            (trough_age is not None and trough_age <= 5, 20),
            (rebound is not None and rebound >= 8, 10),
            (above_ma20, 10),
        ),
        "low_base_breakout": _sum_points(
            (position_120 is not None and position_120 <= 35, 30),
            (return_20 is not None and -8 <= return_20 <= 12, 20),
            (width is not None and width <= 22, 20),
            (breakout is not None and breakout >= -1, 20),
            (slope is not None and slope >= -0.5, 10),
        ),
        "mid_base_breakout": _sum_points(
            (position_120 is not None and 30 < position_120 < 70, 25),
            (return_20 is not None and -8 <= return_20 <= 15, 20),
            (width is not None and width <= 20, 20),
            (breakout is not None and breakout >= -1, 20),
            (slope is not None and slope >= -0.5, 15),
        ),
        "trend_acceleration": _sum_points(
            (position_60 is not None and 50 <= position_60 <= 95, 20),
            (return_20 is not None and 8 <= return_20 <= 35, 25),
            (slope is not None and slope > 0, 20),
            (bullish, 20),
            (distance_high is not None and distance_high < -1, 15),
        ),
        "high_consolidation": _sum_points(
            (position_120 is not None and position_120 >= 70, 30),
            (drawdown is not None and drawdown >= -15, 20),
            (return_20 is not None and -8 <= return_20 <= 20, 15),
            (width is not None and 8 <= width <= 30, 15),
            (distance_high is not None and -15 <= distance_high < -1, 20),
        ),
        "high_breakout": _sum_points(
            (position_120 is not None and position_120 >= 65, 25),
            (distance_high is not None and distance_high >= -1, 35),
            (bullish, 15),
            (return_20 is not None and 5 <= return_20 <= 35, 15),
            (volume_ratio is not None and volume_ratio >= 1.2, 10),
        ),
        "second_wave": _sum_points(
            (wave_gain is not None and wave_gain >= 25, 20),
            (pullback is not None and -30 <= pullback <= -8, 20),
            (peak_age is not None and peak_age >= 7, 10),
            (trough_age is not None and 2 <= trough_age <= 20, 15),
            (retained is not None and retained >= 5, 10),
            (distance_high is not None and distance_high >= -12, 15),
            (breakout is not None and breakout >= -1, 10),
        ),
        "unclassified": 0.0,
    }
    # Path-specific regimes outrank static location labels when their full
    # peak-pullback-recovery sequence is present.
    if scores["v_reversal"] >= 80:
        scores["oversold_rebound"] = min(
            scores["oversold_rebound"],
            scores["v_reversal"] - 10,
        )
    if scores["second_wave"] >= 80:
        scores["high_breakout"] = min(
            scores["high_breakout"],
            scores["second_wave"] - 10,
        )
        scores["trend_acceleration"] = min(
            scores["trend_acceleration"],
            scores["second_wave"] - 10,
        )
    return {key: round(score, 1) for key, score in scores.items()}


def _build_tags(metrics: dict[str, float | None], bar_count: int) -> list[str]:
    tags: list[str] = []
    position = metrics.get("position_120_pct")
    if position is not None:
        tags.append("120日低位" if position <= 35 else "120日高位" if position >= 70 else "120日中位")
    slope = metrics.get("ma20_slope_pct")
    if slope is not None:
        tags.append("MA20向上" if slope > 0.3 else "MA20向下" if slope < -0.3 else "MA20走平")
    breakout = metrics.get("breakout_20d_pct")
    if breakout is not None and breakout >= -1:
        tags.append("突破20日平台")
    volume_ratio = metrics.get("board_day_volume_ratio_5d")
    if volume_ratio is not None and volume_ratio >= 1.5:
        tags.append("首板明显放量")
    if bar_count < 121:
        tags.append("历史不足120日")
    return tags[:5]


def _build_evidence(
    regime: StockPositionRegime,
    metrics: dict[str, float | None],
) -> list[str]:
    evidence: list[str] = []
    position = metrics.get("position_120_pct")
    return_20 = metrics.get("return_20d_pct")
    if position is not None:
        evidence.append(f"首板前位于 120 日价格区间的 {position:.0f}% 位置")
    if return_20 is not None:
        evidence.append(f"首板前 20 日涨跌幅 {return_20:+.1f}%")

    if regime in {"v_reversal", "second_wave"}:
        wave_gain = metrics.get("prior_wave_gain_pct")
        pullback = metrics.get("pullback_from_wave_peak_pct")
        trough_age = metrics.get("wave_trough_age_days")
        if wave_gain is not None and pullback is not None:
            evidence.append(f"前一波上涨 {wave_gain:+.1f}%，随后最大回撤 {pullback:.1f}%")
        if trough_age is not None:
            evidence.append(f"回调低点距首板前 {trough_age:.0f} 个交易日")
    elif regime == "oversold_rebound":
        drawdown = metrics.get("drawdown_from_60d_high_pct")
        slope = metrics.get("ma20_slope_pct")
        if drawdown is not None:
            evidence.append(f"首板前较 60 日高点回撤 {drawdown:.1f}%")
        if slope is not None:
            evidence.append(f"MA20 近期斜率 {slope:+.1f}%")
    elif regime in {"low_base_breakout", "mid_base_breakout"}:
        width = metrics.get("range_width_20d_pct")
        breakout = metrics.get("breakout_20d_pct")
        if width is not None:
            evidence.append(f"近 20 日平台振幅 {width:.1f}%")
        if breakout is not None:
            evidence.append(f"首板收盘相对 20 日平台高点 {breakout:+.1f}%")
    else:
        distance = metrics.get("distance_to_120d_high_after_board_pct")
        slope = metrics.get("ma20_slope_pct")
        if distance is not None:
            evidence.append(f"首板收盘距此前 120 日高点 {distance:+.1f}%")
        if slope is not None:
            evidence.append(f"MA20 近期斜率 {slope:+.1f}%")
    return evidence[:4]


def _unclassified_assessment(
    *,
    bar_count: int,
    evidence: list[str],
) -> StockPositionAssessment:
    return StockPositionAssessment(
        primary=_match("unclassified", 100),
        confidence=0.15,
        tags=["K线样本不足"],
        evidence=evidence,
        metrics={},
        bar_count=bar_count,
        classifier_version=POSITION_CLASSIFIER_VERSION,
    )


def _match(regime: StockPositionRegime, score: float) -> StockPositionMatch:
    return StockPositionMatch(
        regime=regime,
        label=POSITION_LABELS[regime],
        score=round(score, 1),
    )


def _range_position(value: float, low: float, high: float) -> float | None:
    if high <= low:
        return None
    return ((value - low) / (high - low)) * 100


def _period_return(bars: list[StockDailyBar], days: int) -> float | None:
    if len(bars) <= days:
        return None
    return _pct(bars[-1].close, bars[-days - 1].close)


def _pct(value: float, base: float | None) -> float | None:
    if base in (None, 0):
        return None
    return ((value - base) / base) * 100


def _sum_points(*conditions: tuple[bool, float]) -> float:
    return sum(points for matched, points in conditions if matched)


def _rounded(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None
