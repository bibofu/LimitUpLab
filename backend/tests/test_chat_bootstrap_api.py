"""Single-request chat restore preserves ownership and remains read-only."""

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import ChatSessionMessage
from app.repositories import SQLiteChatSessionRepository
from app.routers.agents import router
from app.security import AnonymousVisitorMiddleware, SESSION_COOKIE_NAME, sign_visitor_owner_id


OWNER = "visitor_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OTHER = "visitor_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


@pytest.fixture
def chat_api(tmp_path, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_DATABASE_PATH", str(tmp_path / "bootstrap.sqlite"))
    monkeypatch.setenv("LIMITUPLAB_SESSION_SECRET", "bootstrap-test-secret-at-least-32-characters")
    app = FastAPI()
    app.add_middleware(AnonymousVisitorMiddleware)
    app.include_router(router, prefix="/api/agents")
    with TestClient(app) as client:
        yield client, SQLiteChatSessionRepository()


def sign_in(client, owner):
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE_NAME, sign_visitor_owner_id(owner))


def test_bootstrap_restores_saved_owned_session_with_messages_in_one_read(chat_api):
    client, repository = chat_api
    saved = repository.create_session(owner_id=OWNER, title="早期会话")
    repository.append_message(ChatSessionMessage(
        message_id="history", session_id=saved.session_id, role="assistant",
        content="已有研究回答", created_at=datetime.now(timezone.utc),
    ), owner_id=OWNER)
    latest = repository.create_session(owner_id=OWNER, title="最新会话")
    sign_in(client, OWNER)

    response = client.get("/api/agents/chat/bootstrap", params={"session_id": saved.session_id, "limit": 1})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    result = response.json()
    assert [row["session_id"] for row in result["sessions"]] == [latest.session_id]
    assert result["selected_session"]["session_id"] == saved.session_id
    assert result["selected_session"]["messages"][0]["content"] == "已有研究回答"
    assert len(repository.list_sessions(owner_id=OWNER)) == 2


@pytest.mark.parametrize("saved_id_kind", ["foreign", "deleted", "missing"])
def test_unavailable_saved_session_falls_back_without_exposing_another_owner(chat_api, saved_id_kind):
    client, repository = chat_api
    own = repository.create_session(owner_id=OWNER, title="当前用户")
    foreign = repository.create_session(owner_id=OTHER, title="不得泄露的标题")
    saved_id = "chat_missing"
    if saved_id_kind == "foreign":
        saved_id = foreign.session_id
    elif saved_id_kind == "deleted":
        old = repository.create_session(owner_id=OWNER, title="已删除会话")
        saved_id = old.session_id
        repository.delete_session(saved_id, owner_id=OWNER)
    sign_in(client, OWNER)

    response = client.get("/api/agents/chat/bootstrap", params={"session_id": saved_id})

    assert response.status_code == 200
    assert response.json()["selected_session"]["session_id"] == own.session_id
    assert [row["session_id"] for row in response.json()["sessions"]] == [own.session_id]
    assert foreign.session_id not in response.text
    assert "不得泄露" not in response.text


def test_bootstrap_is_fresh_after_rename_and_delete(chat_api):
    client, repository = chat_api
    own = repository.create_session(owner_id=OWNER, title="旧标题")
    sign_in(client, OWNER)
    assert client.get("/api/agents/chat/bootstrap").json()["selected_session"]["title"] == "旧标题"
    client.patch(f"/api/agents/chat/sessions/{own.session_id}", json={"title": "新标题"}).raise_for_status()
    renamed = client.get("/api/agents/chat/bootstrap", params={"session_id": own.session_id}).json()
    assert renamed["sessions"][0]["title"] == "新标题"
    assert renamed["selected_session"]["title"] == "新标题"
    client.delete(f"/api/agents/chat/sessions/{own.session_id}").raise_for_status()
    deleted = client.get("/api/agents/chat/bootstrap", params={"session_id": own.session_id}).json()
    assert deleted["sessions"] == []
    assert deleted["selected_session"] is None
    assert repository.list_sessions(owner_id=OWNER) == []


def test_missing_cookie_does_not_reuse_saved_identity_or_create_a_session(chat_api):
    client, repository = chat_api
    old = repository.create_session(owner_id=OTHER, title="旧身份私有会话")
    response = client.get("/api/agents/chat/bootstrap", params={"session_id": old.session_id})
    assert response.status_code == 200
    assert response.json()["sessions"] == []
    assert response.json()["selected_session"] is None
    assert "httponly" in response.headers["set-cookie"].lower()
    assert old.session_id not in response.text
    created = client.post("/api/agents/chat/sessions", json={}).json()
    assert created["owner_id"] != OTHER
    assert len(repository.list_sessions(owner_id=OTHER)) == 1
    assert len(repository.list_sessions(owner_id=created["owner_id"])) == 1
    restored = client.get("/api/agents/chat/bootstrap")
    assert "set-cookie" not in restored.headers
    assert restored.json()["selected_session"]["session_id"] == created["session_id"]


def test_bootstrap_validation_rejects_unbounded_queries(chat_api):
    client, _ = chat_api
    assert client.get("/api/agents/chat/bootstrap", params={"limit": 101}).status_code == 422
    assert client.get("/api/agents/chat/bootstrap", params={"session_id": "x" * 101}).status_code == 422
