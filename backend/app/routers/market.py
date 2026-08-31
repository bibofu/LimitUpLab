"""Market overview and post-close special-data API routes."""

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from app.collectors import (
    HithinkFinanceError,
    collect_market_indices,
)
from app.models import DragonTigerReviewResponse, FinanceNewsPage, MarketSummary
from app.repositories import get_limit_up_repository
from app.services.analysis import latest_trade_date, summarize_market
from app.services.dragon_tiger_review import load_dragon_tiger_review
from app.services.finance_news import collect_finance_news

router = APIRouter()


@router.get("/news", response_model=FinanceNewsPage)
def get_finance_news(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=5, le=20),
) -> FinanceNewsPage:
    """Return one page of the latest 24-hour structured market-news feed."""

    try:
        facts = collect_finance_news(limit=2000, hours=24)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail="财经快讯数据源暂不可用，请稍后重试。",
        ) from exc
    ordered = sorted(facts.items, key=lambda item: item.published_at, reverse=True)
    total = len(ordered)
    total_pages = max(1, (total + page_size - 1) // page_size)
    bounded_page = min(page, total_pages)
    start = (bounded_page - 1) * page_size
    return FinanceNewsPage(
        fetched_at=facts.fetched_at,
        sources=facts.sources,
        page=bounded_page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
        items=ordered[start : start + page_size],
    )


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
