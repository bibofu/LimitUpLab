"""Independent callers share PostgreSQL admission and upstream cooldown state."""

from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import threading
import time

import pytest

from app.database import begin_write, connect
from app.services.hithink_rate_limit import HithinkRateLimiter, RATE_KEY


def read_state(url):
    db = connect(url)
    try:
        return dict(db.execute("SELECT * FROM runtime_leases WHERE name=?", (RATE_KEY,)).fetchone())
    finally:
        db.close()


def test_separate_instances_share_start_spacing(postgresql_database):
    first = HithinkRateLimiter(postgresql_database, min_interval_seconds=0.1)
    second = HithinkRateLimiter(postgresql_database, min_interval_seconds=0.1)
    first.wait(time.monotonic() + 3)
    original = read_state(postgresql_database)
    second.wait(time.monotonic() + 3)
    updated = read_state(postgresql_database)
    assert updated["expires_at"] - original["expires_at"] >= 0.1
    assert updated["fence"] == original["fence"] + 1


def test_threads_do_not_reserve_the_same_slot(postgresql_database):
    barrier = threading.Barrier(4)

    def request(_):
        limiter = HithinkRateLimiter(postgresql_database, min_interval_seconds=0.1)
        barrier.wait(timeout=5)
        limiter.wait(time.monotonic() + 5)
        return time.monotonic()

    with ThreadPoolExecutor(max_workers=4) as pool:
        admitted = sorted(pool.map(request, range(4)))
    assert all(right - left >= 0.08 for left, right in zip(admitted, admitted[1:]))
    assert read_state(postgresql_database)["fence"] == 4


def _request_in_process(url):
    limiter = HithinkRateLimiter(url, min_interval_seconds=0.1)
    limiter.wait(time.monotonic() + 8)
    return time.monotonic()


def test_processes_share_admission_without_memory_locks(postgresql_database):
    with multiprocessing.get_context("spawn").Pool(3) as pool:
        admitted = sorted(pool.map(_request_in_process, [postgresql_database] * 3))
    assert all(right - left >= 0.08 for left, right in zip(admitted, admitted[1:]))
    assert read_state(postgresql_database)["fence"] == 3


def test_cooldown_is_shared_and_cannot_be_shortened(postgresql_database):
    first, second = HithinkRateLimiter(postgresql_database), HithinkRateLimiter(postgresql_database)
    first.cooldown(30)
    original = read_state(postgresql_database)["expires_at"]
    second.cooldown(1)
    assert read_state(postgresql_database)["expires_at"] == original
    with pytest.raises(TimeoutError, match="cooldown"):
        second.wait(time.monotonic() + 0.1)
    assert read_state(postgresql_database)["expires_at"] == original


def test_expired_deadline_does_not_admit(postgresql_database):
    limiter = HithinkRateLimiter(postgresql_database)
    with pytest.raises(TimeoutError):
        limiter.wait(time.monotonic() - 1)
    db = connect(postgresql_database)
    try:
        assert db.execute("SELECT 1 FROM runtime_leases WHERE name=?", (RATE_KEY,)).fetchone() is None
    finally:
        db.close()


def test_contended_database_lock_obeys_deadline(postgresql_database):
    db = connect(postgresql_database)
    try:
        with db:
            begin_write(db)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(HithinkRateLimiter(postgresql_database).wait, time.monotonic() + 0.1)
                with pytest.raises(TimeoutError, match="budget"):
                    future.result(timeout=2)
            assert db.execute("SELECT 1 FROM runtime_leases WHERE name=?", (RATE_KEY,)).fetchone() is None
    finally:
        db.close()


def test_exhausted_cooldown_budget_never_opens_database(monkeypatch):
    limiter = HithinkRateLimiter()
    monkeypatch.setattr("app.services.hithink_rate_limit.connect", lambda *_: pytest.fail("No database IO after deadline"))
    with pytest.raises(TimeoutError, match="budget"):
        limiter.cooldown(10, deadline=time.monotonic() - 1)


def test_cooldown_lock_contention_times_out_without_extending_state(postgresql_database):
    limiter = HithinkRateLimiter(postgresql_database)
    limiter.cooldown(1)
    original = read_state(postgresql_database)
    db = connect(postgresql_database)
    try:
        with db:
            begin_write(db)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(limiter.cooldown, 30, deadline=time.monotonic() + 0.1)
                with pytest.raises(TimeoutError, match="budget"):
                    future.result(timeout=2)
    finally:
        db.close()
    assert read_state(postgresql_database) == original


def test_cooldown_statement_timeout_is_a_budget_error(postgresql_database, monkeypatch):
    limiter = HithinkRateLimiter(postgresql_database)
    limiter.cooldown(1)
    original = read_state(postgresql_database)
    monkeypatch.setattr("app.services.hithink_rate_limit.database_now",
                        lambda db: db.execute("SELECT pg_sleep(1)").fetchone()[0])
    with pytest.raises(TimeoutError, match="budget"):
        limiter.cooldown(30, deadline=time.monotonic() + 0.1)
    assert read_state(postgresql_database) == original


def test_sleep_runs_after_transaction_and_connection_are_released(postgresql_database):
    first = HithinkRateLimiter(postgresql_database, min_interval_seconds=0.1)
    first.wait(time.monotonic() + 2)
    observed = []

    def sleep(delay):
        db = connect(postgresql_database)
        try:
            with db:
                db.execute("BEGIN")
                db.execute("SET LOCAL lock_timeout = '100ms'")
                begin_write(db)
                observed.append(True)
        finally:
            db.close()
        time.sleep(delay)

    HithinkRateLimiter(postgresql_database, min_interval_seconds=0.1, sleep=sleep).wait(time.monotonic() + 2)
    assert observed


@pytest.mark.parametrize("configured", ["bad", "nan", "inf", "0", "-1", "61"])
def test_invalid_environment_interval_uses_safe_default(monkeypatch, configured):
    monkeypatch.setenv("LIMITUPLAB_HITHINK_MIN_INTERVAL_SECONDS", configured)
    assert HithinkRateLimiter().interval == 1.0


def test_configured_interval_is_used(monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_HITHINK_MIN_INTERVAL_SECONDS", "2.5")
    assert HithinkRateLimiter().interval == 2.5
