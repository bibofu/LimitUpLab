"""Missing graph hosts and historical runs never select a second runtime."""

import pytest
from fastapi import HTTPException

from app.agents.run_lifecycle import CURRENT_CONTROL, RunControl, RunLifecycle
from app.agents.runner.runner import AgentRunner
from app.models import AgentChatRequest
from app.repositories import ChatSessionRepository
from app.routers import react_chat
from test_agent_run_services import response
from test_runner_shadow import ScriptedProvider, registry


def request():
    return AgentChatRequest(session_id="single-runtime", message_id="message-one", message="什么是首板？")


@pytest.fixture
def unavailable_host(postgresql_database, monkeypatch):
    monkeypatch.setattr("app.agents.runner.durable_runtime.get_runtime", lambda: None)
    return RunLifecycle(postgresql_database)


def test_new_request_without_host_creates_no_session_run_or_usage(unavailable_host):
    with pytest.raises(HTTPException) as caught:
        react_chat.start(request(), None, "owner")
    assert caught.value.status_code == 503
    with unavailable_host.connection() as db:
        for table in ("chat_sessions", "react_runs", "chat_messages", "agent_usage_events"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("version", ["legacy", "durable-v1"])
def test_completed_run_replays_without_host_but_rejects_changed_payload(unavailable_host, version):
    lifecycle = unavailable_host
    ChatSessionRepository().ensure_session(request().session_id, owner_id="owner")
    row, _ = lifecycle.create(request(), "owner", runtime_version=version)
    final = lifecycle.finish(row["run_id"], "owner", response(row))
    reused, replay = react_chat.start(request(), None, "owner")
    assert replay["run_id"] == row["run_id"]
    assert reused.services.registry.delivered_response(row["run_id"], "owner") == final
    with pytest.raises(HTTPException) as caught:
        react_chat.start(request().model_copy(update={"message": "不同的问题"}), None, "owner")
    assert caught.value.status_code == 409


@pytest.mark.parametrize("host_available", [False, True])
def test_legacy_unfinished_run_requires_explicit_cancel_then_new_request(unavailable_host, monkeypatch, host_available):
    lifecycle = unavailable_host
    ChatSessionRepository().ensure_session(request().session_id, owner_id="owner")
    row, _ = lifecycle.create(request(), "owner", runtime_version="legacy")
    if host_available:
        monkeypatch.setattr("app.agents.runner.durable_runtime.get_runtime", lambda: object())
    with pytest.raises(HTTPException) as caught:
        react_chat.start(request(), None, "owner")
    assert caught.value.status_code == 409
    assert "新 message_id" in caught.value.detail
    assert lifecycle.get(row["run_id"], "owner")["runtime_version"] == "legacy"
    assert not lifecycle.executing(row["run_id"])
    assert lifecycle.events(row["run_id"], "owner") == []
    assert react_chat.cancel(row["run_id"], "owner")["cancel_requested"]
    assert lifecycle.services.registry.delivered_response(row["run_id"], "owner")["task_status"] == "cancelled"


def test_durable_unfinished_run_is_preserved_when_host_is_unavailable(unavailable_host):
    lifecycle = unavailable_host
    ChatSessionRepository().ensure_session(request().session_id, owner_id="owner")
    row, _ = lifecycle.create(request(), "owner")
    with pytest.raises(HTTPException) as caught:
        react_chat.start(request(), None, "owner")
    assert caught.value.status_code == 503
    assert lifecycle.get(row["run_id"], "owner")["active"]
    assert lifecycle.get(row["run_id"], "owner")["response_json"] is None


def test_public_runner_rejects_missing_control_without_model_call(unavailable_host):
    provider = ScriptedProvider([])
    runner = AgentRunner(request(), registry(), provider)
    with pytest.raises(RuntimeError, match="durable run"):
        runner.run()
    assert provider.calls == []


def test_public_runner_rejects_legacy_control_without_model_call(unavailable_host):
    ChatSessionRepository().ensure_session(request().session_id, owner_id="owner")
    row, _ = unavailable_host.create(request(), "owner", runtime_version="legacy")
    provider = ScriptedProvider([])
    token = CURRENT_CONTROL.set(RunControl(unavailable_host, row))
    try:
        with pytest.raises(RuntimeError, match="durable run"):
            AgentRunner(request(), registry(), provider).run()
    finally:
        CURRENT_CONTROL.reset(token)
    assert provider.calls == []
