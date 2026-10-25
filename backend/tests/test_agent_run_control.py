"""User control is durable, fenced and independent of final answer delivery."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.agents.runtime_errors import RunPauseRequested
from app.services.agent_run_services import AgentRunServices, RunConflict
from app.services.runtime_coordination import LeaseLost
from database_support import reject_writes
from test_agent_run_services import counts, registered_run, response


def pause(fixture, *, checkpoint="cp-1", mode="checkpoint"):
    services, row, owner, _ = fixture
    run_id = row["run_id"]
    lease = services.coordinator.claim(run_id)
    try:
        requested = services.controls.request(run_id, owner, "pause", "pause-1", 0)
        paused = services.controls.ack_paused(run_id, owner, expected_revision=requested["control_revision"],
            checkpoint_id=checkpoint, pause_mode=mode, execution=lease)
    except BaseException:
        lease.release()
        raise
    return paused, lease


def test_pause_resume_preserves_identity_and_does_not_consume_recovery_budget(registered_run):
    services, original, owner, request = registered_run
    row, lease = pause(registered_run)
    try:
        assert row["run_status"] == "paused" and row["active"] == 1 and row["control_revision"] == 1
        assert row["pause_checkpoint_id"] == "cp-1" and row["paused_at"]
        assert counts(services, row["run_id"]) == (0, 0)
        with pytest.raises(RunConflict):
            services.registry.create(request.model_copy(update={"message_id": "new-message"}), owner)
        resumed = services.controls.request(row["run_id"], owner, "resume", "resume-1", 1)
        started = services.registry.mark_running(row["run_id"], owner, execution=lease)
        assert resumed["run_status"] == "resuming" and started["run_status"] == "running"
        assert started["control_revision"] == 2 and started["resume_count"] == 1 and started["recovery_count"] == 0
        assert started["pause_checkpoint_id"] == "cp-1"
        for key in ("run_id", "session_id", "thread_id", "request_hash", "request_json", "started_at"):
            assert started[key] == original[key]
    finally:
        lease.release()


def test_old_operation_retry_returns_current_state_without_reapplying_pause(registered_run):
    services, row, owner, _ = registered_run
    paused, lease = pause(registered_run)
    try:
        services.controls.request(row["run_id"], owner, "resume", "resume-1", 1)
        current = services.controls.request(row["run_id"], owner, "pause", "pause-1", 0)
        assert current["run_status"] == "resuming" and current["control_revision"] == 2
        with pytest.raises(RunConflict, match="operation_id"):
            services.controls.request(row["run_id"], owner, "resume", "pause-1", 1)
        with pytest.raises(RunConflict, match="状态已变化"):
            services.controls.request(row["run_id"], owner, "pause", "stale-pause", 0)
        with services.storage.connection() as db:
            assert db.execute("SELECT count(*) FROM run_control_operations").fetchone()[0] == 2
            assert db.execute("SELECT count(*) FROM react_events WHERE event='run_state'").fetchone()[0] == 3
    finally:
        lease.release()


def test_continue_before_pause_confirmation_and_foreign_owner_are_rejected(registered_run):
    services, row, owner, _ = registered_run
    services.controls.request(row["run_id"], owner, "pause", "pause-1", 0)
    with pytest.raises(RunConflict, match="尚未确认暂停"):
        services.controls.request(row["run_id"], owner, "resume", "early", 1)
    with pytest.raises(KeyError):
        services.controls.request(row["run_id"], "other-owner", "resume", "foreign", 1)
    assert services.controls.read(row["run_id"], owner)["run_status"] == "pausing"


def test_pausing_cannot_be_overwritten_by_running_interrupted_or_delivery(registered_run):
    services, row, owner, _ = registered_run
    lease = services.coordinator.claim(row["run_id"])
    try:
        services.controls.request(row["run_id"], owner, "pause", "pause-1", 0)
        with pytest.raises(RunPauseRequested) as stopped:
            services.registry.mark_running(row["run_id"], owner, execution=lease)
        assert stopped.value.pause_revision == 1
        assert not services.registry.mark_interrupted(row["run_id"], owner, execution=lease)
        with pytest.raises(RunPauseRequested):
            services.delivery.commit(row["run_id"], owner, response(row), execution=lease)
        assert counts(services, row["run_id"]) == (0, 0)
        assert services.registry.get(row["run_id"], owner)["run_status"] == "pausing"
    finally:
        lease.release()


def test_delivery_pause_keeps_final_candidate_out_of_public_history(registered_run):
    services, row, owner, _ = registered_run
    paused, lease = pause(registered_run, mode="delivery")
    try:
        with pytest.raises(RunPauseRequested):
            services.delivery.commit(row["run_id"], owner, response(row), execution=lease)
        services.controls.request(row["run_id"], owner, "resume", "resume-1", 1)
        services.registry.mark_running(row["run_id"], owner, execution=lease)
        delivered = services.delivery.commit(row["run_id"], owner, response(row), execution=lease)
        assert delivered["task_status"] == "complete" and counts(services, row["run_id"]) == (1, 1)
        terminal = services.controls.request(row["run_id"], owner, "pause", "late-pause", 2)
        assert terminal["run_status"] == "completed" and terminal["control_revision"] == 2
        with pytest.raises(RunConflict):
            services.controls.request(row["run_id"], owner, "resume", "late-resume", 2)
    finally:
        lease.release()


def test_failed_resume_admission_returns_paused_and_revokes_stale_resume_intent(registered_run):
    services, row, owner, _ = registered_run
    paused, lease = pause(registered_run)
    try:
        services.controls.request(row["run_id"], owner, "resume", "resume-1", 1)
        deferred = services.controls.defer_resume(row["run_id"], owner, expected_revision=2, execution=lease)
        assert deferred["run_status"] == "paused" and deferred["control_revision"] == 3
        assert deferred["pause_checkpoint_id"] == "cp-1"
        repeated = services.controls.request(row["run_id"], owner, "resume", "resume-1", 1)
        assert repeated["run_status"] == "paused" and repeated["control_revision"] == 3
        assert services.registry.recoverable_runs() == []
        services.controls.request(row["run_id"], owner, "resume", "resume-2", 3)
        stale = services.controls.defer_resume(row["run_id"], owner, expected_revision=2, execution=lease)
        assert stale["run_status"] == "resuming" and stale["control_revision"] == 4
    finally:
        lease.release()


def test_cancel_dominates_pause_and_is_idempotent(registered_run):
    services, row, owner, _ = registered_run
    paused, lease = pause(registered_run)
    try:
        services.registry.cancel(row["run_id"], owner)
        services.registry.cancel(row["run_id"], owner)
        assert services.registry.get(row["run_id"], owner)["control_revision"] == 2
        with pytest.raises(RunConflict):
            services.controls.request(row["run_id"], owner, "resume", "resume-1", 1)
        with pytest.raises(RunConflict):
            services.controls.ack_paused(row["run_id"], owner, expected_revision=1,
                checkpoint_id="cp-1", execution=lease)
        assert services.delivery.commit(row["run_id"], owner, response(row), execution=lease)["task_status"] == "cancelled"
    finally:
        lease.release()


def test_no_checkpoint_pause_is_valid_and_acknowledged_position_is_immutable(registered_run):
    services, row, owner, _ = registered_run
    paused, lease = pause(registered_run, checkpoint=None)
    try:
        assert paused["pause_checkpoint_id"] is None
        assert services.controls.ack_paused(row["run_id"], owner, expected_revision=1,
            checkpoint_id=None, execution=lease) == paused
        with pytest.raises(RunConflict, match="不能被覆盖"):
            services.controls.ack_paused(row["run_id"], owner, expected_revision=1,
                checkpoint_id="invented", execution=lease)
        with pytest.raises(ValueError, match="committed checkpoint"):
            services.controls.ack_paused(row["run_id"], owner, expected_revision=1,
                checkpoint_id=None, pause_mode="interrupt", execution=lease)
    finally:
        lease.release()


def test_stale_worker_cannot_confirm_or_defer_control(registered_run):
    services, row, owner, _ = registered_run
    paused, old = pause(registered_run)
    old.release()
    current = services.coordinator.claim(row["run_id"])
    try:
        with pytest.raises(LeaseLost):
            services.controls.ack_paused(row["run_id"], owner, expected_revision=1, checkpoint_id="cp-1", execution=old)
        services.controls.request(row["run_id"], owner, "resume", "resume-1", 1)
        with pytest.raises(LeaseLost):
            services.controls.defer_resume(row["run_id"], owner, expected_revision=2, execution=old)
        assert services.registry.get(row["run_id"], owner)["run_status"] == "resuming"
    finally:
        current.release()


def test_recovery_scans_control_reconciliation_but_not_confirmed_pause(registered_run):
    services, row, owner, _ = registered_run
    services.controls.request(row["run_id"], owner, "pause", "pause-1", 0)
    with services.storage.connection() as db:
        db.execute("UPDATE react_runs SET recovery_count=3 WHERE run_id=?", (row["run_id"],))
    assert len(services.registry.recoverable_runs()) == 1
    lease = services.coordinator.claim(row["run_id"])
    services.controls.ack_paused(row["run_id"], owner, expected_revision=1, checkpoint_id=None, execution=lease)
    lease.release()
    assert services.registry.recoverable_runs() == []
    services.controls.request(row["run_id"], owner, "resume", "resume-1", 1)
    assert len(services.registry.recoverable_runs()) == 1


def test_control_operation_and_state_event_rollback_together(registered_run):
    services, row, owner, _ = registered_run
    with services.storage.connection() as db:
        reject_writes(db, table="run_control_operations", trigger="reject_control_receipt")
    with pytest.raises(Exception):
        services.controls.request(row["run_id"], owner, "pause", "pause-1", 0)
    assert services.registry.get(row["run_id"], owner)["control_revision"] == 0
    assert services.events.list(row["run_id"], owner) == []


def test_duplicate_concurrent_pause_applies_once(registered_run):
    services, row, owner, _ = registered_run
    def request(_):
        other = AgentRunServices(services.storage.database_url)
        return other.controls.request(row["run_id"], owner, "pause", "same-op", 0)
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(request, range(4)))
    assert all(item["control_revision"] == 1 for item in replies)
    assert len(services.events.list(row["run_id"], owner)) == 1


def test_pause_delivery_race_has_one_atomic_winner(registered_run):
    services, row, owner, _ = registered_run
    gate = Barrier(2)
    def request_pause():
        gate.wait()
        return services.controls.request(row["run_id"], owner, "pause", "pause-1", 0)
    def deliver():
        gate.wait()
        try:
            return services.delivery.commit(row["run_id"], owner, response(row))
        except RunPauseRequested:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        pause_job, delivery_job = pool.submit(request_pause), pool.submit(deliver)
        pause_job.result()
        delivered = delivery_job.result()
    current = services.registry.get(row["run_id"], owner)
    if delivered is None:
        assert current["run_status"] == "pausing" and counts(services, row["run_id"]) == (0, 0)
    else:
        assert current["run_status"] == "completed" and counts(services, row["run_id"]) == (1, 1)
