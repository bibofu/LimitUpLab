import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException, Request

from app.repositories import SQLiteAgentUsageRepository
from app.routers.agents import _begin_agent_request
from app.services.agent_rate_limit import AgentRateLimitError, AgentRateLimiter
from app.services.agent_rate_limit import agent_rate_limiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class AgentRateLimiterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.limiter = AgentRateLimiter(clock=self.clock)
        self.environment = patch.dict(
            os.environ,
            {
                "LIMITUPLAB_AGENT_RATE_LIMIT_ENABLED": "true",
                "LIMITUPLAB_AGENT_REQUESTS_PER_MINUTE": "2",
                "LIMITUPLAB_AGENT_REQUESTS_PER_DAY": "3",
                "LIMITUPLAB_AGENT_IP_REQUESTS_PER_MINUTE": "10",
                "LIMITUPLAB_AGENT_MAX_CONCURRENT_PER_USER": "1",
                "LIMITUPLAB_AGENT_MAX_CONCURRENT_GLOBAL": "2",
                "LIMITUPLAB_AGENT_DAILY_COST_BUDGET_USD": "0",
            },
            clear=False,
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()

    def test_owner_concurrency_is_released_by_idempotent_lease(self) -> None:
        lease = self.limiter.acquire(
            "owner-a",
            "ip-a",
            daily_request_count=0,
        )
        with self.assertRaises(AgentRateLimitError) as blocked:
            self.limiter.acquire("owner-a", "ip-a", daily_request_count=1)
        self.assertEqual(blocked.exception.code, "owner_concurrency_limit")

        lease.release()
        lease.release()
        self.clock.now = 61
        replacement = self.limiter.acquire(
            "owner-a",
            "ip-a",
            daily_request_count=1,
        )
        self.assertEqual(self.limiter.snapshot()["active_global"], 1)
        replacement.release()

    def test_minute_limit_counts_rejected_concurrent_attempts(self) -> None:
        lease = self.limiter.acquire("owner-a", "ip-a", daily_request_count=0)
        with self.assertRaises(AgentRateLimitError):
            self.limiter.acquire("owner-a", "ip-a", daily_request_count=1)
        with self.assertRaises(AgentRateLimitError) as blocked:
            self.limiter.acquire("owner-a", "ip-a", daily_request_count=1)
        self.assertEqual(blocked.exception.code, "owner_minute_limit")
        lease.release()

    def test_daily_and_global_limits_are_independent(self) -> None:
        with self.assertRaises(AgentRateLimitError) as daily:
            self.limiter.acquire("owner-a", "ip-a", daily_request_count=3)
        self.assertEqual(daily.exception.code, "owner_daily_limit")

        first = self.limiter.acquire("owner-b", "ip-b", daily_request_count=0)
        second = self.limiter.acquire("owner-c", "ip-c", daily_request_count=0)
        with self.assertRaises(AgentRateLimitError) as global_limit:
            self.limiter.acquire("owner-d", "ip-d", daily_request_count=0)
        self.assertEqual(global_limit.exception.code, "global_concurrency_limit")
        first.release()
        second.release()


class AgentRequestGuardIntegrationTest(unittest.TestCase):
    def test_concurrent_request_returns_http_429_and_is_audited(self) -> None:
        database_path = Path(__file__).resolve().parents[1] / f"rate-{uuid4().hex}.sqlite"
        self.addCleanup(database_path.unlink, missing_ok=True)
        owner_id = "visitor_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/agents/chat",
                "headers": [],
                "client": ("127.0.0.1", 12345),
            }
        )
        agent_rate_limiter.reset()
        self.addCleanup(agent_rate_limiter.reset)
        with patch.dict(
            os.environ,
            {
                "LIMITUPLAB_DATABASE_PATH": str(database_path),
                "LIMITUPLAB_AGENT_RATE_LIMIT_ENABLED": "true",
                "LIMITUPLAB_AGENT_REQUESTS_PER_MINUTE": "8",
                "LIMITUPLAB_AGENT_REQUESTS_PER_DAY": "60",
                "LIMITUPLAB_AGENT_IP_REQUESTS_PER_MINUTE": "20",
                "LIMITUPLAB_AGENT_MAX_CONCURRENT_PER_USER": "1",
                "LIMITUPLAB_AGENT_MAX_CONCURRENT_GLOBAL": "4",
            },
            clear=False,
        ):
            lease, _repository, _record = _begin_agent_request(
                request,
                owner_id=owner_id,
                session_id="session-1",
                run_id="run-1",
                started_at=datetime.now(timezone.utc),
            )
            try:
                with self.assertRaises(HTTPException) as blocked:
                    _begin_agent_request(
                        request,
                        owner_id=owner_id,
                        session_id="session-1",
                        run_id="run-2",
                        started_at=datetime.now(timezone.utc),
                    )
            finally:
                lease.release()

            usage = SQLiteAgentUsageRepository(database_path).owner_today(owner_id)

        self.assertEqual(blocked.exception.status_code, 429)
        self.assertEqual(blocked.exception.headers["Retry-After"], "2")
        self.assertEqual(usage.request_count, 1)
        self.assertEqual(usage.rejected_count, 1)


if __name__ == "__main__":
    unittest.main()
