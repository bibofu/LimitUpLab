"""Shared quotas must hold even when each request uses a different limiter."""

from concurrent.futures import ThreadPoolExecutor
import threading
from uuid import uuid4

import pytest

from app.services.agent_rate_limit import AgentRateLimitError
from app.services.runtime_coordination import LeaseLost
from app.services.shared_agent_rate_limit import DatabaseAgentRateLimiter
from test_runtime_coordination import lease_store


@pytest.fixture(autouse=True)
def limits(monkeypatch):
    settings = {
        "RATE_LIMIT_ENABLED": "true", "REQUESTS_PER_MINUTE": "100",
        "REQUESTS_PER_DAY": "60", "IP_REQUESTS_PER_MINUTE": "100",
        "MAX_CONCURRENT_PER_USER": "10", "MAX_CONCURRENT_GLOBAL": "2",
        "DAILY_COST_BUDGET_USD": "0",
    }
    for key, value in settings.items():
        monkeypatch.setenv("LIMITUPLAB_AGENT_" + key, value)


def test_global_slots_are_atomic_across_instances(lease_store):
    barrier = threading.Barrier(8)
    def attempt(_):
        limiter = DatabaseAgentRateLimiter(lease_store.database_url)
        barrier.wait(timeout=10)
        try:
            return limiter.acquire(uuid4().hex, uuid4().hex, daily_request_count=0)
        except AgentRateLimitError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(attempt, range(8)))
    accepted = [item for item in results if not isinstance(item, str)]
    try:
        assert len(accepted) == 2
        assert [item for item in results if isinstance(item, str)] == ["global_concurrency_limit"] * 6
        assert DatabaseAgentRateLimiter(lease_store.database_url).snapshot()["active_global"] == 2
    finally:
        for lease in accepted:
            lease.release()


def test_daily_count_is_reserved_before_usage_record_is_written(lease_store, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_AGENT_REQUESTS_PER_DAY", "2")
    monkeypatch.setenv("LIMITUPLAB_AGENT_MAX_CONCURRENT_GLOBAL", "20")
    owner = uuid4().hex
    barrier = threading.Barrier(6)
    def attempt(_):
        limiter = DatabaseAgentRateLimiter(lease_store.database_url)
        barrier.wait(timeout=10)
        try:
            return limiter.acquire(owner, "ip", daily_request_count=0)
        except AgentRateLimitError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=6) as workers:
        results = list(workers.map(attempt, range(6)))
    accepted = [item for item in results if not isinstance(item, str)]
    try:
        assert len(accepted) == 2
        assert [item for item in results if isinstance(item, str)] == ["owner_daily_limit"] * 4
    finally:
        for lease in accepted:
            lease.release()
    # A restart must not erase the daily admission reservation.
    with pytest.raises(AgentRateLimitError, match="今天"):
        DatabaseAgentRateLimiter(lease_store.database_url).acquire(owner, "ip", daily_request_count=0)


def test_rejected_attempts_count_across_instances(lease_store, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_AGENT_REQUESTS_PER_MINUTE", "2")
    monkeypatch.setenv("LIMITUPLAB_AGENT_MAX_CONCURRENT_PER_USER", "1")
    owner = uuid4().hex
    first = DatabaseAgentRateLimiter(lease_store.database_url)
    other = DatabaseAgentRateLimiter(lease_store.database_url)
    lease = first.acquire(owner, "ip", daily_request_count=0)
    try:
        with pytest.raises(AgentRateLimitError) as blocked:
            other.acquire(owner, "ip", daily_request_count=0)
        assert blocked.value.code == "owner_concurrency_limit"
        with pytest.raises(AgentRateLimitError) as blocked:
            other.acquire(owner, "ip", daily_request_count=0)
        assert blocked.value.code == "owner_minute_limit"
        other.reset()
        assert other.snapshot()["active_global"] == 1
    finally:
        lease.release()
        lease.release()
    assert first.snapshot()["active_global"] == 0


def test_expired_slot_cannot_publish_or_release_replacement(lease_store, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_AGENT_MAX_CONCURRENT_PER_USER", "1")
    owner = uuid4().hex
    first = DatabaseAgentRateLimiter(lease_store.database_url)
    second = DatabaseAgentRateLimiter(lease_store.database_url)
    old = first.acquire(owner, "ip", daily_request_count=0)
    with lease_store.connection() as db:
        db.execute("UPDATE agent_request_slots SET expires_at=0 WHERE token=?", (old.token,))
    current = second.acquire(owner, "ip", daily_request_count=0)
    try:
        with lease_store.connection() as db:
            with pytest.raises(LeaseLost):
                old.assert_current(db)
            current.assert_current(db)
        old.release()
        assert second.snapshot()["active_global"] == 1
    finally:
        old.release()
        current.release()
