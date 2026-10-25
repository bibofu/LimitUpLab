"""Exercise FastAPI lifespan, HTTP worker and the real async graph bridge."""

import asyncio
from contextlib import closing
import threading
import time
from types import SimpleNamespace

from fastapi import Request
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.agents.runner import adapter
from app.agents.runner.durable_runtime import get_runtime
from app.database import connect_readonly
from app.main import app
from app.models import AgentChatRequest
from app.routers import agents, react_chat
from app.services.durable_runtime_host import DurableRuntimeHost
from test_durable_agent import fixed_registry, list_answer, tool_call
from test_runner_production import input_review
from test_runner_shadow import ScriptedProvider


def test_http_lifespan_uses_durable_bridge_and_reopens_committed_graph(postgresql_database, monkeypatch):
    # Only the external model and market-data dependencies are scripted. HTTP,
    # ownership, admission, worker threads, host, saver and delivery are real.
    provider = ScriptedProvider([input_review(), tool_call(), list_answer()])
    tools, calls = fixed_registry()
    monkeypatch.setenv("LIMITUPLAB_LANGFUSE_ENABLED", "false")
    monkeypatch.setenv("LIMITUPLAB_AGENT_COMPLIANCE", "deterministic")
    monkeypatch.setattr(adapter, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(adapter, "AgentToolRegistry", lambda **_: tools)
    monkeypatch.setattr(agents, "get_limit_up_repository", lambda: SimpleNamespace(list_events=lambda: []))

    with TestClient(app) as client:
        first_runtime = get_runtime()
        assert first_runtime is not None and first_runtime.loop.is_running()
        assert client.get("/ready").json() == {"status": "ok", "database": "postgresql"}
        created = client.post("/api/agents/chat/sessions", json={"title": "Durable HTTP fixture"})
        assert created.status_code == 200
        session_id = created.json()["session_id"]
        request = {"session_id": session_id, "message_id": "http-durable-message", "message": "列出固定样本名单"}
        response = client.post("/api/agents/chat", json=request)
        assert response.status_code == 200, response.text
        final = response.json()
        assert final["task_status"] == "complete"
        assert final["tool_calls"] == ["limit_up_events"]
        assert len(provider.calls) == 3 and len(calls) == 1
        run_id = final["run_id"]
        stream = client.get(f"/api/agents/chat/runs/{run_id}/stream?after=0")
        assert stream.status_code == 200 and "event: completed" in stream.text
        assert client.post("/api/agents/chat", json=request).json() == final
        saved_cookies = dict(client.cookies)
        with closing(connect_readonly(postgresql_database)) as db:
            row = db.execute("SELECT runtime_version,thread_id,run_status FROM react_runs WHERE run_id=?", (run_id,)).fetchone()
            assert row["runtime_version"] == "durable-v1"
            assert row["thread_id"] == session_id and row["run_status"] == "completed"
            assert db.execute("SELECT count(*) FROM checkpoints WHERE thread_id=?", (session_id,)).fetchone()[0] >= 5
            assert db.execute("SELECT count(*) FROM agent_tool_results WHERE run_id=?", (run_id,)).fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM chat_messages WHERE role='assistant' AND run_id=?", (run_id,)).fetchone()[0] == 1

    assert get_runtime() is None
    assert first_runtime.loop.is_closed()
    with TestClient(app) as restarted:
        restarted.cookies.update(saved_cookies)
        second_runtime = get_runtime()
        assert second_runtime is not first_runtime
        snapshot = asyncio.run_coroutine_threadsafe(
            second_runtime.graph.aget_state({"configurable": {"thread_id": session_id}}),
            second_runtime.loop,
        ).result(timeout=5)
        assert snapshot.next == () and snapshot.values["run_id"] == run_id
        assert snapshot.values["tool_count"] == 1
        assert restarted.get(f"/api/agents/chat/runs/{run_id}").json()["response"] == final
        assert restarted.post("/api/agents/chat", json=request).json() == final
        assert len(provider.calls) == 3 and len(calls) == 1
    assert get_runtime() is None
    assert second_runtime.loop.is_closed()


def test_host_shutdown_keeps_real_router_worker_run_recoverable(postgresql_database, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    class BlockingProvider(ScriptedProvider):
        worker_thread = None

        def generate_messages(self, messages, tools, **kwargs):
            if len(self.calls) == 1:
                self.worker_thread = threading.current_thread()
                entered.set()
                assert release.wait(timeout=15), "test must release the interrupted model call"
            return super().generate_messages(messages, tools, **kwargs)

    provider = BlockingProvider([input_review(), AIMessage(content="不应正式交付的迟到回答。")])
    tools, calls = fixed_registry()
    monkeypatch.setenv("LIMITUPLAB_AGENT_RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("LIMITUPLAB_AGENT_COMPLIANCE", "deterministic")
    monkeypatch.setattr(adapter, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(adapter, "AgentToolRegistry", lambda **_: tools)
    monkeypatch.setattr(agents, "get_limit_up_repository", lambda: SimpleNamespace(list_events=lambda: []))
    host = DurableRuntimeHost(postgresql_database).start()
    request = AgentChatRequest(session_id="shutdown-fixture", message_id="one", message="解释固定研究术语")
    http = Request({"type": "http", "client": ("192.0.2.1", 5000), "headers": []})
    try:
        lifecycle, row = react_chat.start(request, http, "shutdown-owner")
        assert entered.wait(timeout=5), "model node did not start through the sync bridge"
        host.close()
        deadline = time.monotonic() + 5
        while lifecycle.executing(row["run_id"]) and time.monotonic() < deadline:
            time.sleep(.01)
        assert not lifecycle.executing(row["run_id"])
        interrupted = lifecycle.get(row["run_id"], "shutdown-owner")
        assert interrupted["run_status"] == "interrupted"
        assert interrupted["active"] == 1 and interrupted["response_json"] is None
        with closing(connect_readonly(postgresql_database)) as db:
            assert db.execute("SELECT count(*) FROM checkpoints").fetchone()[0] >= 3
            assert db.execute("SELECT count(*) FROM chat_messages WHERE role='assistant'").fetchone()[0] == 0
        assert calls == [] and get_runtime() is None
    finally:
        if host.thread.is_alive():
            host.close()
        release.set()
        if provider.worker_thread is not None:
            provider.worker_thread.join(timeout=5)
            assert not provider.worker_thread.is_alive()
