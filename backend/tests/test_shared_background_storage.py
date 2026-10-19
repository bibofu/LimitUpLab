"""Cross-instance scheduled jobs and news snapshots use the selected database."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from app.database import begin_write, connect
from app.services.finance_news_latest import LatestFinanceNewsStore
from app.services.runtime_coordination import LeaseLost, LeaseStore
from app.storage.job_context import ACTIVE_JOB, SharedJobLock
from test_finance_news_latest import news


def test_shared_news_survives_instance_changes_and_never_regresses(postgresql_database):
    now = datetime.now(timezone.utc)
    first = LatestFinanceNewsStore(clock=lambda: now, loader=lambda: news(now))
    assert first.read()[0].fetched_at == now
    second = LatestFinanceNewsStore(clock=lambda: now, loader=lambda: pytest.fail("Shared snapshot should be reused"))
    assert second.read()[0].model_dump() == first.read()[0].model_dump()
    with pytest.raises(ValueError, match="regress"):
        second._write_snapshot(news(now - timedelta(minutes=1)))
    assert first.read()[0].fetched_at == now


def test_shared_job_blocks_competitor_and_fences_expired_writer(postgresql_database):
    store = LeaseStore()
    with SharedJobLock("test-background") as owner:
        # Simulate another process, which does not inherit the active ContextVar.
        token = ACTIVE_JOB.set(None)
        try:
            assert store.claim("job:test-background") is None
            with store.connection() as db:
                begin_write(db)
                db.execute("UPDATE runtime_leases SET expires_at=0 WHERE name=?", (owner.lease.name,))
            successor = store.claim(owner.lease.name)
            assert successor.fence > owner.lease.fence
        finally:
            ACTIVE_JOB.reset(token)
        with closing(connect()) as db, pytest.raises(LeaseLost):
            db.execute("INSERT INTO runtime_leases VALUES ('forbidden','old',1,1)")
    successor.release()
    with closing(connect()) as db:
        assert db.execute("SELECT 1 FROM runtime_leases WHERE name='forbidden'").fetchone() is None


def test_ready_uses_postgres_without_request_time_ddl(postgresql_database):
    from app.main import app
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").json() == {"status": "ok", "database": "postgresql"}


def test_readiness_error_does_not_leak_paths_or_create_sqlite(tmp_path, monkeypatch):
    from app.main import app
    path = tmp_path / "missing-private.sqlite"
    monkeypatch.delenv("LIMITUPLAB_DATABASE_URL", raising=False)
    monkeypatch.setenv("LIMITUPLAB_DATABASE_PATH", str(path))
    with TestClient(app) as client:
        result = client.get("/ready")
    assert result.status_code == 503
    assert "private" not in result.text and not path.exists()
