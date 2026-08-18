"""Constrained walk-forward optimization for first-board scoring policies."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from statistics import mean
from uuid import uuid4

from app.models import (
    FirstBoardOutcome,
    FirstBoardRating,
    LimitUpEvent,
    ScoringPolicy,
    ScoringPolicyComparison,
    ScoringPolicyMetrics,
    ScoringPolicyOptimizationResponse,
    ScoringPolicyRegistryResponse,
)
from app.repositories import SQLiteFirstBoardRepository, SQLiteScoringPolicyRepository
from app.services.scoring_policy import (
    FACTOR_KEYS_BY_NAME,
    FACTOR_NAMES,
    SCORING_POLICY_REGISTRY_VERSION,
    validate_policy_factor_keys,
)


SCORING_POLICY_OPTIMIZER_VERSION = "scoring-policy-optimizer-walk-forward-v1"
MIN_TRADE_DATES = 10
MAX_RELATIVE_WEIGHT_CHANGE = 0.12
SEARCH_STRENGTHS = (0.35, 0.7, 1.0)


@dataclass(frozen=True)
class _PolicySample:
    rating: FirstBoardRating
    outcome: FirstBoardOutcome


def optimize_scoring_policy(
    *,
    events: list[LimitUpEvent],
    start_date: date,
    end_date: date,
    first_board_repository: SQLiteFirstBoardRepository | None = None,
    policy_repository: SQLiteScoringPolicyRepository | None = None,
    top_k: int = 10,
    activate_if_eligible: bool = False,
    minimum_trade_dates: int = MIN_TRADE_DATES,
    now: datetime | None = None,
) -> ScoringPolicyOptimizationResponse:
    """Generate, validate and register one bounded Challenger policy."""

    repository = first_board_repository or SQLiteFirstBoardRepository()
    registry = policy_repository or SQLiteScoringPolicyRepository(repository.database_path)
    champion = registry.ensure_default_policy()
    validate_policy_factor_keys(champion)

    outcomes = {
        (item.base_trade_date, item.symbol): item
        for item in repository.list_outcomes_between(start_date, end_date)
        if item.next_day_ready and item.next_open_to_close_pct is not None
    }
    outcome_dates = {trade_date for trade_date, _ in outcomes}
    trade_dates = sorted(
        {
            event.trade_date
            for event in events
            if start_date <= event.trade_date <= end_date
            and event.trade_date in outcome_dates
        }
    )
    if len(trade_dates) < max(minimum_trade_dates, 8):
        raise ValueError(
            "Not enough outcome-ready trade dates for policy optimization: "
            f"{len(trade_dates)} available, {max(minimum_trade_dates, 8)} required."
        )

    train_dates, validation_dates, test_dates = _split_trade_dates(trade_dates)
    factor_correlations = _factor_correlations(
        events=events,
        dates=train_dates,
        outcomes=outcomes,
        policy=champion,
        repository=repository,
    )

    timestamp = now or datetime.now(timezone.utc)
    validation_candidates: list[tuple[ScoringPolicy, ScoringPolicyMetrics]] = []
    for index, strength in enumerate(SEARCH_STRENGTHS, start=1):
        weights = _candidate_weights(champion.factor_weights, factor_correlations, strength)
        candidate = _challenger_policy(
            champion=champion,
            weights=weights,
            correlations=factor_correlations,
            train_dates=train_dates,
            created_at=timestamp,
            version_suffix=f"search-{index}",
        )
        metrics = evaluate_scoring_policy(
            events=events,
            dates=validation_dates,
            outcomes=outcomes,
            policy=candidate,
            repository=repository,
            top_k=top_k,
        )
        validation_candidates.append((candidate, metrics))

    selected, _ = max(
        validation_candidates,
        key=lambda item: (
            item[1].objective_score
            if item[1].objective_score is not None
            else float("-inf")
        ),
    )
    challenger = selected.model_copy(
        update={
            "version": _challenger_version(
                champion.version,
                selected.factor_weights,
                timestamp,
            )
        }
    )

    champion_test = evaluate_scoring_policy(
        events=events,
        dates=test_dates,
        outcomes=outcomes,
        policy=champion,
        repository=repository,
        top_k=top_k,
    )
    challenger_test = evaluate_scoring_policy(
        events=events,
        dates=test_dates,
        outcomes=outcomes,
        policy=challenger,
        repository=repository,
        top_k=top_k,
    )
    comparison = _compare_policies(champion_test, challenger_test)
    registry.upsert_policy(challenger)

    activated = False
    if activate_if_eligible and comparison.promotion_eligible:
        challenger = registry.promote_policy(challenger.version, activated_at=timestamp)
        activated = True

    report = ScoringPolicyOptimizationResponse(
        run_id=f"policy_{uuid4().hex}",
        champion_policy=champion,
        challenger_policy=challenger,
        train_dates=train_dates,
        validation_dates=validation_dates,
        test_dates=test_dates,
        factor_correlations=factor_correlations,
        comparison=comparison,
        activated=activated,
        warnings=_optimization_warnings(trade_dates, comparison, activated),
        generated_by=SCORING_POLICY_OPTIMIZER_VERSION,
    )
    registry.save_optimization_run(report)
    return report


def evaluate_scoring_policy(
    *,
    events: list[LimitUpEvent],
    dates: list[date],
    outcomes: dict[tuple[date, str], FirstBoardOutcome],
    policy: ScoringPolicy,
    repository: SQLiteFirstBoardRepository,
    top_k: int = 10,
) -> ScoringPolicyMetrics:
    """Evaluate Top-K ranking quality without using future dates for fitting."""

    validate_policy_factor_keys(policy)
    top_samples: list[_PolicySample] = []
    pool_samples: list[_PolicySample] = []
    bounded_top_k = max(1, min(top_k, 30))

    from app.agents.first_board import build_first_board_ratings

    for trade_date in dates:
        ratings = build_first_board_ratings(
            events=events,
            trade_date=trade_date,
            first_board_repository=repository,
            scoring_policy=policy,
        )
        daily_pool = [
            _PolicySample(item, outcomes[(trade_date, item.facts.symbol)])
            for item in ratings.candidates
            if (trade_date, item.facts.symbol) in outcomes
        ]
        pool_samples.extend(daily_pool)
        top_symbols = {
            item.facts.symbol for item in ratings.candidates[:bounded_top_k]
        }
        top_samples.extend(
            sample for sample in daily_pool if sample.rating.facts.symbol in top_symbols
        )

    top_next = [sample.outcome.next_open_to_close_pct for sample in top_samples]
    top_three = [sample.outcome.three_day_open_to_close_pct for sample in top_samples]
    top_drawdown = [
        sample.outcome.max_drawdown_from_next_open_3d for sample in top_samples
    ]
    pool_next = [sample.outcome.next_open_to_close_pct for sample in pool_samples]
    avg_next = _average(top_next)
    positive_rate = _positive_rate(top_next)
    avg_three = _average(top_three)
    avg_drawdown = _average(top_drawdown)
    pool_avg = _average(pool_next)
    excess = (
        round(avg_next - pool_avg, 4)
        if avg_next is not None and pool_avg is not None
        else None
    )
    objective = _objective_score(
        avg_next=avg_next,
        positive_rate=positive_rate,
        avg_three=avg_three,
        avg_drawdown=avg_drawdown,
        excess=excess,
    )
    return ScoringPolicyMetrics(
        policy_version=policy.version,
        trade_date_count=len(dates),
        pool_sample_size=len(pool_samples),
        top_sample_size=len(top_samples),
        top_k=bounded_top_k,
        avg_next_open_to_close_pct=avg_next,
        positive_rate=positive_rate,
        avg_three_day_open_to_close_pct=avg_three,
        avg_max_drawdown_from_next_open_3d=avg_drawdown,
        pool_avg_next_open_to_close_pct=pool_avg,
        excess_next_open_to_close_pct=excess,
        objective_score=objective,
    )


def build_scoring_policy_registry(
    repository: SQLiteScoringPolicyRepository | None = None,
    *,
    limit: int = 20,
) -> ScoringPolicyRegistryResponse:
    """Return the active Champion and recent policy history."""

    registry = repository or SQLiteScoringPolicyRepository()
    champion = registry.ensure_default_policy()
    return ScoringPolicyRegistryResponse(
        champion=champion,
        policies=registry.list_policies(limit=limit),
        generated_by=SCORING_POLICY_REGISTRY_VERSION,
    )


def _factor_correlations(
    *,
    events: list[LimitUpEvent],
    dates: list[date],
    outcomes: dict[tuple[date, str], FirstBoardOutcome],
    policy: ScoringPolicy,
    repository: SQLiteFirstBoardRepository,
) -> dict[str, float]:
    """Correlate normalized factor scores with date-centered next-day outcomes."""

    from app.agents.first_board import build_first_board_ratings

    pairs: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for trade_date in dates:
        ratings = build_first_board_ratings(
            events=events,
            trade_date=trade_date,
            first_board_repository=repository,
            scoring_policy=policy,
        )
        ready = [
            (item, outcomes[(trade_date, item.facts.symbol)])
            for item in ratings.candidates
            if (trade_date, item.facts.symbol) in outcomes
        ]
        targets = [outcome.next_open_to_close_pct for _, outcome in ready]
        daily_mean = _average(targets)
        if daily_mean is None:
            continue
        for rating, outcome in ready:
            target = float(outcome.next_open_to_close_pct) - daily_mean
            for item in rating.score_breakdown:
                key = FACTOR_KEYS_BY_NAME.get(item.name)
                if key is None or item.max_score <= 0:
                    continue
                pairs[key].append((item.score / item.max_score, target))

    return {
        key: round(_pearson(pairs.get(key, [])), 4)
        for key in FACTOR_NAMES
    }


def _candidate_weights(
    champion_weights: dict[str, float],
    correlations: dict[str, float],
    strength: float,
) -> dict[str, float]:
    adjusted = {
        key: weight
        * (
            1
            + max(
                -MAX_RELATIVE_WEIGHT_CHANGE,
                min(MAX_RELATIVE_WEIGHT_CHANGE, correlations.get(key, 0.0) * strength),
            )
        )
        for key, weight in champion_weights.items()
    }
    total = sum(adjusted.values())
    normalized = {key: round(value * 100 / total, 4) for key, value in adjusted.items()}
    last_key = next(reversed(normalized))
    normalized[last_key] = round(
        normalized[last_key] + (100 - sum(normalized.values())),
        4,
    )
    return normalized


def _challenger_policy(
    *,
    champion: ScoringPolicy,
    weights: dict[str, float],
    correlations: dict[str, float],
    train_dates: list[date],
    created_at: datetime,
    version_suffix: str,
) -> ScoringPolicy:
    changed = sorted(
        weights,
        key=lambda key: abs(weights[key] - champion.factor_weights[key]),
        reverse=True,
    )[:5]
    rationale = [
        (
            f"{key}: corr={correlations.get(key, 0):+.3f}, "
            f"weight {champion.factor_weights[key]:.2f}->{weights[key]:.2f}"
        )
        for key in changed
    ]
    return ScoringPolicy(
        version=f"{champion.version}-{version_suffix}",
        parent_version=champion.version,
        status="challenger",
        factor_weights=weights,
        source="optimizer",
        rationale=rationale,
        training_start_date=train_dates[0],
        training_end_date=train_dates[-1],
        created_at=created_at,
    )


def _challenger_version(
    champion_version: str,
    weights: dict[str, float],
    timestamp: datetime,
) -> str:
    payload = json.dumps(weights, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]
    return f"{champion_version}-challenger-{timestamp:%Y%m%d}-{digest}"


def _split_trade_dates(dates: list[date]) -> tuple[list[date], list[date], list[date]]:
    count = len(dates)
    train_end = max(4, int(count * 0.6))
    validation_end = max(train_end + 2, int(count * 0.8))
    validation_end = min(validation_end, count - 2)
    return dates[:train_end], dates[train_end:validation_end], dates[validation_end:]


def _compare_policies(
    champion: ScoringPolicyMetrics,
    challenger: ScoringPolicyMetrics,
) -> ScoringPolicyComparison:
    objective_delta = _difference(challenger.objective_score, champion.objective_score)
    positive_delta = _difference(challenger.positive_rate, champion.positive_rate)
    drawdown_delta = _difference(
        challenger.avg_max_drawdown_from_next_open_3d,
        champion.avg_max_drawdown_from_next_open_3d,
    )
    reasons: list[str] = []
    if challenger.trade_date_count < 4:
        reasons.append("测试段少于 4 个独立交易日，禁止晋级。")
    if challenger.top_sample_size < 20:
        reasons.append("测试段有效 Top 样本少于 20，禁止晋级。")
    if objective_delta is None or objective_delta < 0.05:
        reasons.append("测试段综合目标提升不足 0.05。")
    if positive_delta is None or positive_delta < -0.02:
        reasons.append("Top 样本正收益率下降超过允许范围。")
    if drawdown_delta is not None and drawdown_delta < -0.3:
        reasons.append("三日最大不利波动恶化超过 0.3 个百分点。")
    return ScoringPolicyComparison(
        champion=champion,
        challenger=challenger,
        objective_delta=objective_delta,
        positive_rate_delta=positive_delta,
        drawdown_delta=drawdown_delta,
        promotion_eligible=not reasons,
        gate_reasons=reasons or ["全部样本外晋级门槛通过。"],
    )


def _objective_score(
    *,
    avg_next: float | None,
    positive_rate: float | None,
    avg_three: float | None,
    avg_drawdown: float | None,
    excess: float | None,
) -> float | None:
    if avg_next is None or positive_rate is None:
        return None
    value = (
        avg_next
        + 0.35 * (avg_three or 0.0)
        + 2.0 * (positive_rate - 0.5)
        + 0.25 * (avg_drawdown or 0.0)
        + 0.5 * (excess or 0.0)
    )
    return round(value, 4)


def _pearson(pairs: list[tuple[float, float]]) -> float:
    if len(pairs) < 8:
        return 0.0
    xs = [item[0] for item in pairs]
    ys = [item[1] for item in pairs]
    mean_x = mean(xs)
    mean_y = mean(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    denominator = math.sqrt(
        sum((x - mean_x) ** 2 for x in xs)
        * sum((y - mean_y) ** 2 for y in ys)
    )
    return numerator / denominator if denominator else 0.0


def _average(values: list[float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return round(mean(present), 4) if present else None


def _positive_rate(values: list[float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    if not present:
        return None
    return round(sum(value > 0 for value in present) / len(present), 4)


def _difference(left: float | None, right: float | None) -> float | None:
    return round(left - right, 4) if left is not None and right is not None else None


def _optimization_warnings(
    trade_dates: list[date],
    comparison: ScoringPolicyComparison,
    activated: bool,
) -> list[str]:
    warnings = [
        "策略参数来自历史样本，不代表未来收益，必须持续进行样本外验证。"
    ]
    if len(trade_dates) < 60:
        warnings.append(
            f"当前仅有 {len(trade_dates)} 个结果完整交易日，市场环境覆盖仍然偏少。"
        )
    if comparison.promotion_eligible and not activated:
        warnings.append("Challenger 已通过门槛，但保持影子状态，尚未替换 Champion。")
    if not comparison.promotion_eligible:
        warnings.append("Challenger 未通过全部样本外门槛，禁止自动晋级。")
    return warnings
