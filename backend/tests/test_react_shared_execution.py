"""Database ownership, independent transport readers and stale-worker fencing."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from uuid import uuid4

import pytest

from app.agents.run_lifecycle import CURRENT_CONTROL, RunLifecycle, RunControl
from app.models import AgentChatRequest, AgentChatResponse
from app.repositories import ChatSessionRepository
from app.routers import react_chat
from app.services.runtime_coordination import LeaseLost
from test_runtime_coordination import lease_store


@pytest.fixture
def shared_run(lease_store):
    session = "shared-" + uuid4().hex
    owner = "owner-" + uuid4().hex
    ChatSessionRepository(lease_store.database_url).ensure_session(session, owner_id=owner)
    lifecycle = RunLifecycle(lease_store.database_url)
    request = AgentChatRequest(session_id=session, message_id="m", message="什么是首板？")
    row, _ = lifecycle.create(request, owner)
    return lifecycle, row, owner


def answer(row):
    return AgentChatResponse(session_id=row["session_id"], intent="react_research", answer="研究说明。",
                             task_status="complete", stop_reason="answered", generated_by="fixture").model_dump(mode="json")


def test_stale_worker_cannot_publish_any_execution_write(shared_run):
    lifecycle, row, owner = shared_run
    key = row["run_id"]
    old = lifecycle.claim(key)
    context = CURRENT_CONTROL.set(RunControl(lifecycle, row, old))
    try:
        lifecycle.services.registry.mark_running(key, owner, execution=old)
    finally:
        CURRENT_CONTROL.reset(context)
    with lifecycle.connection() as db:
        db.execute("UPDATE runtime_leases SET expires_at=0 WHERE name=?", (old.name,))
    new = RunLifecycle(lifecycle.database_url).claim(key)
    context = CURRENT_CONTROL.set(RunControl(lifecycle, row, old))
    try:
        for operation in (
            lambda: lifecycle.event(key, "progress", {"stale": True}),
            lambda: lifecycle.services.registry.mark_running(key, owner, execution=old),
            lambda: lifecycle.services.registry.mark_interrupted(key, owner, execution=old),
            lambda: lifecycle.finish(key, owner, answer(row)),
        ):
            with pytest.raises(LeaseLost):
                operation()
        assert RunControl(lifecycle, row, old).cancelled()
    finally:
        CURRENT_CONTROL.reset(context)
    assert not lifecycle.events(key, owner)
    assert lifecycle.get(key, owner)["checkpoint_json"] is None
    context = CURRENT_CONTROL.set(RunControl(lifecycle, row, new))
    try:
        assert lifecycle.finish(key, owner, answer(row))["task_status"] == "complete"
    finally:
        CURRENT_CONTROL.reset(context)
        old.release()
        new.release()


def test_duplicate_request_and_claim_have_one_winner(shared_run):
    lifecycle, row, owner = shared_run
    request = AgentChatRequest.model_validate_json(row["request_json"])
    def submit(_):
        remote = RunLifecycle(lifecycle.database_url)
        record, fresh = remote.create(request, owner)
        return record["run_id"], fresh, remote.claim(record["run_id"])
    with ThreadPoolExecutor(max_workers=6) as workers:
        results = list(workers.map(submit, range(6)))
    assert {item[0] for item in results} == {row["run_id"]}
    assert not any(item[1] for item in results)
    winners = [item[2] for item in results if item[2] is not None]
    assert len(winners) == 1
    winners[0].release()


def test_remote_status_stream_and_cancel_ignore_local_thread_registry(shared_run, monkeypatch):
    lifecycle, row, owner = shared_run
    key = row["run_id"]
    execution = lifecycle.claim(key)
    monkeypatch.setattr(react_chat, "RunLifecycle", lambda: RunLifecycle(lifecycle.database_url))
    # Model a different API instance: it owns no local worker for this run.
    assert key not in react_chat._workers
    assert not react_chat.get_run(key, owner)["interrupted"]
    async def read():
        frames = react_chat.stream(RunLifecycle(lifecycle.database_url), row, owner).body_iterator
        assert "accepted" in await anext(frames)
        assert "run_state" in await anext(frames)
        assert "heartbeat" in await anext(frames)
        await frames.aclose()
    asyncio.run(read())
    assert react_chat.cancel(key, owner)["cancel_requested"]
    assert lifecycle.get(key, owner)["response_json"] is None
    context = CURRENT_CONTROL.set(RunControl(lifecycle, row, execution))
    try:
        assert lifecycle.finish(key, owner, answer(row))["task_status"] == "cancelled"
    finally:
        CURRENT_CONTROL.reset(context)
        execution.release()
    assert not react_chat.get_run(key, owner)["active"]


def test_remote_cancel_finalizes_expired_run_without_model(shared_run, monkeypatch):
    lifecycle, row, owner = shared_run
    execution = lifecycle.claim(row["run_id"])
    execution.release()
    monkeypatch.setattr(react_chat, "RunLifecycle", lambda: RunLifecycle(lifecycle.database_url))
    assert react_chat.get_run(row["run_id"], owner)["interrupted"]
    react_chat.cancel(row["run_id"], owner)
    result = lifecycle.get(row["run_id"], owner)
    assert not result["active"] and json.loads(result["response_json"])["task_status"] == "cancelled"
