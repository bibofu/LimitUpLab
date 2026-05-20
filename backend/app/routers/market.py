from fastapi import APIRouter

from app.collectors import collect_market_indices
from app.models import MarketSummary
from app.repositories import get_limit_up_repository
from app.services.analysis import latest_trade_date, summarize_market

router = APIRouter()


@router.get("/summary", response_model=MarketSummary)
def get_market_summary() -> MarketSummary:
    events = get_limit_up_repository().list_events()
    return summarize_market(
        events,
        indices=collect_market_indices(latest_trade_date(events)),
    )


@router.get("/overview", response_model=MarketSummary)
def get_market_overview() -> MarketSummary:
    events = get_limit_up_repository().list_events()
    return summarize_market(
        events,
        indices=collect_market_indices(latest_trade_date(events)),
    )
