"""Deterministic, field-local feature statistics for known review outcomes."""

from collections import Counter
import math
from statistics import mean, median

from app.review_research_models import (
    ReviewBucketStat,
    ReviewFeatureBreakdown,
    ReviewFeatureStudy,
)


MIN_GROUP_SIZE = 3
MIN_RATE_SIZE = 5
_MISSING_LABELS = {"", "none", "null", "unknown", "未知", "未分类", "其他", "-", "--", "—", "数据不足"}
_FEATURES = (
    ("position_label", "首板位置"),
    ("float_market_cap", "流通市值"),
    ("first_limit_minutes", "首次封板时间"),
    ("break_count", "炸板次数"),
    ("turnover_rate", "换手率"),
)
_NUMERIC_BUCKETS = {
    "float_market_cap": ((30, "<30亿元"), (60, "30–60亿元"), (100, "60–100亿元"), (math.inf, "≥100亿元")),
    "first_limit_minutes": ((600, "10点前"), (660, "10–11点"), (math.inf, "11点及以后")),
    "break_count": ((1, "0次"), (2, "1次"), (math.inf, "≥2次")),
    "turnover_rate": ((5, "<5%"), (10, "5–10%"), (20, "10–20%"), (math.inf, "≥20%")),
}


def build_feature_study(
    positive_profiles: list[dict],
    negative_profiles: list[dict],
    *,
    excluded_count: int,
    basis: str,
    positive_outcome: str,
) -> ReviewFeatureStudy:
    """Compare stock/date samples; caller supplies outcome grouping and its basis.

    Missing values are excluded independently for each dimension. Group composition
    describes P(feature | outcome); bucket rates describe P(outcome | feature).
    Neither is an estimate of future performance or a causal effect.
    """
    features = []
    for key, label in _FEATURES:
        groups = [_values(profiles, key) for profiles in (positive_profiles, negative_profiles)]
        summaries = [
            _group_summary(values, key, len(profiles))
            for values, profiles in zip(groups, (positive_profiles, negative_profiles))
        ]
        features.append(ReviewFeatureBreakdown(
            key=key,
            label=label,
            positive_summary=summaries[0][0],
            negative_summary=summaries[1][0],
            positive_detail=summaries[0][1],
            negative_detail=summaries[1][1],
            positive_valid_count=len(groups[0]),
            negative_valid_count=len(groups[1]),
            buckets=_bucket_stats(groups[0], groups[1], key),
        ))
    positive_count, negative_count = len(positive_profiles), len(negative_profiles)
    return ReviewFeatureStudy(
        positive_count=positive_count,
        negative_count=negative_count,
        excluded_count=excluded_count,
        basis=basis,
        baseline_rate=_rate(positive_count, positive_count + negative_count),
        features=features,
        signals=_signals(features, positive_outcome),
    )


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _values(profiles: list[dict], key: str) -> list[str] | list[float]:
    if key == "position_label":
        return sorted(
            value.strip() for profile in profiles
            if isinstance(value := profile.get(key), str)
            and value.strip().lower() not in _MISSING_LABELS
        )
    values = []
    for profile in profiles:
        value = _finite_number(profile.get(key))
        if value is None:
            continue
        if key in ("float_market_cap", "turnover_rate") and value <= 0:
            continue
        if key == "first_limit_minutes" and not 0 <= value < 1440:
            continue
        if key == "break_count" and (value < 0 or not value.is_integer()):
            continue
        values.append(value / 100_000_000 if key == "float_market_cap" else value)
    return sorted(values)


def _quartile(values: list[float], fraction: float) -> float:
    """Linear interpolation on a sorted observed sample, matching review cards."""
    index = (len(values) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (index - lower)


def _clock(minutes: float) -> str:
    # Rounding a valid 23:59 value must not produce an impossible 24:00 clock.
    rounded = min(1439, int(minutes + .5))
    return f"{rounded // 60:02d}:{rounded % 60:02d}"


def _group_summary(values: list, key: str, sample_size: int) -> tuple[str, str]:
    n = len(values)
    coverage = f"有效 {n}/{sample_size} 个样本"
    if n < MIN_GROUP_SIZE:
        return "样本不足", f"{coverage}，至少需 {MIN_GROUP_SIZE} 个"
    if key == "position_label":
        counts = Counter(values)
        ordered = sorted(counts, key=lambda label: (-counts[label], label))
        summary = "；".join(f"{label} {counts[label] / n:.1%}" for label in ordered[:2])
        details = "；".join(f"{label} {counts[label]}/{n}（{counts[label] / n:.1%}）" for label in ordered)
        return summary, f"{coverage}；组内位置占比：{details}"
    if key == "first_limit_minutes":
        return _clock(mean(values)), f"{coverage}；平均首次封板时间"
    if key == "break_count":
        counts = _bucket_counts(values, key)
        summary = "；".join(f"{label} {count / n:.1%}" for label, count in counts.items())
        details = "；".join(f"{label} {count}/{n}" for label, count in counts.items())
        return summary, f"{coverage}；组内炸板次数分布：{details}"
    unit = "亿元" if key == "float_market_cap" else "%"
    summary = f"{median(values):.1f}{unit}"
    detail = f"{coverage}；中位数；中间50%：{_quartile(values, .25):.1f}–{_quartile(values, .75):.1f}{unit}"
    return summary, detail


def _bucket_counts(values: list, key: str) -> dict[str, int]:
    if key == "position_label":
        return dict(sorted(Counter(values).items()))
    counts = dict.fromkeys((label for _, label in _NUMERIC_BUCKETS[key]), 0)
    for value in values:
        for upper, label in _NUMERIC_BUCKETS[key]:
            if value < upper:
                counts[label] += 1
                break
    return counts


def _rate(positive_count: int, sample_size: int) -> float | None:
    return positive_count / sample_size if sample_size >= MIN_RATE_SIZE else None


def _bucket_stats(positive: list, negative: list, key: str) -> list[ReviewBucketStat]:
    counts = [_bucket_counts(group, key) for group in (positive, negative)]
    labels = sorted(counts[0].keys() | counts[1].keys()) if key == "position_label" else list(counts[0])
    baseline = _rate(len(positive), len(positive) + len(negative))
    buckets = []
    for label in labels:
        pos, neg = counts[0].get(label, 0), counts[1].get(label, 0)
        rate = _rate(pos, pos + neg)
        buckets.append(ReviewBucketStat(
            label=label,
            positive_count=pos,
            negative_count=neg,
            sample_size=pos + neg,
            positive_rate=rate,
            baseline_rate=baseline,
            delta_pp=(rate - baseline) * 100 if rate is not None and baseline is not None else None,
        ))
    return buckets


def _signals(features: list[ReviewFeatureBreakdown], positive_outcome: str) -> list[str]:
    candidates = []
    for index, feature in enumerate(features):
        if min(feature.positive_valid_count, feature.negative_valid_count) < MIN_GROUP_SIZE:
            continue
        supported = [bucket for bucket in feature.buckets if bucket.delta_pp is not None]
        if not supported:
            continue
        # Bucket order breaks ties, followed by fixed feature order between dimensions.
        bucket = max(supported, key=lambda item: item.delta_pp)
        candidates.append((index, feature, bucket))
    candidates.sort(key=lambda item: (-item[2].delta_pp, item[0]))
    signals = []
    for _, feature, bucket in candidates[:3]:
        delta = bucket.delta_pp
        if abs(delta) < .05:
            comparison = "与该项有效样本基准相近"
        else:
            direction = "高" if delta > 0 else "低"
            comparison = f"比该项有效样本基准{direction}{abs(delta):.1f}个百分点"
        signals.append(
            f"本批具有“{feature.label}：{bucket.label}”的样本{positive_outcome}比例为"
            f"{bucket.positive_count}/{bucket.sample_size}（{bucket.positive_rate:.1%}），{comparison}。"
        )
    return signals or ["本批两组有效样本或分桶样本不足，暂不提炼特征差异。"]
