from fastapi import APIRouter

from app.models import LimitUpEvent
from app.repositories import get_limit_up_repository
from app.services.analysis import (
    list_continued_board,
    list_failed_events,
    list_first_board,
    list_recent_limit_up,
)

router = APIRouter()


@router.get("/events", response_model=list[LimitUpEvent])
def list_limit_up_events() -> list[LimitUpEvent]:
    events = get_limit_up_repository().list_events()
    return sorted(
        events,
        key=lambda event: (event.trade_date, event.board_height, event.first_limit_time),
        reverse=True,
    )


@router.get("/first-board", response_model=list[LimitUpEvent])
def list_first_board_events() -> list[LimitUpEvent]:
    return list_first_board(get_limit_up_repository().list_events())


@router.get("/continued-board", response_model=list[LimitUpEvent])
def list_continued_board_events() -> list[LimitUpEvent]:
    return list_continued_board(get_limit_up_repository().list_events())


@router.get("/failed", response_model=list[LimitUpEvent])
def list_failed_limit_up_events() -> list[LimitUpEvent]:
    return list_failed_events(get_limit_up_repository().list_events())


@router.get("/recent", response_model=list[LimitUpEvent])
def list_recent_limit_up_events(days: int = 3) -> list[LimitUpEvent]:
    return list_recent_limit_up(get_limit_up_repository().list_events(), days=days)
