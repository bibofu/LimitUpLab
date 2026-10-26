"""Pace remote CLI requests and share upstream cooldowns through PostgreSQL."""

from contextlib import contextmanager
import math
import os
import time

from psycopg.errors import LockNotAvailable, QueryCanceled

from app.database import begin_write, connect, get_database_url, initialize_database
from app.services.runtime_coordination import database_now


RATE_KEY = "rate:hithink-finance"


def _configured_interval():
    try:
        value = float(os.getenv("LIMITUPLAB_HITHINK_MIN_INTERVAL_SECONDS", "1"))
        return value if math.isfinite(value) and 0.05 <= value <= 60 else 1.0
    except ValueError:
        return 1.0


class HithinkRateLimiter:
    """One shared start-time gate; no connection or transaction spans a sleep."""

    def __init__(self, database_url=None, *, min_interval_seconds=None,
                 clock=time.monotonic, sleep=time.sleep):
        self.database_url = get_database_url(database_url)
        self.interval = _configured_interval() if min_interval_seconds is None else min_interval_seconds
        if not math.isfinite(self.interval) or not 0.05 <= self.interval <= 60:
            raise ValueError("Hithink request interval must be between 0.05 and 60 seconds")
        self.clock, self.sleep = clock, sleep

    @contextmanager
    def _connection(self, deadline=None):
        self._check_deadline(deadline)
        db = connect(self.database_url)
        try:
            with db:
                self._check_deadline(deadline)
                if deadline is not None:
                    remaining_ms = max(1, int((deadline - self.clock()) * 1000))
                    # Local settings also cover schema checks, without leaking
                    # a caller's timeout to the next pooled connection user.
                    db.execute("BEGIN")
                    db.execute(f"SET LOCAL lock_timeout = '{remaining_ms}ms'")
                    db.execute(f"SET LOCAL statement_timeout = '{remaining_ms}ms'")
                initialize_database(db)
                yield db
                self._check_deadline(deadline)
        finally:
            db.close()

    def _check_deadline(self, deadline):
        if deadline is not None and deadline <= self.clock():
            raise TimeoutError("Hithink database operation exhausted the request budget")

    def wait(self, deadline):
        """Admit a request before its monotonic deadline, or fail without admission."""
        while True:
            if deadline <= self.clock():
                raise TimeoutError("Timed out waiting for the shared hithink request limit")
            try:
                delay = self._try_admit(deadline)
            except (LockNotAvailable, QueryCanceled) as error:
                raise TimeoutError("Hithink admission lock exceeded the request budget") from error
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise TimeoutError("Hithink request budget expired during admission")
            if delay <= 0:
                return
            if delay >= remaining:
                raise TimeoutError("Shared hithink cooldown exceeds the request budget")
            self.sleep(min(delay, 0.25))

    def _try_admit(self, deadline):
        with self._connection(deadline) as db:
            begin_write(db)
            if deadline <= self.clock():
                raise TimeoutError("Hithink request budget expired while waiting for its lock")
            now = database_now(db)
            row = db.execute("SELECT expires_at FROM runtime_leases WHERE name=?", (RATE_KEY,)).fetchone()
            delay = max(0.0, row[0] - now) if row else 0.0
            if delay <= 0:
                self._save(db, now + self.interval)
            return delay

    def cooldown(self, seconds, *, deadline=None):
        """Extend shared cooldown within the optional caller budget, or fail."""
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Hithink cooldown must be a finite non-negative duration")
        if seconds == 0:
            return
        try:
            with self._connection(deadline) as db:
                begin_write(db)
                self._check_deadline(deadline)
                row = db.execute("SELECT expires_at FROM runtime_leases WHERE name=?", (RATE_KEY,)).fetchone()
                expires_at = max(database_now(db) + seconds, row[0] if row else 0.0)
                self._check_deadline(deadline)
                self._save(db, expires_at)
        except (LockNotAvailable, QueryCanceled) as error:
            raise TimeoutError("Hithink cooldown broadcast exceeded the request budget") from error

    @staticmethod
    def _save(db, expires_at):
        # This namespace is a pacing gate, not a renewable execution/job lease.
        db.execute("""INSERT INTO runtime_leases(name,owner_token,fence,expires_at)
            VALUES(?,?,1,?) ON CONFLICT(name) DO UPDATE SET
            fence=runtime_leases.fence+1, expires_at=excluded.expires_at""",
            (RATE_KEY, "hithink-rate-limit", expires_at))
