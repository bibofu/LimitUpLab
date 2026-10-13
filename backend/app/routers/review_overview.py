"""On-demand SSE review summary; no cached prose or prediction/outcome writes."""

import json
from threading import Event, Thread
from datetime import date, datetime, timezone
from queue import Empty, SimpleQueue
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from app.agents.review_agent import build_review_agent_report
from app.agents.review_overview import OverviewValidationError, build_overview_facts, generate_overview
from app.repositories import SQLiteFirstBoardRepository, get_limit_up_repository
from app.security import current_owner_id
from app.services.llm_provider import DisabledLLMProvider, capture_llm_usage, get_llm_provider
from app.services.promotion_calendar import load_promotion_calendar

router = APIRouter()


class OverviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    end_date: date


@router.post("/review-overview/stream")
def stream_review_overview(
    payload: OverviewRequest, request: Request,
    owner_id: Annotated[str, Depends(current_owner_id)],
):
    # Use the same owner/IP limits and exact usage accounting as chat without creating a chat.
    from app.routers import agents
    provider = get_llm_provider()
    if isinstance(provider, DisabledLLMProvider):
        raise HTTPException(503, "总结模型暂不可用，请检查模型配置后重试。")
    lease, usage_repository, usage_record = agents._begin_agent_request(
        request, owner_id=owner_id, session_id="review-overview", run_id="overview_" + uuid4().hex,
        started_at=datetime.now(timezone.utc),
    )
    queue = SimpleQueue()
    stopped = Event()
    def emit(event, data):
        if stopped.is_set():
            raise RuntimeError("Overview stream closed")
        queue.put((event, data))

    def worker():
        tracker = failure = None
        try:
            with capture_llm_usage() as tracker:
                emit("progress", {"message": "正在核对市场、候选与高标事实"})
                events = [event for event in get_limit_up_repository().list_events()
                          if event.trade_date <= payload.end_date]
                if not events:
                    raise ValueError("No local events")
                observed = sorted({event.trade_date for event in events})
                calendar = load_promotion_calendar(observed[0], observed[-1])
                days = [day for day in calendar.trade_dates if day < payload.end_date][-5:]
                repository = SQLiteFirstBoardRepository()
                report = build_review_agent_report(
                    events=events, start_date=min(days) if days else observed[0], end_date=payload.end_date,
                    repository=repository, min_score=0, top_per_day=10, follow_days=5,
                    provider=DisabledLLMProvider(), trade_dates=calendar.trade_dates,
                )
                facts = build_overview_facts(report=report, events=events, repository=repository,
                                             trade_dates=calendar.trade_dates)
                facts["notes"].extend(calendar.warnings)
                generate_overview(facts=facts, provider=provider, emit=emit, stopped=stopped.is_set)
        except Exception as error:
            failure = RuntimeError(str(error) if isinstance(error, OverviewValidationError) else type(error).__name__)
            queue.put(("error", {"message": "本次总结未完成或未通过核验，请重新生成。"}))
        finally:
            try:
                agents._finish_agent_request(usage_repository, usage_record, tracker, error=failure)
            finally:
                lease.release()

    try:
        thread = Thread(target=worker, daemon=True, name="review-overview")
        thread.start()
    except Exception as error:
        try:
            agents._finish_agent_request(usage_repository, usage_record, None, error=error)
        finally:
            lease.release()
        raise HTTPException(503, "总结暂未能启动，请重试。") from error

    def frames():
        try:
            while True:
                try:
                    event, data = queue.get(timeout=.5)
                except Empty:
                    yield ": heartbeat\n\n"
                    continue
                yield "event: " + event + "\ndata: " + json.dumps(data, ensure_ascii=False) + "\n\n"
                if event in {"completed", "error"}:
                    return
        finally:
            stopped.set()

    stream = StreamingResponse(frames(), media_type="text/event-stream",
                               headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
    return stream
