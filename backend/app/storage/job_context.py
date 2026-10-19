"""Fence database writes made by a scheduled job that lost ownership."""

from contextvars import ContextVar

ACTIVE_JOB = ContextVar("database_job_lease", default=None)


def assert_job_owner(db):
    lease = ACTIVE_JOB.get()
    if lease is not None:
        lease.store.assert_current(db, lease.name, lease)


class SharedJobLock:
    """A renewable cross-host lease; writes are checked within their transaction."""
    def __init__(self, name, database_path=None, *, ttl=60):
        self.name, self.database_path, self.ttl = name, database_path, ttl

    def __enter__(self):
        from app.services.runtime_coordination import LeaseStore
        self.lease = LeaseStore(self.database_path).claim("job:" + self.name, ttl=self.ttl)
        if self.lease is None:
            raise RuntimeError("Scheduled job is already running: " + self.name)
        try:
            self.lease.start_heartbeat()
        except Exception:
            self.lease.release()
            raise
        self.token = ACTIVE_JOB.set(self.lease)
        return self

    def touch(self):
        from app.services.runtime_coordination import LeaseLost
        if not self.lease.renew():
            raise LeaseLost("Scheduled job lost its lease")

    def __exit__(self, kind, value, traceback):
        ACTIVE_JOB.reset(self.token)
        self.lease.release()
