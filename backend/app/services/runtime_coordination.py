"""Database leases with fencing for workers shared by multiple API instances."""

from contextlib import contextmanager
from dataclasses import dataclass, field
import threading
from uuid import uuid4

from app.database import begin_write, connect, get_database_url, initialize_database


class LeaseLost(RuntimeError):
    """The execution no longer owns a live database lease."""


def database_now(db):
    """Use the database clock so worker host clock skew cannot extend a lease."""
    return float(db.execute("SELECT EXTRACT(EPOCH FROM clock_timestamp())").fetchone()[0])


def initialize_coordination_schema(db):
    """Check the Alembic schema without applying request-time DDL."""
    initialize_database(db)


class LeaseStore:
    def __init__(self, database_url: str | None = None):
        self.database_url = get_database_url(database_url)
        with self.connection() as db:
            initialize_coordination_schema(db)

    @contextmanager
    def connection(self):
        db = connect(self.database_url)
        try:
            with db:
                yield db
        finally:
            db.close()

    def claim(self, name, *, ttl=60):
        if ttl <= 0:
            raise ValueError("Lease TTL must be positive")
        with self.connection() as db:
            begin_write(db)
            now = database_now(db)
            old = db.execute("SELECT * FROM runtime_leases WHERE name=?", (name,)).fetchone()
            if old and old["expires_at"] > now:
                return None
            token = uuid4().hex
            fence = old["fence"] + 1 if old else 1
            db.execute("""INSERT INTO runtime_leases(name,owner_token,fence,expires_at)
                VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET
                owner_token=excluded.owner_token,fence=excluded.fence,expires_at=excluded.expires_at""",
                (name, token, fence, now + ttl))
            return ExecutionLease(self, name, token, fence, ttl)

    def live(self, name):
        with self.connection() as db:
            row = db.execute("SELECT expires_at FROM runtime_leases WHERE name=?", (name,)).fetchone()
            return bool(row and row["expires_at"] > database_now(db))

    def renew(self, lease):
        with self.connection() as db:
            begin_write(db)
            now = database_now(db)
            result = db.execute("""UPDATE runtime_leases SET expires_at=?
                WHERE name=? AND owner_token=? AND fence=? AND expires_at>?""",
                (now + lease.ttl, lease.name, lease.token, lease.fence, now))
            return result.rowcount == 1

    def release(self, lease):
        with self.connection() as db:
            begin_write(db)
            db.execute("""UPDATE runtime_leases SET expires_at=0
                WHERE name=? AND owner_token=? AND fence=?""", (lease.name, lease.token, lease.fence))

    @staticmethod
    def assert_current(db, name, lease=None):
        """Call inside the same serialized transaction as the protected write."""
        row = db.execute("SELECT * FROM runtime_leases WHERE name=?", (name,)).fetchone()
        # Pre-migration runs and direct/offline journal users have no lease row.
        if row is None and lease is None:
            return
        if (row is None or lease is None or lease.lost.is_set()
                or row["owner_token"] != lease.token or row["fence"] != lease.fence
                or row["expires_at"] <= database_now(db)):
            raise LeaseLost("Execution lease expired or was replaced")


@dataclass
class ExecutionLease:
    store: LeaseStore
    name: str
    token: str
    fence: int
    ttl: float
    lost: threading.Event = field(default_factory=threading.Event)
    stopped: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

    def renew(self):
        if self.lost.is_set() or not self.store.renew(self):
            self.lost.set()
            return False
        return True

    def start_heartbeat(self):
        def heartbeat():
            while not self.stopped.wait(self.ttl / 3):
                try:
                    if not self.renew():
                        return
                except Exception:
                    # Fail closed: this owner must no longer publish on uncertainty.
                    self.lost.set()
                    return
        self.thread = threading.Thread(target=heartbeat, daemon=True, name="lease-heartbeat")
        self.thread.start()

    def release(self):
        self.stopped.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
        self.store.release(self)
