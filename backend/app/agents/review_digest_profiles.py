"""Deterministic group profiles; each dimension has its own observed denominator."""

from collections import Counter
from datetime import time
import math
from typing import Literal

from app.review_digest_models import (
    DigestBucket, DigestDistribution, DigestGroup, DigestObservation, DigestStock,
)


_FIRST = (
    ("position_label", "首板位置"), ("industry", "首板行业"), ("concepts", "首板题材"),
    ("float_market_cap", "首板流通市值"), ("first_limit_time", "首板首次封板时间"),
    ("break_count", "首板炸板次数"), ("turnover_rate", "首板换手率"),
)
_SECOND = (
    ("second_board_shape", "二板形态"), ("second_limit_time", "二板首次封板时间"),
    ("second_open_pct", "二板开盘涨幅"),
    ("second_break_count", "二板炸板次数"), ("second_turnover_rate", "二板换手率"),
)
_SCOPE_LABELS = {"excellent": "优秀组", "weak": "较差组", "leaders": "高标组"}
_MISSING = {"", "none", "null", "unknown", "未知", "未分类", "其他", "结构不明", "数据不足", "-", "--", "—"}
_TIME_LABELS = ("09:25竞价", "09:30–10:00", "10:00–11:30", "午后")
_NUMERIC_BUCKETS = {
    "float_market_cap": ((30, "<30亿元"), (60, "30–60亿元"), (100, "60–100亿元"), (math.inf, "≥100亿元")),
    "break_count": ((1, "0次"), (2, "1次"), (math.inf, "≥2次")),
    "turnover_rate": ((5, "<5%"), (10, "5–10%"), (20, "10–20%"), (math.inf, "≥20%")),
    "second_open_pct": ((0, "低开"), (3, "0–3%"), (7, "3–7%"), (math.inf, "≥7%")),
}


def build_digest_group(
    scope: Literal["excellent", "weak", "leaders"],
    stocks: list[DigestStock],
    baseline: list[DigestStock] | None = None,
) -> DigestGroup:
    """Build separate marginal profiles, never a joint feature or success probability.

    Candidate comparison uses all supplied candidate records as the baseline,
    including this group. Leaders have no control group and are descriptive only.
    """
    if scope not in _SCOPE_LABELS:
        raise ValueError(f"Unknown digest scope: {scope}")
    group_label = _SCOPE_LABELS[scope]
    if not stocks:
        return DigestGroup(scope=scope, sample_size=0, summary=f"{group_label}暂无可归纳样本。")
    baseline = None if scope == "leaders" else baseline
    dimensions = _FIRST + (_SECOND if scope == "leaders" else ())
    distributions = [_distribution(key, label, stocks, baseline) for key, label in dimensions]
    observations = [
        observation for distribution in distributions
        if (observation := _observation(scope, distribution)) is not None
    ]
    # At most one observation per dimension keeps the candidate pool diverse.
    first_observations = [item for item in observations if not item.dimension.startswith("second_")]
    second_observations = [item for item in observations if item.dimension.startswith("second_")]
    observations = (first_observations + second_observations)[:10]
    second_selected = second_observations[:2]
    if second_observations and second_observations[0].dimension == "second_board_shape":
        second_selected = second_observations[:1] + [
            item for item in second_observations if item.dimension in {"second_limit_time", "second_open_pct"}
        ][:1]
    selected = first_observations[:3] + (second_selected if scope == "leaders" else [])
    selected_ids = [item.id for item in selected]
    if selected:
        summary = (
            "先看首板时的画像，再看二板当天如何封板。" if scope == "leaders"
            else "下面几项首板特征与全部候选的分布存在差异。"
        )
    elif scope == "leaders":
        summary = "高标组有效样本或同类特征支持不足，暂不归纳共同画像。"
    elif baseline is None:
        summary = f"{group_label}缺少全部候选对照，暂不归纳特征差异。"
    else:
        summary = "本组样本偏少或特征差异不突出，可展开查看具体股票与完整分布。"
    notes = [
        "各特征分别统计，不表示这些特征同时出现在同一批股票上。",
        "题材为多标签，每条首板记录的相同题材只计一次，各桶占比合计可能超过100%。",
        "样本按首板记录统计，同一股票不同轮次分别计入。",
    ]
    if scope == "leaders":
        notes.append("高标组仅描述已有样本，不代表特征优势或未来成功概率；二板仅使用有记录的事实。")
    elif baseline is not None:
        notes.append("全部候选对照包含本组；各维度仅使用自己的有效样本，缺失不计入分母。")
    return DigestGroup(
        scope=scope, sample_size=len(stocks), summary=summary,
        observations=observations, selected_observation_ids=selected_ids,
        distributions=distributions, stocks=list(stocks), notes=notes,
    )


def _category(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value.lower() not in _MISSING else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def _base_key(key: str) -> str:
    return {"second_break_count": "break_count", "second_turnover_rate": "turnover_rate"}.get(key, key)


def _time_bucket(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = time.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return None
    seconds = parsed.hour * 3600 + parsed.minute * 60 + parsed.second + parsed.microsecond / 1e6
    if 565 * 60 <= seconds < 566 * 60:
        return _TIME_LABELS[0]
    if 570 * 60 <= seconds < 600 * 60:
        return _TIME_LABELS[1]
    if 600 * 60 <= seconds <= 690 * 60:
        return _TIME_LABELS[2]
    if 780 * 60 <= seconds <= 900 * 60:
        return _TIME_LABELS[3]
    return None


def _labels(stock: DigestStock, key: str) -> list[str]:
    value = getattr(stock, key)
    if key == "concepts":
        return sorted({label for item in value if (label := _category(item)) is not None})
    if key in {"first_limit_time", "second_limit_time"}:
        label = _time_bucket(value)
        return [label] if label is not None else []
    base_key = _base_key(key)
    if base_key not in _NUMERIC_BUCKETS:
        label = _category(value)
        return [label] if label is not None else []
    number = _number(value)
    if number is None:
        return []
    if base_key in {"float_market_cap", "turnover_rate"} and number <= 0:
        return []
    if base_key == "break_count" and (number < 0 or not number.is_integer()):
        return []
    if base_key == "float_market_cap":
        number /= 100_000_000
    return [next(label for upper, label in _NUMERIC_BUCKETS[base_key] if number < upper)]


def _counts(stocks: list[DigestStock], key: str) -> tuple[Counter, int]:
    counts: Counter[str] = Counter()
    valid_count = 0
    for stock in stocks:
        labels = _labels(stock, key)
        if labels:
            valid_count += 1
            counts.update(labels)
    return counts, valid_count


def _distribution(key: str, label: str, stocks: list[DigestStock], baseline: list[DigestStock] | None) -> DigestDistribution:
    counts, valid = _counts(stocks, key)
    baseline_counts, baseline_valid = _counts(baseline, key) if baseline is not None else (Counter(), None)
    base_key = _base_key(key)
    if key in {"first_limit_time", "second_limit_time"}:
        labels = _TIME_LABELS
    elif base_key in _NUMERIC_BUCKETS:
        labels = [item[1] for item in _NUMERIC_BUCKETS[base_key]]
    else:
        labels = sorted(counts.keys() | baseline_counts.keys(), key=lambda item: (-counts[item], item))
    buckets = [DigestBucket(
        label=item, count=counts[item], share=counts[item] / valid if valid else None,
        baseline_count=baseline_counts[item] if baseline is not None else None,
        baseline_share=baseline_counts[item] / baseline_valid if baseline_valid else None,
    ) for item in labels]
    note = f"本组有效{valid}/{len(stocks)}条，缺失{len(stocks) - valid}条。"
    if baseline is not None:
        note += f"全部候选有效{baseline_valid}/{len(baseline)}条，缺失{len(baseline) - baseline_valid}条。"
    if key == "concepts":
        note += "题材多标签，占比合计可能超过100%。"
    return DigestDistribution(
        key=key, label=label, valid_count=valid, total_count=len(stocks),
        baseline_valid_count=baseline_valid, baseline_total_count=len(baseline) if baseline is not None else None,
        buckets=buckets, note=note,
    )


def _observation(scope: str, distribution: DigestDistribution) -> DigestObservation | None:
    if distribution.valid_count < 3:
        return None
    supported = [bucket for bucket in distribution.buckets if bucket.count >= 3]
    if scope != "leaders":
        if not distribution.baseline_valid_count or distribution.baseline_valid_count < 3:
            return None
        supported = [bucket for bucket in supported
                     if bucket.baseline_share is not None
                     and abs(bucket.share - bucket.baseline_share) >= .1 - 1e-12]
    if not supported:
        return None
    if scope == "leaders":
        bucket = min(supported, key=lambda item: (-item.count, item.label))
    else:
        bucket = min(supported, key=lambda item: (-abs(item.share - item.baseline_share), -item.count, item.label))
    text = (
        f"{_SCOPE_LABELS[scope]}{distribution.label}为“{bucket.label}”的有"
        f"{bucket.count}/{distribution.valid_count}条（{bucket.share:.1%}）"
    )
    if scope != "leaders":
        difference = (bucket.share - bucket.baseline_share) * 100
        text += (
            f"，全部候选为{bucket.baseline_count}/{distribution.baseline_valid_count}条"
            f"（{bucket.baseline_share:.1%}），{'高' if difference > 0 else '低'}{abs(difference):.1f}个百分点"
        )
    return DigestObservation(
        id=f"{scope}:{distribution.key}:{bucket.label}", dimension=distribution.key,
        text=text + "。", support_count=bucket.count, sample_size=distribution.valid_count,
    )
