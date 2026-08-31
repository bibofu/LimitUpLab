"""Promotion-first error diagnosis for the first-board Top-K ranking."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import mean

from app.agents.first_board import build_first_board_ratings
from app.models import (
    FirstBoardOutcome,
    FirstBoardRating,
    LimitUpEvent,
    ScoringErrorCase,
    ScoringErrorDiagnosticResponse,
    ScoringFactorErrorDiagnostic,
    ScoringPolicy,
)
from app.repositories import SQLiteFirstBoardRepository, SQLiteScoringPolicyRepository
from app.services.promotion_labels import resolve_first_board_promotion_labels
from app.services.scoring_policy import FACTOR_KEYS_BY_NAME, FACTOR_NAMES


SCORING_ERROR_DIAGNOSTIC_VERSION = "scoring-error-diagnostic-v2-event-labels"
MIN_RELIABLE_TRADE_DATES = 20
ABLATION_ACTION_THRESHOLD = 0.015
ERROR_SLICE_ACTION_THRESHOLD = 0.12


@dataclass(frozen=True)
class _ReadyCandidate:
    rating: FirstBoardRating
    promoted_to_second_board: bool
    outcome: FirstBoardOutcome | None
    rank: int


def build_scoring_error_diagnostic(
    *,
    events: list[LimitUpEvent],
    start_date: date,
    end_date: date,
    first_board_repository: SQLiteFirstBoardRepository | None = None,
    policy_repository: SQLiteScoringPolicyRepository | None = None,
    scoring_policy: ScoringPolicy | None = None,
    top_k: int = 10,
    sample_limit: int = 10,
) -> ScoringErrorDiagnosticResponse:
    """Compare Top-K misses with promoted omissions and run factor ablations."""

    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    bounded_top_k = max(1, min(top_k, 30))
    bounded_sample_limit = max(0, min(sample_limit, 30))
    repository = first_board_repository or SQLiteFirstBoardRepository()
    registry = policy_repository or SQLiteScoringPolicyRepository(
        repository.database_path
    )
    policy = scoring_policy or registry.ensure_default_policy()
    outcomes = {
        (item.base_trade_date, item.symbol): item
        for item in repository.list_outcomes_between(start_date, end_date)
    }
    promotion_labels = resolve_first_board_promotion_labels(events, outcomes)

    daily_samples: dict[date, list[_ReadyCandidate]] = {}
    candidate_dates = sorted(
        {
            item.trade_date
            for item in events
            if start_date <= item.trade_date <= end_date
        }
    )
    for trade_date in candidate_dates:
        ratings = build_first_board_ratings(
            events=events,
            trade_date=trade_date,
            first_board_repository=repository,
            scoring_policy=policy,
        )
        ranked = [
            _ReadyCandidate(
                rating=rating,
                promoted_to_second_board=promotion_labels[
                    (trade_date, rating.facts.symbol)
                ],
                outcome=outcomes.get((trade_date, rating.facts.symbol)),
                rank=rank,
            )
            for rank, rating in enumerate(ratings.candidates, start=1)
            if (trade_date, rating.facts.symbol) in promotion_labels
        ]
        if not ranked:
            continue
        daily_samples[trade_date] = ranked

    pool_samples = [item for rows in daily_samples.values() for item in rows]
    top_samples = [item for item in pool_samples if item.rank <= bounded_top_k]
    false_positives = [
        item for item in top_samples if not item.promoted_to_second_board
    ]
    false_negatives = [
        item
        for item in pool_samples
        if item.rank > bounded_top_k and item.promoted_to_second_board
    ]
    top_promoted_count = sum(
        item.promoted_to_second_board for item in top_samples
    )
    market_promoted_count = sum(
        item.promoted_to_second_board for item in pool_samples
    )
    top_rate = _rate(top_promoted_count, len(top_samples))
    market_rate = _rate(market_promoted_count, len(pool_samples))
    promotion_delta = _difference(top_rate, market_rate)

    factor_rows = _build_factor_rows(
        daily_samples=daily_samples,
        false_positives=false_positives,
        false_negatives=false_negatives,
        top_k=bounded_top_k,
        baseline_rate=top_rate,
    )
    warnings: list[str] = []
    if len(daily_samples) < MIN_RELIABLE_TRADE_DATES:
        warnings.append(
            f"只有 {len(daily_samples)} 个晋级标签完整交易日，因子调整仅作为影子假设。"
        )
    if not pool_samples:
        warnings.append("当前区间没有可用于误差诊断的次日晋级标签。")

    return ScoringErrorDiagnosticResponse(
        start_date=start_date,
        end_date=end_date,
        scoring_version=policy.version,
        top_k=bounded_top_k,
        trade_date_count=len(daily_samples),
        pool_sample_size=len(pool_samples),
        top_sample_size=len(top_samples),
        top_promoted_count=top_promoted_count,
        top_promotion_rate=top_rate,
        market_promoted_count=market_promoted_count,
        market_promotion_rate=market_rate,
        promotion_rate_delta=promotion_delta,
        false_positive_count=len(false_positives),
        false_negative_count=len(false_negatives),
        false_positive_samples=[
            _error_case(item)
            for item in sorted(
                false_positives,
                key=lambda value: (
                    value.outcome.next_open_to_close_pct
                    if value.outcome is not None
                    and value.outcome.next_open_to_close_pct is not None
                    else 0.0,
                    -value.rating.score,
                ),
            )[:bounded_sample_limit]
        ],
        false_negative_samples=[
            _error_case(item)
            for item in sorted(
                false_negatives,
                key=lambda value: (-value.rating.score, value.rank),
            )[:bounded_sample_limit]
        ],
        factors=factor_rows,
        findings=_build_findings(
            top_rate=top_rate,
            market_rate=market_rate,
            promotion_delta=promotion_delta,
            false_positive_count=len(false_positives),
            false_negative_count=len(false_negatives),
            factors=factor_rows,
        ),
        warnings=warnings,
        generated_by=SCORING_ERROR_DIAGNOSTIC_VERSION,
    )


def _build_factor_rows(
    *,
    daily_samples: dict[date, list[_ReadyCandidate]],
    false_positives: list[_ReadyCandidate],
    false_negatives: list[_ReadyCandidate],
    top_k: int,
    baseline_rate: float | None,
) -> list[ScoringFactorErrorDiagnostic]:
    false_positive_scores = _factor_score_groups(false_positives)
    false_negative_scores = _factor_score_groups(false_negatives)
    rows: list[ScoringFactorErrorDiagnostic] = []
    for factor_key, factor_name in FACTOR_NAMES.items():
        false_positive_mean = _average(false_positive_scores[factor_key])
        false_negative_mean = _average(false_negative_scores[factor_key])
        slice_delta = _difference(false_negative_mean, false_positive_mean)
        ablation_promoted = 0
        ablation_count = 0
        for samples in daily_samples.values():
            reranked = sorted(
                samples,
                key=lambda item: (
                    -(
                        item.rating.score
                        - _weighted_factor_score(item.rating, factor_key)
                    ),
                    -item.rating.confidence,
                    item.rating.facts.symbol,
                ),
            )[: min(top_k, len(samples))]
            ablation_count += len(reranked)
            ablation_promoted += sum(
                item.promoted_to_second_board for item in reranked
            )
        ablation_rate = _rate(ablation_promoted, ablation_count)
        ablation_delta = _difference(ablation_rate, baseline_rate)
        recommendation = _recommendation(ablation_delta, slice_delta)
        rows.append(
            ScoringFactorErrorDiagnostic(
                factor_key=factor_key,
                factor_name=factor_name,
                false_positive_mean_score=false_positive_mean,
                false_negative_mean_score=false_negative_mean,
                false_negative_minus_false_positive=slice_delta,
                ablation_top_promotion_rate=ablation_rate,
                ablation_delta=ablation_delta,
                recommendation=recommendation,
                evidence=_factor_evidence(
                    recommendation=recommendation,
                    ablation_delta=ablation_delta,
                    slice_delta=slice_delta,
                ),
            )
        )
    return sorted(
        rows,
        key=lambda item: (
            -abs(item.ablation_delta or 0.0),
            -abs(item.false_negative_minus_false_positive or 0.0),
            item.factor_key,
        ),
    )


def _factor_score_groups(
    samples: list[_ReadyCandidate],
) -> dict[str, list[float]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for sample in samples:
        for item in sample.rating.score_breakdown:
            factor_key = FACTOR_KEYS_BY_NAME.get(item.name)
            if factor_key is not None and item.max_score > 0:
                grouped[factor_key].append(item.score / item.max_score)
    return grouped


def _weighted_factor_score(rating: FirstBoardRating, factor_key: str) -> float:
    return next(
        (
            item.score
            for item in rating.score_breakdown
            if FACTOR_KEYS_BY_NAME.get(item.name) == factor_key
        ),
        0.0,
    )


def _recommendation(
    ablation_delta: float | None,
    slice_delta: float | None,
) -> str:
    if ablation_delta is not None and ablation_delta >= ABLATION_ACTION_THRESHOLD:
        return "decrease"
    if ablation_delta is not None and ablation_delta <= -ABLATION_ACTION_THRESHOLD:
        return "increase"
    if slice_delta is not None and slice_delta >= ERROR_SLICE_ACTION_THRESHOLD:
        return "increase"
    if slice_delta is not None and slice_delta <= -ERROR_SLICE_ACTION_THRESHOLD:
        return "decrease"
    return "neutral"


def _factor_evidence(
    *,
    recommendation: str,
    ablation_delta: float | None,
    slice_delta: float | None,
) -> str:
    action = {
        "increase": "提高关注",
        "decrease": "降低关注",
        "neutral": "暂不调整",
    }[recommendation]
    ablation_text = (
        f"移除后 Top10 晋级率变化 {ablation_delta * 100:+.1f} 个百分点"
        if ablation_delta is not None
        else "缺少可比较的消融结果"
    )
    slice_text = (
        f"漏选晋级与高分误选的归一化得分差 {slice_delta:+.2f}"
        if slice_delta is not None
        else "误选或漏选样本不足"
    )
    return f"{action}：{ablation_text}；{slice_text}。"


def _error_case(sample: _ReadyCandidate) -> ScoringErrorCase:
    leading_factors = [
        item.name
        for item in sorted(
            sample.rating.score_breakdown,
            key=lambda item: -(item.score / item.max_score if item.max_score else 0.0),
        )[:3]
    ]
    return ScoringErrorCase(
        trade_date=sample.rating.facts.trade_date,
        symbol=sample.rating.facts.symbol,
        name=sample.rating.facts.name,
        rank=sample.rank,
        score=sample.rating.score,
        promoted_to_second_board=sample.promoted_to_second_board,
        next_open_to_close_pct=(
            sample.outcome.next_open_to_close_pct
            if sample.outcome is not None
            else None
        ),
        leading_factors=leading_factors,
    )


def _build_findings(
    *,
    top_rate: float | None,
    market_rate: float | None,
    promotion_delta: float | None,
    false_positive_count: int,
    false_negative_count: int,
    factors: list[ScoringFactorErrorDiagnostic],
) -> list[str]:
    if top_rate is None or market_rate is None or promotion_delta is None:
        return ["晋级标签样本不足，暂时无法判断 Top10 是否优于同期首板池。"]
    findings = [
        (
            f"Top10 一进二为 {top_rate:.1%}，同期全部首板为 {market_rate:.1%}，"
            f"相对变化 {promotion_delta * 100:+.1f} 个百分点。"
        ),
        f"识别到 {false_positive_count} 个高分误选和 {false_negative_count} 个晋级漏选。",
    ]
    actionable = [item for item in factors if item.recommendation != "neutral"][:3]
    if actionable:
        findings.append(
            "当前仅值得进入影子验证的因子方向："
            + "、".join(
                f"{item.factor_name}{'上调' if item.recommendation == 'increase' else '下调'}"
                for item in actionable
            )
            + "。"
        )
    else:
        findings.append("逐因子消融尚未给出足够明确的调整方向，保持 Champion 不变。")
    return findings


def _average(values: list[float]) -> float | None:
    return round(mean(values), 4) if values else None


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _difference(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(left - right, 4)
