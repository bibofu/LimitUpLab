"""Business delivery remains atomic and immutable independently of the graph."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from pydantic import ValidationError

from database_support import reject_writes
from app.models import AgentChatRequest, AgentChatResponse
from app.repositories import ChatSessionRepository
from app.services.agent_run_services import AgentRunServices, RunConflict
from app.services.runtime_coordination import LeaseLost


@pytest.fixture
def registered_run(postgresql_database):
    owner = "owner-" + uuid4().hex
    session = "session-" + uuid4().hex
    ChatSessionRepository(postgresql_database).ensure_session(session, owner_id=owner)
    services = AgentRunServices(postgresql_database)
    request = AgentChatRequest(session_id=session, message_id="m1", message="什么是首板？")
    row, fresh = services.registry.create(request, owner)
    assert fresh
    return services, row, owner, request


def response(row, answer="首板是该轮连续涨停中的第一个涨停板。"):
    return AgentChatResponse(
        session_id=row["session_id"], intent="react_research", answer=answer,
        task_status="complete", stop_reason="answered", generated_by="fixture",
    ).model_dump(mode="json")


def counts(services, run_id):
    with services.storage.connection() as db:
        return tuple(db.execute(f"SELECT COUNT(*) FROM {table} WHERE run_id=?", (run_id,)).fetchone()[0]
                     for table in ("agent_runs", "chat_messages"))


def test_delivery_failure_rolls_back_every_public_record_and_can_be_retried(registered_run):
    services, row, owner, _ = registered_run
    run_id = row["run_id"]
    with services.storage.connection() as db:
        reject_writes(db, table="chat_messages", trigger="reject_final_message")
    with pytest.raises(Exception):
        services.delivery.commit(run_id, owner, response(row))
    assert counts(services, run_id) == (0, 0)
    assert services.registry.delivered_response(run_id, owner) is None
    assert services.registry.get(run_id, owner)["active"] == 1
    with services.storage.connection() as db:
        db.execute("DROP TRIGGER reject_final_message ON chat_messages")
    final = services.delivery.commit(run_id, owner, response(row))
    assert services.registry.delivered_response(run_id, owner) == final
    assert counts(services, run_id) == (1, 1)


def test_concurrent_redelivery_commits_only_one_immutable_answer(registered_run):
    services, row, owner, _ = registered_run
    def deliver(index):
        remote = AgentRunServices(services.storage.database_url)
        return remote.delivery.commit(row["run_id"], owner, response(row, f"研究说明 {index}"))
    with ThreadPoolExecutor(max_workers=6) as workers:
        answers = list(workers.map(deliver, range(6)))
    assert all(answer == answers[0] for answer in answers)
    assert counts(services, row["run_id"]) == (1, 1)


def test_delivered_result_prevents_reexecution_even_after_lease_expiry(registered_run):
    services, row, owner, request = registered_run
    execution = services.coordinator.claim(row["run_id"])
    final = services.delivery.commit(row["run_id"], owner, response(row), execution=execution)
    execution.release()
    repeated, fresh = services.registry.create(request, owner)
    assert not fresh and not repeated["active"]
    assert services.registry.delivered_response(row["run_id"], owner) == final
    assert services.delivery.commit(row["run_id"], owner, {}) == final


def test_validation_failure_cannot_publish_an_invalid_document(registered_run):
    services, row, owner, _ = registered_run
    with pytest.raises(ValidationError):
        services.delivery.commit(row["run_id"], owner, {"answer": "invalid"})
    assert counts(services, row["run_id"]) == (0, 0)
    assert services.registry.delivered_response(row["run_id"], owner) is None


def test_owner_is_checked_for_registry_delivery_events_and_cancel(registered_run):
    services, row, _, _ = registered_run
    run_id = row["run_id"]
    for operation in (
        lambda: services.registry.get(run_id, "another-owner"),
        lambda: services.registry.delivered_response(run_id, "another-owner"),
        lambda: services.events.list(run_id, "another-owner"),
        lambda: services.delivery.commit(run_id, "another-owner", response(row)),
        lambda: services.registry.cancel(run_id, "another-owner"),
    ):
        with pytest.raises(KeyError):
            operation()
    assert counts(services, run_id) == (0, 0)


def test_cancellation_wins_over_uncommitted_candidate(registered_run):
    services, row, owner, _ = registered_run
    services.registry.cancel(row["run_id"], owner)
    final = services.delivery.commit(row["run_id"], owner, response(row))
    assert final["task_status"] == "cancelled"
    assert final["answer"] != response(row)["answer"]
    assert services.registry.delivered_response(row["run_id"], owner) == final


def test_old_worker_cannot_append_progress_or_deliver_after_takeover(registered_run):
    services, row, owner, _ = registered_run
    run_id = row["run_id"]
    old = services.coordinator.claim(run_id)
    old.release()
    current = services.coordinator.claim(run_id)
    try:
        with pytest.raises(LeaseLost):
            services.events.append(run_id, "progress", {"stage": "tools"}, execution=old)
        with pytest.raises(LeaseLost):
            services.delivery.commit(run_id, owner, response(row), execution=old)
        assert services.events.list(run_id, owner) == []
        assert services.registry.delivered_response(run_id, owner) is None
        services.events.append(run_id, "progress", {"stage": "checking"}, execution=current)
        events = services.events.list(run_id, owner)
        assert len(events) == 1
        assert services.events.list(run_id, owner, events[0]["seq"]) == []
    finally:
        current.release()


def test_request_identity_rejects_changed_payload_and_concurrent_turn(registered_run):
    services, row, owner, request = registered_run
    with pytest.raises(RunConflict):
        services.registry.create(request.model_copy(update={"message": "不同的问题"}), owner)
    with pytest.raises(RunConflict):
        services.registry.create(request.model_copy(update={"message_id": "m2"}), owner)
    assert services.registry.get(row["run_id"], owner)["request_json"] == row["request_json"]
