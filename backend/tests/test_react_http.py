"""Real loopback HTTP against the production routes, with frozen model/data."""

import json
import socket
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request

from app.agents.react_runtime.lifecycle import Journal
from app.agents.react_runtime.runtime import run
from app.agents.tools import TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest
from app.repositories import SQLiteChatSessionRepository
from app.routers import agents, react_chat
from app.security import current_owner_id
from test_react_lifecycle import Model


@pytest.fixture(params=["react", "legacy", "task"])
def http_server(tmp_path, monkeypatch, request):
    monkeypatch.setenv("LIMITUPLAB_DATABASE_PATH", str(tmp_path / "http.sqlite"))
    monkeypatch.setenv("LIMITUPLAB_AGENT_RUNTIME", request.param)
    state = SimpleNamespace(calls=0, block=False, entered=threading.Event(), release=threading.Event())
    def ranking(**args):
        state.calls += 1
        state.entered.set()
        if state.block:
            assert state.release.wait(10)
        return ToolResult(name="hot_stock_ranking", input=args, summary="fixture", output={"items": [{"symbol": "000001"}]})
    registry = SimpleNamespace(events=[], schemas=lambda: TOOL_SCHEMAS, is_enabled=lambda _: True, hot_stock_ranking=ranking)
    monkeypatch.setattr(agents, "answer_first_board_chat", lambda request, **kw: run(request, registry, Model(), progress=kw.get("progress_callback")))
    monkeypatch.setattr(agents, "prepare_session_context", lambda **kw: ([], None))
    monkeypatch.setattr(agents, "get_limit_up_repository", lambda: SimpleNamespace(list_events=lambda: []))
    monkeypatch.setattr(agents, "_begin_agent_request", lambda *a, **kw: (SimpleNamespace(release=lambda: None), None, None))
    monkeypatch.setattr(agents, "_finish_agent_request", lambda *a, **kw: None)
    app = FastAPI()
    def owner(request: Request):
        return request.headers.get("x-fixture-owner", "owner")
    app.dependency_overrides[current_owner_id] = owner
    app.include_router(agents.router, prefix="/api/agents")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline: time.sleep(.01)
    assert server.started
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}/api/agents", trust_env=False, timeout=10) as client:
            yield client, state
    finally:
        state.release.set()
        for worker in list(react_chat._workers.values()): worker.join(5)
        server.should_exit = True
        thread.join(5)
        sock.close()
        assert not thread.is_alive()


PAYLOAD = {"session_id": "http-s", "message_id": "http-m", "message": "查询热榜，缺失时说明缺口"}


def test_http_idempotency_reconnect_owner_and_delete(http_server):
    client, state = http_server
    response = client.post("/chat", json=PAYLOAD)
    assert response.status_code == 200
    result = response.json()
    key = result["run_id"]
    assert result["task_status"] == "partial"
    assert client.post("/chat", json=PAYLOAD).json() == result
    assert state.calls == 1
    assert client.post("/chat", json={**PAYLOAD, "message": "different"}).status_code == 409
    for path in (f"/chat/runs/{key}", f"/chat/runs/{key}/stream"):
        assert client.get(path, headers={"x-fixture-owner": "other"}).status_code == 404
    assert client.post(f"/chat/runs/{key}/cancel", headers={"x-fixture-owner": "other"}).status_code == 404
    events = Journal().events(key, "owner")
    replay = client.get(f"/chat/runs/{key}/stream", params={"after": events[-1]["seq"]})
    assert "event: completed" in replay.text and "event: progress" not in replay.text
    assert '"task_status": "partial"' in replay.text or '"task_status":"partial"' in replay.text
    assert state.calls == 1
    assert client.delete("/chat/sessions/http-s").status_code == 200
    assert client.get(f"/chat/runs/{key}").status_code == 404


def test_disconnect_does_not_cancel_and_explicit_cancel_releases_session(http_server):
    client, state = http_server
    state.block = True
    with client.stream("POST", "/chat/stream", json=PAYLOAD) as response:
        assert response.status_code == 200
        key = response.headers["x-agent-run-id"]
        for line in response.iter_lines():
            if line.startswith("data:"): break
    assert state.entered.wait(5)
    status = client.get(f"/chat/runs/{key}").json()
    assert status["active"] and not status["cancel_requested"]
    assert client.post("/chat", json={**PAYLOAD, "message_id": "second"}).status_code == 409
    assert client.delete("/chat/sessions/http-s").status_code == 409
    assert client.post(f"/chat/runs/{key}/cancel").status_code == 200
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = client.get(f"/chat/runs/{key}").json()
        if not status["active"]: break
        time.sleep(.02)
    assert not status["active"] and status["response"]["task_status"] == "cancelled"
    assert state.calls == 1
    state.release.set()
    replay = client.post("/chat", json=PAYLOAD).json()
    assert replay["task_status"] == "cancelled" and state.calls == 1


def test_cancel_interrupted_run_does_not_restart_model(http_server):
    client, state = http_server
    SQLiteChatSessionRepository().ensure_session("http-s", owner_id="owner")
    journal = Journal()
    row, _ = journal.create(AgentChatRequest(**PAYLOAD), "owner")
    key = row["run_id"]
    assert client.get(f"/chat/runs/{key}").json()["interrupted"]
    assert "event: error" in client.get(f"/chat/runs/{key}/stream").text
    assert state.calls == 0
    assert client.post(f"/chat/runs/{key}/cancel").status_code == 200
    result = client.get(f"/chat/runs/{key}").json()
    assert not result["active"] and result["response"]["task_status"] == "cancelled"
    assert state.calls == 0
