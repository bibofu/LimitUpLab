"""Recovery preserves admission limits, request identity and final delivery."""

from datetime import datetime, timezone
import time
from types import SimpleNamespace

from fastapi import HTTPException, Request
import pytest

from app.agents.runtime_errors import RecoverableRunError
from app.models import AgentChatRequest, AgentChatResponse, AgentUsageRecord
from app.repositories import ChatSessionRepository
from app.repositories.agent_usage_repository import AgentUsageRepository
from app.routers import agents, react_chat
from app.services.agent_run_services import AgentRunServices
from app.services.agent_run_recovery import recover_once
from app.services.shared_agent_rate_limit import DatabaseAgentRateLimiter


@pytest.fixture
def limits(monkeypatch, postgresql_database):
    for key, value in {
        "RATE_LIMIT_ENABLED": "true", "REQUESTS_PER_MINUTE": "100",
        "REQUESTS_PER_DAY": "1", "IP_REQUESTS_PER_MINUTE": "100",
        "MAX_CONCURRENT_PER_USER": "1", "MAX_CONCURRENT_GLOBAL": "2",
        "DAILY_COST_BUDGET_USD": "0",
    }.items():
        monkeypatch.setenv("LIMITUPLAB_AGENT_" + key, value)
    limiter = DatabaseAgentRateLimiter(postgresql_database)
    monkeypatch.setattr(agents, "agent_rate_limiter", limiter)
    yield limiter
    limiter.reset()


def admit(*, resume=False):
    return agents._begin_agent_request(
        None if resume else Request({"type": "http", "client": ("192.0.2.1", 5000), "headers": []}),
        owner_id="owner", session_id="session", run_id="run", started_at=datetime.now(timezone.utc),
        resume_run_id="run" if resume else None,
    )


def test_recovery_reuses_ip_and_daily_quota_but_accounts_for_each_attempt(limits):
    lease, repository, first = admit()
    repository.finish(first.model_copy(update={"status": "error", "estimated_cost_usd": 0.25}))
    lease.release()
    resumed, _, second = admit(resume=True)
    try:
        assert second.ip_hash == first.ip_hash and second.usage_id != first.usage_id
        assert repository.owner_today("owner").request_count == 1
        repository.finish(second.model_copy(update={"status": "success", "estimated_cost_usd": 0.5}))
        assert repository.owner_today("owner").estimated_cost_usd == 0.75
        with limits._leases[resumed.token].store.connection() as db:
            assert db.execute("SELECT request_count FROM agent_request_days WHERE owner_id='owner'").fetchone()[0] == 1
    finally:
        resumed.release()


def test_recovery_cannot_bypass_concurrency_or_cost_limits(limits, monkeypatch):
    lease, repository, first = admit()
    with pytest.raises(HTTPException) as blocked:
        admit(resume=True)
    assert blocked.value.headers["X-Agent-Limit-Reason"] == "owner_concurrency_limit"
    repository.finish(first.model_copy(update={"status": "error", "estimated_cost_usd": 1.0}))
    lease.release()
    monkeypatch.setenv("LIMITUPLAB_AGENT_DAILY_COST_BUDGET_USD", "1")
    with pytest.raises(HTTPException) as blocked:
        admit(resume=True)
    assert blocked.value.headers["X-Agent-Limit-Reason"] == "owner_daily_cost_limit"


def test_background_recovery_requires_the_original_owner_admission(limits):
    with pytest.raises(HTTPException) as missing:
        admit(resume=True)
    assert missing.value.status_code == 503
    lease, repository, first = admit()
    lease.release()
    assert repository.recovery_ip_hash(first.run_id, "different-owner") is None
    with pytest.raises(ValueError, match="original run"):
        agents._begin_agent_request(
            None, owner_id="owner", session_id="session", run_id="different-run",
            started_at=datetime.now(timezone.utc), resume_run_id="run",
        )


def test_supervisor_only_dispatches_accepted_bounded_durable_runs(postgresql_database, monkeypatch):
    from app.agents.runner import durable_runtime
    monkeypatch.setattr(durable_runtime, "get_runtime", lambda: object())
    services = AgentRunServices()
    usage = AgentUsageRepository()
    rows = {}
    for name in ("ready", "cancelled", "legacy", "exhausted", "unadmitted"):
        ChatSessionRepository().ensure_session(name, owner_id="owner")
        row, _ = services.registry.create(
            AgentChatRequest(session_id=name, message_id="m", message="什么是首板？"), "owner",
            runtime_version="legacy" if name == "legacy" else "durable-v1",
        )
        rows[name] = row
        if name != "unadmitted":
            usage.start(AgentUsageRecord(
                usage_id="usage-" + name, run_id=row["run_id"], session_id=name, owner_id="owner",
                ip_hash="original-ip-hash", started_at=datetime.now(timezone.utc),
            ))
    services.registry.cancel(rows["cancelled"]["run_id"], "owner")
    with services.storage.connection() as db:
        db.execute("UPDATE react_runs SET recovery_count=3 WHERE run_id=?", (rows["exhausted"]["run_id"],))
    started, cancelled = [], []
    monkeypatch.setattr(react_chat, "start", lambda request, http, owner: started.append((request, http, owner)))
    monkeypatch.setattr(react_chat, "cancel", lambda run, owner: cancelled.append((run, owner)))
    assert recover_once() == 1
    assert [(request.session_id, http, owner) for request, http, owner in started] == [("ready", None, "owner")]
    assert cancelled == [(rows["cancelled"]["run_id"], "owner")]


def test_worker_interruption_keeps_no_answer_and_same_post_can_finish(postgresql_database, monkeypatch):
    from app.agents.runner import durable_runtime
    monkeypatch.setattr(durable_runtime, "get_runtime", lambda: object())
    monkeypatch.setenv("LIMITUPLAB_AGENT_RATE_LIMIT_ENABLED", "false")
    monkeypatch.setattr(agents, "get_limit_up_repository", lambda: SimpleNamespace(list_events=lambda: []))
    monkeypatch.setattr(agents, "prepare_session_context", lambda **kwargs: pytest.fail("durable run wrote legacy memory"))
    attempts = []
    def answer(**kwargs):
        from app.agents.run_lifecycle import CURRENT_CONTROL
        attempts.append(kwargs["request"])
        if len(attempts) == 1:
            raise RecoverableRunError("fixture checkpoint interruption")
        assert CURRENT_CONTROL.get().row["recovery_count"] == 1
        return AgentChatResponse(session_id="recover-session", intent="react_research",
                                 answer="首板是该轮连续涨停中的第一个涨停板。", task_status="complete",
                                 stop_reason="answered", generated_by="fixture")
    monkeypatch.setattr(agents, "answer_first_board_chat", answer)
    request = AgentChatRequest(session_id="recover-session", message_id="m", message="什么是首板？")
    http = Request({"type": "http", "client": ("192.0.2.1", 5000), "headers": []})
    journal, row = react_chat.start(request, http, "owner")
    def wait_for_release():
        deadline = time.monotonic() + 5
        while journal.executing(row["run_id"]) and time.monotonic() < deadline:
            time.sleep(.01)
        assert not journal.executing(row["run_id"])
    wait_for_release()
    interrupted = journal.get(row["run_id"], "owner")
    assert interrupted["run_status"] == "interrupted" and interrupted["response_json"] is None
    assert "恢复" in react_chat.interruption_message(interrupted)
    react_chat.start(request, None, "owner")
    wait_for_release()
    final = journal.services.registry.delivered_response(row["run_id"], "owner")
    assert final["task_status"] == "complete"
    assert journal.get(row["run_id"], "owner")["recovery_count"] == 1
    messages = ChatSessionRepository().get_session("recover-session", owner_id="owner").messages
    assert [message.role for message in messages] == ["user", "assistant"]


def test_accounting_failure_after_delivery_does_not_rerun_or_republish(postgresql_database, monkeypatch):
    from app.agents.runner import durable_runtime
    monkeypatch.setattr(durable_runtime, "get_runtime", lambda: object())
    monkeypatch.setenv("LIMITUPLAB_AGENT_RATE_LIMIT_ENABLED", "false")
    monkeypatch.setattr(agents, "get_limit_up_repository", lambda: SimpleNamespace(list_events=lambda: []))
    answers, accounting = [], []
    def answer(**kwargs):
        answers.append(kwargs["request"])
        return AgentChatResponse(session_id="delivered-session", intent="react_research",
                                 answer="这是已保存的研究说明。", task_status="complete",
                                 stop_reason="answered", generated_by="fixture")
    def unavailable_accounting(self, record):
        accounting.append(record)
        raise RuntimeError("fixture usage storage unavailable")
    monkeypatch.setattr(agents, "answer_first_board_chat", answer)
    monkeypatch.setattr(AgentUsageRepository, "finish", unavailable_accounting)
    request = AgentChatRequest(session_id="delivered-session", message_id="m", message="什么是首板？")
    http = Request({"type": "http", "client": ("192.0.2.1", 5000), "headers": []})
    lifecycle, row = react_chat.start(request, http, "owner")
    deadline = time.monotonic() + 5
    while lifecycle.executing(row["run_id"]) and time.monotonic() < deadline:
        time.sleep(.01)
    assert not lifecycle.executing(row["run_id"])
    assert len(accounting) == 1
    final = lifecycle.services.registry.delivered_response(row["run_id"], "owner")
    repeated, repeated_row = react_chat.start(request, http, "owner")
    assert repeated_row["run_status"] == "completed"
    assert repeated.services.registry.delivered_response(row["run_id"], "owner") == final
    assert len(answers) == 1 and len(accounting) == 1
    messages = ChatSessionRepository().get_session("delivered-session", owner_id="owner").messages
    assert [message.role for message in messages] == ["user", "assistant"]
