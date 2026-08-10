"""Agent-style first-board rating API routes."""

from datetime import date, datetime, timezone
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query

from app.agents import answer_first_board_chat, build_first_board_ratings
from app.models import (
    AgentChatRequest,
    AgentChatResponse,
    AgentDataHealthResponse,
    AgentRun,
    FirstBoardCriticResponse,
    FirstBoardRatingsResponse,
    RatingBacktestResponse,
    SimilarFirstBoardCasesResponse,
)
from app.repositories import (
    SQLiteAgentRunRepository,
    SQLiteFirstBoardRepository,
    get_limit_up_repository,
)
from app.services.similar_cases import find_similar_first_board_cases
from app.services.data_health import build_agent_data_health
from app.services.first_board_critic import build_first_board_critic
from app.services.rating_backtest import build_rating_backtest

router = APIRouter()


@router.get("/first-board-ratings", response_model=FirstBoardRatingsResponse)
def get_first_board_ratings(
    trade_date: Optional[date] = None,
) -> FirstBoardRatingsResponse:
    """Return explainable first-board candidate ratings for a trade date."""

    events = get_limit_up_repository().list_events()
    return build_first_board_ratings(events=events, trade_date=trade_date)


@router.get("/data-health", response_model=AgentDataHealthResponse)
def get_agent_data_health(
    trade_date: Optional[date] = None,
    top_limit: int = Query(default=5, ge=1, le=20),
) -> AgentDataHealthResponse:
    """Return health status for Agent data dependencies."""

    return build_agent_data_health(
        events=get_limit_up_repository().list_events(),
        first_board_repository=SQLiteFirstBoardRepository(),
        trade_date=trade_date,
        top_limit=top_limit,
    )


@router.get("/rating-backtest", response_model=RatingBacktestResponse)
def get_rating_backtest(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    failure_limit: int = Query(default=8, ge=0, le=30),
) -> RatingBacktestResponse:
    """Return MVP self-evaluation for first-board rating outcomes."""

    events = get_limit_up_repository().list_events()
    if not events:
        raise HTTPException(status_code=404, detail="No local limit-up events available.")
    available_dates = sorted({event.trade_date for event in events})
    resolved_end = end_date or available_dates[-1]
    resolved_start = start_date or available_dates[max(0, len(available_dates) - 20)]
    if resolved_start > resolved_end:
        raise HTTPException(status_code=400, detail="start_date must be before end_date.")
    return build_rating_backtest(
        events=events,
        start_date=resolved_start,
        end_date=resolved_end,
        first_board_repository=SQLiteFirstBoardRepository(),
        failure_limit=failure_limit,
    )


@router.get("/first-board-similar-cases", response_model=SimilarFirstBoardCasesResponse)
def get_first_board_similar_cases(
    symbol: str,
    trade_date: date,
    limit: int = Query(default=5, ge=1, le=20),
    window_days: Optional[int] = Query(default=None, ge=30, le=360),
) -> SimilarFirstBoardCasesResponse:
    """Return historical first-board cases similar to the target candidate."""

    try:
        return find_similar_first_board_cases(
            symbol=symbol,
            trade_date=trade_date,
            repository=SQLiteFirstBoardRepository(),
            limit=limit,
            window_days=window_days,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/first-board-critic", response_model=FirstBoardCriticResponse)
def get_first_board_critic(
    symbol: str,
    trade_date: Optional[date] = None,
    similar_limit: int = Query(default=5, ge=0, le=10),
) -> FirstBoardCriticResponse:
    """Return a critic review for one first-board candidate rating."""

    try:
        return build_first_board_critic(
            events=get_limit_up_repository().list_events(),
            symbol=symbol,
            trade_date=trade_date,
            first_board_repository=SQLiteFirstBoardRepository(),
            similar_limit=similar_limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/chat", response_model=AgentChatResponse)
def chat_with_first_board_agent(request: AgentChatRequest) -> AgentChatResponse:
    """Answer first-board questions and persist an Agent run trace."""

    run_id = f"run_{uuid4().hex}"
    started_at = datetime.now(timezone.utc)
    run_repository = SQLiteAgentRunRepository()

    try:
        response = answer_first_board_chat(
            request=request,
            events=get_limit_up_repository().list_events(),
            repository=SQLiteFirstBoardRepository(),
            recent_runs=run_repository.list_recent_runs(request.session_id, limit=10),
        )
        response.run_id = run_id
        run_repository.save_run(
            AgentRun(
                run_id=run_id,
                session_id=request.session_id,
                run_type="agent_chat",
                status="success",
                intent=response.intent,
                tool_calls=response.tool_calls,
                input_json=request.model_dump(mode="json"),
                output_json=response.model_dump(mode="json"),
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
            )
        )
        return response
    except Exception as error:
        run_repository.save_run(
            AgentRun(
                run_id=run_id,
                session_id=request.session_id,
                run_type="agent_chat",
                status="error",
                input_json=request.model_dump(mode="json"),
                error_message=str(error),
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
            )
        )
        raise
