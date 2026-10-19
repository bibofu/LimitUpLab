"""Public reads, chat persistence and quality review work with SQLite disabled."""

from contextlib import closing
from datetime import datetime, timezone
import sqlite3

from fastapi.testclient import TestClient

from app.agents.react_runtime.lifecycle import Journal
from app.database import connect_readonly
from app.main import app
from app.models import AgentChatRequest, AgentChatResponse, StockDailyBar
from app.repositories import SQLiteChatSessionRepository, SQLiteFirstBoardRepository, SQLiteLimitUpRepository
from app.security import SESSION_COOKIE_NAME, sign_visitor_owner_id
from app.services.sample_data import SAMPLE_EVENTS
from scripts.verify_local_database import verify_write_paths


def test_application_http_with_no_sqlite_access(postgresql_database, tmp_path, monkeypatch):
    absent = tmp_path / "absent" / "limituplab.sqlite"
    monkeypatch.setenv("LIMITUPLAB_DATABASE_PATH", str(absent))
    monkeypatch.setenv("LIMITUPLAB_ADMIN_KEY", "isolated-pg-independence-test")

    def forbid_sqlite(*args, **kwargs):
        raise AssertionError("PostgreSQL functionality attempted to open SQLite")

    monkeypatch.setattr(sqlite3, "connect", forbid_sqlite)
    SQLiteLimitUpRepository().upsert_events(SAMPLE_EVENTS)
    event = max(SAMPLE_EVENTS, key=lambda item: item.trade_date)
    SQLiteFirstBoardRepository().upsert_daily_bars([StockDailyBar(
        symbol=event.symbol, trade_date=event.trade_date, open=10, high=11, low=9, close=10.5,
        volume=100, amount=1000, source="synthetic-test", created_at=datetime.now(timezone.utc),
    )])
    assert verify_write_paths()["status"] == "passed"
    owner = "visitor_" + "c" * 32
    chats = SQLiteChatSessionRepository()
    chats.create_session(session_id="pg-only-session", owner_id=owner)
    journal = Journal()
    run, _ = journal.create(AgentChatRequest(
        session_id="pg-only-session", message_id="question", message="解释已有研究数据",
    ), owner)
    final = journal.finish(run["run_id"], owner, AgentChatResponse(
        session_id="pg-only-session", intent="react_research", answer="隔离测试的研究记录。",
        task_status="complete", stop_reason="answered", generated_by="synthetic-test",
    ).model_dump(mode="json"))
    admin = {"X-LimitUpLab-Admin-Key": "isolated-pg-independence-test"}
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, sign_visitor_owner_id(owner))
        assert client.get("/ready").json() == {"status": "ok", "database": "postgresql"}
        for path in ("/api/limit-up/events", "/api/limit-up/first-board",
                     "/api/limit-up/continued-board", "/api/limit-up/failed",
                     "/api/agents/review-snapshots", "/api/strategies/consolidation"):
            assert client.get(path).status_code == 200, path
        kline = client.get(f"/api/stocks/{event.symbol}/kline?days=1")
        assert kline.status_code == 200 and kline.json()[0]["close"] == 10.5
        created = client.post("/api/agents/chat/sessions", json={"title": "PG-only CRUD"})
        assert created.status_code == 200
        session_path = "/api/agents/chat/sessions/" + created.json()["session_id"]
        assert client.patch(session_path, json={"title": "Updated"}).status_code == 200
        assert client.get(session_path).json()["title"] == "Updated"
        assert client.delete(session_path).status_code == 200
        assert client.get(session_path).status_code == 404
        run_path = "/api/agents/chat/runs/" + run["run_id"]
        assert client.get(run_path).json()["response"] == final
        assert "event: completed" in client.get(run_path + "/stream").text
        assert client.put(run_path + "/feedback", json={"vote": "down", "reasons": ["data_error"]}).status_code == 200
        cases = client.get("/api/admin/quality/cases", headers=admin)
        assert cases.status_code == 200 and cases.json()["total"] == 1
        case_id = cases.json()["items"][0]["case_id"]
        assert client.get("/api/admin/quality/cases/" + case_id, headers=admin).status_code == 200
        assert client.get("/api/admin/quality/evaluations", headers=admin).status_code == 200
        assert client.get("/api/admin/quality/jobs", headers=admin).status_code == 200
    with closing(connect_readonly()) as database:
        assert database.execute("SELECT COUNT(*) FROM limit_up_events").fetchone()[0] == len(SAMPLE_EVENTS)
    assert not absent.parent.exists()
