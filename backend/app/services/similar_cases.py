"""Similar first-board case retrieval and weighted reranking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.models import (
    FirstBoardFeature,
    SimilarCaseDailyBar,
    SimilarCaseOutcome,
    SimilarFirstBoardCase,
    SimilarFirstBoardCasesResponse,
)
from app.repositories import SQLiteFirstBoardRepository


SIMILAR_CASES_VERSION = "similar-first-board-v1"
DEFAULT_WINDOWS = (60, 120, 180, 360)
MIN_RECALL_SIZE = 100
MAX_RECALL_SIZE = 500


@dataclass(frozen=True)
class SimilarityResult:
    """Internal weighted similarity output."""

    score: float
    reasons: list[str]
    differences: list[str]


def find_similar_first_board_cases(
    symbol: str,
    trade_date: date,
    repository: SQLiteFirstBoardRepository | None = None,
    limit: int = 5,
    window_days: int | None = None,
) -> SimilarFirstBoardCasesResponse:
    """Find historical first-board cases similar to the target feature row."""

    active_repository = repository or SQLiteFirstBoardRepository()
    target = active_repository.get_feature(symbol=symbol, trade_date=trade_date)
    if target is None:
        raise ValueError("target first-board feature not found")

    candidates: list[FirstBoardFeature] = []
    selected_window = window_days or DEFAULT_WINDOWS[-1]
    windows = (window_days,) if window_days else DEFAULT_WINDOWS
    for candidate_window in windows:
        selected_window = candidate_window
        earliest_date = _earliest_trade_date_for_window(
            repository=active_repository,
            target_date=target.trade_date,
            window_days=candidate_window,
        )
        candidates = active_repository.recall_similar_features(
            target=target,
            earliest_trade_date=earliest_date,
            limit=MAX_RECALL_SIZE,
        )
        if window_days or len(candidates) >= MIN_RECALL_SIZE:
            break

    ranked = sorted(
        (
            (candidate, calculate_similarity(target, candidate))
            for candidate in candidates
            if not (
                candidate.symbol == target.symbol
                and candidate.trade_date == target.trade_date
            )
        ),
        key=lambda item: item[1].score,
        reverse=True,
    )[:limit]

    return SimilarFirstBoardCasesResponse(
        target=target,
        cases=[
            _case_from_feature(
                feature=feature,
                similarity=result,
                repository=active_repository,
            )
            for feature, result in ranked
        ],
        window_days=selected_window,
        recall_count=len(candidates),
        generated_by=SIMILAR_CASES_VERSION,
    )


def calculate_similarity(
    target: FirstBoardFeature,
    candidate: FirstBoardFeature,
) -> SimilarityResult:
    """Calculate explainable weighted similarity between two feature rows."""

    reasons: list[str] = []
    differences: list[str] = []

    seal_score = _bounded_similarity(
        abs(target.break_count - candidate.break_count) / 3 * 0.65
        + abs(target.seal_count - candidate.seal_count) / 5 * 0.35
    )
    _explain_delta(
        reasons,
        differences,
        seal_score,
        "炸板/封板次数接近",
        "封板稳定性差异较大",
    )

    time_score = _bounded_similarity(
        abs(target.first_limit_minutes - candidate.first_limit_minutes) / 90
    )
    if target.first_limit_bucket == candidate.first_limit_bucket:
        time_score = min(1.0, time_score + 0.12)
    _explain_delta(
        reasons,
        differences,
        time_score,
        "首封时间区间接近",
        "首封时间差异明显",
    )

    activity_score = (
        _bounded_similarity(abs(target.turnover_rate - candidate.turnover_rate) / 18) * 0.5
        + _bounded_similarity(abs(target.amount_log - candidate.amount_log) / 1.4) * 0.5
    )
    _explain_delta(
        reasons,
        differences,
        activity_score,
        "换手率和成交额规模接近",
        "交易活跃度差异较大",
    )

    market_score = (
        _bounded_similarity(
            abs(target.market_failed_limit_up_rate - candidate.market_failed_limit_up_rate)
            / 0.45
        )
        * 0.55
        + _bounded_similarity(
            abs(target.market_max_board_height - candidate.market_max_board_height) / 5
        )
        * 0.25
        + (1.0 if target.market_sentiment == candidate.market_sentiment else 0.45)
        * 0.2
    )
    _explain_delta(
        reasons,
        differences,
        market_score,
        "当日市场环境接近",
        "市场环境差异较大",
    )

    topic_score = (
        (1.0 if target.industry == candidate.industry else 0.35) * 0.35
        + (1.0 if target.concept == candidate.concept else 0.35) * 0.2
        + _bounded_similarity(
            abs(
                target.same_industry_limit_up_count
                - candidate.same_industry_limit_up_count
            )
            / 8
        )
        * 0.25
        + _bounded_similarity(
            abs(
                target.same_concept_limit_up_count
                - candidate.same_concept_limit_up_count
            )
            / 8
        )
        * 0.2
    )
    _explain_delta(
        reasons,
        differences,
        topic_score,
        "行业/题材热度接近",
        "行业或题材热度差异较大",
    )

    score = (
        seal_score * 0.25
        + activity_score * 0.2
        + market_score * 0.2
        + topic_score * 0.15
        + time_score * 0.2
    )

    if target.industry == candidate.industry:
        reasons.append("同属一个行业")
    elif target.concept == candidate.concept:
        reasons.append("题材标签相同")

    return SimilarityResult(
        score=round(score, 4),
        reasons=reasons[:5],
        differences=differences[:5],
    )


def _case_from_feature(
    feature: FirstBoardFeature,
    similarity: SimilarityResult,
    repository: SQLiteFirstBoardRepository,
) -> SimilarFirstBoardCase:
    """Build API case model with optional outcome and post bars."""

    outcome = repository.get_outcome(feature.symbol, feature.trade_date)
    post_bars = repository.list_post_bars(feature.symbol, feature.trade_date, limit=6)

    return SimilarFirstBoardCase(
        symbol=feature.symbol,
        name=feature.name,
        trade_date=feature.trade_date,
        similarity=similarity.score,
        reasons=similarity.reasons,
        differences=similarity.differences,
        outcome=SimilarCaseOutcome(
            next_trade_date=outcome.next_trade_date,
            next_open_pct=outcome.next_open_pct,
            next_high_pct=outcome.next_high_pct,
            next_close_pct=outcome.next_close_pct,
            three_day_high_pct=outcome.three_day_high_pct,
            three_day_close_pct=outcome.three_day_close_pct,
            max_drawdown_3d=outcome.max_drawdown_3d,
            promoted_to_second_board=outcome.promoted_to_second_board,
            outcome_ready=outcome.outcome_ready,
        )
        if outcome
        else None,
        post_bars=[
            SimilarCaseDailyBar(
                trade_date=bar.trade_date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                amount=bar.amount,
            )
            for bar in post_bars
        ],
    )


def _earliest_trade_date_for_window(
    repository: SQLiteFirstBoardRepository,
    target_date: date,
    window_days: int,
) -> date:
    """Approximate a trading-day window from persisted feature dates."""

    all_dates = repository.list_feature_trade_dates_before(target_date)
    selected = all_dates[:window_days]
    return selected[-1] if selected else target_date


def _bounded_similarity(normalized_difference: float) -> float:
    """Convert a normalized difference to 0-1 similarity."""

    return max(0.0, min(1.0, 1.0 - normalized_difference))


def _explain_delta(
    reasons: list[str],
    differences: list[str],
    score: float,
    reason: str,
    difference: str,
) -> None:
    """Add a reason or difference label from a component score."""

    if score >= 0.72:
        reasons.append(reason)
    elif score <= 0.45:
        differences.append(difference)
