"""Backtest and self-evaluate first-board rating outcomes."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import mean

from app.models import (
    FirstBoardOutcome,
    FirstBoardRating,
    LimitUpEvent,
    RatingBacktestBucket,
    RatingBacktestFailureSample,
    RatingBacktestResponse,
    ScoringPolicy,
)
from app.repositories import SQLiteFirstBoardRepository


RATING_BACKTEST_VERSION = "rating-backtest-entry-open-v2"
RATING_ORDER = ("A", "B", "C", "D")


@dataclass(frozen=True)
class _BacktestSample:
    rating: FirstBoardRating
    outcome: FirstBoardOutcome | None


def build_rating_backtest(
    events: list[LimitUpEvent],
    start_date: date,
    end_date: date,
    first_board_repository: SQLiteFirstBoardRepository | None = None,
    failure_limit: int = 8,
    scoring_policy: ScoringPolicy | None = None,
) -> RatingBacktestResponse:
    """Evaluate first-board rating buckets against persisted outcomes."""

    repository = first_board_repository or SQLiteFirstBoardRepository()
    trade_dates = sorted(
        {
            event.trade_date
            for event in events
            if start_date <= event.trade_date <= end_date
        }
    )
    outcome_lookup = {
        (outcome.base_trade_date, outcome.symbol): outcome
        for outcome in repository.list_outcomes_between(start_date, end_date)
    }
    samples: list[_BacktestSample] = []
    warnings: list[str] = []

    for trade_date in trade_dates:
        from app.agents.first_board import build_first_board_ratings

        ratings = build_first_board_ratings(
            events=events,
            trade_date=trade_date,
            first_board_repository=repository,
            scoring_policy=scoring_policy,
        )
        samples.extend(
            _BacktestSample(
                rating=item,
                outcome=outcome_lookup.get((trade_date, item.facts.symbol)),
            )
            for item in ratings.candidates
        )

    if not samples:
        warnings.append("No first-board rating samples found for the selected range.")

    buckets = _build_buckets(samples)
    failure_samples = _build_failure_samples(samples, failure_limit=failure_limit)
    observations = _build_observations(buckets=buckets, samples=samples)
    return RatingBacktestResponse(
        start_date=start_date,
        end_date=end_date,
        trade_dates=trade_dates,
        sample_size=len(samples),
        outcome_ready_count=sum(1 for sample in samples if _ready(sample.outcome)),
        buckets=buckets,
        failure_samples=failure_samples,
        observations=observations,
        warnings=warnings,
        generated_by=RATING_BACKTEST_VERSION,
    )


def _build_buckets(samples: list[_BacktestSample]) -> list[RatingBacktestBucket]:
    """Aggregate samples by A/B/C/D rating."""

    grouped: dict[str, list[_BacktestSample]] = defaultdict(list)
    for sample in samples:
        grouped[sample.rating.rating].append(sample)

    buckets: list[RatingBacktestBucket] = []
    for rating in RATING_ORDER:
        group = grouped.get(rating, [])
        ready_outcomes = [sample.outcome for sample in group if _ready(sample.outcome)]
        buckets.append(
            RatingBacktestBucket(
                rating=rating,
                sample_size=len(group),
                outcome_ready_count=len(ready_outcomes),
                avg_next_open_pct=_avg([item.next_open_pct for item in ready_outcomes]),
                avg_next_high_pct=_avg([item.next_high_pct for item in ready_outcomes]),
                avg_next_close_pct=_avg([item.next_close_pct for item in ready_outcomes]),
                avg_next_open_to_high_pct=_avg(
                    [item.next_open_to_high_pct for item in ready_outcomes]
                ),
                avg_next_open_to_close_pct=_avg(
                    [item.next_open_to_close_pct for item in ready_outcomes]
                ),
                avg_next_open_to_low_pct=_avg(
                    [item.next_open_to_low_pct for item in ready_outcomes]
                ),
                avg_three_day_high_pct=_avg(
                    [item.three_day_high_pct for item in ready_outcomes]
                ),
                avg_three_day_close_pct=_avg(
                    [item.three_day_close_pct for item in ready_outcomes]
                ),
                avg_three_day_open_to_close_pct=_avg(
                    [item.three_day_open_to_close_pct for item in ready_outcomes]
                ),
                avg_max_drawdown_from_next_open_3d=_avg(
                    [item.max_drawdown_from_next_open_3d for item in ready_outcomes]
                ),
                next_open_to_close_positive_rate=_rate(
                    [
                        item.next_open_to_close_pct > 0
                        for item in ready_outcomes
                        if item.next_open_to_close_pct is not None
                    ]
                ),
                next_open_to_close_large_loss_rate=_rate(
                    [
                        item.next_open_to_close_pct <= -3
                        for item in ready_outcomes
                        if item.next_open_to_close_pct is not None
                    ]
                ),
                promoted_to_second_board_rate=_rate(
                    [item.promoted_to_second_board for item in ready_outcomes]
                ),
            )
        )
    return buckets


def _build_failure_samples(
    samples: list[_BacktestSample],
    failure_limit: int,
) -> list[RatingBacktestFailureSample]:
    """Pick high-rated samples whose post-board outcome was weak."""

    high_rated = [
        sample
        for sample in samples
        if sample.rating.rating in {"A", "B"}
        and _ready(sample.outcome)
        and sample.outcome.next_open_to_close_pct is not None
        and sample.outcome.next_open_to_close_pct < 0
    ]
    high_rated.sort(
        key=lambda sample: (
            sample.outcome.three_day_open_to_close_pct
            if sample.outcome and sample.outcome.three_day_open_to_close_pct is not None
            else sample.outcome.next_open_to_close_pct
            if sample.outcome and sample.outcome.next_open_to_close_pct is not None
            else 0
        )
    )
    return [
        RatingBacktestFailureSample(
            symbol=sample.rating.facts.symbol,
            name=sample.rating.facts.name,
            trade_date=sample.rating.facts.trade_date,
            rating=sample.rating.rating,
            score=sample.rating.score,
            next_close_pct=sample.outcome.next_close_pct,
            next_open_to_close_pct=sample.outcome.next_open_to_close_pct,
            next_open_to_low_pct=sample.outcome.next_open_to_low_pct,
            three_day_close_pct=sample.outcome.three_day_close_pct,
            three_day_open_to_close_pct=sample.outcome.three_day_open_to_close_pct,
            promoted_to_second_board=sample.outcome.promoted_to_second_board,
            reasons=sample.rating.reasons,
            risks=sample.rating.risks,
        )
        for sample in high_rated[: max(failure_limit, 0)]
        if sample.outcome is not None
    ]


def _build_observations(
    buckets: list[RatingBacktestBucket],
    samples: list[_BacktestSample],
) -> list[str]:
    """Build concise self-evaluation notes from bucket metrics."""

    observations: list[str] = []
    ready_count = sum(1 for sample in samples if _ready(sample.outcome))
    if samples and ready_count / len(samples) < 0.5:
        observations.append("Outcome coverage is still thin; conclusions should be treated as directional.")

    a_bucket = next((bucket for bucket in buckets if bucket.rating == "A"), None)
    b_bucket = next((bucket for bucket in buckets if bucket.rating == "B"), None)
    if (
        a_bucket
        and b_bucket
        and a_bucket.avg_next_open_to_close_pct is not None
        and b_bucket.avg_next_open_to_close_pct is not None
    ):
        if a_bucket.avg_next_open_to_close_pct >= b_bucket.avg_next_open_to_close_pct:
            observations.append("A 级样本的次日开盘到收盘收益不低于 B 级。")
        else:
            observations.append("A 级样本的次日开盘到收盘收益低于 B 级，评分排序需要重审。")

    if a_bucket and a_bucket.promoted_to_second_board_rate is not None:
        observations.append(
            f"A 级样本次日晋级率为 {a_bucket.promoted_to_second_board_rate:.0%}。"
        )
    if not observations:
        observations.append("Not enough ready outcome data to form a reliable self-evaluation yet.")
    return observations


def _ready(outcome: FirstBoardOutcome | None) -> bool:
    """Return whether an outcome has enough data for backtest aggregation."""

    return bool(outcome and outcome.next_day_ready)


def _avg(values: list[float | None]) -> float | None:
    """Average non-null percentages with two decimals."""

    present = [value for value in values if value is not None]
    return round(mean(present), 2) if present else None


def _rate(values: list[bool]) -> float | None:
    """Return true-rate rounded to four decimals."""

    return round(sum(1 for value in values if value) / len(values), 4) if values else None
