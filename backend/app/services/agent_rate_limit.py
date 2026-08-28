"""In-process rate and concurrency limits for public Agent requests."""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable

from app.config import env_bool


DEFAULT_REQUESTS_PER_MINUTE = 8
DEFAULT_REQUESTS_PER_DAY = 60
DEFAULT_IP_REQUESTS_PER_MINUTE = 20
DEFAULT_MAX_CONCURRENT_PER_OWNER = 1
DEFAULT_MAX_CONCURRENT_GLOBAL = 4


@dataclass(frozen=True)
class AgentRateLimitConfig:
    """Runtime limits for one backend process."""

    enabled: bool
    requests_per_minute: int
    requests_per_day: int
    ip_requests_per_minute: int
    max_concurrent_per_owner: int
    max_concurrent_global: int
    daily_cost_budget_usd: float


class AgentRateLimitError(RuntimeError):
    """A public Agent request exceeded one configured boundary."""

    def __init__(self, code: str, message: str, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after_seconds = max(1, retry_after_seconds)


class AgentRequestLease:
    """Idempotent lease representing one active Agent execution."""

    def __init__(
        self,
        limiter: "AgentRateLimiter",
        owner_id: str,
        *,
        enabled: bool,
        daily_remaining: int,
    ) -> None:
        self._limiter = limiter
        self._owner_id = owner_id
        self._enabled = enabled
        self._released = False
        self.daily_remaining = max(0, daily_remaining)

    def release(self) -> None:
        """Release concurrency capacity exactly once."""

        if self._released:
            return
        self._released = True
        if self._enabled:
            self._limiter.release(self._owner_id)


class AgentRateLimiter:
    """Thread-safe fixed-window request limiter plus concurrency guard."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._owner_attempts: dict[str, deque[float]] = defaultdict(deque)
        self._ip_attempts: dict[str, deque[float]] = defaultdict(deque)
        self._active_by_owner: dict[str, int] = defaultdict(int)
        self._active_global = 0

    def acquire(
        self,
        owner_id: str,
        ip_address: str,
        *,
        daily_request_count: int,
        daily_cost_usd: float = 0.0,
    ) -> AgentRequestLease:
        """Consume request capacity and reserve one execution slot."""

        config = load_agent_rate_limit_config()
        if not config.enabled:
            return AgentRequestLease(
                self,
                owner_id,
                enabled=False,
                daily_remaining=config.requests_per_day,
            )

        now = self._clock()
        with self._lock:
            owner_attempts = self._owner_attempts[owner_id]
            ip_attempts = self._ip_attempts[ip_address]
            _trim_window(owner_attempts, now)
            _trim_window(ip_attempts, now)

            if len(owner_attempts) >= config.requests_per_minute:
                raise AgentRateLimitError(
                    "owner_minute_limit",
                    "提问过于频繁，请稍后再试。",
                    _retry_after(owner_attempts, now),
                )
            if len(ip_attempts) >= config.ip_requests_per_minute:
                raise AgentRateLimitError(
                    "ip_minute_limit",
                    "当前网络请求过于频繁，请稍后再试。",
                    _retry_after(ip_attempts, now),
                )

            # Rejected attempts also occupy the short request window. This keeps
            # clients from bypassing the guard by repeatedly submitting work.
            owner_attempts.append(now)
            ip_attempts.append(now)

            if daily_request_count >= config.requests_per_day:
                raise AgentRateLimitError(
                    "owner_daily_limit",
                    "今天的 Agent 使用次数已达上限，请明天再试。",
                    3600,
                )
            if (
                config.daily_cost_budget_usd > 0
                and daily_cost_usd >= config.daily_cost_budget_usd
            ):
                raise AgentRateLimitError(
                    "owner_daily_cost_limit",
                    "今天的 Agent 成本额度已用完，请明天再试。",
                    3600,
                )
            if self._active_by_owner[owner_id] >= config.max_concurrent_per_owner:
                raise AgentRateLimitError(
                    "owner_concurrency_limit",
                    "上一条问题仍在处理中，请等待回答完成。",
                    2,
                )
            if self._active_global >= config.max_concurrent_global:
                raise AgentRateLimitError(
                    "global_concurrency_limit",
                    "Agent 当前任务较多，请稍后再试。",
                    3,
                )

            self._active_by_owner[owner_id] += 1
            self._active_global += 1
            return AgentRequestLease(
                self,
                owner_id,
                enabled=True,
                daily_remaining=config.requests_per_day - daily_request_count - 1,
            )

    def release(self, owner_id: str) -> None:
        """Return one active execution slot to the limiter."""

        with self._lock:
            active = self._active_by_owner.get(owner_id, 0)
            if active > 1:
                self._active_by_owner[owner_id] = active - 1
            else:
                self._active_by_owner.pop(owner_id, None)
            self._active_global = max(0, self._active_global - 1)

    def snapshot(self) -> dict[str, int]:
        """Return non-sensitive live concurrency metrics."""

        with self._lock:
            return {
                "active_global": self._active_global,
                "active_owner_count": len(self._active_by_owner),
            }

    def reset(self) -> None:
        """Clear in-memory state for tests and controlled restarts."""

        with self._lock:
            self._owner_attempts.clear()
            self._ip_attempts.clear()
            self._active_by_owner.clear()
            self._active_global = 0


def load_agent_rate_limit_config() -> AgentRateLimitConfig:
    """Load public Agent limits from environment variables."""

    return AgentRateLimitConfig(
        enabled=env_bool("LIMITUPLAB_AGENT_RATE_LIMIT_ENABLED", True),
        requests_per_minute=_positive_int(
            "LIMITUPLAB_AGENT_REQUESTS_PER_MINUTE",
            DEFAULT_REQUESTS_PER_MINUTE,
        ),
        requests_per_day=_positive_int(
            "LIMITUPLAB_AGENT_REQUESTS_PER_DAY",
            DEFAULT_REQUESTS_PER_DAY,
        ),
        ip_requests_per_minute=_positive_int(
            "LIMITUPLAB_AGENT_IP_REQUESTS_PER_MINUTE",
            DEFAULT_IP_REQUESTS_PER_MINUTE,
        ),
        max_concurrent_per_owner=_positive_int(
            "LIMITUPLAB_AGENT_MAX_CONCURRENT_PER_USER",
            DEFAULT_MAX_CONCURRENT_PER_OWNER,
        ),
        max_concurrent_global=_positive_int(
            "LIMITUPLAB_AGENT_MAX_CONCURRENT_GLOBAL",
            DEFAULT_MAX_CONCURRENT_GLOBAL,
        ),
        daily_cost_budget_usd=_non_negative_float(
            "LIMITUPLAB_AGENT_DAILY_COST_BUDGET_USD",
            0.0,
        ),
    )


def _trim_window(attempts: deque[float], now: float) -> None:
    while attempts and now - attempts[0] >= 60:
        attempts.popleft()


def _retry_after(attempts: deque[float], now: float) -> int:
    if not attempts:
        return 1
    return max(1, int(61 - (now - attempts[0])))


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "").strip())
    except ValueError:
        return default
    return value if value > 0 else default


def _non_negative_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "").strip())
    except ValueError:
        return default
    return value if value >= 0 else default


agent_rate_limiter = AgentRateLimiter()
