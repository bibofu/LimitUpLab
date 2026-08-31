"""Agent-style first-board rating API routes."""

import hashlib
import json
import os
import queue
import threading
from datetime import date, datetime, timezone
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Callable, TypeVar
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
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
    AgentUsageAdminResponse,
    AgentUsageRecord,
    AgentToolTrace,
    ChatSessionCreateRequest,
    ChatSessionDetail,
    ChatSessionMessage,
    ChatSessionsResponse,
    ChatSessionUpdateRequest,
    DailyPipelineStatusResponse,
    DailyReviewSnapshotsResponse,
    FirstBoardCriticResponse,
    FirstBoardDiscoveryResponse,
    FirstBoardRatingsResponse,
    FactorSignalDiagnosticResponse,
    OutcomeCompletenessReport,
    PredictionQualityAuditResponse,
    RatingBacktestResponse,
    RecommendationIntelligenceResponse,
    ReviewAgentReportResponse,
    ScoringErrorDiagnosticResponse,
    ScoringPolicyOptimizationResponse,
    ScoringPolicyRegistryResponse,
)
from app.repositories import (
    SQLiteAgentCacheRepository,
    SQLiteAgentRunRepository,
    SQLiteAgentUsageRepository,
    SQLiteChatSessionRepository,
    SQLiteDailyPipelineRepository,
    SQLiteFirstBoardRepository,
    SQLiteFirstBoardDiscoveryRepository,
    SQLiteReviewSnapshotRepository,
    SQLiteRecommendationIntelligenceRepository,
    SQLiteScoringPolicyRepository,
    SessionOwnershipError,
    get_limit_up_repository,
)
from app.services.data_health import build_agent_data_health
from app.services.factor_signal_diagnostic import build_factor_signal_diagnostic
from app.services.evaluation_agent import build_agent_evaluation
from app.services.first_board_critic import build_first_board_critic
from app.services.first_board_discovery import FIRST_BOARD_DISCOVERY_VERSION
from app.services.dragon_tiger_review import load_dragon_tiger_review
from app.services.agent_rate_limit import (
    AgentRateLimitError,
    AgentRequestLease,
    agent_rate_limiter,
    load_agent_rate_limit_config,
)
from app.services.llm_provider import (
    DisabledLLMProvider,
    LLMUsageTracker,
    capture_llm_usage,
)
from app.config import env_bool
from app.services.prediction_quality_audit import build_prediction_quality_audit
from app.services.outcome_completeness import build_top10_outcome_completeness
from app.services.rating_backtest import build_rating_backtest
from app.services.scoring_error_diagnostic import build_scoring_error_diagnostic
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


def _begin_agent_request(
    http_request: Request,
    *,
    owner_id: str,
    session_id: str,
    run_id: str,
    started_at: datetime,
) -> tuple[AgentRequestLease, SQLiteAgentUsageRepository, AgentUsageRecord]:
    """Apply request limits and persist an accepted usage record."""

    usage_repository = SQLiteAgentUsageRepository()
    today = usage_repository.owner_today(owner_id)
    ip_hash = _agent_client_ip_hash(http_request)
    try:
        lease = agent_rate_limiter.acquire(
            owner_id,
            ip_hash,
            daily_request_count=today.request_count,
            daily_cost_usd=today.estimated_cost_usd,
        )
    except AgentRateLimitError as error:
        rejected_at = datetime.now(timezone.utc)
        try:
            usage_repository.record_rejection(
                AgentUsageRecord(
                    usage_id=f"usage_{uuid4().hex}",
                    run_id=run_id,
                    session_id=session_id,
                    owner_id=owner_id,
                    ip_hash=ip_hash,
                    status="rejected",
                    started_at=started_at,
                    finished_at=rejected_at,
                    duration_ms=max(
                        0,
                        round((rejected_at - started_at).total_seconds() * 1000),
                    ),
                    error_message=error.code,
                )
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=error.message,
            headers={
                "Retry-After": str(error.retry_after_seconds),
                "X-Agent-Limit-Reason": error.code,
            },
        ) from error

    record = AgentUsageRecord(
        usage_id=f"usage_{uuid4().hex}",
        run_id=run_id,
        session_id=session_id,
        owner_id=owner_id,
        ip_hash=ip_hash,
        started_at=started_at,
    )
    try:
        usage_repository.start(record)
    except Exception:
        lease.release()
        raise
    return lease, usage_repository, record


def _finish_agent_request(
    usage_repository: SQLiteAgentUsageRepository,
    record: AgentUsageRecord,
    tracker: LLMUsageTracker | None,
    *,
    response: AgentChatResponse | None = None,
    error: Exception | None = None,
) -> None:
    """Finalize measured usage without turning accounting failures into chat failures."""

    finished_at = datetime.now(timezone.utc)
    token_usage_complete = bool(tracker and tracker.token_usage_complete)
    prompt_tokens = tracker.prompt_tokens if token_usage_complete and tracker else None
    completion_tokens = (
        tracker.completion_tokens if token_usage_complete and tracker else None
    )
    total_tokens = tracker.total_tokens if token_usage_complete and tracker else None
    performance = response.performance if response is not None else None
    finalized = record.model_copy(
        update={
            "status": "error" if error is not None else "success",
            "model": tracker.model if tracker else None,
            "llm_call_count": tracker.call_count if tracker else 0,
            "failed_llm_call_count": tracker.failed_call_count if tracker else 0,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "token_usage_complete": token_usage_complete,
            "planner_prompt_chars": performance.planner_prompt_chars if performance else 0,
            "answer_prompt_chars": performance.answer_prompt_chars if performance else 0,
            "answer_chars": len(response.answer) if response else 0,
            "estimated_cost_usd": _estimate_llm_cost_usd(
                prompt_tokens,
                completion_tokens,
            ),
            "finished_at": finished_at,
            "duration_ms": max(
                0,
                round((finished_at - record.started_at).total_seconds() * 1000),
            ),
            "error_message": str(error)[:500] if error is not None else None,
        }
    )
    try:
        usage_repository.finish(finalized)
    except Exception:
        # The Agent response is more important than optional accounting. A stale
        # `running` row still consumes the daily quota and is visible to admins.
        return


def _agent_client_ip_hash(request: Request) -> str:
    """Hash the client address, trusting proxy headers only when configured."""

    client_ip = request.client.host if request.client is not None else "unknown"
    if env_bool("LIMITUPLAB_TRUST_PROXY_HEADERS"):
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            client_ip = forwarded.split(",", 1)[0].strip() or client_ip
    return hashlib.sha256(client_ip.encode("utf-8")).hexdigest()


def _estimate_llm_cost_usd(
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> float:
    """Estimate cost only when exact usage and explicit model prices exist."""

    if prompt_tokens is None or completion_tokens is None:
        return 0.0
    input_rate = _non_negative_env_float("LIMITUPLAB_LLM_INPUT_COST_PER_MILLION")
    output_rate = _non_negative_env_float("LIMITUPLAB_LLM_OUTPUT_COST_PER_MILLION")
    if input_rate == 0 and output_rate == 0:
        return 0.0
    return round(
        (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000,
        8,
    )


def _non_negative_env_float(name: str) -> float:
    try:
        value = float(os.getenv(name, "").strip())
    except ValueError:
        return 0.0
    return value if value >= 0 else 0.0


@router.get("/first-board-ratings", response_model=FirstBoardRatingsResponse)
def get_first_board_ratings(
    trade_date: Optional[date] = None,
) -> FirstBoardRatingsResponse:
    """Return explainable first-board candidate ratings for a trade date."""

    events = get_limit_up_repository().list_events()
    resolved_trade_date = _resolve_trade_date(events, trade_date)
    first_board_repository = SQLiteFirstBoardRepository()
    if resolved_trade_date is not None:
        live_snapshot = first_board_repository.get_live_prediction_snapshot(
            resolved_trade_date
        )
        if live_snapshot is not None:
            return live_snapshot
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
        ).model_copy(
            update={
                "snapshot_source": "calculated",
                "data_as_of": resolved_trade_date,
            }
        ),
    )


@router.get(
    "/first-board-discovery",
    response_model=FirstBoardDiscoveryResponse,
)
def get_first_board_discovery(
    data_as_of: date | None = None,
) -> FirstBoardDiscoveryResponse:
    """Return a persisted next-session first-board discovery snapshot."""

    repository = SQLiteFirstBoardDiscoveryRepository()
    response = (
        repository.get(data_as_of, FIRST_BOARD_DISCOVERY_VERSION)
        if data_as_of is not None
        else repository.get_latest()
    )
    if response is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No first-board discovery snapshot is available. "
                "Run the daily update first."
            ),
        )
    return response


@router.get(
    "/recommendation-intelligence",
    response_model=RecommendationIntelligenceResponse,
)
def get_recommendation_intelligence() -> RecommendationIntelligenceResponse:
    """Return the latest half-hour quote, news and financial refresh."""

    response = SQLiteRecommendationIntelligenceRepository().get_latest()
    if response is None:
        raise HTTPException(
            status_code=404,
            detail="No recommendation intelligence snapshot is available.",
        )
    return response


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


@router.get(
    "/outcome-completeness",
    response_model=OutcomeCompletenessReport,
)
def get_outcome_completeness(
    _admin: Annotated[None, Depends(require_admin_access)],
    as_of_date: Optional[date] = None,
    tracking_days: int = Query(default=6, ge=1, le=60),
    top_per_day: int = Query(default=10, ge=1, le=20),
) -> OutcomeCompletenessReport:
    """Return exact D+1, D+3 and D+5 coverage for recent predictions."""

    return build_top10_outcome_completeness(
        events=get_limit_up_repository().list_events(),
        repository=SQLiteFirstBoardRepository(),
        as_of_date=as_of_date,
        tracking_days=tracking_days,
        top_per_day=top_per_day,
    )


@router.get("/usage", response_model=AgentUsageAdminResponse)
def get_agent_usage(
    _admin: Annotated[None, Depends(require_admin_access)],
    days: int = Query(default=1, ge=1, le=90),
) -> AgentUsageAdminResponse:
    """Return protected Agent request, token and cost accounting totals."""

    config = load_agent_rate_limit_config()
    return AgentUsageAdminResponse(
        usage=SQLiteAgentUsageRepository().summary(days=days),
        limits={
            "enabled": config.enabled,
            "requests_per_minute": config.requests_per_minute,
            "requests_per_day": config.requests_per_day,
            "ip_requests_per_minute": config.ip_requests_per_minute,
            "max_concurrent_per_user": config.max_concurrent_per_owner,
            "max_concurrent_global": config.max_concurrent_global,
            "daily_cost_budget_usd": config.daily_cost_budget_usd,
        },
        concurrency=agent_rate_limiter.snapshot(),
        generated_by="agent-usage-accounting-v1",
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


@router.get(
    "/scoring-error-diagnostic",
    response_model=ScoringErrorDiagnosticResponse,
)
def get_scoring_error_diagnostic(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    top_k: int = Query(default=10, ge=3, le=30),
) -> ScoringErrorDiagnosticResponse:
    """Return promotion-first Top-K mistake and factor-ablation evidence."""

    events = get_limit_up_repository().list_events()
    if not events:
        raise HTTPException(status_code=404, detail="No local limit-up events available.")
    available_dates = sorted({event.trade_date for event in events})
    resolved_end = end_date or available_dates[-1]
    eligible_dates = [item for item in available_dates if item <= resolved_end]
    if not eligible_dates:
        raise HTTPException(status_code=404, detail="No events available before end_date.")
    resolved_start = start_date or eligible_dates[max(0, len(eligible_dates) - 60)]
    if resolved_start > resolved_end:
        raise HTTPException(status_code=400, detail="start_date must be before end_date.")
    try:
        return build_scoring_error_diagnostic(
            events=events,
            start_date=resolved_start,
            end_date=resolved_end,
            first_board_repository=SQLiteFirstBoardRepository(),
            top_k=top_k,
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
    if (
        start_date is None
        and min_score == 0
        and top_per_day == 10
        and follow_days == 5
    ):
        snapshot = SQLiteReviewSnapshotRepository().get_snapshot(resolved_end)
        if snapshot is not None:
            return snapshot.report
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


@router.get("/review-snapshots", response_model=DailyReviewSnapshotsResponse)
def list_daily_review_snapshots(
    limit: int = Query(default=20, ge=1, le=100),
) -> DailyReviewSnapshotsResponse:
    """Return persisted review dates for historical playback."""

    return DailyReviewSnapshotsResponse(
        snapshots=SQLiteReviewSnapshotRepository().list_summaries(limit=limit),
        generated_by="daily-review-snapshot-index-v1",
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
    http_request: Request,
    http_response: Response,
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
    lease, usage_repository, usage_record = _begin_agent_request(
        http_request,
        owner_id=owner_id,
        session_id=request.session_id,
        run_id=run_id,
        started_at=started_at,
    )
    http_response.headers["X-Agent-Daily-Remaining"] = str(lease.daily_remaining)
    tracker: LLMUsageTracker | None = None
    response: AgentChatResponse | None = None

    try:
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
        with capture_llm_usage() as tracker:
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
        _finish_agent_request(
            usage_repository,
            usage_record,
            tracker,
            response=response,
        )
        return response
    except Exception as error:
        try:
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
        finally:
            _finish_agent_request(
                usage_repository,
                usage_record,
                tracker,
                response=response,
                error=error,
            )
        raise
    finally:
        lease.release()


@router.post("/chat/stream")
def stream_first_board_agent_chat(
    request: AgentChatRequest,
    http_request: Request,
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
    lease, usage_repository, usage_record = _begin_agent_request(
        http_request,
        owner_id=owner_id,
        session_id=request.session_id,
        run_id=run_id,
        started_at=started_at,
    )
    try:
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
    except Exception as error:
        _finish_agent_request(
            usage_repository,
            usage_record,
            None,
            error=error,
        )
        lease.release()
        raise
    event_queue: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()

    def emit(event: str, data: dict[str, Any]) -> None:
        event_queue.put((event, data))

    def run_agent() -> None:
        answer_started = False
        tracker: LLMUsageTracker | None = None
        response: AgentChatResponse | None = None
        execution_error: Exception | None = None

        def emit_progress(stage: str, message: str) -> None:
            emit("progress", {"stage": stage, "message": message})

        def emit_answer_delta(delta: str) -> None:
            nonlocal answer_started
            answer_started = True
            emit("answer_delta", {"delta": delta})

        try:
            with capture_llm_usage() as tracker:
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
            execution_error = error
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
            _finish_agent_request(
                usage_repository,
                usage_record,
                tracker,
                response=response,
                error=execution_error,
            )
            lease.release()
            event_queue.put(None)

    def event_stream():
        while True:
            item = event_queue.get()
            if item is None:
                break
            event, data = item
            payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            yield f"event: {event}\ndata: {payload}\n\n"

    worker = threading.Thread(target=run_agent, daemon=True)
    worker.start()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Agent-Daily-Remaining": str(lease.daily_remaining),
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
