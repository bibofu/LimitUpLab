from fastapi import APIRouter

from app.models import MarketSummary
from app.services.analysis import summarize_market
from app.services.sample_data import SAMPLE_EVENTS

router = APIRouter()


@router.get("/summary", response_model=MarketSummary)
def get_market_summary() -> MarketSummary:
    return summarize_market(SAMPLE_EVENTS)


@router.get("/overview", response_model=MarketSummary)
def get_market_overview() -> MarketSummary:
    return summarize_market(SAMPLE_EVENTS)
