"""Behavioral contracts exercised against SQLite and a real PostgreSQL database."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, datetime, timezone
import json
from threading import Barrier

import pytest

from app.agents.react_runtime.lifecycle import Journal
from app.database import connect, connect_readonly, initialize_database
from app.models import AgentChatRequest, AgentPrediction, ChatSessionMessage, StockDailyBar
from app.quality_models import QualityConflict
from app.repositories.chat_session_repository import SQLiteChatSessionRepository, SessionOwnershipError
from app.repositories.first_board_repository import SQLiteFirstBoardRepository
from app.repositories.limit_up_repository import SQLiteLimitUpRepository
from app.repositories.consolidation_repository import load_consolidation_pool
from app.repositories.post_limit_repository import load_post_limit_dataset
from app.repositories.quality_judge_repository import JudgeBudgetExceeded
from app.repositories.quality_repository import QualityRepository
from app.repositories.scoring_policy_repository import SQLiteScoringPolicyRepository
from app.services.scoring_policy import build_default_scoring_policy
from app.services.sample_data import SAMPLE_EVENTS


@pytest.fixture(params=["sqlite", "postgresql"])
def storage(request, tmp_path):
    if request.param == "postgresql":
        request.getfixturevalue("postgresql_database")
        return None
    path = tmp_path / "contracts.sqlite"
    with closing(connect(path)) as db:
        initialize_database(db)
    return path


@pytest.fixture
def completed(storage):
    chats = SQLiteChatSessionRepository(storage)
    chats.create_session(session_id="session", owner_id="owner")
    journal = Journal(storage)
    row, _ = journal.create(
        AgentChatRequest(session_id="session", message_id="question", message="解释记录的数据"), "owner"
    )
    journal.finish(row["run_id"], "owner", {
        "answer": "记录中的研究解释。", "intent": "research", "task_status": "complete"
    })
    return QualityRepository(storage), row["run_id"], chats


def test_same_timestamp_messages_preserve_insertion_order_and_idempotency(storage):
    chats = SQLiteChatSessionRepository(storage)
    stamp = datetime(2026, 10, 19, 7, tzinfo=timezone.utc)
    for identity in ("z-first", "a-second", "m-third"):
        chats.append_message(ChatSessionMessage(
            message_id=identity, session_id="ordered", role="user", content=identity, created_at=stamp
        ))
    chats.append_message(ChatSessionMessage(
        message_id="a-second", session_id="ordered", role="user", content="must not overwrite", created_at=stamp
    ))
    detail = chats.get_session("ordered", message_limit=2)
    assert [item.message_id for item in detail.messages] == ["a-second", "m-third"]
    assert [item.content for item in detail.messages] == ["a-second", "m-third"]
    assert detail.message_count == 3
    assert chats.list_sessions()[0].last_message_preview == "m-third"


def test_simultaneous_session_creation_cannot_change_owner(storage):
    barrier = Barrier(2)

    def create(owner):
        barrier.wait(timeout=10)
        try:
            return SQLiteChatSessionRepository(storage).create_session(session_id="shared", owner_id=owner).owner_id
        except SessionOwnershipError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, ["one", "two"]))
    assert sum(result is not None for result in results) == 1
    winner = next(result for result in results if result)
    chats = SQLiteChatSessionRepository(storage)
    assert chats.get_session("shared", owner_id=winner).owner_id == winner
    assert chats.get_session("shared", owner_id="two" if winner == "one" else "one") is None


def test_message_identity_cannot_expose_another_session(storage):
    chats = SQLiteChatSessionRepository(storage)
    chats.create_session(session_id="first", owner_id="first-owner")
    chats.append_message(ChatSessionMessage(
        message_id="same-message", session_id="first", role="user", content="first-owner content",
        created_at=datetime.now(timezone.utc),
    ), owner_id="first-owner")
    chats.create_session(session_id="second", owner_id="second-owner")
    with pytest.raises(SessionOwnershipError, match="another session"):
        chats.append_message(ChatSessionMessage(
            message_id="same-message", session_id="second", role="user", content="second-owner content",
            created_at=datetime.now(timezone.utc),
        ), owner_id="second-owner")
    assert chats.get_session("second", owner_id="second-owner").message_count == 0
    assert chats.get_session("first", owner_id="first-owner").messages[0].content == "first-owner content"


def test_message_write_rechecks_owner_after_session_recreation(storage, monkeypatch):
    chats = SQLiteChatSessionRepository(storage)
    chats.create_session(session_id="recreated", owner_id="original")
    original_ensure = chats.ensure_session

    def recreate(*args, **kwargs):
        result = original_ensure(*args, **kwargs)
        chats.delete_session("recreated", owner_id="original")
        chats.create_session(session_id="recreated", owner_id="replacement")
        return result

    monkeypatch.setattr(chats, "ensure_session", recreate)
    with pytest.raises(SessionOwnershipError, match="changed"):
        chats.append_message(ChatSessionMessage(
            message_id="late-message", session_id="recreated", role="user", content="original content",
            created_at=datetime.now(timezone.utc),
        ), owner_id="original")
    assert chats.get_session("recreated", owner_id="replacement").message_count == 0


def test_two_workers_claim_once_and_reject_stale_lease(completed):
    repo, run_id, _ = completed
    barrier = Barrier(2)

    def claim(worker):
        barrier.wait(timeout=10)
        return repo.claim_job(worker)

    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = list(executor.map(claim, ["worker-one", "worker-two"]))
    assert sum(job is not None for job in jobs) == 1
    first = next(job for job in jobs if job)
    with repo.connection() as db:
        db.execute("UPDATE quality_jobs SET lease_until=0 WHERE id=?", (first["id"],))
    second = repo.claim_job("replacement")
    assert second["id"] == first["id"]
    assert second["lease_token"] != first["lease_token"]
    assert second["bundle"] == first["bundle"]
    with pytest.raises(QualityConflict):
        repo.renew_job(first)
    assert repo.evaluation_history(run_id)["total"] == 2


def test_budget_is_atomic_across_independent_repository_instances(completed, storage):
    repo, run_id, _ = completed
    for number in range(2):
        repo.enqueue_evaluation(run_id, job_kind="llm_judge", evaluator_version="contract", config={"n": number})
    jobs = [repo.claim_job(str(number), job_kind="llm_judge", evaluator_version="contract", max_concurrency=2)
            for number in range(2)]
    barrier = Barrier(2)

    def reserve(job):
        independent = QualityRepository(storage)
        barrier.wait(timeout=10)
        try:
            return independent.reserve_budget(job, "call", units=1, limit=1, period="2026-10-19")
        except QualityConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, jobs))
    assert sum(result is not None for result in results) == 1
    winner = jobs[next(index for index, result in enumerate(results) if result)]
    assert repo.reserve_budget(winner, "call", units=1, limit=1, period="2026-10-19")["units"] == 1
    with repo.connection() as db:
        assert db.execute("SELECT reserved_units FROM quality_budget_usage WHERE job_kind='llm_judge'").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM quality_budget_reservations").fetchone()[0] == 1


def test_judge_call_daily_quota_is_shared_and_audited(completed, storage):
    repo, run_id, _ = completed
    for number in range(2):
        repo.enqueue_evaluation(run_id, job_kind="llm_judge", evaluator_version="judge-contract", config={"n": number})
    jobs = [repo.claim_job(str(number), job_kind="llm_judge", evaluator_version="judge-contract", max_concurrency=2)
            for number in range(2)]
    barrier = Barrier(2)

    def reserve(job):
        independent = QualityRepository(storage)
        barrier.wait(timeout=10)
        try:
            return independent.reserve_judge_call(job, "first", daily_limit=1, job_limit=1, max_concurrency=2)
        except JudgeBudgetExceeded as error:
            return error.reason

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, jobs))
    assert results.count("daily_limit") == 1
    index = next(index for index, result in enumerate(results) if isinstance(result, dict))
    call = results[index]
    repo.start_judge_call(jobs[index], call["call_id"])
    repo.finish_judge_call(call["call_id"], status="succeeded", usage={"total_tokens": 5})
    history = repo.judge_call_history(jobs[index]["id"])["items"]
    assert len(history) == 1 and history[0]["status"] == "succeeded"
    assert history[0]["total_tokens"] == 5
    with repo.connection() as db:
        assert db.execute("SELECT reserved_units FROM quality_budget_usage WHERE job_kind='llm_judge'").fetchone()[0] == 1


def test_diagnostic_sql_failure_does_not_rollback_final_answer(storage, monkeypatch):
    from app.services import quality_evidence

    def broken_capture(db, *_args, **_kwargs):
        db.execute("SELECT absent_column FROM quality_evidence_bundles")

    monkeypatch.setattr(quality_evidence, "capture_evidence_bundle", broken_capture)
    chats = SQLiteChatSessionRepository(storage)
    chats.create_session(session_id="session", owner_id="owner")
    journal = Journal(storage)
    row, _ = journal.create(AgentChatRequest(session_id="session", message="研究", message_id="m"), "owner")
    journal.finish(row["run_id"], "owner", {"answer": "已保存答案", "intent": "research", "task_status": "complete"})
    assert chats.get_session("session", owner_id="owner").messages[-1].content == "已保存答案"
    with journal.connection() as db:
        bundle = json.loads(db.execute("SELECT payload_json FROM quality_evidence_bundles WHERE run_id=?", (row["run_id"],)).fetchone()[0])
        assert db.execute("SELECT count(*) FROM quality_jobs WHERE run_id=?", (row["run_id"],)).fetchone()[0] == 1
    assert bundle["manifest"]["missing"] == [{"path": "", "reason": "evidence_capture_failed"}]


def test_historical_prediction_insert_count_and_original_content(storage):
    repository = SQLiteFirstBoardRepository(storage)
    prediction = AgentPrediction(
        prediction_id="historical", trade_date=date(2026, 10, 16), symbol="000001", name="fixture",
        score=80, rating="A", confidence=0.8, scoring_version="contract-v1", prediction_source="historical_backtest",
        data_as_of=date(2026, 10, 16), facts_json={"missing": True}, reasons=[], risks=[],
        created_at=datetime(2026, 10, 16, 8, tzinfo=timezone.utc),
    )
    assert repository._upsert_historical_predictions([prediction]) == 1
    assert repository._upsert_historical_predictions([prediction.model_copy(update={"score": 90})]) == 0
    with closing(connect(storage)) as db:
        row = db.execute("SELECT score,prediction_source FROM agent_predictions WHERE prediction_id=?", ("historical",)).fetchone()
        assert row["score"] == 80
        assert row["prediction_source"] == "historical_backtest"


def test_competing_champion_updates_leave_one_active_version(storage):
    repository = SQLiteScoringPolicyRepository(storage)
    default = build_default_scoring_policy()
    repository.upsert_policy(default)
    barrier = Barrier(2)

    def publish(version):
        barrier.wait(timeout=10)
        SQLiteScoringPolicyRepository(storage).upsert_policy(default.model_copy(update={"version": version}))

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(publish, ["policy-one", "policy-two"]))
    with closing(connect(storage)) as db:
        assert db.execute("SELECT count(*) FROM scoring_policies WHERE status='champion'").fetchone()[0] == 1
    assert repository.get_champion().version in {"policy-one", "policy-two"}


def test_readonly_repository_connection_rejects_writes(storage):
    chats = SQLiteChatSessionRepository(storage)
    chats.create_session(session_id="retained")
    with closing(connect_readonly(storage)) as db:
        with pytest.raises(Exception):
            db.execute("DELETE FROM chat_sessions WHERE session_id=?", ("retained",))
    assert chats.get_session("retained") is not None


def test_research_readers_use_the_selected_database_and_date_cutoff(storage):
    observed = date(2026, 10, 16)
    later = date(2026, 10, 19)
    event = SAMPLE_EVENTS[0].model_copy(update={"trade_date": observed, "closed_limit": True})
    SQLiteLimitUpRepository(storage).upsert_events([event])
    bars = [StockDailyBar(
        symbol=event.symbol, trade_date=day, open=10, high=10.5, low=9.5, close=10, volume=100,
        amount=1000, source="synthetic-test-unadjusted", created_at=datetime(2026, 10, 19, 8, tzinfo=timezone.utc),
    ) for day in (observed, later)]
    first_board = SQLiteFirstBoardRepository(storage)
    first_board.upsert_daily_bars(bars)
    selected = load_post_limit_dataset(observed, database_path=storage)
    assert selected.calendar == [observed.isoformat()]
    assert selected.event_dates == [observed.isoformat()]
    assert selected.events[0]["symbol"] == event.symbol
    assert selected.bars[0]["close"] == 10
    assert selected.latest_data_date == later
    assert first_board.list_daily_bars_for_symbols([event.symbol], end_date=observed) == bars[:1]
    pool = load_consolidation_pool(observed, datetime(2026, 10, 19, 8, tzinfo=timezone.utc), database_path=storage)
    assert pool.latest_data_date == later
    assert pool.available_dates == [later, observed]
