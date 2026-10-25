"""Native PostgreSQL checkpoints, fencing and immutable tool receipts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from langgraph.checkpoint.base import empty_checkpoint
import pytest

from app.agents.run_lifecycle import CURRENT_CONTROL, Journal, RunConflict, RunControl
from app.models import AgentChatRequest, AgentChatResponse
from app.repositories import ChatSessionRepository
from app.repositories.tool_result_repository import ToolResultRepository
from app.services.runtime_coordination import LeaseLost
from durable_runtime_fixtures import execution_config, fixture_graph, native_saver, run_async


@pytest.fixture
def durable_run(postgresql_database):
    owner = "owner-" + uuid4().hex
    chats = ChatSessionRepository(postgresql_database)
    session = chats.create_session(owner_id=owner)
    journal = Journal(postgresql_database)
    request = AgentChatRequest(session_id=session.session_id, message_id="message-1", message="固定数据研究")
    row, fresh = journal.create(request, owner)
    assert fresh
    lease = journal.claim(row["run_id"])
    assert lease is not None
    control = RunControl(journal, row, lease)
    token = CURRENT_CONTROL.set(control)
    try:
        yield journal, row, lease, control
    finally:
        CURRENT_CONTROL.reset(token)
        lease.release()


def checkpoint(value):
    result = empty_checkpoint()
    result["channel_values"] = {"observation": value}
    result["channel_versions"] = {"observation": "1"}
    return result


async def save(saver, config, value, step=0):
    result = await saver.aput(config, checkpoint(value),
                             {"source": "loop", "step": step, "parents": {}},
                             {"observation": "1"})
    return {"configurable": {**config["configurable"], **result["configurable"]}}


def replace_lease(journal, lease, run_id):
    with journal.connection() as db:
        db.execute("UPDATE runtime_leases SET expires_at=0 WHERE name=?", (lease.name,))
    replacement = journal.claim(run_id)
    assert replacement is not None and replacement.fence > lease.fence
    return replacement


def public_answer(row):
    return AgentChatResponse(session_id=row["session_id"], intent="react_research",
                             answer="已完成固定数据研究。", task_status="complete",
                             generated_by="durable-fixture").model_dump(mode="json")


def test_native_state_and_history_survive_new_pool_without_repeating_tool(durable_run):
    journal, row, lease, _ = durable_run
    calls = []
    config = execution_config(row, lease)

    def query(_state):
        calls.append("tool")
        return {"observations": [{"symbol": "600001", "as_of": "2026-09-01"}]}

    def fail_answer(_state):
        raise ConnectionError("injected model interruption")

    async def scenario():
        async with native_saver(journal.database_url) as saver:
            graph = fixture_graph(saver, query, fail_answer)
            with pytest.raises(ConnectionError, match="model interruption"):
                await graph.ainvoke({}, config, durability="sync")
            state = await graph.aget_state(config)
            assert state.next == ("answer",)
            assert state.values["observations"][0]["symbol"] == "600001"
        async with native_saver(journal.database_url) as saver:
            graph = fixture_graph(saver, query, lambda state: {"answer": state["observations"][0]["symbol"]})
            result = await graph.ainvoke(None, config, durability="sync")
            history = [state async for state in graph.aget_state_history(config)]
            assert result["answer"] == "600001"
            assert history[0].next == ()
            assert len(history) >= 4
            assert calls == ["tool"]

    run_async(scenario())


def test_real_process_kill_restores_committed_superstep(durable_run, tmp_path):
    journal, row, lease, _ = durable_run
    ready = tmp_path / "checkpoint.ready"
    config = execution_config(row, lease)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
           "DURABLE_FIXTURE_URL": journal.database_url,
           "DURABLE_FIXTURE_READY": str(ready), "DURABLE_FIXTURE_CONFIG": json.dumps(config)}
    worker = Path(__file__).with_name("durable_runtime_fixtures.py")
    process = subprocess.Popen(
        [sys.executable, str(worker)], cwd=Path(__file__).resolve().parents[1], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        deadline = time.monotonic() + 20
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists(), "worker did not reach the committed checkpoint"
        process.kill()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)
    assert ready.with_suffix(".calls").read_text(encoding="utf-8") == "1"

    def forbidden_query(_state):
        pytest.fail("completed query must not be replayed after process death")

    async def resume():
        async with native_saver(journal.database_url) as saver:
            graph = fixture_graph(saver, forbidden_query, lambda _: {"answer": "resumed"})
            result = await graph.ainvoke(None, config, durability="sync")
            assert result["answer"] == "resumed"
            assert result["observations"][0]["source"] == "fixture://frozen"

    run_async(resume())


def test_takeover_rejects_both_stale_checkpoint_and_pending_writes(durable_run):
    journal, row, lease, _ = durable_run
    old = execution_config(row, lease)

    async def scenario():
        async with native_saver(journal.database_url) as saver:
            saved = await save(saver, old, "original")
            replacement = replace_lease(journal, lease, row["run_id"])
            try:
                current = execution_config(row, replacement)
                await save(saver, current, "new-worker", 1)
                with pytest.raises(LeaseLost):
                    await save(saver, old, "stale-worker", 2)
                with pytest.raises(LeaseLost):
                    await saver.aput_writes(saved, [("answer", "stale-worker")], "old-task")
                history = [item async for item in saver.alist(current)]
                assert [item.checkpoint["channel_values"]["observation"] for item in history] == ["new-worker", "original"]
                with journal.connection() as db:
                    assert db.execute("SELECT count(*) FROM checkpoint_writes").fetchone()[0] == 0
            finally:
                replacement.release()

    run_async(scenario())


@pytest.mark.parametrize("field,value", [
    ("owner_id", "another-owner"), ("run_id", "another-run"),
    ("thread_id", "another-thread"), ("lease_token", "stale-token"), ("fence", -1),
])
def test_wrong_execution_identity_cannot_write_checkpoint(durable_run, field, value):
    journal, row, lease, _ = durable_run
    config = execution_config(row, lease)
    config["configurable"][field] = value

    async def scenario():
        async with native_saver(journal.database_url) as saver:
            with pytest.raises(LeaseLost):
                await save(saver, config, "forbidden")
        with journal.connection() as db:
            assert db.execute("SELECT count(*) FROM checkpoints").fetchone()[0] == 0

    run_async(scenario())


def test_owner_checked_session_deletion_cascades_native_checkpoint_tables(durable_run):
    journal, row, lease, _ = durable_run

    async def scenario():
        async with native_saver(journal.database_url) as saver:
            config = await save(saver, execution_config(row, lease), [{"symbol": "600001"}])
            await saver.aput_writes(config, [("answer", "saved-write")], "fixture-task")
            chats = ChatSessionRepository(journal.database_url)
            with pytest.raises(RunConflict):
                chats.delete_session(row["session_id"], owner_id=row["owner_id"])
            journal.finish(row["run_id"], row["owner_id"], public_answer(row))
            assert not chats.delete_session(row["session_id"], owner_id="other-owner")
            assert await saver.aget_tuple(config) is not None
            assert chats.delete_session(row["session_id"], owner_id=row["owner_id"])
            assert await saver.aget_tuple(config) is None
        with journal.connection() as db:
            for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0

    run_async(scenario())


def test_completed_tool_receipt_survives_checkpoint_failure(durable_run):
    journal, row, lease, control = durable_run
    results = ToolResultRepository(control)
    call = {"id": "tool-1", "name": "fixture_quote", "args": {"symbol": "600001"}}
    observed = {"status": "ok", "rows": [{"symbol": "600001", "close": 12.5}], "source": "fixture://frozen"}
    calls = []
    assert results.execute(call, lambda: calls.append(1) or observed) == observed

    async def fail_checkpoint():
        async with native_saver(journal.database_url) as saver:
            config = execution_config(row, lease)
            config["configurable"]["fence"] = -1
            with pytest.raises(LeaseLost):
                await save(saver, config, observed)

    run_async(fail_checkpoint())
    recovered = ToolResultRepository(RunControl(Journal(journal.database_url), row, lease))
    assert recovered.execute(call, lambda: calls.append(2) or {"close": 99}) == observed
    assert calls == [1]
    with pytest.raises(RunConflict):
        recovered.execute({**call, "args": {"symbol": "000002"}}, lambda: {})


def test_unknown_tool_receipt_never_automatically_repeats_operation(durable_run):
    _, _, _, control = durable_run
    results = ToolResultRepository(control)
    call = {"id": "tool-uncertain", "name": "fixture", "args": {}}
    calls = []

    def operation():
        calls.append("external-call")
        raise ConnectionError("response lost after side effect")

    with pytest.raises(ConnectionError):
        results.execute(call, operation)
    recovered = results.execute(call, operation)
    assert calls == ["external-call"]
    assert recovered["status"] == "error"
    assert recovered["error_type"] == "UncertainExecution"
    assert recovered["data_missing"] == ["tool_execution_receipt"]


def test_tool_receipt_cannot_be_committed_by_worker_that_lost_lease(durable_run):
    journal, row, lease, control = durable_run
    replacement = None

    def operation():
        nonlocal replacement
        replacement = replace_lease(journal, lease, row["run_id"])
        return {"status": "ok", "source": "fixture://late"}

    try:
        with pytest.raises(LeaseLost):
            ToolResultRepository(control).execute({"id": "late", "name": "fixture", "args": {}}, operation)
        with journal.connection() as db:
            receipt = db.execute("SELECT status,payload_json FROM agent_tool_results").fetchone()
            assert receipt["status"] == "started" and receipt["payload_json"] is None
    finally:
        if replacement is not None:
            replacement.release()
