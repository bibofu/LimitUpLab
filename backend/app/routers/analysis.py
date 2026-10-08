"""Analysis API routes built from persisted limit-up events."""

from datetime import date

from fastapi import APIRouter, Query

from app.models import (
    ContinuationStat,
    DailyBoardPromotionReport,
    DailyBoardPromotionStat,
    FailedRateStat,
    PostPerformanceStat,
)
from app.repositories import get_limit_up_repository
from app.services.analysis import (
    calculate_continuation,
    calculate_failed_rates,
    calculate_post_performance,
)
from app.services.daily_promotion import build_daily_promotion_report

router = APIRouter()


@router.get("/continuation", response_model=list[ContinuationStat])
def get_continuation_stats() -> list[ContinuationStat]:
    """Return next-day continuation statistics grouped by board height."""

    return calculate_continuation(get_limit_up_repository().list_events())


@router.get("/daily-promotion", response_model=list[DailyBoardPromotionStat])
def get_daily_board_promotion(
    days: int = Query(default=5, ge=1, le=60),
    end_date: date | None = None,
) -> list[DailyBoardPromotionStat]:
    """Return recent daily board-promotion rates from persisted close data."""

    return build_daily_promotion_report(
        get_limit_up_repository().list_events(),
        days=days,
        end_date=end_date,
    ).items


@router.get("/daily-promotion-report", response_model=DailyBoardPromotionReport)
def get_daily_board_promotion_report(
    days: int = Query(default=5, ge=1, le=60),
    end_date: date | None = None,
) -> DailyBoardPromotionReport:
    """Return promotion rates together with explicit calendar/data gaps."""
    return build_daily_promotion_report(
        get_limit_up_repository().list_events(), days=days, end_date=end_date,
    )


@router.get("/failed-rate", response_model=list[FailedRateStat])
def get_failed_rate_stats() -> list[FailedRateStat]:
    """Return intraday break-rate statistics grouped by board height."""

    return calculate_failed_rates(get_limit_up_repository().list_events())


@router.get("/post-performance", response_model=list[PostPerformanceStat])
def get_post_performance_stats() -> list[PostPerformanceStat]:
    """Return average post-limit-up performance grouped by board height."""

    return calculate_post_performance(get_limit_up_repository().list_events())
