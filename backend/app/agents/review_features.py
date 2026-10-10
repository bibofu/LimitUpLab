"""Three descriptive feature comparisons, without model-generated statistics."""

from collections import Counter
import math
from statistics import mean, median

from app.models import ReviewFeatureCard, ReviewFeatureSummary, ReviewFeatureValue


MIN_SAMPLES = 3


def build_review_feature_summary(
    positive_profiles: list[dict],
    negative_profiles: list[dict],
) -> ReviewFeatureSummary:
    """Inputs are grouped by first-board-to-observed-close return, not next-day labels.

    Each profile is a stock/date sample, not necessarily an independent stock.
    Missing values are excluded separately for each feature.
    """
    return ReviewFeatureSummary(
        positive_count=len(positive_profiles),
        negative_count=len(negative_profiles),
        cards=[
            _position_card(positive_profiles, negative_profiles),
            _market_cap_card(positive_profiles, negative_profiles),
            _first_seal_card(positive_profiles, negative_profiles),
        ],
    )


def _value(n: int, size: int, text: str, detail: str = "") -> ReviewFeatureValue:
    if n < MIN_SAMPLES:
        text = "样本不足"
        detail = f"有效 {n}/{size} 个样本，至少需 {MIN_SAMPLES} 个"
    return ReviewFeatureValue(text=text, detail=detail, valid_count=n, sample_size=size)


def _position_values(profiles: list[dict]) -> list[str]:
    missing = {"", "none", "null", "unknown", "未知", "未分类", "其他", "-", "--", "—", "数据不足"}
    return [
        label.strip() for profile in profiles
        if isinstance(label := profile.get("position_label"), str)
        and label.strip().lower() not in missing
    ]


def _position_card(positive: list[dict], negative: list[dict]) -> ReviewFeatureCard:
    groups = [_position_values(positive), _position_values(negative)]
    counts = [Counter(group) for group in groups]
    # A stable tie break prevents input ordering from changing the comparison label.
    label = min(counts[0], key=lambda item: (-counts[0][item], item)) if len(groups[0]) >= MIN_SAMPLES else None
    values = []
    for group, count, profiles in zip(groups, counts, (positive, negative)):
        text = f"{label} · {count[label] / len(group):.1%}" if label and group else "暂无对照"
        detail = f"{count[label]}/{len(group)} 个有效位置样本" if label else "正收益组位置样本不足"
        values.append(_value(len(group), len(profiles), text, detail))
    if any(len(group) < MIN_SAMPLES for group in groups):
        observation = "任一组有效位置样本少于3个，暂不比较。"
    else:
        difference = counts[0][label] * len(groups[1]) - counts[1][label] * len(groups[0])
        direction = "更高" if difference > 0 else "更低" if difference < 0 else "相同"
        observation = f"正收益组该位置占比{direction}，这是组内占比，不是胜率。"
    return ReviewFeatureCard(
        key="position", label="首板位置", positive=values[0], negative=values[1], observation=observation,
    )


def _numeric_values(profiles: list[dict], key: str) -> list[float]:
    return [
        float(value) for profile in profiles
        if isinstance(value := profile.get(key), (int, float))
        and not isinstance(value, bool) and math.isfinite(value)
    ]


def _quartile(values: list[float], fraction: float) -> float:
    """Use linear interpolation on the sorted observed sample."""
    index = (len(values) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (index - lower)


def _market_cap_card(positive: list[dict], negative: list[dict]) -> ReviewFeatureCard:
    groups = [
        sorted(value / 100_000_000 for value in _numeric_values(profiles, "float_market_cap") if value > 0)
        for profiles in (positive, negative)
    ]
    values = []
    for group, profiles in zip(groups, (positive, negative)):
        values.append(_value(
            len(group), len(profiles), f"{median(group):.1f} 亿元" if group else "",
            f"中间50%：{_quartile(group, .25):.1f}–{_quartile(group, .75):.1f} 亿元" if group else "",
        ))
    if any(len(group) < MIN_SAMPLES for group in groups):
        observation = "任一组有效市值样本少于3个，暂不比较。"
    else:
        medians = [median(group) for group in groups]
        direction = "相近" if f"{medians[0]:.1f}" == f"{medians[1]:.1f}" else "偏大" if medians[0] > medians[1] else "偏小"
        overlaps = max(_quartile(group, .25) for group in groups) <= min(_quartile(group, .75) for group in groups)
        observation = f"正收益组中位市值{direction}，中间50%区间{'重叠' if overlaps else '不重叠'}。"
    return ReviewFeatureCard(
        key="market_cap", label="流通市值（中位数）", positive=values[0], negative=values[1], observation=observation,
    )


def _clock(minutes: float) -> str:
    rounded = int(minutes + .5)
    return f"{rounded // 60:02d}:{rounded % 60:02d}"


def _first_seal_card(positive: list[dict], negative: list[dict]) -> ReviewFeatureCard:
    groups = [
        [value for value in _numeric_values(profiles, "first_limit_minutes") if 0 <= value < 1440]
        for profiles in (positive, negative)
    ]
    values = [
        _value(len(group), len(profiles), _clock(mean(group)) if group else "", "平均首次封板时间")
        for group, profiles in zip(groups, (positive, negative))
    ]
    if any(len(group) < MIN_SAMPLES for group in groups):
        observation = "任一组有效首封样本少于3个，暂不比较。"
    else:
        difference = mean(groups[0]) - mean(groups[1])
        if abs(difference) < 1:
            observation = "两组平均首封时间相近，不代表越早越好。"
        else:
            direction = "晚" if difference > 0 else "早"
            observation = f"正收益组平均首封{direction}约{int(abs(difference) + .5)}分钟，不代表优劣。"
    return ReviewFeatureCard(
        key="first_seal", label="首次封板时间（均值）", positive=values[0], negative=values[1], observation=observation,
    )
