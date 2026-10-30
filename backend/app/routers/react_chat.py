"""Durable ReAct HTTP/SSE transport; reconnect only reads, never reruns tools."""

import json
import logging
import os
import threading
from contextlib import ExitStack
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agents.run_lifecycle import CURRENT_CONTROL, RunLifecycle, RunConflict, RunControl
from app.agents.runtime_errors import RecoverableRunError, RunPauseRequested
from app.agents.run_pause import PausedRunError
from app.agents.runtime_version import VERSION
from app.models import AgentChatResponse, ChatSessionMessage
from app.repositories import ChatSessionRepository, SessionOwnershipError
from app.security import current_owner_id
from app.services.agent_tracing import get_agent_tracer, trace_context, trace_stage
from app.services.agent_stream import PUBLIC_STREAM_EVENTS
from app.services.agent_run_state import public_run_state

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


def owned(lifecycle, run_id, owner_id):
    try:
        return lifecycle.get(run_id, owner_id)
    except KeyError as error:
        raise HTTPException(404, "Agent run not found") from error


def acknowledge_pause(control, outcome=None):
    from app.agents.runner.durable_runtime import get_runtime

    runtime = get_runtime()
    if runtime is None:
        raise RecoverableRunError("暂停意图已保存，等待运行时核对执行进度。")
    outcome = outcome or runtime.pause_sync(control)
    row = control.lifecycle.services.controls.ack_paused(
        control.row["run_id"], control.row["owner_id"],
        expected_revision=outcome.pause_revision, checkpoint_id=outcome.checkpoint_id,
        pause_mode=outcome.resume_mode, interrupt_id=outcome.interrupt_id,
        execution=control.execution, admission=control.admission,
    )
    notify_streams()
    return row


def dispatch_control_after_release(lifecycle, run_id, owner_id):
    """Close the acknowledgement/release window for a concurrent user action."""
    from app.models import AgentChatRequest

    try:
        latest = lifecycle.get(run_id, owner_id)
        if latest["active"] and latest["cancelled"]:
            cancel(run_id, owner_id)
        elif latest["active"] and latest["run_status"] == "resuming":
            start(AgentChatRequest.model_validate_json(latest["request_json"]), None, owner_id)
    except Exception:
        logger.info("Control dispatch deferred for %s", run_id, exc_info=True)


def settle_pause(lifecycle, row):
    """Reconcile a durable pause after a crash, without executing business nodes."""
    from app.agents.runner.durable_runtime import get_runtime

    if get_runtime() is None or row["run_status"] != "pausing":
        return row
    execution = lifecycle.claim(row["run_id"])
    if execution is None:
        return row
    try:
        execution.start_heartbeat()
        latest = lifecycle.get(row["run_id"], row["owner_id"])
        if latest["run_status"] != "pausing" or latest["cancelled"]:
            return latest
        return acknowledge_pause(RunControl(lifecycle, latest, execution))
    finally:
        execution.release()
        notify_streams()
        dispatch_control_after_release(lifecycle, row["run_id"], row["owner_id"])


def start(request, http_request, owner_id):
    """Create once, or reattach to the same active/completed request."""
    from app.routers import agents
    from app.agents.runner.durable_runtime import get_runtime
    chats = ChatSessionRepository()
    lifecycle = RunLifecycle()
    with _lock:
        try:
            existing = lifecycle.services.registry.find_request(request, owner_id)
            if existing and not existing["active"]:
                return lifecycle, existing
            if existing and existing["run_status"] in {"pausing", "paused"} and not existing["cancelled"]:
                return lifecycle, existing
            if existing and existing["runtime_version"] != "durable-v1":
                raise HTTPException(409, "旧版未完成任务无法恢复。请先取消该任务，再以新 message_id 重新提交。")
            if get_runtime() is None:
                raise HTTPException(503, "持久化运行时暂不可用，请稍后重试；已有任务已保留。")
            session = chats.ensure_session(request.session_id, first_message=request.message, owner_id=owner_id)
            row, fresh = lifecycle.create(request, owner_id)
        except RunConflict as error:
            raise HTTPException(409, str(error)) from error
        except (KeyError, SessionOwnershipError) as error:
            raise HTTPException(404, "Chat session not found") from error
        run_id = row["run_id"]
        if not row["active"]:
            return lifecycle, row
        if row["runtime_version"] != "durable-v1":
            raise HTTPException(409, "旧版未完成任务无法恢复。请先取消该任务，再以新 message_id 重新提交。")
        execution = lifecycle.claim(run_id)
        if execution is None:
            return lifecycle, row
        # A remote worker may have finished after create() returned its snapshot.
        row = lifecycle.get(run_id, owner_id)
        if not row["active"]:
            execution.release()
            return lifecycle, row
        # POST may resume the saved graph; GET/reconnect remains read-only.
        started_at = datetime.fromisoformat(row["started_at"])
        try:
            execution.start_heartbeat()
            if row["run_status"] == "resuming":
                try:
                    get_runtime().validate_resume_sync(RunControl(lifecycle, row, execution))
                except RecoverableRunError as error:
                    raise HTTPException(409, "暂停位置无法核对，任务保持暂停，请检查服务日志。") from error
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
                    lifecycle.discard_unstarted(run_id, owner_id, execution)
                elif row["run_status"] == "resuming":
                    lifecycle.services.controls.defer_resume(
                        run_id, owner_id, expected_revision=row["control_revision"], execution=execution,
                    )
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
                    lifecycle.discard_unstarted(run_id, owner_id, execution)
                elif row["run_status"] == "resuming":
                    lifecycle.services.controls.defer_resume(
                        run_id, owner_id, expected_revision=row["control_revision"], execution=execution,
                    )
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
            paused = False
            control = RunControl(lifecycle, row, execution, lease)
            control.notify = notify_streams
            token = CURRENT_CONTROL.set(control)
            # Tracing starts inside the worker thread: ContextVar/OTel context does
            # not cross the thread boundary from the HTTP handler.
            trace_scope = ExitStack()
            trace_scope.enter_context(trace_context(
                run_id=run_id, session_id=request.session_id, thread_id=request.session_id,
                runtime_version=row["runtime_version"],
                recovery_count=row.get("recovery_count", 0) + int(row["run_status"] not in {"pending", "resuming"}),
            ))
            run_span = trace_scope.enter_context(
                get_agent_tracer().run(
                    run_id=run_id,
                    session_id=request.session_id,
                    owner_id=owner_id,
                    message=request.message,
                    metadata={
                        "model": os.getenv("LIMITUPLAB_LLM_MODEL", ""),
                        "profile": os.getenv("LIMITUPLAB_AGENT_PROFILE", ""),
                        "thread_id": request.session_id,
                        "session_id": request.session_id,
                        "runtime_version": row["runtime_version"],
                        "recovery_count": row.get("recovery_count", 0) + int(row["run_status"] not in {"pending", "resuming"}),
                        "model_version": os.getenv("LIMITUPLAB_LLM_MODEL", ""),
                    },
                    tags=["chat"],
                )
            )
            def progress(stage, message):
                lifecycle.event(run_id, "progress", {"stage": stage, "message": message})
                notify_streams()
            try:
                control.row = lifecycle.services.registry.mark_running(
                    run_id, owner_id, execution=execution, admission=lease,
                )
                progress("preparing", "正在准备会话与研究数据")
                # A resumed attempt must not append onto a draft that a crash or
                # a pause left visible. Withdraw it and continue in a new revision.
                revision = answer_revision_base(lifecycle, run_id, owner_id)
                if revision:
                    try:
                        lifecycle.event(run_id, "answer_reset", {
                            "event_version": 1, "stage": "answering", "run_id": run_id,
                            "thread_id": request.session_id, "message": "正在重新整理回答",
                        })
                        notify_streams()
                    except Exception:
                        # Draft cleanup is presentation, not execution state.
                        logger.info("Unable to publish draft reset for %s", run_id, exc_info=True)
                with agents.capture_llm_usage() as tracker:
                    response = agents.answer_first_board_chat(
                        request=request, events=agents.get_limit_up_repository().list_events(),
                        repository=agents.FirstBoardRepository(), conversation_messages=history,
                        progress_callback=progress, answer_revision_base=revision,
                    )
                response.run_id = run_id
            except (PausedRunError, RunPauseRequested) as error:
                try:
                    acknowledge_pause(control, getattr(error, "outcome", None))
                    paused = True
                except Exception as pause_error:
                    failure = RuntimeError(type(pause_error).__name__)
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
                        with trace_stage("final_delivery"):
                            response = AgentChatResponse.model_validate(lifecycle.finish(run_id, owner_id, response.model_dump(mode="json")))
                        notify_streams()
                except RunPauseRequested:
                    response = None
                    try:
                        acknowledge_pause(control)
                        paused = True
                    except Exception as error:
                        failure = RuntimeError(type(error).__name__)
                except Exception as error:
                    # Saving is part of delivery: leave the run record retryable
                    # and let the stream report interruption, never publish a candidate.
                    failure = RuntimeError(type(error).__name__)
                    response = None
                finally:
                    try:
                        if response is None and not paused:
                            try:
                                lifecycle.services.registry.mark_interrupted(
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
                                        response.task_status if response is not None else "paused" if paused else "error"
                                    ),
                                    "stop_reason": (
                                        response.stop_reason if response is not None else None
                                    ),
                                    "input_tokens": tracker.prompt_tokens if tracker and tracker.token_usage_complete else None,
                                    "output_tokens": tracker.completion_tokens if tracker and tracker.token_usage_complete else None,
                                    "tool_calls": len(response.tool_calls) if response is not None else None,
                                    "recovery_count": control.row.get("recovery_count", 0),
                                },
                                error=failure,
                            )
                            trace_scope.close()
                            dispatch_control_after_release(lifecycle, run_id, owner_id)
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
                        if row["run_status"] == "resuming":
                            lifecycle.services.controls.defer_resume(
                                run_id, owner_id, expected_revision=row["control_revision"], execution=execution,
                            )
                    finally:
                        try:
                            lease.release()
                        finally:
                            execution.release()
                finally:
                    notify_streams()
            raise HTTPException(503, "任务暂未能启动，请重试原请求以恢复。") from error
    return lifecycle, row


def failed_response(row, reason):
    return AgentChatResponse(session_id=row["session_id"], run_id=row["run_id"], intent="react_research",
        answer="本次研究未能完成，请稍后重试。", task_status="error", stop_reason=reason, generated_by=VERSION)


def answer_revision_base(lifecycle, run_id, owner_id):
    """Highest candidate-answer revision already persisted for this run."""

    revisions = []
    for event in lifecycle.events(run_id, owner_id):
        if event["event"] != "answer_start":
            continue
        try:
            revisions.append(int(json.loads(event["payload_json"]).get("revision") or 0))
        except (TypeError, ValueError):
            continue
    return max(revisions, default=0)


def answer_snapshot(lifecycle, run_id, owner_id):
    """Replay the current provisional answer revision for a new connection."""

    frames = []
    for event in lifecycle.events(run_id, owner_id):
        kind = event["event"]
        if kind == "answer_start":
            frames = [event]
        elif kind == "answer_reset":
            frames = []
        elif kind == "answer_delta" and frames:
            frames.append(event)
    return frames


def interruption_message(row):
    if row.get("runtime_version") == "durable-v1":
        return "运行已中断；原任务和已保存的执行进度已保留，可重试同一请求恢复。"
    return "旧版任务无法恢复。请先取消该任务，再以新 message_id 重新提交。"


def stream(lifecycle, row, owner_id, after=0):
    run_id = row["run_id"]
    def frames():
        cursor = after
        observed_state = None
        yield "event: accepted\ndata: " + json.dumps({"run_id": run_id, "session_id": row["session_id"],
            "state_revision": row.get("control_revision", 0)}) + "\n\n"
        if cursor:
            # A new subscription (page reload or a fresh transport) has no local
            # draft. Re-send the current revision before the durable cursor so the
            # reader can rebuild it; replayed frames are idempotent for a reader
            # that still holds the same buffer.
            latest = owned(lifecycle, run_id, owner_id)
            if not latest["response_json"]:
                for event in answer_snapshot(lifecycle, run_id, owner_id):
                    yield f"event: {event['event']}\ndata: {event['payload_json']}\n\n"
        while True:
            with _updates:
                revision = _update_revision
            latest = owned(lifecycle, run_id, owner_id)
            if latest["response_json"]:
                # Completed reconnects skip withdrawn drafts and publish only the authoritative answer.
                yield "event: completed\ndata: " + latest["response_json"] + "\n\n"
                return
            state_key = (latest["run_status"], latest.get("control_revision", 0), latest["cancelled"])
            if state_key != observed_state:
                yield "event: run_state\ndata: " + json.dumps(public_run_state(lifecycle, latest), ensure_ascii=False) + "\n\n"
                observed_state = state_key
            for event in lifecycle.events(run_id, owner_id, cursor):
                cursor = event["seq"]
                # Old runs may contain draft events this protocol no longer uses.
                # Advance the cursor without replaying unknown kinds.
                if event["event"] in PUBLIC_STREAM_EVENTS:
                    yield f"id: {cursor}\nevent: {event['event']}\ndata: {event['payload_json']}\n\n"
            if latest["run_status"] == "paused" and not latest["cancelled"]:
                return
            if not lifecycle.executing(run_id):
                # Completion may have committed since the first poll above.
                refreshed = owned(lifecycle, run_id, owner_id)
                if refreshed["response_json"] or refreshed["run_status"] != latest["run_status"]:
                    # Re-read completion on the next poll.
                    continue
                if latest["run_status"] not in {"pausing", "resuming"} and not latest["cancelled"]:
                    yield "event: error\ndata: " + json.dumps({"run_id": run_id, "message": interruption_message(latest)}, ensure_ascii=False) + "\n\n"
                    return
            yield ": heartbeat\n\n"
            with _updates:
                _updates.wait_for(lambda: _update_revision != revision, timeout=.5)
    return StreamingResponse(frames(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Agent-Run-Id": run_id})


def synchronous(lifecycle, row, owner_id):
    while True:
        latest = owned(lifecycle, row["run_id"], owner_id)
        if latest["response_json"]:
            return AgentChatResponse.model_validate_json(latest["response_json"])
        if latest["run_status"] == "paused" and not latest["cancelled"]:
            return JSONResponse(public_run_state(lifecycle, latest), status_code=202)
        if not lifecycle.executing(row["run_id"]):
            refreshed = owned(lifecycle, row["run_id"], owner_id)
            if refreshed["response_json"]:
                return AgentChatResponse.model_validate_json(refreshed["response_json"])
            if refreshed["run_status"] != latest["run_status"]:
                continue
            if refreshed["run_status"] not in {"pausing", "resuming"} and not refreshed["cancelled"]:
                raise HTTPException(503, interruption_message(latest))
        with _updates:
            _updates.wait(timeout=.5)


@router.get("/chat/runs/{run_id}")
def get_run(run_id: str, owner_id: Annotated[str, Depends(current_owner_id)]):
    lifecycle = RunLifecycle()
    row = owned(lifecycle, run_id, owner_id)
    return public_run_state(lifecycle, row)


class RunControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=1, max_length=128)
    expected_state_revision: int = Field(ge=0)


@router.post("/chat/runs/{run_id}/pause")
def pause(run_id: str, request: RunControlRequest, owner_id: Annotated[str, Depends(current_owner_id)]):
    lifecycle = RunLifecycle()
    owned(lifecycle, run_id, owner_id)
    try:
        row = lifecycle.services.controls.request_pause(
            run_id, owner_id, operation_id=request.operation_id, expected_revision=request.expected_state_revision,
        )
        row = settle_pause(lifecycle, row)
    except RunConflict as error:
        raise HTTPException(409, str(error)) from error
    notify_streams()
    row = lifecycle.get(run_id, owner_id)
    return JSONResponse(public_run_state(lifecycle, row), status_code=202 if row["active"] else 200)


@router.post("/chat/runs/{run_id}/resume")
def resume(run_id: str, request: RunControlRequest, http_request: Request,
           owner_id: Annotated[str, Depends(current_owner_id)]):
    from app.agents.runner.durable_runtime import get_runtime
    from app.models import AgentChatRequest

    lifecycle = RunLifecycle()
    owned(lifecycle, run_id, owner_id)
    if get_runtime() is None:
        raise HTTPException(503, "持久化运行时暂不可用，任务保持暂停。")
    try:
        row = lifecycle.services.controls.request_resume(
            run_id, owner_id, operation_id=request.operation_id, expected_revision=request.expected_state_revision,
        )
        if row["run_status"] == "resuming" and not row["cancelled"]:
            start(AgentChatRequest.model_validate_json(row["request_json"]), http_request, owner_id)
    except RunConflict as error:
        raise HTTPException(409, str(error)) from error
    notify_streams()
    row = lifecycle.get(run_id, owner_id)
    return JSONResponse(public_run_state(lifecycle, row), status_code=202 if row["active"] else 200)


@router.get("/chat/runs/{run_id}/stream")
def reconnect(run_id: str, owner_id: Annotated[str, Depends(current_owner_id)], after: int = Query(0, ge=0)):
    lifecycle = RunLifecycle()
    return stream(lifecycle, owned(lifecycle, run_id, owner_id), owner_id, after)


@router.post("/chat/runs/{run_id}/cancel")
def cancel(run_id: str, owner_id: Annotated[str, Depends(current_owner_id)]):
    lifecycle = RunLifecycle()
    with _lock:
        row = owned(lifecycle, run_id, owner_id)
        lifecycle.cancel(run_id, owner_id)
        execution = lifecycle.claim(run_id) if row["active"] else None
        if execution is not None:
            token = CURRENT_CONTROL.set(RunControl(lifecycle, row, execution))
            try:
                lifecycle.finish(run_id, owner_id, failed_response(row, "cancelled").model_dump(mode="json"))
            finally:
                CURRENT_CONTROL.reset(token)
                execution.release()
        notify_streams()
    return {"run_id": run_id, "cancel_requested": bool(row["active"] or row["cancelled"])}
