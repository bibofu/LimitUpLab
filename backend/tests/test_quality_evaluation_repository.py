"""Frozen evaluation persistence and queue migrations use real isolated SQLite."""

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import time

import pytest

from app.agents.react_runtime.lifecycle import Journal
from app.database import connect
from app.models import AgentChatRequest
from app.quality_models import QualityConflict
from app.quality_schema import DETECTOR_VERSION, answer_hash, encode, initialize_quality_schema
from app.repositories.chat_session_repository import SQLiteChatSessionRepository
from app.repositories.quality_repository import QualityRepository


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_QUALITY_NORMAL_SAMPLE_RATE", "0.05")
    path = tmp_path / "evaluation.sqlite"
    chats = SQLiteChatSessionRepository(path)
    chats.ensure_session("s", owner_id="owner")
    journal = Journal(path)
    row, _ = journal.create(AgentChatRequest(session_id="s", message_id="m", message="说明数据"), "owner")
    journal.checkpoint(row["run_id"], "owner", {"runtime": {"answer": "固定答案"}, "state": {"done": True}})
    journal.finish(row["run_id"], "owner", {"answer": "固定答案", "intent": "research", "task_status": "complete"})
    return QualityRepository(path), row["run_id"], chats


def report(job, verdict="pass"):
    return {"evaluator_kind": job["job_kind"], "evaluator_version": job["detector_version"],
        "execution_status": "succeeded", "verdict": verdict, "checks": [{
            "check_id": "saved_answer", "dimension": "delivery", "verdict": verdict,
            "summary": "检查持久化一致性", "unknown_reason": "缺少旧材料" if verdict == "unknown" else None}],
        "metadata": {"model_calls": 0}}


def test_bundle_freezes_at_completion_and_retry_uses_same_material(store):
    repo, run_id, _ = store
    first = repo.claim_job("first")
    assert first["bundle"]["manifest"]["capture_mode"] == "completion"
    assert first["config"]["normal_sample_rate"] == 0.05
    with repo.connection() as db:
        db.execute("UPDATE react_runs SET checkpoint_json='{}' WHERE run_id=?", (run_id,))
        db.execute("DELETE FROM quality_trace_snapshots WHERE run_id=?", (run_id,))
    repo.fail_job(first, "TemporaryError")
    with repo.connection() as db:
        db.execute("UPDATE quality_jobs SET available_at=0")
    second = repo.claim_job("second")
    assert second["bundle"] == first["bundle"]
    assert second["bundle_id"] == first["bundle_id"]
    repo.complete_job(second, [], report(second))
    history = repo.evaluation_history(run_id)
    assert history["total"] == 2
    assert {(item["execution_status"], item["verdict"]) for item in history["items"]} == {("error", "unknown"), ("succeeded", "pass")}
    assert repo.list_evaluations(verdict="pass")["total"] == 1
    assert repo.list_cases()["total"] == 0
    with repo.connection() as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE quality_evidence_bundles SET payload_json='{}'")


def test_kind_config_bundle_identity_and_explicit_version_selection(store):
    repo, run_id, _ = store
    first = repo.claim_job("deterministic")
    repo.complete_job(first, [], report(first))
    same = repo.enqueue_evaluation(run_id)
    assert same == {"created": False, "job_id": first["id"], "bundle_id": first["bundle_id"]}
    changed = repo.enqueue_evaluation(run_id, config={"normal_sample_rate": 0})
    assert changed["created"] and changed["job_id"] != first["id"]
    judge = repo.enqueue_evaluation(run_id, job_kind="llm_judge", evaluator_version="judge-contract-only")
    selected = repo.claim_job("deterministic")
    assert selected["id"] == changed["job_id"]
    repo.complete_job(selected, [], report(selected))
    assert repo.claim_job("deterministic") is None
    assert repo.claim_job("future-protocol", job_kind="llm_judge", evaluator_version="judge-contract-only")["id"] == judge["job_id"]
    assert repo.backfill_jobs() == 0


def test_skipped_report_cannot_create_a_case_even_when_called_without_worker(store):
    repo, run_id, _ = store
    job = repo.claim_job("test")
    skipped = {**report(job, "unknown"), "execution_status": "skipped"}
    signal = {"source": "deterministic", "code": "unfinished_check", "severity": "medium",
              "summary": "未完成检查", "evidence": {}, "detector_version": DETECTOR_VERSION}
    with pytest.raises(ValueError, match="cannot publish signals"):
        repo.complete_job(job, [signal], skipped)
    assert repo.list_cases()["total"] == 0
    assert repo.evaluation_history(run_id)["items"][0]["execution_status"] == "running"
    repo.complete_job(job, [], skipped)
    assert repo.evaluation_history(run_id)["items"][0]["execution_status"] == "skipped"


def test_renewal_stale_tokens_and_expired_attempt_audit(store):
    repo, run_id, _ = store
    first = repo.claim_job("first", lease_seconds=1)
    assert repo.renew_job(first, lease_seconds=60)["lease_until"] > first["lease_until"]
    assert repo.claim_job("second") is None
    with repo.connection() as db:
        db.execute("UPDATE quality_jobs SET lease_until=0")
    second = repo.claim_job("second")
    for operation in (lambda: repo.renew_job(first), lambda: repo.complete_job(first, [], report(first)),
                      lambda: repo.fail_job(first, "OldWorker")):
        with pytest.raises(QualityConflict):
            operation()
    repo.complete_job(second, [], report(second))
    expired = next(item for item in repo.evaluation_history(run_id)["items"] if item["attempt"] == 1)
    assert expired["execution_status"] == "error" and expired["error_type"] == "LeaseExpired"


def test_new_findings_preserve_human_decision_and_attempt_atomicity(store):
    repo, run_id, _ = store
    repo.save_feedback(run_id, "owner", {"vote": "down"})
    case_id = repo.list_cases()["items"][0]["case_id"]
    repo.review_case(case_id, {"actor_id": "admin", "status": "not_a_bug", "expected_revision": 0, "note": "人工确认"})
    first = repo.claim_job("first")
    signal = {"source": "deterministic", "code": "new_check", "severity": "critical", "summary": "新检查线索",
              "evidence": {}, "detector_version": DETECTOR_VERSION}
    with repo.connection() as db:
        db.execute("CREATE TRIGGER reject_attempt BEFORE UPDATE ON quality_evaluations BEGIN SELECT RAISE(ABORT,'fixture'); END")
    with pytest.raises(sqlite3.IntegrityError):
        repo.complete_job(first, [signal], report(first, "fail"))
    assert len(repo.case_detail(case_id)["signals"]) == 1
    with repo.connection() as db:
        db.execute("DROP TRIGGER reject_attempt")
    repo.complete_job(first, [signal], report(first, "fail"))
    detail = repo.case_detail(case_id)
    assert detail["case"]["status"] == "not_a_bug" and detail["case"]["severity"] == "medium"
    assert detail["case"]["revision"] == 1 and len(detail["reviews"]) == 1
    assert detail["has_new_signals_since_review"]
    repo.enqueue_evaluation(run_id, config={"normal_sample_rate": 0})
    second = repo.claim_job("second")
    repo.complete_job(second, [signal], report(second, "fail"))
    assert len(repo.case_detail(case_id)["signals"]) == 3


def test_atomic_quota_idempotency_and_session_delete_does_not_refund(store):
    repo, run_id, chats = store
    deterministic = repo.claim_job("deterministic")
    with pytest.raises(ValueError):
        repo.reserve_budget(deterministic, "invalid", units=1, limit=1, period="2026-10-17")
    repo.complete_job(deterministic, [], report(deterministic))
    for number in range(2):
        repo.enqueue_evaluation(run_id, job_kind="llm_judge", evaluator_version="contract-only", config={"n": number})
    jobs = [repo.claim_job(str(n), job_kind="llm_judge", evaluator_version="contract-only") for n in range(2)]
    def reserve(job):
        try:
            return repo.reserve_budget(job, "call-1", units=1, limit=1, period="2026-10-17")
        except QualityConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, jobs))
    assert sum(value is not None for value in outcomes) == 1
    winner = jobs[next(i for i, value in enumerate(outcomes) if value)]
    assert reserve(winner)["units"] == 1
    assert chats.delete_session("s", owner_id="owner")
    with repo.connection() as db:
        for table in ("quality_evidence_bundles", "quality_evaluations", "quality_jobs", "quality_budget_reservations"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert db.execute("SELECT reserved_units FROM quality_budget_usage").fetchone()[0] == 1


def legacy_database(path):
    SQLiteChatSessionRepository(path).ensure_session("s", owner_id="owner")
    with connect(path) as db:
        db.execute("""CREATE TABLE react_runs(run_id TEXT PRIMARY KEY,owner_id TEXT,session_id TEXT,message_key TEXT,
            request_json TEXT,request_hash TEXT,active INTEGER,cancelled INTEGER,started_at TEXT,checkpoint_json TEXT,response_json TEXT)""")
        response = {"answer": "旧答案", "task_status": "complete"}
        db.execute("INSERT INTO react_runs VALUES('legacy','owner','s','m','{}','hash',0,0,'2026-10-17T00:00:00+00:00',NULL,?)", (encode(response),))
        db.execute("""CREATE TABLE quality_jobs(id TEXT PRIMARY KEY,run_id TEXT,answer_hash TEXT,detector_version TEXT,
            status TEXT,attempts INTEGER,max_attempts INTEGER,available_at REAL,lease_token TEXT,lease_until REAL,
            worker_id TEXT,last_error TEXT,created_at TEXT,updated_at TEXT,UNIQUE(run_id,answer_hash,detector_version))""")
        db.execute("INSERT INTO quality_jobs VALUES('old','legacy',?,'deterministic-v1','running',1,3,0,'stale',?,'old-worker',NULL,'old','old')",
                   (answer_hash(response), time.time() + 1000))


def test_v1_migration_is_idempotent_preserves_jobs_and_marks_historical_capture(tmp_path):
    path = tmp_path / "legacy.sqlite"
    legacy_database(path)
    repo = QualityRepository(path)
    with repo.connection() as db:
        old = dict(db.execute("SELECT * FROM quality_jobs WHERE id='old'").fetchone())
        assert old["status"] == "superseded" and old["attempts"] == 1 and old["lease_token"] is None
        old["lease_token"] = "stale"
    with pytest.raises(QualityConflict):
        repo.fail_job(old, "StaleWorker")
    QualityRepository(path)
    assert repo.evaluation_history("legacy")["total"] == 1
    legacy = repo.evaluation_history("legacy")["items"][0]
    assert legacy["execution_status"] == "skipped" and legacy["verdict"] == "unknown"
    assert legacy["report"]["metadata"]["legacy_job_status"] == "running"
    assert repo.backfill_jobs() == 1
    job = repo.claim_job("new-worker")
    assert job["detector_version"] == DETECTOR_VERSION
    assert job["bundle"]["manifest"]["capture_mode"] == "historical"
    assert repo.backfill_jobs() == 0


def test_migration_rollback_preserves_old_outbox(tmp_path, monkeypatch):
    from app.database import ResilientSQLiteConnection
    path = tmp_path / "rollback.sqlite"
    legacy_database(path)
    original = ResilientSQLiteConnection.execute
    def failing(self, sql, parameters=()):
        if "CREATE TABLE IF NOT EXISTS quality_evaluations" in sql:
            raise sqlite3.OperationalError("migration interrupted")
        return original(self, sql, parameters)
    with monkeypatch.context() as patch:
        patch.setattr(ResilientSQLiteConnection, "execute", failing)
        with pytest.raises(sqlite3.OperationalError):
            with connect(path) as db:
                initialize_quality_schema(db)
    with connect(path) as db:
        assert "job_kind" not in {r[1] for r in db.execute("PRAGMA table_info(quality_jobs)")}
        assert db.execute("SELECT status,lease_token FROM quality_jobs").fetchone()[:] == ("running", "stale")


def test_concurrent_initialization_and_backfill_share_one_migration(tmp_path):
    path = tmp_path / "concurrent.sqlite"
    legacy_database(path)
    with ThreadPoolExecutor(max_workers=3) as pool:
        repositories = list(pool.map(lambda _: QualityRepository(path), range(3)))
        backfilled = list(pool.map(lambda repo: repo.backfill_jobs(), repositories))
    assert sum(backfilled) == 1
    assert repositories[0].evaluation_history("legacy")["total"] == 1
    with repositories[0].connection() as db:
        assert db.execute("SELECT count(*) FROM quality_jobs").fetchone()[0] == 2


def test_capture_failure_preserves_answer_and_freezes_explicit_unknown(store, monkeypatch):
    from app.services import quality_evidence
    repo, _, _ = store
    journal = Journal(repo.path)
    row, _ = journal.create(AgentChatRequest(session_id="s", message_id="second", message="正常请求"), "owner")
    def broken(*args, **kwargs):
        raise RuntimeError("private diagnostic exception body")
    monkeypatch.setattr(quality_evidence, "capture_evidence_bundle", broken)
    response = journal.finish(row["run_id"], "owner", {"answer": "正常答案", "intent": "research", "task_status": "complete"})
    assert response["answer"] == "正常答案"
    with repo.connection() as db:
        bundle_id = db.execute("SELECT bundle_id FROM quality_jobs WHERE run_id=?", (row["run_id"],)).fetchone()[0]
        assert db.execute("SELECT content FROM chat_messages WHERE run_id=?", (row["run_id"],)).fetchone()[0] == "正常答案"
    bundle = repo.get_bundle(bundle_id)
    assert bundle["response"] is None
    assert bundle["manifest"]["capture_error_type"] == "RuntimeError"
    assert bundle["manifest"]["missing"] == [{"path": "", "reason": "evidence_capture_failed"}]
    assert "private diagnostic" not in json.dumps(bundle)
