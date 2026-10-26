"""Full application lifespan: authenticated HTTP -> sync bridge -> durable graph.

Only model and market adapters are scripted. There are no external requests.
"""

import asyncio
from contextlib import closing
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.agents.runner import adapter
from app.agents.runner.durable_runtime import get_runtime
from app.database import connect_readonly
from app.main import app
from app.routers import agents
from app.security import SESSION_COOKIE_NAME, sign_visitor_owner_id
from test_durable_agent import fixed_registry
from test_durable_long_term_memory import review_memory
from test_runner_production import input_review
from test_runner_shadow import ScriptedProvider


BASE = "/api/agents/chat"


def model_context(call):
    return json.loads(call[1].content.split("\n", 1)[1])


def new_session(client):
    response = client.post(BASE + "/sessions", json={"title": "Synthetic LTM HTTP acceptance"})
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def question(client, session_id, message_id, message):
    response = client.post(BASE, json={"session_id": session_id, "message_id": message_id, "message": message})
    assert response.status_code == 200, response.text
    assert response.json()["task_status"] == "complete", response.text
    return response.json()


def test_http_memory_survives_restart_cross_session_and_revokes_checkpoint_context(postgresql_database, monkeypatch):
    save_query = "请长期记住：以后回答保持简洁"
    provider = ScriptedProvider([
        review_memory(save_query),
        input_review(), AIMessage(content="简洁说明：首板是首次涨停。"),
        input_review(), AIMessage(content="连板表示连续交易日涨停；这是研究术语。"),
        input_review(), AIMessage(content="涨停是价格达到当日涨幅限制。"),
    ])
    tools, market_calls = fixed_registry()
    monkeypatch.setenv("LIMITUPLAB_LANGFUSE_ENABLED", "false")
    monkeypatch.setenv("LIMITUPLAB_AGENT_RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("LIMITUPLAB_AGENT_COMPLIANCE", "deterministic")
    monkeypatch.setattr(adapter, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(adapter, "AgentToolRegistry", lambda **_: tools)
    monkeypatch.setattr(agents, "get_limit_up_repository", lambda: SimpleNamespace(list_events=lambda: []))

    with TestClient(app) as client:
        source_session = new_session(client)
        saved = question(client, source_session, "save-memory", save_query)
        assert "已保存长期记忆" in saved["answer"] and len(provider.calls) == 1
        snapshot = client.get(BASE + "/memories").json()
        assert [(item["key"], item["value"]) for item in snapshot["items"]] == [("response_style", "concise")]
        memory = snapshot["items"][0]
        cookies = dict(client.cookies)
    assert get_runtime() is None

    with TestClient(app) as client:
        client.cookies.update(cookies)
        fresh_session = new_session(client)
        assert fresh_session != source_session
        recalled = question(client, fresh_session, "recall", "解释首板")
        assert model_context(provider.calls[2])["long_term_memories"][0]["value"] == "concise"
        assert recalled["memory_context_used"]

        updated = client.put(BASE + "/memories/preference/response_style", json={
            "value": "detailed", "expected_revision": memory["revision"], "source_user_message": "以后详细解释",
        }, headers={"Idempotency-Key": "http-update"})
        assert updated.status_code == 200, updated.text
        memory = updated.json()["current"]
        question(client, fresh_session, "updated", "解释连板")
        assert model_context(provider.calls[4])["long_term_memories"][0]["value"] == "detailed"
        assert all(message.content != "简洁说明：首板是首次涨停。" for message in provider.calls[4])

        deleted = client.delete(BASE + "/memories/" + memory["memory_id"],
            params={"expected_revision": memory["revision"]}, headers={"Idempotency-Key": "http-delete"})
        assert deleted.status_code == 200 and deleted.json()["current"]["value"] is None
        after_delete = question(client, fresh_session, "revoked", "解释涨停")
        assert model_context(provider.calls[6])["long_term_memories"] == []
        assert all(message.content != "连板表示连续交易日涨停；这是研究术语。" for message in provider.calls[6])
        assert not after_delete["memory_context_used"]
        assert client.get(BASE + "/memories").json()["items"] == []

        # Retrying a delivered request may return its immutable receipt, but
        # must never execute its saved write again after the explicit deletion.
        assert question(client, source_session, "save-memory", save_query) == saved
        assert client.get(BASE + "/memories").json()["items"] == []
        assert len(provider.calls) == 7 and market_calls == []
        runtime = get_runtime()
        state = asyncio.run_coroutine_threadsafe(runtime.graph.aget_state({
            "configurable": {"thread_id": fresh_session}}), runtime.loop).result(timeout=5)
        assert state.next == () and state.values["run_id"] == after_delete["run_id"]
        assert "long_term_memories" not in state.values["conversation"]
        with closing(connect_readonly(postgresql_database)) as db:
            assert db.execute("SELECT count(*) FROM checkpoints WHERE thread_id=?", (fresh_session,)).fetchone()[0] >= 9
            assert db.execute("SELECT count(*) FROM memory_operation_receipts").fetchone()[0] == 3

        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE_NAME, sign_visitor_owner_id("visitor_" + "f" * 32))
        assert client.get(BASE + "/memories/" + memory["memory_id"], params={"include_inactive": True}).status_code == 404
        assert client.get(BASE + "/memories").json()["items"] == []
