"""Agent-style first-board rating API routes."""

import hashlib
import json
import queue
import threading
from datetime import date, datetime, timezone
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Callable, TypeVar
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.agents import answer_first_board_chat, build_first_board_ratings, build_review_agent_report
from app.agents.eval_runner import eval_suite_report, load_eval_cases, run_agent_eval_suite
from app.collectors import HithinkFinanceError
from app.models import (
    AgentChatRequest,
    AgentChatResponse,
    AgentDataHealthResponse,
    AgentEvalCaseReport,
    AgentEvalReportResponse,
    AgentEvaluationResponse,
    AgentRun,
    AgentRunsResponse,
    AgentRunSummary,
    AgentSystemHealthResponse,
    AgentToolTrace,
    ChatSessionCreateRequest,
    ChatSessionDetail,
    ChatSessionMessage,
    ChatSessionsResponse,
    ChatSessionUpdateRequest,
    DailyPipelineStatusResponse,
    FirstBoardCriticResponse,
    FirstBoardRatingsResponse,
    FactorSignalDiagnosticResponse,
    PredictionQualityAuditResponse,
    RatingBacktestResponse,
    ReviewAgentReportResponse,
    ScoringPolicyOptimizationResponse,
    ScoringPolicyRegistryResponse,
)
from app.repositories import (
    SQLiteAgentCacheRepository,
    SQLiteAgentRunRepository,
    SQLiteChatSessionRepository,
    SQLiteDailyPipelineRepository,
    SQLiteFirstBoardRepository,
    SQLiteScoringPolicyRepository,
    SessionOwnershipError,
    get_limit_up_repository,
)
from app.services.data_health import build_agent_data_health
from app.services.factor_signal_diagnostic import build_factor_signal_diagnostic
from app.services.evaluation_agent import build_agent_evaluation
from app.services.first_board_critic import build_first_board_critic
from app.services.dragon_tiger_review import load_dragon_tiger_review
from app.services.llm_provider import DisabledLLMProvider
from app.services.prediction_quality_audit import build_prediction_quality_audit
from app.services.rating_backtest import build_rating_backtest
from app.services.scoring_policy_optimizer import (
    build_scoring_policy_registry,
    optimize_scoring_policy,
)
from app.services.sample_data import SAMPLE_EVENTS
from app.services.system_health import build_agent_system_health
from app.security import current_owner_id, require_admin_access

router = APIRouter()
ResponseModel = TypeVar("ResponseModel")
STRUCTURED_CACHE_TTL_MINUTES = 10
BACKEND_ROOT = Path(__file__).resolve().parents[2]
CHAT_SESSIONS_VERSION = "chat-sessions-v1"


@router.get("/first-board-ratings", response_model=FirstBoardRatingsResponse)
def get_first_board_ratings(
    trade_date: Optional[date] = None,
) -> FirstBoardRatingsResponse:
    """Return explainable first-board candidate ratings for a trade date."""

    events = get_limit_up_repository().list_events()
    resolved_trade_date = _resolve_trade_date(events, trade_date)
    first_board_repository = SQLiteFirstBoardRepository()
    if resolved_trade_date is not None:
        try:
            load_dragon_tiger_review(
                events,
                trade_date=resolved_trade_date,
                repository=first_board_repository,
            )
        except HithinkFinanceError:
            pass
    policy_repository = SQLiteScoringPolicyRepository(
        first_board_repository.database_path
    )
    scoring_policy = policy_repository.ensure_default_policy()
    enrichment = (
        first_board_repository.list_enrichment_for_date(resolved_trade_date)
        if resolved_trade_date
        else []
    )
    return _cached_response(
        scope="first_board_ratings",
        key_parts={
            "trade_date": resolved_trade_date.isoformat() if resolved_trade_date else None,
            "events": _events_signature(events, start_date=resolved_trade_date, end_date=resolved_trade_date),
            "enrichment": [
                (
                    item.symbol,
                    item.feature_version,
                    item.created_at.isoformat(),
                    item.position.classifier_version if item.position else None,
                    item.position.primary.regime if item.position else None,
                )
                for item in enrichment
            ],
            "scoring_version": scoring_policy.version,
        },
        response_model=FirstBoardRatingsResponse,
        builder=lambda: build_first_board_ratings(
            events=events,
            trade_date=trade_date,
            first_board_repository=first_board_repository,
            scoring_policy=scoring_policy,
        ),
    )


@router.get(
    "/scoring-policies",
    response_model=ScoringPolicyRegistryResponse,
)
def get_scoring_policies(
    _admin: Annotated[None, Depends(require_admin_access)],
    limit: int = Query(default=20, ge=1, le=100),
) -> ScoringPolicyRegistryResponse:
    """Return the active Champion and recent policy versions."""

    return build_scoring_policy_registry(limit=limit)


@router.post(
    "/scoring-policies/optimize",
    response_model=ScoringPolicyOptimizationResponse,
)
def optimize_first_board_scoring_policy(
    _admin: Annotated[None, Depends(require_admin_access)],
    start_date: date | None = None,
    end_date: date | None = None,
    top_k: int = Query(default=10, ge=3, le=30),
    activate_if_eligible: bool = False,
) -> ScoringPolicyOptimizationResponse:
    """Generate and validate a bounded Challenger without activating by default."""

    events = get_limit_up_repository().list_events()
    available_dates = sorted({event.trade_date for event in events})
    if not available_dates:
        raise HTTPException(status_code=404, detail="No local limit-up events available.")
    resolved_end = end_date or available_dates[-1]
    eligible_dates = [item for item in available_dates if item <= resolved_end]
    if not eligible_dates:
        raise HTTPException(
            status_code=404,
            detail="No local limit-up events are available on or before end_date.",
        )
    resolved_start = start_date or eligible_dates[max(0, len(eligible_dates) - 120)]
    if resolved_start > resolved_end:
        raise HTTPException(
            status_code=422,
            detail="start_date must be on or before end_date.",
        )
    first_board_repository = SQLiteFirstBoardRepository()
    try:
        return optimize_scoring_policy(
            events=events,
            start_date=resolved_start,
            end_date=resolved_end,
            first_board_repository=first_board_repository,
            policy_repository=SQLiteScoringPolicyRepository(
                first_board_repository.database_path
            ),
            top_k=top_k,
            activate_if_eligible=activate_if_eligible,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/data-health", response_model=AgentDataHealthResponse)
def get_agent_data_health(
    _admin: Annotated[None, Depends(require_admin_access)],
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


@router.get("/system-health", response_model=AgentSystemHealthResponse)
def get_agent_system_health(
    _admin: Annotated[None, Depends(require_admin_access)],
    run_offline_eval: bool = False,
) -> AgentSystemHealthResponse:
    """Return local runtime health for data, LLM and Agent eval status."""

    return build_agent_system_health(
        events=get_limit_up_repository().list_events(),
        first_board_repository=SQLiteFirstBoardRepository(),
        run_offline_eval=run_offline_eval,
    )


@router.get("/daily-pipeline-status", response_model=DailyPipelineStatusResponse)
def get_daily_pipeline_status(
    _admin: Annotated[None, Depends(require_admin_access)],
    limit: int = Query(default=5, ge=1, le=30),
) -> DailyPipelineStatusResponse:
    """Return the latest automated after-close pipeline executions."""

    runs = SQLiteDailyPipelineRepository().list_recent(limit=limit)
    return DailyPipelineStatusResponse(
        latest=runs[0] if runs else None,
        recent=runs,
        generated_by="daily-close-loop-status-v1",
    )


@router.get("/eval", response_model=AgentEvalReportResponse)
def get_agent_eval_report(
    _admin: Annotated[None, Depends(require_admin_access)],
) -> AgentEvalReportResponse:
    """Run deterministic chat Agent eval cases for the quality panel."""

    fixture_path = BACKEND_ROOT / "tests" / "fixtures" / "agent_eval_cases.json"
    if not fixture_path.exists():
        raise HTTPException(status_code=404, detail="Agent eval fixture not found.")

    suite = run_agent_eval_suite(
        cases=load_eval_cases(fixture_path),
        events=SAMPLE_EVENTS,
        check_intent=True,
    )
    report = eval_suite_report(suite)
    total = int(report["total"])
    return AgentEvalReportResponse(
        mode="offline",
        total=total,
        passed=int(report["passed"]),
        failed=int(report["failed"]),
        pass_rate=(int(report["passed"]) / total) if total else 0,
        results=[
            AgentEvalCaseReport(
                case_id=item["case_id"],
                passed=item["passed"],
                failures=item["failures"],
                intent=item["intent"],
                planner_tool_calls=item["planner_tool_calls"],
                final_tool_calls=item["final_tool_calls"],
                backend_repaired_tools=item["backend_repaired_tools"],
                repair_reasons=item["repair_reasons"],
                trace_names=item["trace_names"],
                warnings=item["warnings"],
                answer_preview=item["answer_preview"],
            )
            for item in report["results"]
        ],
        generated_by="agent-eval-panel-v1",
    )


@router.get(
    "/prediction-quality-audit",
    response_model=PredictionQualityAuditResponse,
)
def get_prediction_quality_audit(
    _admin: Annotated[None, Depends(require_admin_access)],
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    scoring_version: Optional[str] = None,
    top_k: int = Query(default=10, ge=3, le=30),
) -> PredictionQualityAuditResponse:
    """Return source-aware prediction coverage and deterministic baselines."""

    events = get_limit_up_repository().list_events()
    if not events:
        raise HTTPException(status_code=404, detail="No local limit-up events available.")
    available_dates = sorted({event.trade_date for event in events})
    resolved_start = start_date or available_dates[0]
    resolved_end = end_date or available_dates[-1]
    if resolved_start > resolved_end:
        raise HTTPException(status_code=400, detail="start_date must be before end_date.")
    try:
        return build_prediction_quality_audit(
            events=events,
            start_date=resolved_start,
            end_date=resolved_end,
            first_board_repository=SQLiteFirstBoardRepository(),
            scoring_version=scoring_version,
            top_k=top_k,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get(
    "/factor-signal-diagnostic",
    response_model=FactorSignalDiagnosticResponse,
)
def get_factor_signal_diagnostic(
    _admin: Annotated[None, Depends(require_admin_access)],
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    outcome_measure: str = "next_open_to_close_pct",
) -> FactorSignalDiagnosticResponse:
    """Return a date-aware falsification diagnostic for the scoring factors."""

    events = get_limit_up_repository().list_events()
    if not events:
        raise HTTPException(status_code=404, detail="No local limit-up events available.")
    available_dates = sorted({event.trade_date for event in events})
    resolved_start = start_date or available_dates[0]
    resolved_end = end_date or available_dates[-1]
    if resolved_start > resolved_end:
        raise HTTPException(status_code=400, detail="start_date must be before end_date.")
    try:
        return build_factor_signal_diagnostic(
            events=events,
            start_date=resolved_start,
            end_date=resolved_end,
            first_board_repository=SQLiteFirstBoardRepository(),
            outcome_measure=outcome_measure,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


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
    return _cached_response(
        scope="rating_backtest",
        key_parts={
            "start_date": resolved_start.isoformat(),
            "end_date": resolved_end.isoformat(),
            "failure_limit": failure_limit,
            "metric_version": "entry-open-v2",
            "events": _events_signature(events, start_date=resolved_start, end_date=resolved_end),
        },
        response_model=RatingBacktestResponse,
        builder=lambda: build_rating_backtest(
            events=events,
            start_date=resolved_start,
            end_date=resolved_end,
            first_board_repository=SQLiteFirstBoardRepository(),
            failure_limit=failure_limit,
        ),
    )


@router.get("/rating-evaluation", response_model=AgentEvaluationResponse)
def get_rating_evaluation(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: int = Query(default=30, ge=1, le=100),
) -> AgentEvaluationResponse:
    """Return Evaluation Agent review for persisted first-board predictions."""

    events = get_limit_up_repository().list_events()
    if not events:
        raise HTTPException(status_code=404, detail="No local limit-up events available.")
    available_dates = sorted({event.trade_date for event in events})
    resolved_end = end_date or available_dates[-1]
    resolved_start = start_date or available_dates[max(0, len(available_dates) - 20)]
    if resolved_start > resolved_end:
        raise HTTPException(status_code=400, detail="start_date must be before end_date.")
    return _cached_response(
        scope="rating_evaluation",
        key_parts={
            "start_date": resolved_start.isoformat(),
            "end_date": resolved_end.isoformat(),
            "limit": limit,
            "metric_version": "entry-open-v2-source-aware",
            "events": _events_signature(events, start_date=resolved_start, end_date=resolved_end),
        },
        response_model=AgentEvaluationResponse,
        builder=lambda: build_agent_evaluation(
            events=events,
            start_date=resolved_start,
            end_date=resolved_end,
            first_board_repository=SQLiteFirstBoardRepository(),
            limit=limit,
        ),
    )


@router.get("/review-report", response_model=ReviewAgentReportResponse)
def get_review_agent_report(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    min_score: float = Query(default=0, ge=0, le=100),
    top_per_day: int = Query(default=10, ge=1, le=20),
    follow_days: int = Query(default=5, ge=1, le=10),
    use_llm: bool = Query(default=True),
) -> ReviewAgentReportResponse:
    """Return Review Agent tracking for recent daily top first-board picks."""

    events = get_limit_up_repository().list_events()
    if not events:
        raise HTTPException(status_code=404, detail="No local limit-up events available.")
    available_dates = sorted({event.trade_date for event in events})
    resolved_end = end_date or available_dates[-1]
    if start_date is None:
        end_index = available_dates.index(resolved_end) if resolved_end in available_dates else len(available_dates) - 1
        resolved_start = available_dates[max(0, end_index - 5)]
    else:
        resolved_start = start_date
    if resolved_start > resolved_end:
        raise HTTPException(status_code=400, detail="start_date must be before end_date.")
    return build_review_agent_report(
        events=events,
        start_date=resolved_start,
        end_date=resolved_end,
        repository=SQLiteFirstBoardRepository(),
        min_score=min_score,
        top_per_day=top_per_day,
        follow_days=follow_days,
        provider=None if use_llm else DisabledLLMProvider(),
    )


@router.get("/runs", response_model=AgentRunsResponse)
def list_agent_runs(
    _admin: Annotated[None, Depends(require_admin_access)],
    session_id: Optional[str] = None,
    owner_id: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=50),
) -> AgentRunsResponse:
    """Return recent Agent runs for the observability panel."""

    runs = SQLiteAgentRunRepository().list_runs(
        session_id=session_id,
        limit=limit,
        owner_id=owner_id,
    )
    return AgentRunsResponse(
        runs=[_summarize_agent_run(run) for run in runs],
        generated_by="agent-run-observability-v1",
    )


@router.post("/chat/sessions", response_model=ChatSessionDetail)
def create_chat_session(
    request: ChatSessionCreateRequest,
    owner_id: Annotated[str, Depends(current_owner_id)],
) -> ChatSessionDetail:
    """Create an empty resumable Agent conversation."""

    return SQLiteChatSessionRepository().create_session(
        title=request.title,
        owner_id=owner_id,
    )


@router.get("/chat/sessions", response_model=ChatSessionsResponse)
def list_chat_sessions(
    owner_id: Annotated[str, Depends(current_owner_id)],
    limit: int = Query(default=30, ge=1, le=100),
) -> ChatSessionsResponse:
    """Return active conversations ordered by latest activity."""

    return ChatSessionsResponse(
        sessions=SQLiteChatSessionRepository().list_sessions(
            limit=limit,
            owner_id=owner_id,
        ),
        generated_by=CHAT_SESSIONS_VERSION,
    )


@router.get("/chat/sessions/{session_id}", response_model=ChatSessionDetail)
def get_chat_session(
    session_id: str,
    owner_id: Annotated[str, Depends(current_owner_id)],
) -> ChatSessionDetail:
    """Return one conversation with its persisted messages."""

    session = SQLiteChatSessionRepository().get_session(
        session_id,
        owner_id=owner_id,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Chat session not found.")
    return session


@router.patch("/chat/sessions/{session_id}", response_model=ChatSessionDetail)
def update_chat_session(
    session_id: str,
    request: ChatSessionUpdateRequest,
    owner_id: Annotated[str, Depends(current_owner_id)],
) -> ChatSessionDetail:
    """Rename one active conversation."""

    session = SQLiteChatSessionRepository().rename_session(
        session_id,
        request.title,
        owner_id=owner_id,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Chat session not found.")
    return session


@router.delete("/chat/sessions/{session_id}")
def delete_chat_session(
    session_id: str,
    owner_id: Annotated[str, Depends(current_owner_id)],
) -> dict[str, bool]:
    """Permanently delete one local conversation and its stored data."""

    deleted = SQLiteChatSessionRepository().delete_session(
        session_id,
        owner_id=owner_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Chat session not found.")
    return {"deleted": True}


@router.get("/first-board-critic", response_model=FirstBoardCriticResponse)
def get_first_board_critic(
    symbol: str,
    trade_date: Optional[date] = None,
) -> FirstBoardCriticResponse:
    """Return a critic review for one first-board candidate rating."""

    try:
        return build_first_board_critic(
            events=get_limit_up_repository().list_events(),
            symbol=symbol,
            trade_date=trade_date,
            first_board_repository=SQLiteFirstBoardRepository(),
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/chat", response_model=AgentChatResponse)
def chat_with_first_board_agent(
    request: AgentChatRequest,
    owner_id: Annotated[str, Depends(current_owner_id)],
) -> AgentChatResponse:
    """Answer first-board questions and persist an Agent run trace."""

    run_id = f"run_{uuid4().hex}"
    started_at = datetime.now(timezone.utc)
    run_repository = SQLiteAgentRunRepository()
    chat_repository = SQLiteChatSessionRepository()
    try:
        session = chat_repository.ensure_session(
            request.session_id,
            first_message=request.message,
            owner_id=owner_id,
        )
    except SessionOwnershipError as error:
        raise HTTPException(status_code=404, detail="Chat session not found.") from error
    conversation_messages = session.messages[-8:]
    chat_repository.append_message(
        ChatSessionMessage(
            message_id=request.message_id or f"msg_{uuid4().hex}",
            session_id=request.session_id,
            role="user",
            content=request.message,
            created_at=started_at,
        ),
        owner_id=owner_id,
    )

    try:
        response = answer_first_board_chat(
            request=request,
            events=get_limit_up_repository().list_events(),
            repository=SQLiteFirstBoardRepository(),
            recent_runs=run_repository.list_recent_runs(
                request.session_id,
                limit=10,
                owner_id=owner_id,
            ),
            conversation_messages=conversation_messages,
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
        chat_repository.append_message(
            ChatSessionMessage(
                message_id=f"msg_{uuid4().hex}",
                session_id=request.session_id,
                role="assistant",
                content=response.answer,
                run_id=run_id,
                metadata=response.model_dump(mode="json"),
                created_at=datetime.now(timezone.utc),
            ),
            owner_id=owner_id,
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
        chat_repository.append_message(
            ChatSessionMessage(
                message_id=f"msg_{uuid4().hex}",
                session_id=request.session_id,
                role="assistant",
                content="Agent 回答失败，请稍后重试。",
                status="error",
                run_id=run_id,
                metadata={"error": str(error)},
                created_at=datetime.now(timezone.utc),
            ),
            owner_id=owner_id,
        )
        raise


@router.post("/chat/stream")
def stream_first_board_agent_chat(
    request: AgentChatRequest,
    owner_id: Annotated[str, Depends(current_owner_id)],
) -> StreamingResponse:
    """Stream Agent progress, answer deltas and the complete persisted response."""

    run_id = f"run_{uuid4().hex}"
    started_at = datetime.now(timezone.utc)
    run_repository = SQLiteAgentRunRepository()
    chat_repository = SQLiteChatSessionRepository()
    try:
        session = chat_repository.ensure_session(
            request.session_id,
            first_message=request.message,
            owner_id=owner_id,
        )
    except SessionOwnershipError as error:
        raise HTTPException(status_code=404, detail="Chat session not found.") from error
    conversation_messages = session.messages[-8:]
    chat_repository.append_message(
        ChatSessionMessage(
            message_id=request.message_id or f"msg_{uuid4().hex}",
            session_id=request.session_id,
            role="user",
            content=request.message,
            created_at=started_at,
        ),
        owner_id=owner_id,
    )
    event_queue: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()

    def emit(event: str, data: dict[str, Any]) -> None:
        event_queue.put((event, data))

    def run_agent() -> None:
        answer_started = False

        def emit_progress(stage: str, message: str) -> None:
            emit("progress", {"stage": stage, "message": message})

        def emit_answer_delta(delta: str) -> None:
            nonlocal answer_started
            answer_started = True
            emit("answer_delta", {"delta": delta})

        try:
            response = answer_first_board_chat(
                request=request,
                events=get_limit_up_repository().list_events(),
                repository=SQLiteFirstBoardRepository(),
                recent_runs=run_repository.list_recent_runs(
                    request.session_id,
                    limit=10,
                    owner_id=owner_id,
                ),
                conversation_messages=conversation_messages,
                progress_callback=emit_progress,
                answer_delta_callback=emit_answer_delta,
            )
            response.run_id = run_id
            if not answer_started:
                emit_progress("answering", "正在整理最终回答")
                emit_answer_delta(response.answer)
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
            chat_repository.append_message(
                ChatSessionMessage(
                    message_id=f"msg_{uuid4().hex}",
                    session_id=request.session_id,
                    role="assistant",
                    content=response.answer,
                    run_id=run_id,
                    metadata=response.model_dump(mode="json"),
                    created_at=datetime.now(timezone.utc),
                ),
                owner_id=owner_id,
            )
            emit("completed", response.model_dump(mode="json"))
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
            chat_repository.append_message(
                ChatSessionMessage(
                    message_id=f"msg_{uuid4().hex}",
                    session_id=request.session_id,
                    role="assistant",
                    content="Agent 回答失败，请稍后重试。",
                    status="error",
                    run_id=run_id,
                    metadata={"error": str(error)},
                    created_at=datetime.now(timezone.utc),
                ),
                owner_id=owner_id,
            )
            emit(
                "error",
                {
                    "message": "Agent 流式回答失败",
                    "run_id": run_id,
                },
            )
        finally:
            event_queue.put(None)

    def event_stream():
        worker = threading.Thread(target=run_agent, daemon=True)
        worker.start()
        while True:
            item = event_queue.get()
            if item is None:
                break
            event, data = item
            payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            yield f"event: {event}\ndata: {payload}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _summarize_agent_run(run: AgentRun) -> AgentRunSummary:
    """Convert a persisted run into a compact UI trace summary."""

    input_json = run.input_json or {}
    output_json = run.output_json or {}
    answer = output_json.get("answer")
    tool_results = [
        AgentToolTrace.model_validate(tool)
        for tool in output_json.get("tool_results", []) or []
    ]
    duration_ms = max(
        0,
        int((run.finished_at - run.started_at).total_seconds() * 1000),
    )
    return AgentRunSummary(
        run_id=run.run_id,
        session_id=run.session_id,
        status=run.status,
        intent=run.intent,
        message=str(input_json.get("message") or ""),
        answer_preview=str(answer)[:180] if answer else None,
        tool_calls=run.tool_calls,
        tool_results=tool_results,
        warnings=[
            str(warning)
            for warning in output_json.get("warnings", []) or []
            if warning
        ],
        error_message=run.error_message,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_ms=duration_ms,
    )


def _cached_response(
    *,
    scope: str,
    key_parts: dict[str, Any],
    response_model: type[ResponseModel],
    builder: Callable[[], ResponseModel],
) -> ResponseModel:
    """Return a cached structured response, or build and store it."""

    cache_key = _build_cache_key(scope, key_parts)
    cache_repository = SQLiteAgentCacheRepository()
    cached_payload = cache_repository.get_json(cache_key)
    if cached_payload is not None:
        return response_model.model_validate(cached_payload)  # type: ignore[attr-defined]

    response = builder()
    cache_repository.set_json(
        cache_key=cache_key,
        scope=scope,
        payload=response.model_dump(mode="json"),  # type: ignore[attr-defined]
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=STRUCTURED_CACHE_TTL_MINUTES),
    )
    cache_repository.delete_expired()
    return response


def _build_cache_key(scope: str, key_parts: dict[str, Any]) -> str:
    raw = json.dumps(key_parts, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{scope}:{digest}"


def _resolve_trade_date(events: list[Any], trade_date: date | None) -> date | None:
    if trade_date is not None:
        return trade_date
    if not events:
        return None
    return max(event.trade_date for event in events)


def _events_signature(
    events: list[Any],
    *,
    start_date: date | None,
    end_date: date | None,
) -> dict[str, Any]:
    selected = [
        event
        for event in events
        if (start_date is None or event.trade_date >= start_date)
        and (end_date is None or event.trade_date <= end_date)
    ]
    symbols = [
        f"{event.trade_date.isoformat()}:{event.symbol}:{event.board_height}:{int(event.closed_limit)}"
        for event in selected
    ]
    digest = hashlib.sha256("|".join(sorted(symbols)).encode("utf-8")).hexdigest()
    return {
        "count": len(selected),
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "digest": digest,
    }
