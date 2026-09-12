"""Durable ReAct HTTP/SSE transport; reconnect only reads, never reruns tools."""

import json
import threading
import time
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.agents.react_runtime.lifecycle import CURRENT_CONTROL, Journal, RunConflict, RunControl
from app.models import AgentChatResponse, ChatSessionMessage
from app.repositories import SQLiteChatSessionRepository, SessionOwnershipError
from app.security import current_owner_id

router = APIRouter()
_lock = threading.Lock()
_workers: dict[str, threading.Thread] = {}


def owned(journal, run_id, owner_id):
    try:
        return journal.get(run_id, owner_id)
    except KeyError as error:
        raise HTTPException(404, "Agent run not found") from error


def start(request, http_request, owner_id):
    """Create once, or reattach to the same active/completed request."""
    from app.routers import agents
    chats = SQLiteChatSessionRepository()
    try:
        session = chats.ensure_session(request.session_id, first_message=request.message, owner_id=owner_id)
    except SessionOwnershipError as error:
        raise HTTPException(404, "Chat session not found") from error
    journal = Journal()
    with _lock:
        try:
            row, _ = journal.create(request, owner_id)
        except RunConflict as error:
            raise HTTPException(409, str(error)) from error
        except KeyError as error:
            raise HTTPException(404, "Chat session not found") from error
        run_id = row["run_id"]
        if not row["active"] or (_workers.get(run_id) and _workers[run_id].is_alive()):
            return journal, row
        # Explicit POST of the same request may resume a saved checkpoint after
        # worker loss. GET/reconnect never starts a worker or pays for a model.
        started_at = datetime.fromisoformat(row["started_at"])
        try:
            lease, usage_repository, usage_record = agents._begin_agent_request(
                http_request, owner_id=owner_id, session_id=request.session_id,
                run_id=run_id, started_at=datetime.now(timezone.utc),
            )
        except Exception:
            journal.finish(run_id, owner_id, AgentChatResponse(
                session_id=request.session_id, run_id=run_id, intent="react_research", answer="请求未能启动，请稍后重试。",
                task_status="error", stop_reason="request_rejected", generated_by="react-runtime-v1",
            ).model_dump(mode="json"))
            raise
        try:
            chats.append_message(ChatSessionMessage(
                message_id="user_" + run_id, session_id=request.session_id, role="user",
                content=request.message, run_id=run_id, created_at=started_at,
            ), owner_id=owner_id)
        except Exception as error:
            try:
                journal.finish(run_id, owner_id, failed_response(row, "message_persistence_failed").model_dump(mode="json"))
            finally:
                try:
                    agents._finish_agent_request(usage_repository, usage_record, None, error=error)
                finally:
                    lease.release()
            raise
        history = [m for m in session.messages if m.created_at < started_at]

        def worker():
            tracker = response = failure = None
            control = RunControl(journal, row)
            token = CURRENT_CONTROL.set(control)
            try:
                with agents.capture_llm_usage() as tracker:
                    context, memory = agents.prepare_session_context(
                        session_id=request.session_id, owner_id=owner_id, messages=history,
                        repository=agents.SQLiteChatMemoryRepository(),
                    )
                    response = agents.answer_first_board_chat(
                        request=request, events=agents.get_limit_up_repository().list_events(),
                        repository=agents.SQLiteFirstBoardRepository(), conversation_messages=context,
                        session_memory=memory,
                        progress_callback=lambda stage, message: journal.event(run_id, "progress", {"stage": stage, "message": message}),
                    )
                response.run_id = run_id
            except Exception as error:
                failure = RuntimeError(type(error).__name__)
                response = AgentChatResponse(
                    session_id=request.session_id, run_id=run_id, intent="react_research",
                    answer="本次研究执行失败，已保留运行记录。可以重试或缩小查询范围。",
                    task_status="error", stop_reason="execution_error", generated_by="react-runtime-v1",
                )
            finally:
                try:
                    if response is not None:
                        # Persist before publishing any final answer. Message id is
                        # stable across crash recovery and repeated submissions.
                        response = AgentChatResponse.model_validate(journal.finish(run_id, owner_id, response.model_dump(mode="json")))
                finally:
                    try:
                        agents._finish_agent_request(usage_repository, usage_record, tracker, response=response, error=failure)
                    finally:
                        lease.release()
                        CURRENT_CONTROL.reset(token)
                        with _lock:
                            _workers.pop(run_id, None)
        thread = threading.Thread(target=worker, daemon=True, name="react-" + run_id)
        _workers[run_id] = thread
        thread.start()
    return journal, row


def failed_response(row, reason):
    return AgentChatResponse(session_id=row["session_id"], run_id=row["run_id"], intent="react_research",
        answer="本次研究未能完成，请稍后重试。", task_status="error", stop_reason=reason, generated_by="react-runtime-v1")


def stream(journal, row, owner_id, after=0):
    run_id = row["run_id"]
    def frames():
        cursor = after
        yield "event: accepted\ndata: " + json.dumps({"run_id": run_id, "session_id": row["session_id"]}) + "\n\n"
        while True:
            latest = owned(journal, run_id, owner_id)
            for event in journal.events(run_id, owner_id, cursor):
                cursor = event["seq"]
                yield f"id: {cursor}\nevent: {event['event']}\ndata: {event['payload_json']}\n\n"
            if latest["response_json"]:
                yield "event: completed\ndata: " + latest["response_json"] + "\n\n"
                return
            worker = _workers.get(run_id)
            if worker is None or not worker.is_alive():
                # Completion may have committed since the first poll above.
                finished = owned(journal, run_id, owner_id)["response_json"]
                if finished:
                    yield "event: completed\ndata: " + finished + "\n\n"
                    return
                yield "event: error\ndata: " + json.dumps({"run_id": run_id, "message": "运行已中断；重新提交同一请求可恢复，已完成工具不会重复执行。"}, ensure_ascii=False) + "\n\n"
                return
            yield ": heartbeat\n\n"
            time.sleep(.5)
    return StreamingResponse(frames(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Agent-Run-Id": run_id})


def synchronous(journal, row, owner_id):
    while True:
        latest = owned(journal, row["run_id"], owner_id)
        if latest["response_json"]:
            return AgentChatResponse.model_validate_json(latest["response_json"])
        thread = _workers.get(row["run_id"])
        if thread is None or not thread.is_alive():
            finished = owned(journal, row["run_id"], owner_id)["response_json"]
            if finished:
                return AgentChatResponse.model_validate_json(finished)
            raise HTTPException(503, "Run interrupted; resubmit the same message_id to recover")
        thread.join(.5)


@router.get("/chat/runs/{run_id}")
def get_run(run_id: str, owner_id: Annotated[str, Depends(current_owner_id)]):
    row = owned(Journal(), run_id, owner_id)
    thread = _workers.get(run_id)
    return {"run_id": run_id, "active": bool(row["active"]), "cancel_requested": bool(row["cancelled"]),
            "interrupted": bool(row["active"] and (thread is None or not thread.is_alive())),
            "response": json.loads(row["response_json"]) if row["response_json"] else None}


@router.get("/chat/runs/{run_id}/stream")
def reconnect(run_id: str, owner_id: Annotated[str, Depends(current_owner_id)], after: int = Query(0, ge=0)):
    journal = Journal()
    return stream(journal, owned(journal, run_id, owner_id), owner_id, after)


@router.post("/chat/runs/{run_id}/cancel")
def cancel(run_id: str, owner_id: Annotated[str, Depends(current_owner_id)]):
    journal = Journal()
    with _lock:
        row = owned(journal, run_id, owner_id)
        journal.cancel(run_id, owner_id)
        thread = _workers.get(run_id)
        if row["active"] and (thread is None or not thread.is_alive()):
            journal.finish(run_id, owner_id, failed_response(row, "cancelled").model_dump(mode="json"))
    return {"run_id": run_id, "cancel_requested": bool(row["active"] or row["cancelled"])}
