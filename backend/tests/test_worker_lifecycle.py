"""Workers release PostgreSQL ownership on shutdown and prove readiness."""

from datetime import datetime, timedelta, timezone
import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import worker_lifecycle as lifecycle
from app.services.runtime_coordination import LeaseStore


@pytest.fixture
def worker_name(postgresql_database, tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "marker_path", lambda name: tmp_path / f"{name}.json")
    return "test-worker-" + uuid4().hex


def started_before_now():
    return (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()


def test_sigterm_drains_before_releasing_and_restores_handlers(worker_name):
    previous = signal.getsignal(signal.SIGTERM)
    with lifecycle.WorkerLifecycle(worker_name) as worker:
        lease = worker.claim()
        assert lease is not None
        worker.ready()
        assert lifecycle.probe(worker_name, started_before_now())["ready"]

        # The handler must not release ownership while the current batch drains.
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert worker.stop_requested
        assert LeaseStore().live("job:" + worker_name)
        started = time.monotonic()
        assert worker.wait(60)
        assert time.monotonic() - started < 1

    assert signal.getsignal(signal.SIGTERM) == previous
    assert not LeaseStore().live("job:" + worker_name)
    assert not lifecycle.marker_path(worker_name).exists()
    replacement = LeaseStore().claim("job:" + worker_name)
    assert replacement is not None and replacement.token != lease.token
    replacement.release()


def test_initialization_failure_releases_claimed_lease(worker_name):
    with pytest.raises(RuntimeError, match="initialization failed"):
        with lifecycle.WorkerLifecycle(worker_name) as worker:
            assert worker.claim() is not None
            raise RuntimeError("initialization failed")
    replacement = LeaseStore().claim("job:" + worker_name)
    assert replacement is not None
    replacement.release()
    assert not lifecycle.marker_path(worker_name).exists()


def test_probe_requires_readiness_after_initialization(worker_name):
    with lifecycle.WorkerLifecycle(worker_name) as worker:
        assert worker.claim() is not None
        assert not lifecycle.probe(worker_name, started_before_now())["ready"]
        worker.ready()
        assert lifecycle.probe(worker_name, started_before_now())["ready"]
    assert not lifecycle.probe(worker_name, started_before_now())["ready"]


def test_probe_rejects_marker_from_previous_container_start(worker_name):
    with lifecycle.WorkerLifecycle(worker_name) as worker:
        assert worker.claim() is not None
        worker.ready()
        marker = json.loads(lifecycle.marker_path(worker_name).read_text())
        restarted = datetime.fromtimestamp(marker["created_at"] + 1, timezone.utc).isoformat()
        assert not lifecycle.probe(worker_name, restarted)["ready"]


@pytest.mark.parametrize("replacement_owner", [False, True])
def test_probe_rejects_expired_or_replaced_lease(worker_name, replacement_owner):
    store = LeaseStore()
    with lifecycle.WorkerLifecycle(worker_name) as worker:
        lease = worker.claim()
        assert lease is not None
        worker.ready()
        lease.release()
        replacement = store.claim("job:" + worker_name) if replacement_owner else None
        try:
            assert not lifecycle.probe(worker_name, started_before_now())["ready"]
        finally:
            if replacement is not None:
                replacement.release()


def test_probe_rejects_malformed_marker(worker_name):
    lifecycle.marker_path(worker_name).write_text("not json", encoding="utf-8")
    assert not lifecycle.probe(worker_name, started_before_now())["ready"]


def test_probe_hides_database_failure_details(worker_name, monkeypatch):
    with lifecycle.WorkerLifecycle(worker_name) as worker:
        assert worker.claim() is not None
        worker.ready()

        def unavailable():
            raise RuntimeError("postgresql://private:password@database")

        monkeypatch.setattr(lifecycle, "LeaseStore", unavailable)
        result = lifecycle.probe(worker_name, started_before_now())
        assert not result["ready"]
        assert "private" not in json.dumps(result) and "password" not in json.dumps(result)


def test_marker_cleanup_failure_still_releases_lease(worker_name):
    def cannot_remove(**kwargs):
        raise PermissionError("marker directory unavailable")

    with pytest.raises(PermissionError):
        with lifecycle.WorkerLifecycle(worker_name) as worker:
            assert worker.claim() is not None
            worker.ready()
            marker = worker.path.read_text()
            worker.path = SimpleNamespace(
                unlink=cannot_remove, read_text=lambda **kwargs: marker,
            )
    assert not LeaseStore().live("job:" + worker_name)


def test_old_owner_does_not_remove_replacement_readiness(worker_name):
    replacement = None
    try:
        with lifecycle.WorkerLifecycle(worker_name) as worker:
            old = worker.claim()
            worker.ready()
            old.release()
            replacement = LeaseStore().claim("job:" + worker_name)
            assert replacement is not None
            path = lifecycle.marker_path(worker_name)
            marker = json.loads(path.read_text())
            marker.update(token=replacement.token, fence=replacement.fence)
            path.write_text(json.dumps(marker), encoding="utf-8")
        assert lifecycle.probe(worker_name, started_before_now())["ready"]
    finally:
        if replacement is not None:
            replacement.release()


def test_duplicate_process_preserves_incumbent_readiness(worker_name):
    with lifecycle.WorkerLifecycle(worker_name) as incumbent:
        assert incumbent.claim() is not None
        incumbent.ready()
        marker = lifecycle.marker_path(worker_name).read_text()
        with lifecycle.WorkerLifecycle(worker_name) as duplicate:
            assert duplicate.claim() is None
        assert lifecycle.marker_path(worker_name).read_text() == marker
        assert lifecycle.probe(worker_name, started_before_now())["ready"]


@pytest.mark.parametrize("module_name,service_name,constructor", [
    ("run_review_worker", "review-worker", "ReviewJobService"),
    ("run_memory_extraction", "memory-extraction", "get_llm_provider"),
    ("run_memory_embeddings", "memory-embeddings", "LongTermMemoryRepository"),
])
def test_script_initialization_failure_releases_lease(
    worker_name, monkeypatch, module_name, service_name, constructor,
):
    script = importlib.import_module("scripts." + module_name)
    monkeypatch.setattr(script, "configure_runtime_environment", lambda: None)
    if service_name == "review-worker":
        monkeypatch.setattr(script, "review_settings", lambda: SimpleNamespace(enabled=True))
    elif service_name == "memory-extraction":
        monkeypatch.setattr(script, "auto_learn_mode", lambda: "safe_auto")
    else:
        monkeypatch.setattr(script, "embeddings_enabled", lambda: True)

    def fail_initialization():
        raise RuntimeError("dependency initialization failed")

    monkeypatch.setattr(script, constructor, fail_initialization)
    with pytest.raises(RuntimeError, match="dependency initialization failed"):
        script.main(["--once"])
    replacement = LeaseStore().claim("job:" + service_name)
    assert replacement is not None
    replacement.release()
    assert not lifecycle.marker_path(service_name).exists()


def test_review_main_finishes_batch_after_shutdown_signal(worker_name, monkeypatch):
    from scripts import run_review_worker as script

    monkeypatch.setattr(script, "configure_runtime_environment", lambda: None)
    monkeypatch.setattr(script, "review_settings", lambda: SimpleNamespace(enabled=True))
    batches = []

    class Service:
        def run_once(self, **kwargs):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            assert LeaseStore().live("job:review-worker")
            batches.append("completed")
            return {"sampled_runs": 0, "processed_jobs": 0, "aggregated_signals": 0}

    monkeypatch.setattr(script, "ReviewJobService", Service)
    assert script.main(["--interval", "60"]) == 0
    assert batches == ["completed"]
    assert not LeaseStore().live("job:review-worker")
    assert not lifecycle.marker_path("review-worker").exists()


def test_refresh_shutdown_does_not_retry_another_batch(worker_name, monkeypatch, tmp_path):
    from scripts import run_recommendation_refresh_loop as script

    attempts = []

    def fail_after_shutdown(*args):
        attempts.append("attempt")
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        raise RuntimeError("provider failed during shutdown")

    monkeypatch.setattr(script, "_run_refresh", fail_after_shutdown)
    with lifecycle.WorkerLifecycle(worker_name) as worker:
        assert worker.claim() is not None
        started = time.monotonic()
        script._run_refresh_with_retries(5, tmp_path / "report.json", lifecycle=worker)
        assert time.monotonic() - started < 1
    assert attempts == ["attempt"]
    assert not LeaseStore().live("job:" + worker_name)


@pytest.mark.skipif(os.name == "nt", reason="Windows terminate does not deliver POSIX SIGTERM")
@pytest.mark.parametrize("phase", ["waiting", "processing"])
def test_real_sigterm_releases_lease_after_current_batch(postgresql_database, tmp_path, phase):
    """Exercise the OS signal, separate interpreter and real PostgreSQL lease."""
    name = "signal-worker-" + uuid4().hex
    marker = tmp_path / "ready.json"
    drained = tmp_path / "drained"
    output = tmp_path / "child.log"
    script = """
from pathlib import Path
import sys, time
from app.services import worker_lifecycle as lifecycle
name, marker, drained, phase = sys.argv[1:]
lifecycle.marker_path = lambda name: Path(marker)
with lifecycle.WorkerLifecycle(name) as worker:
    assert worker.claim() is not None
    worker.ready()
    if phase == 'processing':
        # A bounded batch must finish before its lease becomes available.
        while not worker.stop_requested:
            time.sleep(.02)
        time.sleep(.2)
        Path(drained).write_text('complete')
    else:
        worker.wait(60)
"""
    with output.open("w", encoding="utf-8") as child_output:
        child = subprocess.Popen(
            [sys.executable, "-c", script, name, str(marker), str(drained), phase],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "LIMITUPLAB_DATABASE_URL": postgresql_database},
            stdout=child_output, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 15
            while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(.025)
            assert marker.exists(), output.read_text(encoding="utf-8")
            child.send_signal(signal.SIGTERM)
            assert child.wait(timeout=10) == 0, output.read_text(encoding="utf-8")
            if phase == "processing":
                assert drained.read_text() == "complete"
            assert not marker.exists()
            replacement = LeaseStore().claim("job:" + name)
            assert replacement is not None, "SIGTERM left the old 300 second lease live"
            replacement.release()
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
