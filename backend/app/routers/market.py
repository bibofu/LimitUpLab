"""Market overview and post-close special-data API routes."""

from datetime import date
from threading import Lock
from time import monotonic

from fastapi import APIRouter, HTTPException

from app.collectors import (
    HithinkFinanceCollector,
    HithinkFinanceError,
    collect_market_indices,
)
from app.models import DragonTigerReviewResponse, MarketSummary
from app.repositories import get_limit_up_repository
from app.services.analysis import latest_trade_date, summarize_market
from app.services.dragon_tiger_review import build_dragon_tiger_review

router = APIRouter()
_DRAGON_TIGER_CACHE_TTL_SECONDS = 300.0
_dragon_tiger_cache: dict[date, tuple[float, DragonTigerReviewResponse]] = {}
_dragon_tiger_cache_lock = Lock()


@router.get("/summary", response_model=MarketSummary)
def get_market_summary() -> MarketSummary:
    """Return the latest market sentiment summary with index snapshots."""

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
    now = monotonic()
    with _dragon_tiger_cache_lock:
        cached = _dragon_tiger_cache.get(target_date)
        if cached is not None and now - cached[0] < _DRAGON_TIGER_CACHE_TTL_SECONDS:
            return cached[1]

        try:
            snapshot = HithinkFinanceCollector().collect_dragon_tiger(
                trade_date=target_date,
                board_type="all",
                limit=200,
            )
        except HithinkFinanceError as exc:
            raise HTTPException(
                status_code=502,
                detail="龙虎榜数据源暂不可用，请稍后重试。",
            ) from exc

        response = build_dragon_tiger_review(
            snapshot,
            events,
            trade_date=target_date,
        )
        _dragon_tiger_cache[target_date] = (monotonic(), response)
        return response
