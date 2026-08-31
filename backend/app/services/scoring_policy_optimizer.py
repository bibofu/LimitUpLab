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
    ScoringWalkForwardFold,
)
from app.repositories import SQLiteFirstBoardRepository, SQLiteScoringPolicyRepository
from app.services.promotion_labels import resolve_first_board_promotion_labels
from app.services.scoring_policy import (
    FACTOR_KEYS_BY_NAME,
    FACTOR_NAMES,
    SCORING_POLICY_REGISTRY_VERSION,
    validate_policy_factor_keys,
)


SCORING_POLICY_OPTIMIZER_VERSION = "scoring-policy-optimizer-v5-event-labels"
MIN_TRADE_DATES = 10
MIN_PROMOTION_TRADE_DATES = 60
MAX_RELATIVE_WEIGHT_CHANGE = 0.12
SEARCH_STRENGTHS = (0.35, 0.7, 1.0)
TARGET_WEIGHTS = {
    "next_open_to_close": 0.20,
    "promotion": 0.60,
    "downside_protection": 0.20,
}
LARGE_LOSS_THRESHOLD_PCT = -3.0


@dataclass(frozen=True)
class _PolicySample:
    rating: FirstBoardRating
    promoted_to_second_board: bool
    outcome: FirstBoardOutcome | None = None


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
    }
    promotion_labels = resolve_first_board_promotion_labels(events, outcomes)
    promotion_dates = {trade_date for trade_date, _ in promotion_labels}
    trade_dates = sorted(
        {
            event.trade_date
            for event in events
            if start_date <= event.trade_date <= end_date
            and event.trade_date in promotion_dates
        }
    )
    if len(trade_dates) < max(minimum_trade_dates, 8):
        raise ValueError(
            "Not enough promotion-ready trade dates for policy optimization: "
            f"{len(trade_dates)} available, {max(minimum_trade_dates, 8)} required."
        )

    timestamp = now or datetime.now(timezone.utc)
    fold_reports: list[ScoringWalkForwardFold] = []
    fold_champions: list[ScoringPolicyMetrics] = []
    fold_challengers: list[ScoringPolicyMetrics] = []
    latest_selected: ScoringPolicy | None = None
    latest_target_correlations: dict[str, dict[str, float]] = {}
    latest_split: tuple[list[date], list[date], list[date]] | None = None

    for fold_index, split in enumerate(_walk_forward_splits(trade_dates), start=1):
        train_dates, validation_dates, test_dates = split
        target_correlations = _factor_target_correlations(
            events=events,
            dates=train_dates,
            outcomes=outcomes,
            promotion_labels=promotion_labels,
            policy=champion,
            repository=repository,
        )
        factor_correlations = target_correlations["composite"]
        validation_candidates: list[
            tuple[float, ScoringPolicy, ScoringPolicyMetrics]
        ] = []
        for search_index, strength in enumerate(SEARCH_STRENGTHS, start=1):
            weights = _candidate_weights(
                champion.factor_weights,
                factor_correlations,
                strength,
            )
            candidate = _challenger_policy(
                champion=champion,
                weights=weights,
                target_correlations=target_correlations,
                train_dates=train_dates,
                created_at=timestamp,
                version_suffix=f"fold-{fold_index}-search-{search_index}",
            )
            metrics = evaluate_scoring_policy(
                events=events,
                dates=validation_dates,
                outcomes=outcomes,
                promotion_labels=promotion_labels,
                policy=candidate,
                repository=repository,
                top_k=top_k,
            )
            validation_candidates.append((strength, candidate, metrics))

        selected_strength, selected, _ = max(
            validation_candidates,
            key=lambda item: _policy_selection_key(item[2]),
        )
        champion_test = evaluate_scoring_policy(
            events=events,
            dates=test_dates,
            outcomes=outcomes,
            promotion_labels=promotion_labels,
            policy=champion,
            repository=repository,
            top_k=top_k,
        )
        challenger_test = evaluate_scoring_policy(
            events=events,
            dates=test_dates,
            outcomes=outcomes,
            promotion_labels=promotion_labels,
            policy=selected,
            repository=repository,
            top_k=top_k,
        )
        fold_reports.append(
            ScoringWalkForwardFold(
                fold_index=fold_index,
                train_dates=train_dates,
                validation_dates=validation_dates,
                test_dates=test_dates,
                selected_strength=selected_strength,
                champion_metrics=champion_test,
                challenger_metrics=challenger_test,
            )
        )
        fold_champions.append(champion_test)
        fold_challengers.append(challenger_test)
        latest_selected = selected
        latest_target_correlations = target_correlations
        latest_split = split

    if latest_selected is None or latest_split is None:
        raise ValueError("No valid walk-forward folds could be built.")
    train_dates, validation_dates, test_dates = latest_split
    challenger = latest_selected.model_copy(
        update={
            "version": _challenger_version(
                champion.version,
                latest_selected.factor_weights,
                timestamp,
            )
        }
    )
    champion_test = _aggregate_metrics(champion.version, fold_champions)
    challenger_test = _aggregate_metrics(challenger.version, fold_challengers)
    comparison = _compare_policies(
        champion_test,
        challenger_test,
        eligible_trade_date_count=len(trade_dates),
        fold_count=len(fold_reports),
    )
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
        factor_correlations=latest_target_correlations["composite"],
        target_correlations=latest_target_correlations,
        target_weights=TARGET_WEIGHTS,
        walk_forward_folds=fold_reports,
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
    promotion_labels: dict[tuple[date, str], bool] | None = None,
    policy: ScoringPolicy,
    repository: SQLiteFirstBoardRepository,
    top_k: int = 10,
) -> ScoringPolicyMetrics:
    """Evaluate Top-K ranking quality without using future dates for fitting."""

    validate_policy_factor_keys(policy)
    labels = promotion_labels or resolve_first_board_promotion_labels(events, outcomes)
    top_samples: list[_PolicySample] = []
    pool_samples: list[_PolicySample] = []
    evaluated_dates: set[date] = set()
    bounded_top_k = max(1, min(top_k, 30))

    from app.agents.first_board import build_first_board_ratings

    for trade_date in dates:
        ratings = build_first_board_ratings(
            events=events,
            trade_date=trade_date,
            first_board_repository=repository,
            scoring_policy=policy,
        )
        daily_pool = []
        for item in ratings.candidates:
            key = (trade_date, item.facts.symbol)
            if key not in labels:
                continue
            daily_pool.append(
                _PolicySample(
                    rating=item,
                    promoted_to_second_board=labels[key],
                    outcome=outcomes.get(key),
                )
            )
        if daily_pool:
            evaluated_dates.add(trade_date)
        pool_samples.extend(daily_pool)
        top_symbols = {
            item.facts.symbol for item in ratings.candidates[:bounded_top_k]
        }
        top_samples.extend(
            sample for sample in daily_pool if sample.rating.facts.symbol in top_symbols
        )

    top_outcomes = [sample.outcome for sample in top_samples if sample.outcome]
    pool_outcomes = [sample.outcome for sample in pool_samples if sample.outcome]
    top_next = [outcome.next_open_to_close_pct for outcome in top_outcomes]
    top_three = [outcome.three_day_open_to_close_pct for outcome in top_outcomes]
    top_drawdown = [
        outcome.max_drawdown_from_next_open_3d for outcome in top_outcomes
    ]
    top_next_low = [outcome.next_open_to_low_pct for outcome in top_outcomes]
    promotion_rate = _boolean_rate(
        [sample.promoted_to_second_board for sample in top_samples]
    )
    pool_promotion_rate = _boolean_rate(
        [sample.promoted_to_second_board for sample in pool_samples]
    )
    promotion_lift = _difference(promotion_rate, pool_promotion_rate)
    large_loss_rate = _threshold_rate(
        top_next,
        threshold=LARGE_LOSS_THRESHOLD_PCT,
    )
    pool_next = [outcome.next_open_to_close_pct for outcome in pool_outcomes]
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
        promotion_lift=promotion_lift,
        large_loss_rate=large_loss_rate,
        excess=excess,
    )
    return ScoringPolicyMetrics(
        policy_version=policy.version,
        trade_date_count=len(evaluated_dates),
        pool_sample_size=len(pool_samples),
        top_sample_size=len(top_samples),
        pool_return_sample_size=len([value for value in pool_next if value is not None]),
        top_return_sample_size=len([value for value in top_next if value is not None]),
        top_k=bounded_top_k,
        avg_next_open_to_close_pct=avg_next,
        positive_rate=positive_rate,
        avg_three_day_open_to_close_pct=avg_three,
        avg_max_drawdown_from_next_open_3d=avg_drawdown,
        promoted_to_second_board_rate=promotion_rate,
        pool_promoted_to_second_board_rate=pool_promotion_rate,
        promotion_rate_lift=promotion_lift,
        large_loss_rate=large_loss_rate,
        avg_next_open_to_low_pct=_average(top_next_low),
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


def _factor_target_correlations(
    *,
    events: list[LimitUpEvent],
    dates: list[date],
    outcomes: dict[tuple[date, str], FirstBoardOutcome],
    promotion_labels: dict[tuple[date, str], bool],
    policy: ScoringPolicy,
    repository: SQLiteFirstBoardRepository,
) -> dict[str, dict[str, float]]:
    """Estimate robust factor direction for return, promotion and downside safety."""

    from app.agents.first_board import build_first_board_ratings

    pairs: dict[str, dict[str, list[tuple[float, float]]]] = {
        target: defaultdict(list) for target in TARGET_WEIGHTS
    }
    for trade_date in dates:
        ratings = build_first_board_ratings(
            events=events,
            trade_date=trade_date,
            first_board_repository=repository,
            scoring_policy=policy,
        )
        ready = [
            (
                item,
                promotion_labels[(trade_date, item.facts.symbol)],
                outcomes.get((trade_date, item.facts.symbol)),
            )
            for item in ratings.candidates
            if (trade_date, item.facts.symbol) in promotion_labels
        ]
        target_rows = {
            "next_open_to_close": [
                float(outcome.next_open_to_close_pct)
                for _, _, outcome in ready
                if outcome is not None and outcome.next_open_to_close_pct is not None
            ],
            "promotion": [
                1.0 if promoted else 0.0 for _, promoted, _ in ready
            ],
            "downside_protection": [
                float(outcome.next_open_to_low_pct)
                for _, _, outcome in ready
                if outcome is not None and outcome.next_open_to_low_pct is not None
            ],
        }
        target_means = {
            key: _average(values) for key, values in target_rows.items()
        }
        for rating, promoted, outcome in ready:
            target_values = {
                "next_open_to_close": (
                    outcome.next_open_to_close_pct if outcome is not None else None
                ),
                "promotion": 1.0 if promoted else 0.0,
                "downside_protection": (
                    outcome.next_open_to_low_pct if outcome is not None else None
                ),
            }
            for item in rating.score_breakdown:
                key = FACTOR_KEYS_BY_NAME.get(item.name)
                if key is None or item.max_score <= 0:
                    continue
                normalized_score = item.score / item.max_score
                for target_name, target_value in target_values.items():
                    target_mean = target_means[target_name]
                    if target_value is None or target_mean is None:
                        continue
                    pairs[target_name][key].append(
                        (normalized_score, float(target_value) - target_mean)
                    )

    correlations = {
        target: {
            key: round(_spearman(target_pairs.get(key, [])), 4)
            for key in FACTOR_NAMES
        }
        for target, target_pairs in pairs.items()
    }
    correlations["composite"] = {
        key: round(
            sum(
                TARGET_WEIGHTS[target] * correlations[target][key]
                for target in TARGET_WEIGHTS
            ),
            4,
        )
        for key in FACTOR_NAMES
    }
    return correlations


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
    target_correlations: dict[str, dict[str, float]],
    train_dates: list[date],
    created_at: datetime,
    version_suffix: str,
) -> ScoringPolicy:
    changed = sorted(
        weights,
        key=lambda key: abs(weights[key] - champion.factor_weights[key]),
        reverse=True,
    )[:5]
    composite = target_correlations["composite"]
    rationale = [
        (
            f"{key}: composite={composite.get(key, 0):+.3f}, "
            f"return={target_correlations['next_open_to_close'].get(key, 0):+.3f}, "
            f"promotion={target_correlations['promotion'].get(key, 0):+.3f}, "
            f"safety={target_correlations['downside_protection'].get(key, 0):+.3f}, "
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
    parent_digest = hashlib.sha256(champion_version.encode("utf-8")).hexdigest()[:4]
    return f"first-board-rule-v3-{timestamp:%Y%m%d}-{parent_digest}-{digest}"


def _walk_forward_splits(
    dates: list[date],
) -> list[tuple[list[date], list[date], list[date]]]:
    """Build expanding train windows with chronological validation and tests."""

    count = len(dates)
    validation_size = max(2, count // 10) if count >= 30 else 2
    test_size = max(2, count // 10) if count >= 30 else 2
    minimum_train = max(4, int(count * 0.4))
    minimum_train = min(minimum_train, count - validation_size - test_size)
    if minimum_train < 4:
        return []

    splits: list[tuple[list[date], list[date], list[date]]] = []
    train_end = minimum_train
    while train_end + validation_size + test_size <= count:
        validation_end = train_end + validation_size
        test_end = validation_end + test_size
        splits.append(
            (
                dates[:train_end],
                dates[train_end:validation_end],
                dates[validation_end:test_end],
            )
        )
        train_end += test_size

    final_train_end = count - validation_size - test_size
    final_split = (
        dates[:final_train_end],
        dates[final_train_end : final_train_end + validation_size],
        dates[final_train_end + validation_size :],
    )
    tested_dates = {
        trade_date
        for _, _, split_test_dates in splits
        for trade_date in split_test_dates
    }
    if final_split not in splits and tested_dates.isdisjoint(final_split[2]):
        splits.append(final_split)
    return splits


def _aggregate_metrics(
    policy_version: str,
    metrics: list[ScoringPolicyMetrics],
) -> ScoringPolicyMetrics:
    """Aggregate non-overlapping walk-forward test folds by sample count."""

    top_size = sum(item.top_sample_size for item in metrics)
    pool_size = sum(item.pool_sample_size for item in metrics)
    top_return_size = sum(item.top_return_sample_size for item in metrics)
    pool_return_size = sum(item.pool_return_sample_size for item in metrics)

    def return_weighted(field: str) -> float | None:
        return _weighted_metric(metrics, field, "top_return_sample_size")

    avg_next = return_weighted("avg_next_open_to_close_pct")
    positive_rate = return_weighted("positive_rate")
    avg_three = return_weighted("avg_three_day_open_to_close_pct")
    avg_drawdown = return_weighted("avg_max_drawdown_from_next_open_3d")
    promotion_rate = _weighted_metric(
        metrics,
        "promoted_to_second_board_rate",
        "top_sample_size",
    )
    pool_promotion_rate = _weighted_metric(
        metrics,
        "pool_promoted_to_second_board_rate",
        "pool_sample_size",
    )
    promotion_lift = _difference(promotion_rate, pool_promotion_rate)
    large_loss_rate = return_weighted("large_loss_rate")
    pool_avg = _weighted_metric(
        metrics,
        "pool_avg_next_open_to_close_pct",
        "pool_return_sample_size",
    )
    excess = (
        round(avg_next - pool_avg, 4)
        if avg_next is not None and pool_avg is not None
        else None
    )
    return ScoringPolicyMetrics(
        policy_version=policy_version,
        trade_date_count=sum(item.trade_date_count for item in metrics),
        pool_sample_size=pool_size,
        top_sample_size=top_size,
        pool_return_sample_size=pool_return_size,
        top_return_sample_size=top_return_size,
        top_k=metrics[0].top_k if metrics else 10,
        avg_next_open_to_close_pct=avg_next,
        positive_rate=positive_rate,
        avg_three_day_open_to_close_pct=avg_three,
        avg_max_drawdown_from_next_open_3d=avg_drawdown,
        promoted_to_second_board_rate=promotion_rate,
        pool_promoted_to_second_board_rate=pool_promotion_rate,
        promotion_rate_lift=promotion_lift,
        large_loss_rate=large_loss_rate,
        avg_next_open_to_low_pct=return_weighted("avg_next_open_to_low_pct"),
        pool_avg_next_open_to_close_pct=pool_avg,
        excess_next_open_to_close_pct=excess,
        objective_score=_objective_score(
            avg_next=avg_next,
            positive_rate=positive_rate,
            avg_three=avg_three,
            avg_drawdown=avg_drawdown,
            promotion_lift=promotion_lift,
            large_loss_rate=large_loss_rate,
            excess=excess,
        ),
    )


def _weighted_metric(
    metrics: list[ScoringPolicyMetrics],
    field: str,
    weight_field: str,
) -> float | None:
    values = [
        (float(value), int(getattr(item, weight_field)))
        for item in metrics
        if (value := getattr(item, field)) is not None
        and int(getattr(item, weight_field)) > 0
    ]
    total_weight = sum(weight for _, weight in values)
    if not total_weight:
        return None
    return round(
        sum(value * weight for value, weight in values) / total_weight,
        4,
    )


def _compare_policies(
    champion: ScoringPolicyMetrics,
    challenger: ScoringPolicyMetrics,
    *,
    eligible_trade_date_count: int,
    fold_count: int,
) -> ScoringPolicyComparison:
    objective_delta = _difference(challenger.objective_score, champion.objective_score)
    positive_delta = _difference(challenger.positive_rate, champion.positive_rate)
    drawdown_delta = _difference(
        challenger.avg_max_drawdown_from_next_open_3d,
        champion.avg_max_drawdown_from_next_open_3d,
    )
    promotion_delta = _difference(
        challenger.promoted_to_second_board_rate,
        champion.promoted_to_second_board_rate,
    )
    promotion_lift_delta = _difference(
        challenger.promotion_rate_lift,
        champion.promotion_rate_lift,
    )
    large_loss_delta = _difference(
        challenger.large_loss_rate,
        champion.large_loss_rate,
    )
    reasons: list[str] = []
    if eligible_trade_date_count < MIN_PROMOTION_TRADE_DATES:
        reasons.append(
            f"晋级标签完整交易日只有 {eligible_trade_date_count}，"
            f"少于 v3 晋级门槛 {MIN_PROMOTION_TRADE_DATES}。"
        )
    if fold_count < 3:
        reasons.append("有效 walk-forward 测试折少于 3，禁止晋级。")
    if challenger.trade_date_count < 8:
        reasons.append("累计样本外测试少于 8 个独立交易日，禁止晋级。")
    if challenger.top_sample_size < 50:
        reasons.append("累计样本外 Top 样本少于 50，禁止晋级。")
    if promotion_lift_delta is None or promotion_lift_delta < 0.02:
        reasons.append("Top10 相对全市场的一进二优势提升不足 2 个百分点。")
    if objective_delta is None or objective_delta <= 0:
        reasons.append("晋级优先综合目标没有改善。")
    if positive_delta is None or positive_delta < -0.02:
        reasons.append("Top 样本正收益率下降超过允许范围。")
    if drawdown_delta is not None and drawdown_delta < -0.3:
        reasons.append("三日最大不利波动恶化超过 0.3 个百分点。")
    if promotion_delta is not None and promotion_delta < -0.02:
        reasons.append("二板晋级率下降超过 2 个百分点。")
    if large_loss_delta is not None and large_loss_delta > 0.02:
        reasons.append("次日大跌率上升超过 2 个百分点。")
    return ScoringPolicyComparison(
        champion=champion,
        challenger=challenger,
        objective_delta=objective_delta,
        positive_rate_delta=positive_delta,
        drawdown_delta=drawdown_delta,
        promotion_rate_delta=promotion_delta,
        promotion_lift_delta=promotion_lift_delta,
        large_loss_rate_delta=large_loss_delta,
        promotion_eligible=not reasons,
        gate_reasons=reasons or ["全部样本外晋级门槛通过。"],
    )


def _objective_score(
    *,
    avg_next: float | None,
    positive_rate: float | None,
    avg_three: float | None,
    avg_drawdown: float | None,
    promotion_lift: float | None,
    large_loss_rate: float | None,
    excess: float | None,
) -> float | None:
    if promotion_lift is None:
        return None
    # Express probabilities as percentage points before combining them with
    # percentage returns. Promotion lift remains the dominant objective.
    value = (
        promotion_lift * 100
        + 0.25 * (excess or 0.0)
        + 0.10 * (avg_next or 0.0)
        + 0.05 * (avg_three or 0.0)
        + 0.05 * (avg_drawdown or 0.0)
        - 0.10 * (large_loss_rate or 0.0) * 100
        + 0.02 * ((positive_rate - 0.5) * 100 if positive_rate is not None else 0.0)
    )
    return round(value, 4)


def _policy_selection_key(metrics: ScoringPolicyMetrics) -> tuple[float, ...]:
    """Select validation candidates by promotion lift before risk and return."""

    return (
        metrics.promotion_rate_lift
        if metrics.promotion_rate_lift is not None
        else float("-inf"),
        metrics.promoted_to_second_board_rate
        if metrics.promoted_to_second_board_rate is not None
        else float("-inf"),
        -(metrics.large_loss_rate or 0.0),
        metrics.excess_next_open_to_close_pct
        if metrics.excess_next_open_to_close_pct is not None
        else float("-inf"),
        metrics.avg_max_drawdown_from_next_open_3d
        if metrics.avg_max_drawdown_from_next_open_3d is not None
        else float("-inf"),
    )


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


def _spearman(pairs: list[tuple[float, float]]) -> float:
    """Return rank correlation to reduce sensitivity to extreme return values."""

    if len(pairs) < 8:
        return 0.0
    xs = _ranks([item[0] for item in pairs])
    ys = _ranks([item[1] for item in pairs])
    return _pearson(list(zip(xs, ys)))


def _ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(indexed):
        end = cursor + 1
        while end < len(indexed) and indexed[end][1] == indexed[cursor][1]:
            end += 1
        average_rank = (cursor + end - 1) / 2 + 1
        for index, _ in indexed[cursor:end]:
            ranks[index] = average_rank
        cursor = end
    return ranks


def _average(values: list[float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return round(mean(present), 4) if present else None


def _positive_rate(values: list[float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    if not present:
        return None
    return round(sum(value > 0 for value in present) / len(present), 4)


def _boolean_rate(values: list[bool]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _threshold_rate(
    values: list[float | None],
    *,
    threshold: float,
) -> float | None:
    present = [float(value) for value in values if value is not None]
    if not present:
        return None
    return round(sum(value <= threshold for value in present) / len(present), 4)


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
            f"当前仅有 {len(trade_dates)} 个晋级标签完整交易日，市场环境覆盖仍然偏少。"
        )
    if comparison.promotion_eligible and not activated:
        warnings.append("Challenger 已通过门槛，但保持影子状态，尚未替换 Champion。")
    if not comparison.promotion_eligible:
        warnings.append("Challenger 未通过全部样本外门槛，禁止自动晋级。")
    return warnings
