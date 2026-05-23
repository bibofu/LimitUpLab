"""Analysis API routes built from persisted limit-up events."""

from fastapi import APIRouter

from app.models import ContinuationStat, FailedRateStat, PostPerformanceStat
from app.repositories import get_limit_up_repository
from app.services.analysis import (
    calculate_continuation,
    calculate_failed_rates,
    calculate_post_performance,
)

router = APIRouter()


@router.get("/continuation", response_model=list[ContinuationStat])
def get_continuation_stats() -> list[ContinuationStat]:
    """Return next-day continuation statistics grouped by board height."""

    return calculate_continuation(get_limit_up_repository().list_events())


@router.get("/failed-rate", response_model=list[FailedRateStat])
def get_failed_rate_stats() -> list[FailedRateStat]:
    """Return intraday break-rate statistics grouped by board height."""

    return calculate_failed_rates(get_limit_up_repository().list_events())


@router.get("/post-performance", response_model=list[PostPerformanceStat])
def get_post_performance_stats() -> list[PostPerformanceStat]:
    """Return average post-limit-up performance grouped by board height."""

    return calculate_post_performance(get_limit_up_repository().list_events())
