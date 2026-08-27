"""Market overview and post-close special-data API routes."""

from datetime import date

from fastapi import APIRouter, HTTPException

from app.collectors import (
    HithinkFinanceError,
    collect_market_indices,
)
from app.models import DragonTigerReviewResponse, MarketSummary
from app.repositories import get_limit_up_repository
from app.services.analysis import latest_trade_date, summarize_market
from app.services.dragon_tiger_review import load_dragon_tiger_review

router = APIRouter()


@router.get("/summary", response_model=MarketSummary)
def get_market_summary() -> MarketSummary:
    """Return the latest objective market summary with index snapshots."""

    events = get_limit_up_repository().list_events()
    trade_date = latest_trade_date(events)
    try:
        indices = collect_market_indices(trade_date)
    except Exception:
        indices = []
    return summarize_market(
        events,
        indices=indices,
    )


@router.get("/overview", response_model=MarketSummary)
def get_market_overview() -> MarketSummary:
    """Return the dashboard overview payload."""

    events = get_limit_up_repository().list_events()
    trade_date = latest_trade_date(events)
    try:
        indices = collect_market_indices(trade_date)
    except Exception:
        indices = []
    return summarize_market(
        events,
        indices=indices,
    )


@router.get("/dragon-tiger", response_model=DragonTigerReviewResponse)
def get_dragon_tiger_review(
    trade_date: date | None = None,
) -> DragonTigerReviewResponse:
    """Return a deduplicated Tonghuashun Dragon-Tiger list for review."""

    events = get_limit_up_repository().list_events()
    target_date = trade_date or latest_trade_date(events)
    try:
        return load_dragon_tiger_review(
            events,
            trade_date=target_date,
        )
    except HithinkFinanceError as exc:
        raise HTTPException(
            status_code=502,
            detail="龙虎榜数据源暂不可用，请稍后重试。",
        ) from exc
