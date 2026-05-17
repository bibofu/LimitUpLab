from fastapi import APIRouter

from app.models import MarketSummary
from app.repositories import get_limit_up_repository
from app.services.analysis import summarize_market

router = APIRouter()


@router.get("/summary", response_model=MarketSummary)
def get_market_summary() -> MarketSummary:
    return summarize_market(get_limit_up_repository().list_events())


@router.get("/overview", response_model=MarketSummary)
def get_market_overview() -> MarketSummary:
    return summarize_market(get_limit_up_repository().list_events())
