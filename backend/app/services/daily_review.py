"""Build and persist one immutable after-close review snapshot."""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.agents.review_agent import build_review_agent_report
from app.models import (
    AgentPrediction,
    DailyReviewSnapshot,
    LimitUpEvent,
    ReviewAgentReportResponse,
)
from app.repositories.first_board_repository import SQLiteFirstBoardRepository
from app.repositories.review_snapshot_repository import SQLiteReviewSnapshotRepository
from app.repositories.scoring_policy_repository import SQLiteScoringPolicyRepository
from app.services.evaluation_agent import select_canonical_prediction_snapshots
from app.services.llm_provider import LLMProvider
from app.services.scoring_policy import DEFAULT_SCORING_POLICY_VERSION


DAILY_REVIEW_SNAPSHOT_VERSION = "daily-review-snapshot-v1"


def review_snapshot_matches_current_predictions(
    *,
    report: ReviewAgentReportResponse,
    first_board_repository: SQLiteFirstBoardRepository,
    top_per_day: int = 10,
) -> bool:
    """Return whether a persisted report covers the current canonical Top-N."""

    champion = SQLiteScoringPolicyRepository(
        first_board_repository.database_path
    ).get_champion()
    preferred_version = (
        champion.version if champion else DEFAULT_SCORING_POLICY_VERSION
    )
    predictions = select_canonical_prediction_snapshots(
        first_board_repository.list_predictions_between(
            report.start_date,
            report.end_date,
        ),
        preferred_scoring_version=preferred_version,
    )
    expected = _daily_top_prediction_keys(predictions, top_per_day=top_per_day)
    actual = {(item.trade_date, item.symbol) for item in report.reviewed_picks}
    return actual == expected


def _daily_top_prediction_keys(
    predictions: list[AgentPrediction],
    *,
    top_per_day: int,
) -> set[tuple[date, str]]:
    """Select the same daily Top-N identity set used by the Review Agent."""

    by_date: dict[date, list[AgentPrediction]] = {}
    for item in predictions:
        by_date.setdefault(item.trade_date, []).append(item)
    selected: set[tuple[date, str]] = set()
    for trade_date, daily in by_date.items():
        ranked = sorted(
            daily,
            key=lambda item: (-item.score, -item.confidence, item.symbol),
        )[: max(top_per_day, 0)]
        selected.update((trade_date, item.symbol) for item in ranked)
    return selected


def build_daily_review_snapshot(
    *,
    events: list[LimitUpEvent],
    as_of_date: date,
    first_board_repository: SQLiteFirstBoardRepository,
    snapshot_repository: SQLiteReviewSnapshotRepository | None = None,
    provider: LLMProvider | None = None,
    history_dates: int = 6,
    top_per_day: int = 10,
    follow_days: int = 5,
) -> DailyReviewSnapshot:
    """Generate and save the review visible at one completed market cutoff."""

    active_snapshot_repository = snapshot_repository or SQLiteReviewSnapshotRepository(
        first_board_repository.database_path
    )
    existing = active_snapshot_repository.get_snapshot(as_of_date)
    if existing is not None:
        return existing

    available_dates = sorted(
        {item.trade_date for item in events if item.trade_date <= as_of_date}
    )
    if not available_dates or as_of_date not in available_dates:
        raise ValueError(f"No limit-up events available for {as_of_date.isoformat()}.")
    end_index = available_dates.index(as_of_date)
    start_date = available_dates[max(0, end_index - max(1, history_dates - 1))]
    report = build_review_agent_report(
        events=events,
        start_date=start_date,
        end_date=as_of_date,
        repository=first_board_repository,
        min_score=0,
        top_per_day=top_per_day,
        follow_days=follow_days,
        provider=provider,
    )
    snapshot = DailyReviewSnapshot(
        as_of_date=as_of_date,
        start_date=start_date,
        report=report,
        generated_by=DAILY_REVIEW_SNAPSHOT_VERSION,
        generated_at=datetime.now(timezone.utc),
    )
    active_snapshot_repository.save_snapshot(snapshot)
    return active_snapshot_repository.get_snapshot(as_of_date) or snapshot
