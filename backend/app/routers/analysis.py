from fastapi import APIRouter

from app.models import ContinuationStat, FailedRateStat, PostPerformanceStat
from app.services.analysis import (
    calculate_continuation,
    calculate_failed_rates,
    calculate_post_performance,
)
from app.services.sample_data import SAMPLE_EVENTS

router = APIRouter()


@router.get("/continuation", response_model=list[ContinuationStat])
def get_continuation_stats() -> list[ContinuationStat]:
    return calculate_continuation(SAMPLE_EVENTS)


@router.get("/failed-rate", response_model=list[FailedRateStat])
def get_failed_rate_stats() -> list[FailedRateStat]:
    return calculate_failed_rates(SAMPLE_EVENTS)


@router.get("/post-performance", response_model=list[PostPerformanceStat])
def get_post_performance_stats() -> list[PostPerformanceStat]:
    return calculate_post_performance(SAMPLE_EVENTS)
