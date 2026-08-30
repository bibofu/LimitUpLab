"""Build and persist one immutable after-close review snapshot."""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.agents.review_agent import build_review_agent_report
from app.models import DailyReviewSnapshot, LimitUpEvent
from app.repositories.first_board_repository import SQLiteFirstBoardRepository
from app.repositories.review_snapshot_repository import SQLiteReviewSnapshotRepository
from app.services.llm_provider import LLMProvider


DAILY_REVIEW_SNAPSHOT_VERSION = "daily-review-snapshot-v1"


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
