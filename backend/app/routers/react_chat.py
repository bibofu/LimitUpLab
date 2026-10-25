"""Durable ReAct HTTP/SSE transport; reconnect only reads, never reruns tools."""

import json
import logging
import os
import threading
from contextlib import ExitStack
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.agents.run_lifecycle import CURRENT_CONTROL, RunLifecycle, RunConflict, RunControl
from app.agents.runtime_errors import RecoverableRunError
from app.agents.runtime_version import VERSION
from app.models import AgentChatResponse, ChatSessionMessage
from app.repositories import ChatSessionRepository, SessionOwnershipError
from app.security import current_owner_id
from app.services.agent_tracing import get_agent_tracer

router = APIRouter()
_lock = threading.Lock()
_workers: dict[str, threading.Thread] = {}
_updates = threading.Condition()
_update_revision = 0
logger = logging.getLogger(__name__)


def notify_streams():
    """Wake local readers after committed changes; polling remains a fallback."""
    global _update_revision
    with _updates:
        _update_revision += 1
        _updates.notify_all()


def owned(journal, run_id, owner_id):
    try:
        return journal.get(run_id, owner_id)
    except KeyError as error:
        raise HTTPException(404, "Agent run not found") from error


def start(request, http_request, owner_id):
    """Create once, or reattach to the same active/completed request."""
    from app.routers import agents
    from app.agents.runner.durable_runtime import get_runtime
    chats = ChatSessionRepository()
    journal = RunLifecycle()
    with _lock:
        try:
            existing = journal.services.registry.find_request(request, owner_id)
            if existing and not existing["active"]:
                return journal, existing
            if existing and existing["runtime_version"] != "durable-v1":
                raise HTTPException(409, "旧版未完成任务无法恢复。请先取消该任务，再以新 message_id 重新提交。")
            if get_runtime() is None:
                raise HTTPException(503, "持久化运行时暂不可用，请稍后重试；已有任务已保留。")
            session = chats.ensure_session(request.session_id, first_message=request.message, owner_id=owner_id)
            row, fresh = journal.create(request, owner_id)
        except RunConflict as error:
            raise HTTPException(409, str(error)) from error
        except (KeyError, SessionOwnershipError) as error:
            raise HTTPException(404, "Chat session not found") from error
        run_id = row["run_id"]
        if not row["active"]:
            return journal, row
        if row["runtime_version"] != "durable-v1":
            raise HTTPException(409, "旧版未完成任务无法恢复。请先取消该任务，再以新 message_id 重新提交。")
        execution = journal.claim(run_id)
        if execution is None:
            return journal, row
        # A remote worker may have finished after create() returned its snapshot.
        row = journal.get(run_id, owner_id)
        if not row["active"]:
            execution.release()
            return journal, row
        # POST may resume the saved graph; GET/reconnect remains read-only.
        started_at = datetime.fromisoformat(row["started_at"])
        try:
            execution.start_heartbeat()
            lease, usage_repository, usage_record = agents._begin_agent_request(
                http_request, owner_id=owner_id, session_id=request.session_id,
                run_id=run_id, started_at=datetime.now(timezone.utc),
                resume_run_id=run_id if not fresh else None,
            )
        except Exception:
            # Admission failures are retryable, not completed chat answers. Keep
            # an existing run record intact, and release a brand-new reservation
            # so it cannot strand the conversation behind an unknown active run.
            try:
                if fresh:
                    journal.discard_unstarted(run_id, owner_id, execution)
            finally:
                execution.release()
            raise
        try:
            chats.append_message(ChatSessionMessage(
                message_id="user_" + run_id, session_id=request.session_id, role="user",
                content=request.message, run_id=run_id, created_at=started_at,
            ), owner_id=owner_id)
        except Exception as error:
            try:
                if fresh:
                    journal.discard_unstarted(run_id, owner_id, execution)
            finally:
                try:
                    agents._finish_agent_request(usage_repository, usage_record, None, error=error)
                finally:
                    try:
                        lease.release()
                    finally:
                        execution.release()
            raise HTTPException(503, "消息暂未能保存，请重试原请求。") from error
        history = [m for m in session.messages if m.created_at < started_at]

        def worker():
            tracker = response = failure = None
            control = RunControl(journal, row, execution, lease)
            token = CURRENT_CONTROL.set(control)
            # Tracing starts inside the worker thread: ContextVar/OTel context does
            # not cross the thread boundary from the HTTP handler.
            trace_scope = ExitStack()
            run_span = trace_scope.enter_context(
                get_agent_tracer().run(
                    run_id=run_id,
                    session_id=request.session_id,
                    owner_id=owner_id,
                    message=request.message,
                    metadata={
                        "model": os.getenv("LIMITUPLAB_LLM_MODEL", ""),
                        "profile": os.getenv("LIMITUPLAB_AGENT_PROFILE", ""),
                    },
                    tags=["chat"],
                )
            )
            def progress(stage, message):
                journal.event(run_id, "progress", {"stage": stage, "message": message})
                notify_streams()
            try:
                control.row = journal.services.registry.mark_running(
                    run_id, owner_id, execution=execution, admission=lease,
                )
                progress("preparing", "正在准备会话与研究数据")
                with agents.capture_llm_usage() as tracker:
                    response = agents.answer_first_board_chat(
                        request=request, events=agents.get_limit_up_repository().list_events(),
                        repository=agents.FirstBoardRepository(), conversation_messages=history,
                        progress_callback=progress,
                    )
                response.run_id = run_id
            except RecoverableRunError as error:
                failure = RuntimeError(type(error).__name__)
                response = None
            except Exception as error:
                failure = RuntimeError(type(error).__name__)
                response = AgentChatResponse(
                    session_id=request.session_id, run_id=run_id, intent="react_research",
                    answer="本次研究执行失败，已保留运行记录。可以重试或缩小查询范围。",
                    task_status="error", stop_reason="execution_error", generated_by=VERSION,
                )
            finally:
                try:
                    if response is not None:
                        # Persist the authoritative answer before publishing completed. Message id is
                        # stable across crash recovery and repeated submissions.
                        progress("checking", "正在保存研究结果")
                        if run_span.trace_id:
                            response = response.model_copy(
                                update={"trace_url": run_span.url()}
                            )
                        response = AgentChatResponse.model_validate(journal.finish(run_id, owner_id, response.model_dump(mode="json")))
                        notify_streams()
                except Exception as error:
                    # Saving is part of delivery: leave the run record retryable
                    # and let the stream report interruption, never publish a candidate.
                    failure = RuntimeError(type(error).__name__)
                    response = None
                finally:
                    try:
                        if response is None:
                            try:
                                journal.services.registry.mark_interrupted(
                                    run_id, owner_id, execution=execution,
                                )
                            except Exception:
                                # An expired owner must not overwrite a successor.
                                logger.warning("Unable to mark interrupted run %s", run_id, exc_info=True)
                        agents._finish_agent_request(usage_repository, usage_record, tracker, response=response, error=failure)
                    finally:
                        try:
                            try:
                                lease.release()
                            finally:
                                execution.release()
                        finally:
                            CURRENT_CONTROL.reset(token)
                            with _lock:
                                if _workers.get(run_id) is threading.current_thread():
                                    _workers.pop(run_id, None)
                            notify_streams()
                            run_span.finish(
                                output={
                                    "status": (
                                        response.task_status if response is not None else "error"
                                    ),
                                    "stop_reason": (
                                        response.stop_reason if response is not None else None
                                    ),
                                },
                                error=failure,
                            )
                            trace_scope.close()
        try:
            thread = threading.Thread(target=worker, daemon=True, name="react-" + run_id)
            _workers[run_id] = thread
            thread.start()
        except Exception as error:
            _workers.pop(run_id, None)
            try:
                agents._finish_agent_request(usage_repository, usage_record, None, error=error)
            finally:
                try:
                    try:
                        lease.release()
                    finally:
                        execution.release()
                finally:
                    notify_streams()
            raise HTTPException(503, "任务暂未能启动，请重试原请求以恢复。") from error
    return journal, row


def failed_response(row, reason):
    return AgentChatResponse(session_id=row["session_id"], run_id=row["run_id"], intent="react_research",
        answer="本次研究未能完成，请稍后重试。", task_status="error", stop_reason=reason, generated_by=VERSION)


def interruption_message(row):
    if row.get("runtime_version") == "durable-v1":
        return "运行已中断；原任务和已保存的执行进度已保留，可重试同一请求恢复。"
    return "旧版任务无法恢复。请先取消该任务，再以新 message_id 重新提交。"


def stream(journal, row, owner_id, after=0):
    run_id = row["run_id"]
    def frames():
        cursor = after
        yield "event: accepted\ndata: " + json.dumps({"run_id": run_id, "session_id": row["session_id"]}) + "\n\n"
        while True:
            with _updates:
                revision = _update_revision
            latest = owned(journal, run_id, owner_id)
            if latest["response_json"]:
                # Completed reconnects skip withdrawn drafts and publish only the authoritative answer.
                yield "event: completed\ndata: " + latest["response_json"] + "\n\n"
                return
            for event in journal.events(run_id, owner_id, cursor):
                cursor = event["seq"]
                # Old interrupted runs may contain draft events. Advance the
                # cursor without replaying them; only committed answers are public.
                if event["event"] == "progress":
                    yield f"id: {cursor}\nevent: progress\ndata: {event['payload_json']}\n\n"
            if not journal.executing(run_id):
                # Completion may have committed since the first poll above.
                finished = owned(journal, run_id, owner_id)["response_json"]
                if finished:
                    # Re-read completion on the next poll.
                    continue
                yield "event: error\ndata: " + json.dumps({"run_id": run_id, "message": interruption_message(latest)}, ensure_ascii=False) + "\n\n"
                return
            yield ": heartbeat\n\n"
            with _updates:
                _updates.wait_for(lambda: _update_revision != revision, timeout=.5)
    return StreamingResponse(frames(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Agent-Run-Id": run_id})


def synchronous(journal, row, owner_id):
    while True:
        latest = owned(journal, row["run_id"], owner_id)
        if latest["response_json"]:
            return AgentChatResponse.model_validate_json(latest["response_json"])
        if not journal.executing(row["run_id"]):
            finished = owned(journal, row["run_id"], owner_id)["response_json"]
            if finished:
                return AgentChatResponse.model_validate_json(finished)
            raise HTTPException(503, interruption_message(latest))
        with _updates:
            _updates.wait(timeout=.5)


@router.get("/chat/runs/{run_id}")
def get_run(run_id: str, owner_id: Annotated[str, Depends(current_owner_id)]):
    journal = RunLifecycle()
    row = owned(journal, run_id, owner_id)
    return {"run_id": run_id, "active": bool(row["active"]), "cancel_requested": bool(row["cancelled"]),
            "interrupted": bool(row["active"] and not journal.executing(run_id)),
            "run_status": row["run_status"], "recovery_count": row["recovery_count"],
            "response": json.loads(row["response_json"]) if row["response_json"] else None}


@router.get("/chat/runs/{run_id}/stream")
def reconnect(run_id: str, owner_id: Annotated[str, Depends(current_owner_id)], after: int = Query(0, ge=0)):
    journal = RunLifecycle()
    return stream(journal, owned(journal, run_id, owner_id), owner_id, after)


@router.post("/chat/runs/{run_id}/cancel")
def cancel(run_id: str, owner_id: Annotated[str, Depends(current_owner_id)]):
    journal = RunLifecycle()
    with _lock:
        row = owned(journal, run_id, owner_id)
        journal.cancel(run_id, owner_id)
        execution = journal.claim(run_id) if row["active"] else None
        if execution is not None:
            token = CURRENT_CONTROL.set(RunControl(journal, row, execution))
            try:
                journal.finish(run_id, owner_id, failed_response(row, "cancelled").model_dump(mode="json"))
            finally:
                CURRENT_CONTROL.reset(token)
                execution.release()
        notify_streams()
    return {"run_id": run_id, "cancel_requested": bool(row["active"] or row["cancelled"])}
