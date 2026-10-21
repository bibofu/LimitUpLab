"""Isolated HTTP and background processing checks, using durable PostgreSQL state."""

from database_support import isolated_database_url

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.agents.react_runtime.lifecycle import Journal
from app.models import AgentChatRequest, AgentChatResponse
from app.quality_schema import answer_hash
from app.repositories import ChatSessionRepository
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
    path = isolated_database_url('quality-http.sqlite')
    ChatSessionRepository(path).ensure_session("session", owner_id=OWNER)
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


def test_active_run_accepts_owned_interruption_and_defers_case_until_completion(env):
    client, repo, _, response = env
    journal = Journal(repo.database_url)
    run, _ = journal.create(AgentChatRequest(session_id="session", message_id="active", message="研究请求"), OWNER)
    run_id = run["run_id"]
    url = f"/api/agents/chat/runs/{run_id}/delivery"
    for event in ("received", "displayed"):
        assert client.post(url, json={"event": event}).status_code == 422
    assert client.post(url, json={"event": "interrupted", "answer_hash": "0" * 64}).status_code == 409
    client.cookies.set(SESSION_COOKIE_NAME, sign_visitor_owner_id(OTHER))
    assert client.post(url, json={"event": "interrupted"}).status_code == 404
    client.cookies.set(SESSION_COOKIE_NAME, sign_visitor_owner_id(OWNER))
    for _ in range(2):
        saved = client.post(url, json={"event": "interrupted", "answer_hash": None})
        assert saved.status_code == 200
        assert saved.json()["delivery_status"] == "interrupted"
    state = client.get("/api/agents/chat/sessions/session/quality").json()["runs"][run_id]
    assert state["answer_hash"] is None and state["delivery_status"] == "interrupted"
    assert repo.list_cases()["total"] == 0
    final = journal.finish(run_id, OWNER, response)
    quality_detection.process_pending(repo)
    case = repo.list_cases()["items"][0]
    assert case["status"] == "pending" and case["severity"] == "low"
    detail = repo.case_detail(case["case_id"])
    assert len(detail["signals"]) == 1 and detail["signals"][0]["evidence"]["status"] == "unknown"
    assert len(detail["run"]["delivery_events"]) == 1
    assert detail["run"]["delivery_events"][0]["answer_hash"] == ""
    displayed = client.post(url, json={"event": "displayed", "answer_hash": answer_hash(final)})
    assert displayed.json()["delivery_status"] == "displayed"
    assert repo.case_detail(case["case_id"])["run"]["delivery_status"] == "displayed"


def test_delivery_recovery_before_worker_avoids_new_candidate_and_bad_hash(env):
    client, repo, run_id, response = env
    url = f"/api/agents/chat/runs/{run_id}/delivery"
    assert client.post(url, json={"event": "displayed", "answer_hash": answer_hash(response)}).status_code == 200
    assert client.post(url, json={"event": "interrupted", "answer_hash": "0" * 64}).status_code == 409
    assert client.post(url, json={"event": "interrupted"}).json()["delivery_status"] == "displayed"
    quality_detection.process_pending(repo)
    assert repo.list_cases()["total"] == 0


def test_completed_interruption_immediate_and_background_signal_are_deduplicated(env):
    client, repo, run_id, _ = env
    assert client.post(f"/api/agents/chat/runs/{run_id}/delivery", json={"event": "interrupted"}).status_code == 200
    quality_detection.process_pending(repo)
    case = repo.list_cases()["items"][0]
    assert len(repo.case_detail(case["case_id"])["signals"]) == 1


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
        db.execute("DELETE FROM quality_evidence_bundles")
        db.execute("UPDATE react_runs SET response_json=jsonb_set(response_json::jsonb,'{task_status}', '\"error\"')::text WHERE run_id=?", (run_id,))
    result = quality_detection.process_pending(repo)
    assert result["backfilled"] == 1 and result["succeeded"] == 1
    assert quality_detection.process_pending(repo)["processed"] == 0
    assert repo.list_cases()["total"] == 1
    assert repo.job_health()["counts"]["done"] == 1
    assert client.post("/api/admin/quality/process?limit=51", headers=ADMIN).status_code == 422
    assert client.post("/api/admin/quality/process?limit=2", headers=ADMIN).status_code == 200


def test_worker_records_sanitized_failure_for_retry(env, monkeypatch):
    _, repo, run_id, _ = env
    def fail(*_, **__):
        raise RuntimeError("private prompt must not enter error log")
    from app.services import quality_evaluation
    monkeypatch.setattr(quality_evaluation, "evaluate_bundle", fail)
    result = quality_detection.process_pending(repo)
    assert result["failed"] == 1
    with repo.connection() as db:
        row = db.execute("SELECT status,last_error,attempts FROM quality_jobs").fetchone()
    assert dict(row) == {"status": "pending", "last_error": "RuntimeError", "attempts": 1}
    assert repo.list_cases()["total"] == 0
    item = repo.evaluation_history(run_id)["items"][0]
    assert item["execution_status"] == "error" and item["verdict"] == "unknown"
    assert item["report"]["metadata"]["error_type"] == "RuntimeError"


def test_worker_timeout_retains_frozen_evidence_for_retry_without_failing_answer(env, monkeypatch):
    _, repo, run_id, _ = env
    from app.services import quality_evaluation

    def timed_out(bundle, *, deadline):
        assert deadline > 0 and bundle["manifest"]["schema_version"] == "quality-evidence-v1"
        raise TimeoutError("stop pure computation")

    monkeypatch.setattr(quality_evaluation, "evaluate_bundle", timed_out)
    assert quality_detection.process_pending(repo)["failed"] == 1
    report = repo.evaluation_history(run_id)["items"][0]
    assert report["execution_status"] == "error" and report["verdict"] == "unknown"
    assert report["report"]["metadata"]["error_type"] == "TimeoutError"
    assert repo.get_bundle(report["bundle_id"])["response"]["task_status"] == "complete"
    assert repo.job_health()["counts"]["pending"] == 1
    assert repo.list_cases()["total"] == 0


def test_evaluation_reports_include_unknown_coverage_without_creating_a_case(env):
    client, repo, run_id, _ = env
    history_url = f"/api/admin/quality/runs/{run_id}/evaluations"
    assert client.get(history_url).status_code == 401
    assert client.get("/api/admin/quality/evaluations").status_code == 401
    result = quality_detection.process_pending(repo)
    assert result["succeeded"] == 1
    reports = client.get(history_url, headers=ADMIN).json()
    assert reports["total"] == 1
    item = reports["items"][0]
    assert item["execution_status"] == "succeeded"
    assert item["verdict"] == "unknown"  # The fixture has no final checkpoint.
    assert item["report"]["checks"]
    assert item["report"]["metadata"]["model_calls"] == 0
    assert repo.list_cases()["total"] == 0
    bundle_url = f"/api/admin/quality/bundles/{item['bundle_id']}"
    assert client.get(bundle_url).status_code == 401
    frozen = client.get(bundle_url, headers=ADMIN)
    assert frozen.status_code == 200
    assert client.get("/api/admin/quality/evaluations?verdict=unknown", headers=ADMIN).json()["total"] == 1
    assert client.get("/api/admin/quality/evaluations?verdict=invalid", headers=ADMIN).status_code == 422
    same = client.post(history_url, headers=ADMIN, json={})
    assert same.status_code == 200 and same.json()["created"] is False
    resampled = client.post(history_url, headers=ADMIN, json={"normal_sample_rate": 1})
    assert resampled.status_code == 200 and resampled.json()["created"] is True
    assert resampled.json()["bundle_id"] == item["bundle_id"]
    assert client.post(history_url, headers=ADMIN, json={"job_kind": "judge"}).status_code == 422
    assert quality_detection.process_pending(repo)["succeeded"] == 1
    assert repo.list_cases(source="normal_sample")["total"] == 1
    assert client.get(bundle_url, headers=ADMIN).json() == frozen.json()
    ChatSessionRepository(repo.database_url).delete_session("session", owner_id=OWNER)
    assert client.get(history_url, headers=ADMIN).status_code == 404
    assert client.get(bundle_url, headers=ADMIN).status_code == 404
