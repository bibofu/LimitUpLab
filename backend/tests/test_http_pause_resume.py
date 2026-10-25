"""Real HTTP, PostgreSQL and LangGraph pause controls with deterministic I/O."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app.agents.runner import adapter
from app.agents.runner.durable_runtime import get_runtime
from app.agents.run_lifecycle import RunLifecycle
from app.database import connect_readonly
from app.main import app
from app.routers import agents
from app.services.agent_run_recovery import recover_once
from app.services.agent_run_services import RunDelivery
from test_durable_agent import fixed_registry, list_answer, tool_call
from test_runner_production import input_review
from test_runner_shadow import ScriptedProvider


def wait_until(function, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = function()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError("Expected durable state was not reached")


def checkpoint(session_id):
    runtime = get_runtime()
    return asyncio.run_coroutine_threadsafe(runtime.graph.aget_state(
        {"configurable": {"thread_id": session_id}}), runtime.loop).result(timeout=5)


@pytest.fixture
def environment(postgresql_database, monkeypatch):
    provider = ScriptedProvider([input_review(), tool_call(), list_answer()])
    tools, calls = fixed_registry()
    monkeypatch.setenv("LIMITUPLAB_LANGFUSE_ENABLED", "false")
    monkeypatch.setenv("LIMITUPLAB_AGENT_COMPLIANCE", "deterministic")
    monkeypatch.setenv("LIMITUPLAB_AGENT_RATE_LIMIT_ENABLED", "false")
    monkeypatch.setattr(adapter, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(adapter, "AgentToolRegistry", lambda **_: tools)
    monkeypatch.setattr(agents, "get_limit_up_repository", lambda: SimpleNamespace(list_events=lambda: []))
    return SimpleNamespace(url=postgresql_database, provider=provider, tools=tools, calls=calls)


def run_row(url, session):
    with closing(connect_readonly(url)) as db:
        return dict(db.execute("SELECT * FROM react_runs WHERE session_id=?", (session,)).fetchone())


def control_body(state, operation=None):
    return {"operation_id": operation or uuid4().hex, "expected_state_revision": state["state_revision"]}


def pause_at_gate(client, env, monkeypatch, *, delivery=False, racing_control=False):
    entered, release = threading.Event(), threading.Event()
    target = RunDelivery.commit if delivery else env.tools.limit_up_events

    def gated(*args, **kwargs):
        result = None if delivery else target(*args, **kwargs)
        entered.set()
        assert release.wait(timeout=10)
        return target(*args, **kwargs) if delivery else result

    if delivery:
        monkeypatch.setattr(RunDelivery, "commit", gated)
    else:
        monkeypatch.setattr(env.tools, "limit_up_events", gated)
    session = client.post("/api/agents/chat/sessions", json={}).json()["session_id"]
    body = {"session_id": session, "message_id": uuid4().hex, "message": "列出固定样本名单"}
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, "/api/agents/chat", json=body)
        try:
            assert entered.wait(timeout=8)
            row = run_row(env.url, session)
            url = "/api/agents/chat/runs/" + row["run_id"]
            state = client.get(url).json()
            operation = control_body(state)
            accepted = client.post(url + "/pause", json=operation)
            assert accepted.status_code == 202, accepted.text
            assert accepted.json()["run_status"] == "pausing"
            assert client.post(url + "/resume", json=control_body(accepted.json())).status_code == 409
        finally:
            release.set()
        result = pending.result(timeout=8)
    assert result.status_code in {200, 202}, result.text
    if not racing_control:
        assert result.status_code == 202 and result.json()["run_status"] == "paused"
    wait_until(lambda: not RunLifecycle().executing(row["run_id"]))
    return session, body, url, operation


@pytest.mark.parametrize("delivery", [False, True])
def test_pause_survives_lifespan_restart_and_resumes_once(environment, monkeypatch, delivery):
    env = environment
    with TestClient(app) as client:
        session, body, url, operation = pause_at_gate(client, env, monkeypatch, delivery=delivery)
        state = client.get(url).json()
        saved = checkpoint(session)
        assert saved.values["tool_count"] == 1
        assert len(env.calls) == 1
        assert run_row(env.url, session)["pause_mode"] == ("delivery" if delivery else "interrupt")
        cookies = dict(client.cookies)
        assert client.get("/api/agents/chat/bootstrap", params={"session_id": session}).json()["selected_session"]["active_run"]["run_id"] == state["run_id"]
        replay = client.get(url + "/stream").text
        assert "event: run_state" in replay and '"run_status": "paused"' in replay
        assert "event: completed" not in replay and "event: error" not in replay
        assert client.post("/api/agents/chat", json=body).status_code == 202
        count = len(env.provider.calls)
    with TestClient(app) as restarted:
        restarted.cookies.update(cookies)
        assert recover_once() == recover_once() == 0
        assert restarted.get(url).json()["run_status"] == "paused"
        assert checkpoint(session).config["configurable"]["checkpoint_id"] == saved.config["configurable"]["checkpoint_id"]
        assert len(env.provider.calls) == count and len(env.calls) == 1
        with closing(connect_readonly(env.url)) as db:
            assert db.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM chat_messages WHERE role='assistant'").fetchone()[0] == 0
        resume_body = control_body(restarted.get(url).json())
        response = restarted.post(url + "/resume", json=resume_body)
        assert response.status_code in {200, 202}, response.text
        final = wait_until(lambda: restarted.get(url).json()["response"])
        assert final["task_status"] == "complete"
        assert len(env.calls) == 1
        assert len(env.provider.calls) == count + (0 if delivery else 1)
        assert restarted.post(url + "/resume", json=resume_body).json()["run_status"] == "completed"
        assert restarted.post(url + "/pause", json=operation).json()["run_status"] == "completed"
        assert restarted.post("/api/agents/chat", json=body).json() == final
        assert restarted.get(url + "/stream").text.count("event: completed") == 1
        assert restarted.get(url).json()["recovery_count"] == 0
        with closing(connect_readonly(env.url)) as db:
            assert db.execute("SELECT count(*) FROM agent_tool_results").fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM chat_messages WHERE role='assistant'").fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM chat_messages WHERE role='user'").fetchone()[0] == 1


def test_resume_rejection_keeps_paused_and_owner_controls_are_private(environment, monkeypatch):
    env = environment
    with TestClient(app) as client:
        session, body, url, _ = pause_at_gate(client, env, monkeypatch)
        state = client.get(url).json()
        stranger = TestClient(app)
        try:
            assert stranger.get(url).status_code == 404
            assert stranger.post(url + "/resume", json=control_body(state)).status_code == 404
        finally:
            stranger.close()
        def denied(*args, **kwargs):
            raise HTTPException(429, "fixture concurrency limit")
        monkeypatch.setattr(agents, "_begin_agent_request", denied)
        response = client.post(url + "/resume", json=control_body(state))
        assert response.status_code == 429
        current = client.get(url).json()
        assert current["run_status"] == "paused" and current["can_resume"]
        assert current["state_revision"] > state["state_revision"]
        assert len(env.calls) == 1 and len(env.provider.calls) == 2
        assert client.post(url + "/resume", json={**control_body(current), "checkpoint_id": "arbitrary"}).status_code == 422
        assert client.post(url + "/cancel").status_code == 200
        assert client.get(url).json()["run_status"] == "cancelled"
        assert client.post(url + "/resume", json=control_body(client.get(url).json())).status_code == 409


@pytest.mark.parametrize("action", ["resume", "cancel"])
def test_control_during_pause_ack_is_dispatched_after_worker_releases(environment, monkeypatch, action):
    from app.services.agent_run_control import RunControlService

    original = RunControlService.ack_paused
    with TestClient(app) as client:
        def acknowledge(service, run_id, owner_id, **kwargs):
            row = original(service, run_id, owner_id, **kwargs)
            url = "/api/agents/chat/runs/" + run_id
            state = client.get(url).json()
            assert RunLifecycle().executing(run_id)
            request = client.post(url + "/" + action, json=control_body(state) if action == "resume" else {})
            assert request.status_code in {200, 202}
            assert client.get(url).json()["response"] is None
            return row
        monkeypatch.setattr(RunControlService, "ack_paused", acknowledge)
        _, _, url, _ = pause_at_gate(client, environment, monkeypatch, racing_control=True)
        response = wait_until(lambda: client.get(url).json()["response"])
        assert response["task_status"] == ("complete" if action == "resume" else "cancelled")
        assert len(environment.calls) == 1


def test_unadmitted_resume_after_crash_returns_to_paused(environment):
    from app.models import AgentChatRequest
    from app.repositories import ChatSessionRepository
    from app.services.agent_run_services import AgentRunServices

    services = AgentRunServices()
    ChatSessionRepository().ensure_session("unadmitted-pause", owner_id="owner")
    row, _ = services.registry.create(AgentChatRequest(session_id="unadmitted-pause", message="什么是首板"), "owner")
    run_id = row["run_id"]
    row = services.controls.request_pause(run_id, "owner", operation_id="pause", expected_revision=0)
    execution = services.coordinator.claim(run_id)
    services.controls.ack_paused(run_id, "owner", expected_revision=row["control_revision"],
                                checkpoint_id=None, execution=execution)
    execution.release()
    services.controls.request_resume(run_id, "owner", operation_id="resume", expected_revision=row["control_revision"])
    with TestClient(app):
        recover_once()
        assert services.registry.get(run_id, "owner")["run_status"] == "paused"
        assert environment.provider.calls == [] and environment.calls == []


def test_resume_during_reconciliation_release_dispatches_without_supervisor(environment, monkeypatch):
    from app.services.agent_run_control import RunControlService

    env = environment
    provider = ScriptedProvider([input_review(), ConnectionError("fixture model failure"), tool_call(), list_answer()])
    monkeypatch.setattr(adapter, "get_llm_provider", lambda: provider)
    original = RunControlService.ack_paused
    with TestClient(app) as client:
        session = client.post("/api/agents/chat/sessions", json={}).json()["session_id"]
        body = {"session_id": session, "message_id": uuid4().hex, "message": "列出固定样本名单"}
        assert client.post("/api/agents/chat", json=body).status_code == 503
        row = run_row(env.url, session)
        url = "/api/agents/chat/runs/" + row["run_id"]
        def acknowledge(service, run_id, owner_id, **kwargs):
            paused = original(service, run_id, owner_id, **kwargs)
            assert RunLifecycle().executing(run_id)
            response = client.post(url + "/resume", json=control_body(client.get(url).json()))
            assert response.status_code == 202 and response.json()["run_status"] == "resuming"
            return paused
        monkeypatch.setattr(RunControlService, "ack_paused", acknowledge)
        response = client.post(url + "/pause", json=control_body(client.get(url).json()))
        assert response.status_code in {200, 202}, response.text
        final = wait_until(lambda: client.get(url).json()["response"])
        assert final["task_status"] == "complete" and len(env.calls) == 1
