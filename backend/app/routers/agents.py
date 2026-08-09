"""Agent-style first-board rating API routes."""

from datetime import date, datetime, timezone
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query

from app.agents import answer_first_board_chat, build_first_board_ratings
from app.models import (
    AgentChatRequest,
    AgentChatResponse,
    AgentRun,
    FirstBoardRatingsResponse,
    SimilarFirstBoardCasesResponse,
)
from app.repositories import (
    SQLiteAgentRunRepository,
    SQLiteFirstBoardRepository,
    get_limit_up_repository,
)
from app.services.similar_cases import find_similar_first_board_cases

router = APIRouter()


@router.get("/first-board-ratings", response_model=FirstBoardRatingsResponse)
def get_first_board_ratings(
    trade_date: Optional[date] = None,
) -> FirstBoardRatingsResponse:
    """Return explainable first-board candidate ratings for a trade date."""

    events = get_limit_up_repository().list_events()
    return build_first_board_ratings(events=events, trade_date=trade_date)


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
