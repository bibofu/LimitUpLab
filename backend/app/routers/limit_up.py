from fastapi import APIRouter

from app.models import LimitUpEvent
from app.services.analysis import (
    list_continued_board,
    list_failed_events,
    list_first_board,
    list_recent_limit_up,
)
from app.services.sample_data import SAMPLE_EVENTS

router = APIRouter()


@router.get("/events", response_model=list[LimitUpEvent])
def list_limit_up_events() -> list[LimitUpEvent]:
    return sorted(
        SAMPLE_EVENTS,
        key=lambda event: (event.trade_date, event.board_height, event.first_limit_time),
        reverse=True,
    )


@router.get("/first-board", response_model=list[LimitUpEvent])
def list_first_board_events() -> list[LimitUpEvent]:
    return list_first_board(SAMPLE_EVENTS)


@router.get("/continued-board", response_model=list[LimitUpEvent])
def list_continued_board_events() -> list[LimitUpEvent]:
    return list_continued_board(SAMPLE_EVENTS)


@router.get("/failed", response_model=list[LimitUpEvent])
def list_failed_limit_up_events() -> list[LimitUpEvent]:
    return list_failed_events(SAMPLE_EVENTS)


@router.get("/recent", response_model=list[LimitUpEvent])
def list_recent_limit_up_events(days: int = 3) -> list[LimitUpEvent]:
    return list_recent_limit_up(SAMPLE_EVENTS, days=days)
