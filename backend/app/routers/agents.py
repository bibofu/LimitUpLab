"""Agent-style review API routes."""

from fastapi import APIRouter

from app.agents import build_daily_review
from app.collectors import collect_market_indices
from app.models import DailyReview
from app.repositories import get_limit_up_repository
from app.services.analysis import latest_trade_date, summarize_market

router = APIRouter()


@router.get("/daily-review", response_model=DailyReview)
def get_daily_review() -> DailyReview:
    """Return a rule-based after-close review for the latest persisted day."""

    events = get_limit_up_repository().list_events()
    summary = summarize_market(
        events,
        indices=collect_market_indices(latest_trade_date(events)),
    )
    return build_daily_review(summary=summary, events=events)
