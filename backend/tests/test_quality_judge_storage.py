"""Judge reservations share real SQLite transactions across workers and retries."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import sqlite3
import time

import pytest

from app.agents.react_runtime.lifecycle import Journal
from app.models import AgentChatRequest
from app.quality_models import QualityConflict
from app.repositories.chat_session_repository import SQLiteChatSessionRepository
from app.repositories.quality_judge_repository import JudgeBudgetExceeded
from app.repositories.quality_repository import QualityRepository


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "judge.sqlite"
    chats = SQLiteChatSessionRepository(path)
    chats.ensure_session("session", owner_id="owner")
    journal = Journal(path)
    run, _ = journal.create(AgentChatRequest(session_id="session", message_id="m", message="解释数据"), "owner")
    journal.finish(run["run_id"], "owner", {"answer": "证据中的答案", "task_status": "complete", "intent": "research"})
    return QualityRepository(path), run["run_id"], chats


def queue(repo, run_id, index=0, version="judge-v1"):
    return repo.enqueue_evaluation(run_id, job_kind="llm_judge", evaluator_version=version,
                                   config={"manual_trigger": True, "index": index})


def claim(repo, *, version="judge-v1", concurrency=1):
    return repo.claim_job("worker", job_kind="llm_judge", evaluator_version=version,
                          max_concurrency=concurrency)


def reserve(repo, job, key="call-1", **kwargs):
    return repo.reserve_judge_call(job, key, **{"daily_limit": 10, "job_limit": 2, **kwargs})


def retry_ready(repo):
    with repo.connection() as db:
        db.execute("UPDATE quality_jobs SET available_at=0")


def test_no_automatic_judge_enqueue_and_global_claim_limit_across_versions(store):
    repo, run_id, _ = store
    assert repo.backfill_jobs() == 0
    assert all(item["job_kind"] == "deterministic" for item in repo.job_history(run_id)["items"])
    for index in range(3):
        queue(repo, run_id, index, version=f"judge-v{index}")
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = list(pool.map(lambda index: claim(repo, version=f"judge-v{index}"), range(3)))
    assert sum(job is not None for job in jobs) == 1
    assert repo.claim_job("deterministic")["job_kind"] == "deterministic"
    active = next(job for job in jobs if job)
    repo.fail_job(active, "ProviderError")
    assert claim(repo, version=f"judge-v{next(index for index, job in enumerate(jobs) if job is None)}")


def test_daily_budget_is_atomic_across_workers(store):
    repo, run_id, _ = store
    for index in range(2):
        queue(repo, run_id, index)
    jobs = [claim(repo, concurrency=2) for _ in range(2)]
    def attempt(job):
        try:
            return reserve(repo, job, daily_limit=1, max_concurrency=2)
        except JudgeBudgetExceeded as error:
            return error.reason
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, jobs))
    assert outcomes.count("daily_limit") == 1
    with repo.connection() as db:
        assert db.execute("SELECT reserved_units FROM quality_budget_usage WHERE job_kind='llm_judge'").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM quality_judge_calls").fetchone()[0] == 1


def test_job_cap_accumulates_across_attempts_and_call_keys_cannot_be_reissued(store):
    repo, run_id, _ = store
    queue(repo, run_id)
    first = claim(repo)
    call = reserve(repo, first)
    repo.start_judge_call(first, call["call_id"])
    repo.finish_judge_call(call["call_id"], status="error", error_type="TimeoutError")
    repo.fail_job(first, "TimeoutError")
    retry_ready(repo)
    second = claim(repo)
    with pytest.raises(QualityConflict, match="cannot be reused"):
        reserve(repo, second)
    second_call = reserve(repo, second, "call-2")
    repo.start_judge_call(second, second_call["call_id"])
    repo.finish_judge_call(second_call["call_id"], status="error", error_type="OutputProtocolError")
    with pytest.raises(JudgeBudgetExceeded) as caught:
        reserve(repo, second, "call-3")
    assert caught.value.reason == "job_limit"
    result = repo.defer_judge_job(second, "job_limit")
    assert result["status"] == "dead"
    reports = repo.evaluation_history(run_id)
    assert reports["total"] == 2
    assert {item["execution_status"] for item in reports["items"]} == {"error", "skipped"}
    assert repo.list_cases()["total"] == 0
    assert repo.list_evaluations(evaluator_kind="llm_judge")["total"] == 2


def test_daily_deferral_keeps_immutable_unknown_reports_without_spending_retry_allowance(store):
    repo, run_id, _ = store
    queue(repo, run_id)
    for _ in range(5):
        job = claim(repo)
        with pytest.raises(JudgeBudgetExceeded) as caught:
            reserve(repo, job, daily_limit=0)
        assert caught.value.reason == "daily_limit"
        outcome = repo.defer_judge_job(job, caught.value.reason)
        deadline = datetime.fromtimestamp(outcome["available_at"], timezone.utc)
        assert deadline.hour == deadline.minute == deadline.second == 0
        assert outcome["available_at"] > time.time()
        assert claim(repo) is None
        retry_ready(repo)
    details = next(item for item in repo.job_history(run_id)["items"] if item["job_kind"] == "llm_judge")
    assert (details["status"], details["attempts"], details["max_attempts"]) == ("pending", 5, 8)
    assert not details["calls"]
    assert all(item["execution_status"] == "skipped" and item["verdict"] == "unknown"
               for item in repo.evaluation_history(run_id)["items"])


def test_deferral_preserves_redacted_context_and_server_derived_call_count(store):
    repo, run_id, _ = store
    queue(repo, run_id)
    job = claim(repo)
    call = reserve(repo, job, daily_limit=1)
    repo.start_judge_call(job, call["call_id"])
    repo.finish_judge_call(call["call_id"], status="succeeded", usage={"total_tokens": 27})
    repo.defer_judge_job(job, "daily_limit", metadata={"reason": "incorrect", "model_calls": 999,
        "judge_input": {"answer": "原答案", "api_key": "private-secret"}, "repair_input": {"category": "tool_selection"}})
    saved = repo.evaluation_history(run_id)["items"][0]["report"]["metadata"]
    assert saved["reason"] == "daily_limit" and saved["model_calls"] == 1
    assert saved["judge_input"]["answer"] == "原答案"
    assert saved["judge_input"]["api_key"] != "private-secret"
    assert saved["repair_input"]["category"] == "tool_selection"
    item = next(item for item in repo.job_history(run_id)["items"] if item["job_kind"] == "llm_judge")
    assert item["id"] == item["job_id"] == job["id"]
    assert item["calls"][0]["total_tokens"] == 27


def test_lease_loss_retains_call_slot_until_deadline_and_late_usage_does_not_change_report(store):
    repo, run_id, _ = store
    queue(repo, run_id)
    first = claim(repo)
    call = reserve(repo, first, request_hash="a" * 64, phase="assessment")
    repo.start_judge_call(first, call["call_id"])
    with repo.connection() as db:
        db.execute("UPDATE quality_jobs SET lease_until=0 WHERE id=?", (first["id"],))
    assert claim(repo) is None
    before = repo.evaluation_history(run_id)["items"][0]
    assert before["error_type"] == "LeaseExpired"
    with pytest.raises(QualityConflict):
        repo.start_judge_call(first, call["call_id"])
    with repo.connection() as db:
        db.execute("UPDATE quality_judge_calls SET expires_at=0")
    second = claim(repo)
    assert second["attempts"] == 2
    audit = repo.judge_call_history(first["id"])["items"][0]
    assert audit["status"] == "unknown" and audit["error_type"] == "CallDeadlineExceeded"
    late = repo.finish_judge_call(call["call_id"], status="succeeded",
                                  usage={"prompt_tokens": 7, "completion_tokens": None}, duration_ms=100)
    assert late["status"] == "unknown" and late["late_status"] == "succeeded"
    assert late["prompt_tokens"] == 7 and late["completion_tokens"] is late["total_tokens"] is None
    after = next(item for item in repo.evaluation_history(run_id)["items"] if item["evaluation_id"] == before["evaluation_id"])
    assert after == before
    with pytest.raises(QualityConflict, match="already recorded"):
        repo.finish_judge_call(call["call_id"], status="error", usage={"total_tokens": 0})


def test_only_one_request_can_be_started_per_reservation_and_active_calls_are_limited(store):
    repo, run_id, _ = store
    for index in range(2):
        queue(repo, run_id, index)
    first, second = [claim(repo, concurrency=2) for _ in range(2)]
    call = reserve(repo, first)
    repo.start_judge_call(first, call["call_id"])
    with pytest.raises(QualityConflict, match="already issued"):
        repo.start_judge_call(first, call["call_id"])
    with pytest.raises(JudgeBudgetExceeded) as caught:
        reserve(repo, second)
    assert caught.value.reason == "concurrency_limit"
    repo.defer_judge_job(second, "concurrency_limit")
    repo.finish_judge_call(call["call_id"], status="succeeded")
    retry_ready(repo)
    # The first worker still owns a job slot until its report has been persisted.
    assert claim(repo) is None
    repo.fail_job(first, "FixtureError")
    assert claim(repo)


def test_call_insert_failure_rolls_back_budget_and_delete_does_not_refund(store):
    repo, run_id, chats = store
    queue(repo, run_id)
    job = claim(repo)
    with repo.connection() as db:
        db.execute("CREATE TRIGGER reject_call BEFORE INSERT ON quality_judge_calls BEGIN SELECT RAISE(ABORT,'fixture'); END")
    with pytest.raises(sqlite3.IntegrityError):
        reserve(repo, job)
    with repo.connection() as db:
        assert db.execute("SELECT count(*) FROM quality_budget_usage").fetchone()[0] == 0
        db.execute("DROP TRIGGER reject_call")
    call = reserve(repo, job)
    repo.start_judge_call(job, call["call_id"])
    assert chats.delete_session("session", owner_id="owner")
    with repo.connection() as db:
        for table in ("quality_judge_calls", "quality_budget_reservations", "quality_jobs", "quality_evaluations"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert db.execute("SELECT reserved_units FROM quality_budget_usage").fetchone()[0] == 1
    assert repo.finish_judge_call(call["call_id"], status="error") == {"recorded": False, "reason": "deleted"}


@pytest.mark.parametrize("arguments", [{"daily_limit": True}, {"job_limit": 0}, {"max_concurrency": 0},
    {"timeout_seconds": float("nan")}, {"timeout_seconds": float("inf")}, {"request_hash": "raw prompt"}])
def test_invalid_budget_parameters_never_spend_quota(store, arguments):
    repo, run_id, _ = store
    queue(repo, run_id)
    with pytest.raises(ValueError):
        reserve(repo, claim(repo), **arguments)
    with repo.connection() as db:
        assert db.execute("SELECT count(*) FROM quality_judge_calls").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM quality_budget_usage").fetchone()[0] == 0
