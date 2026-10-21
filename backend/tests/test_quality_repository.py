"""Quality persistence exercises real PostgreSQL ownership, transactions and leases."""

from database_support import isolated_database_url

import psycopg
from database_support import reject_writes

import pytest

from app.agents.react_runtime.lifecycle import Journal
from app.models import AgentChatRequest
from app.quality_models import QualityConflict
from app.quality_schema import DETECTOR_VERSION, answer_hash, enqueue_completed
from app.repositories.chat_session_repository import ChatSessionRepository
from app.repositories.quality_repository import QualityRepository


@pytest.fixture
def store(tmp_path):
    path = isolated_database_url('quality.sqlite')
    chats = ChatSessionRepository(path)
    chats.ensure_session("session", owner_id="owner")
    journal = Journal(path)
    repo = QualityRepository(path)
    return repo, journal, chats


def finish(store, message_id="message", answer="研究数据缺失。"):
    repo, journal, _ = store
    row, _ = journal.create(AgentChatRequest(session_id="session", message_id=message_id, message="解释现有证据"), "owner")
    response = journal.finish(row["run_id"], "owner", {"answer": answer, "intent": "research", "task_status": "partial", "generated_by": "test"})
    return row["run_id"], response


def downvote(repo, run_id):
    return repo.save_feedback(run_id, "owner", {"vote": "down", "reasons": ["incomplete"], "comment": "说明遗漏了日期"})


def test_feedback_owner_idempotency_withdrawal_and_delivery(store):
    repo, _, _ = store
    run_id, response = finish(store)
    with pytest.raises(KeyError):
        repo.save_feedback(run_id, "someone-else", {"vote": "down"})
    with pytest.raises(KeyError):
        repo.session_state("session", "someone-else")
    for _ in range(2):
        assert downvote(repo, run_id)["vote"] == "down"
    assert repo.list_cases()["total"] == 1
    detail = repo.case_detail(repo.list_cases()["items"][0]["case_id"])
    assert len(detail["signals"]) == 1 and detail["case"]["status"] == "pending"
    assert detail["run"]["execution_status"] == "succeeded"
    assert detail["run"]["quality_status"] == "unreviewed"
    assert detail["run"]["delivery_status"] == "unknown"
    repo.save_feedback(run_id, "owner", {"vote": None})
    assert repo.list_cases()["total"] == 1
    assert repo.session_state("session", "owner")["runs"][run_id]["feedback"]["vote"] is None
    with pytest.raises(QualityConflict):
        repo.record_delivery(run_id, "owner", "displayed", "0" * 64)
    with pytest.raises(KeyError):
        repo.record_delivery(run_id, "someone-else", "displayed", answer_hash(response))
    for event in ("interrupted", "received", "displayed", "interrupted"):
        repo.record_delivery(run_id, "owner", event, answer_hash(response))
    assert repo.session_state("session", "owner")["runs"][run_id]["delivery_status"] == "displayed"
    assert len(repo.case_detail(detail["case"]["case_id"])["signals"]) == 2


def test_atomic_answer_outbox_and_active_feedback_rejected(store):
    repo, journal, _ = store
    row, _ = journal.create(AgentChatRequest(session_id="session", message_id="m", message="研究"), "owner")
    with pytest.raises(QualityConflict):
        downvote(repo, row["run_id"])
    with repo.connection() as db:
        reject_writes(db, table="quality_jobs", trigger="fail_job", event="INSERT")
    with pytest.raises(psycopg.IntegrityError):
        journal.finish(row["run_id"], "owner", {"answer": "研究", "intent": "research", "task_status": "complete"})
    assert journal.get(row["run_id"], "owner")["active"] == 1
    with repo.connection() as db:
        assert db.execute("SELECT count(*) FROM chat_messages").fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_job ON quality_jobs")
    journal.finish(row["run_id"], "owner", {"answer": "研究", "intent": "research", "task_status": "complete"})
    assert repo.job_health()["counts"]["pending"] == 1
    assert repo.backfill_jobs() == 0
    with repo.connection() as db:
        db.execute("DELETE FROM quality_jobs")
    assert repo.backfill_jobs() == 1


def test_review_state_revision_audit_and_reopen(store):
    repo, _, _ = store
    run_id, _ = finish(store)
    downvote(repo, run_id)
    case = repo.list_cases()["items"][0]
    update = {"actor_id": "admin", "status": "confirmed", "expected_revision": 0, "note": "已核对", "symptom": "未说明日期"}
    detail = repo.review_case(case["case_id"], update)
    assert detail["case"]["revision"] == 1
    assert detail["run"]["quality_status"] == "fail"
    with pytest.raises(QualityConflict):
        repo.review_case(case["case_id"], update)
    with pytest.raises(QualityConflict):
        repo.review_case(case["case_id"], {**update, "expected_revision": 1, "status": "closed"})
    with pytest.raises(ValueError):
        repo.review_case(case["case_id"], {**update, "expected_revision": 1, "status": "verifying"})
    for revision, status in enumerate(("in_progress", "verifying", "closed", "pending"), start=1):
        detail = repo.review_case(case["case_id"], {**update, "status": status, "expected_revision": revision,
            "root_cause": "说明构建遗漏", "fix_version": "commit123"})
    assert len(detail["reviews"]) == 5
    assert detail["reviews"][0]["changes"]["symptom"]["before"] == ""
    with repo.connection() as db, pytest.raises(psycopg.IntegrityError):
        db.execute("UPDATE quality_reviews SET note='rewritten'")


def test_lease_recovery_stale_workers_and_finite_retries(store):
    repo, _, _ = store
    run_id, _ = finish(store)
    first = repo.claim_job("worker-1")
    assert first["run_id"] == run_id and first["attempts"] == 1
    assert repo.claim_job("worker-2") is None
    with repo.connection() as db:
        db.execute("UPDATE quality_jobs SET lease_until=0")
    second = repo.claim_job("worker-2")
    assert second["attempts"] == 2 and second["lease_token"] != first["lease_token"]
    with pytest.raises(QualityConflict):
        repo.complete_job(first, [])
    with pytest.raises(QualityConflict):
        repo.fail_job(first, "stale")
    assert repo.fail_job(second, "temporary")["status"] == "pending"
    with repo.connection() as db:
        db.execute("UPDATE quality_jobs SET available_at=0")
    third = repo.claim_job("worker-3")
    assert repo.fail_job(third, "final")["status"] == "dead"
    assert repo.claim_job("worker-4") is None
    assert repo.job_health()["counts"]["dead"] == 1


def test_signal_dedup_new_detector_and_session_deletion_cascade(store):
    repo, journal, chats = store
    run_id, response = finish(store)
    signal = {"source": "deterministic", "code": "missing", "severity": "high", "summary": "缺失线索", "evidence": {}, "detector_version": DETECTOR_VERSION}
    downvote(repo, run_id)
    job = repo.claim_job("worker")
    repo.complete_job(job, [signal, signal])
    case_id = repo.list_cases(source="deterministic", severity="high")["items"][0]["case_id"]
    assert len(repo.case_detail(case_id)["signals"]) == 2
    with repo.connection() as db:
        row = db.execute("SELECT * FROM react_runs WHERE run_id=?", (run_id,)).fetchone()
        enqueue_completed(db, row, response, "deterministic-future")
        db.execute("INSERT INTO quality_trace_snapshots(run_id,stage,captured_at,payload_json) VALUES(?,?,?,?)", (run_id, "model_input", "2026-10-17", '{}'))
    assert repo.claim_job("new-version", evaluator_version="deterministic-future")["detector_version"] == "deterministic-future"
    assert chats.delete_session("session", owner_id="owner")
    with journal.connection() as db:
        for table in ("quality_feedback", "quality_delivery", "quality_cases", "quality_signals", "quality_reviews", "quality_jobs", "quality_trace_snapshots"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_admin_material_scrubs_configured_secrets_and_feedback_at_rest(store, monkeypatch):
    secret = "fixture-private-api-key"
    monkeypatch.setenv("QUALITY_TEST_API_KEY", secret)
    repo, _, _ = store
    run_id, _ = finish(store, answer="工具报错 " + secret)
    repo.save_feedback(run_id, "owner", {"vote": "down", "comment": secret})
    detail = repo.case_detail(repo.list_cases()["items"][0]["case_id"])
    assert secret not in str(detail)
    with repo.connection() as db:
        assert db.execute("SELECT comment FROM quality_feedback").fetchone()[0] == "[redacted]"
