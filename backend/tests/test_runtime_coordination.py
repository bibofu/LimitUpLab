"""Run against both retained SQLite and a migrated PostgreSQL test database."""

from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import os
import threading
import time
from uuid import uuid4

import pytest

from app.database import begin_write
from app.services.runtime_coordination import ExecutionLease, LeaseLost, LeaseStore, database_now


@pytest.fixture(params=["sqlite", "postgresql"])
def lease_store(request, tmp_path, monkeypatch):
    if request.param == "postgresql":
        request.getfixturevalue("postgresql_database")
        return LeaseStore()
    return LeaseStore(tmp_path / "leases.sqlite")


def test_only_one_instance_can_claim(lease_store):
    name = "test:" + uuid4().hex
    gate = threading.Barrier(8)
    def claim(_):
        gate.wait(timeout=10)
        return LeaseStore(lease_store.database_path).claim(name)
    with ThreadPoolExecutor(max_workers=8) as workers:
        claims = list(workers.map(claim, range(8)))
    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    assert lease_store.live(name)
    winners[0].release()
    assert not lease_store.live(name)


def test_takeover_fences_old_owner_and_release(lease_store):
    name = "test:" + uuid4().hex
    old = lease_store.claim(name)
    with lease_store.connection() as db:
        begin_write(db)
        db.execute("UPDATE runtime_leases SET expires_at=0 WHERE name=?", (name,))
    new = LeaseStore(lease_store.database_path).claim(name)
    assert new.fence == old.fence + 1 and new.token != old.token
    assert not old.renew()
    old.release()
    assert lease_store.live(name)
    with lease_store.connection() as db:
        begin_write(db)
        with pytest.raises(LeaseLost):
            lease_store.assert_current(db, name, old)
        lease_store.assert_current(db, name, new)
    new.release()


def test_managed_lease_requires_current_token(lease_store):
    name = "test:" + uuid4().hex
    lease = lease_store.claim(name)
    with lease_store.connection() as db:
        begin_write(db)
        with pytest.raises(LeaseLost):
            lease_store.assert_current(db, name)
        lease_store.assert_current(db, name, lease)
    lease.release()
    with lease_store.connection() as db:
        begin_write(db)
        with pytest.raises(LeaseLost):
            lease_store.assert_current(db, name, lease)


def test_heartbeat_renews_using_database_time(lease_store):
    name = "test:" + uuid4().hex
    lease = lease_store.claim(name, ttl=1)
    lease.start_heartbeat()
    try:
        with lease_store.connection() as db:
            original = db.execute("SELECT expires_at FROM runtime_leases WHERE name=?", (name,)).fetchone()[0]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with lease_store.connection() as db:
                current = db.execute("SELECT expires_at FROM runtime_leases WHERE name=?", (name,)).fetchone()[0]
                assert abs(database_now(db) - time.time()) < 10
            if current > original:
                break
            time.sleep(.05)
        assert current > original
        assert not lease.lost.is_set()
    finally:
        lease.release()


def test_expired_owner_cannot_resurrect_itself(lease_store):
    name = "test:" + uuid4().hex
    lease = lease_store.claim(name)
    with lease_store.connection() as db:
        db.execute("UPDATE runtime_leases SET expires_at=0 WHERE name=?", (name,))
    assert not lease.renew()
    with lease_store.connection() as db:
        begin_write(db)
        with pytest.raises(LeaseLost):
            lease_store.assert_current(db, name, lease)


def _claim_in_process(arguments):
    path, url, name = arguments
    if url:
        os.environ["LIMITUPLAB_DATABASE_URL"] = url
    store = LeaseStore(path)
    lease = store.claim(name)
    return (lease.token, lease.fence) if lease is not None else None


def test_claim_is_exclusive_across_processes(lease_store):
    """Independent interpreters cannot rely on the router or connection's locks."""
    name = "process:" + uuid4().hex
    arguments = (lease_store.database_path, lease_store.database_url, name)
    with multiprocessing.get_context("spawn").Pool(4) as processes:
        results = processes.map(_claim_in_process, [arguments] * 4)
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    token, fence = winners[0]
    ExecutionLease(lease_store, name, token, fence, 60).release()

