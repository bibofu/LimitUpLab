"""Isolated HTTP and background processing checks, using durable SQLite state."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.agents.react_runtime.lifecycle import Journal
from app.models import AgentChatRequest, AgentChatResponse
from app.quality_schema import answer_hash
from app.repositories import SQLiteChatSessionRepository
from app.repositories.quality_repository import QualityRepository
from app.routers import quality
from app.security import AnonymousVisitorMiddleware, SESSION_COOKIE_NAME, sign_visitor_owner_id
from app.services import quality_detection


OWNER = "visitor_" + "1" * 32
OTHER = "visitor_" + "2" * 32
ADMIN = {"X-LimitUpLab-Admin-Key": "quality-test-key"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_ADMIN_KEY", ADMIN["X-LimitUpLab-Admin-Key"])
    monkeypatch.setenv("LIMITUPLAB_QUALITY_NORMAL_SAMPLE_RATE", "0")
    path = tmp_path / "quality-http.sqlite"
    SQLiteChatSessionRepository(path).ensure_session("session", owner_id=OWNER)
    journal = Journal(path)
    run, _ = journal.create(AgentChatRequest(session_id="session", message_id="message", message="研究问题"), OWNER)
    response = journal.finish(run["run_id"], OWNER, AgentChatResponse(
        session_id="session", intent="react_research", answer="研究结果。", task_status="complete",
        stop_reason="answered", generated_by="fixture-version").model_dump(mode="json"))
    repo = QualityRepository(path)
    app = FastAPI()
    app.add_middleware(AnonymousVisitorMiddleware)
    app.include_router(quality.router)
    app.dependency_overrides[quality.repository] = lambda: repo
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, sign_visitor_owner_id(OWNER))
        yield client, repo, run["run_id"], response


def test_feedback_edit_cancel_persistence_and_ownership(env):
    client, repo, run_id, _ = env
    url = f"/api/agents/chat/runs/{run_id}/feedback"
    for vote in ("down", "down", "up", None):
        response = client.put(url, json={"vote": vote, "reasons": ["data_error"], "comment": "请复核"})
        assert response.status_code == 200
        assert response.json()["feedback"]["vote"] == vote
    assert repo.list_cases()["total"] == 1  # Cancelling does not erase review history.
    state = client.get("/api/agents/chat/sessions/session/quality").json()["runs"][run_id]
    assert state["feedback"]["vote"] is None
    assert state["delivery_status"] == "unknown"
    assert client.put(url, json={"vote": "down", "owner_id": OTHER}).status_code == 422
    client.cookies.set(SESSION_COOKIE_NAME, sign_visitor_owner_id(OTHER))
    assert client.put(url, json={"vote": "down"}).status_code == 404
    assert client.get("/api/agents/chat/sessions/session/quality").status_code == 404
    assert client.post(f"/api/agents/chat/runs/{run_id}/delivery", json={
        "event": "displayed", "answer_hash": state["answer_hash"],
    }).status_code == 404


def test_delivery_hash_and_later_success_do_not_claim_user_reading(env):
    client, repo, run_id, response = env
    url = f"/api/agents/chat/runs/{run_id}/delivery"
    assert client.post(url, json={"event": "displayed", "answer_hash": "0" * 64}).status_code == 409
    for event in ("interrupted", "displayed", "received", "displayed"):
        saved = client.post(url, json={"event": event, "answer_hash": answer_hash(response)})
        assert saved.status_code == 200
    assert saved.json()["delivery_status"] == "displayed"
    detail = repo.case_detail(repo.list_cases()["items"][0]["case_id"])
    assert detail["run"]["delivery_status"] == "displayed"
    assert len(detail["run"]["delivery_events"]) == 3


def test_admin_review_requires_key_and_enforces_auditable_transitions(env):
    client, repo, run_id, _ = env
    client.put(f"/api/agents/chat/runs/{run_id}/feedback", json={"vote": "down"})
    for path in ("cases", "jobs"):
        assert client.get("/api/admin/quality/" + path).status_code == 401
    assert client.post("/api/admin/quality/process").status_code == 401
    listed = client.get("/api/admin/quality/cases?source=feedback&version=fixture-version", headers=ADMIN)
    case = listed.json()["items"][0]
    url = "/api/admin/quality/cases/" + case["case_id"]
    assert client.get(url).status_code == 401
    assert client.patch(url, json={"status": "confirmed", "expected_revision": 0, "note": "审核"}).status_code == 401
    assert client.patch(url, headers=ADMIN, json={
        "status": "confirmed", "expected_revision": 0, "note": "缺少症状",
    }).status_code == 422
    for revision, status in enumerate(("confirmed", "in_progress", "verifying", "closed")):
        updated = client.patch(url, headers=ADMIN, json={
            "status": status, "expected_revision": revision, "note": "已核对证据",
            "symptom": "回答遗漏", "root_cause": "上下文缺失", "fix_version": "fix-fixture",
        })
        assert updated.status_code == 200, updated.text
        assert updated.json()["case"]["status"] == status
    detail = client.get(url, headers=ADMIN).json()
    assert len(detail["reviews"]) == 4
    assert all(review["actor_id"] == "admin" for review in detail["reviews"])
    assert client.patch(url, headers=ADMIN, json={
        "status": "pending", "expected_revision": 0, "note": "stale",
    }).status_code == 409
    assert client.patch(url, headers=ADMIN, json={
        "status": "pending", "expected_revision": 4, "note": "spoof", "actor_id": "someone",
    }).status_code == 422


def test_worker_durable_backfill_recovery_and_no_duplicate_cases(env, monkeypatch):
    client, repo, run_id, _ = env
    with repo.connection() as db:
        db.execute("DELETE FROM quality_jobs")  # Simulate a pre-migration finalized run.
        db.execute("UPDATE react_runs SET response_json=json_set(response_json,'$.task_status','error') WHERE run_id=?", (run_id,))
    result = quality_detection.process_pending(repo)
    assert result["backfilled"] == 1 and result["succeeded"] == 1
    assert quality_detection.process_pending(repo)["processed"] == 0
    assert repo.list_cases()["total"] == 1
    assert repo.job_health()["counts"]["done"] == 1
    assert client.post("/api/admin/quality/process?limit=51", headers=ADMIN).status_code == 422
    assert client.post("/api/admin/quality/process?limit=2", headers=ADMIN).status_code == 200


def test_worker_records_sanitized_failure_for_retry(env, monkeypatch):
    _, repo, _, _ = env
    def fail(*_, **__):
        raise RuntimeError("private prompt must not enter error log")
    monkeypatch.setattr(quality_detection, "detect_run", fail)
    result = quality_detection.process_pending(repo)
    assert result["failed"] == 1
    with repo.connection() as db:
        row = db.execute("SELECT status,last_error,attempts FROM quality_jobs").fetchone()
    assert dict(row) == {"status": "pending", "last_error": "RuntimeError", "attempts": 1}
    assert repo.list_cases()["total"] == 0
