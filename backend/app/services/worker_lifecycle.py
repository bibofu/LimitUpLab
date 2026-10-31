"""Cooperative worker shutdown and process-specific PostgreSQL readiness."""

from contextlib import AbstractContextManager
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import tempfile
import time

from app.services.runtime_coordination import LeaseStore, database_now


def marker_path(name: str) -> Path:
    if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in name):
        raise ValueError("Invalid worker name")
    return Path(tempfile.gettempdir()) / f"limituplab-worker-{name}.json"


class WorkerLifecycle(AbstractContextManager):
    """Finish the active batch before releasing ownership; never do I/O in a signal handler."""

    def __init__(self, name: str):
        self.name = name
        self.path = marker_path(name)
        self.stop_requested = False
        self.lease = None
        self._owns_lease = False
        self._marker_written = False
        self._handlers = {}

    def _stop(self, signum, frame):
        # Event.set() also takes a lock: a plain flag is safe during arbitrary I/O.
        self.stop_requested = True

    def __enter__(self):
        for signum in (signal.SIGTERM, signal.SIGINT):
            self._handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, self._stop)
        return self

    def claim(self, *, ttl=300):
        self.lease = LeaseStore().claim("job:" + self.name, ttl=ttl)
        if self.lease is not None:
            self._owns_lease = True
            self.lease.start_heartbeat()
        return self.lease

    def ready(self, lease=None):
        if lease is not None:
            self.lease = lease
        if self.lease is None or self.lease.lost.is_set() or self.stop_requested:
            return
        payload = {"name": self.lease.name, "token": self.lease.token,
                   "fence": self.lease.fence, "pid": os.getpid(), "created_at": time.time()}
        temporary = self.path.with_suffix(f".{os.getpid()}.tmp")
        try:
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(self.path)
            self._marker_written = True
        finally:
            temporary.unlink(missing_ok=True)

    def wait(self, seconds: float) -> bool:
        deadline = time.monotonic() + max(0.0, seconds)
        while not self.stop_requested and time.monotonic() < deadline:
            if self.lease is not None and self.lease.lost.is_set():
                break
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
        return self.stop_requested

    def __exit__(self, kind, value, traceback):
        try:
            if self._owns_lease:
                self.lease.release()
        finally:
            try:
                if self._marker_written:
                    try:
                        marker = json.loads(self.path.read_text(encoding="utf-8"))
                    except FileNotFoundError:
                        marker = {}
                    if (marker.get("token"), marker.get("fence")) == (self.lease.token, self.lease.fence):
                        self.path.unlink(missing_ok=True)
            finally:
                for signum, handler in self._handlers.items():
                    signal.signal(signum, handler)


def probe(name: str, started_at: str) -> dict:
    """Fail closed unless this container start published the current live lease."""
    try:
        marker = json.loads(marker_path(name).read_text(encoding="utf-8"))
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00")).timestamp()
        if marker["created_at"] < started or marker["name"] != "job:" + name:
            return {"ready": False, "reason": "stale readiness marker"}
        store = LeaseStore()
        with store.connection() as db:
            row = db.execute("SELECT * FROM runtime_leases WHERE name=?", (marker["name"],)).fetchone()
            ready = bool(row and row["owner_token"] == marker["token"]
                         and row["fence"] == marker["fence"]
                         and row["expires_at"] > database_now(db))
        return {"ready": ready, "reason": "lease owned" if ready else "lease absent, expired or replaced"}
    except FileNotFoundError:
        return {"ready": False, "reason": "readiness marker missing"}
    except Exception:
        # Database exceptions may contain credentials. The release log only needs a safe diagnosis.
        return {"ready": False, "reason": "readiness probe unavailable"}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["probe"])
    parser.add_argument("name")
    parser.add_argument("started_at")
    args = parser.parse_args()
    print(json.dumps(probe(args.name, args.started_at)))
