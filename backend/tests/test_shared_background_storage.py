"""Cross-instance scheduled jobs and news snapshots use the selected database."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
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


@pytest.mark.parametrize("operation", ["intraday_warmup", "intraday_history", "candidate_evidence"])
def test_background_thread_workers_preserve_expired_job_fencing(postgresql_database, operation):
    from app.services.stock_kline import load_stock_intraday_history
    from app.services.recommendation_intelligence import _collect_candidate_evidence
    from scripts.update_daily_data import warm_latest_intraday_cache
    day = datetime.now(timezone.utc).date()
    observed = []

    def stale_collector(*args, **kwargs):
        observed.append(ACTIVE_JOB.get())
        with closing(connect()) as db, db:
            db.execute("INSERT INTO runtime_leases VALUES ('stale-thread-write','old',1,1)")
        return []

    with SharedJobLock("thread-fencing") as owner:
        token = ACTIVE_JOB.set(None)
        try:
            with closing(connect()) as db, db:
                db.execute("UPDATE runtime_leases SET expires_at=0 WHERE name=?", (owner.lease.name,))
        finally:
            ACTIVE_JOB.reset(token)
        if operation == "intraday_warmup":
            warm_latest_intraday_cache(events=[SimpleNamespace(trade_date=day, symbol="000001")],
                                       trade_date=day, repository=None, loader=stale_collector)
        elif operation == "intraday_history":
            load_stock_intraday_history(symbol="000001", trade_dates=[day], period=1,
                                        daily_bars=[], collector=stale_collector)
        else:
            _collect_candidate_evidence([SimpleNamespace(symbol="000001", name="fixture")],
                refreshed_at=datetime.now(timezone.utc), previous_financials={}, previous_news={},
                news_collector=stale_collector, financial_collector=lambda _: [], max_workers=1)
        assert observed == [owner.lease]
    with closing(connect()) as db:
        assert db.execute("SELECT 1 FROM runtime_leases WHERE name='stale-thread-write'").fetchone() is None


def test_sql_commit_cannot_bypass_job_fencing(postgresql_database, monkeypatch):
    from app.services import runtime_coordination
    with SharedJobLock("sql-commit-fencing"):
        with closing(connect()) as db:
            db.execute("INSERT INTO runtime_leases VALUES ('expired-commit','old',1,1)")
            now = runtime_coordination.database_now(db)
            with monkeypatch.context() as patcher:
                patcher.setattr(runtime_coordination, "database_now", lambda _: now + 120)
                with pytest.raises(LeaseLost):
                    db.execute("COMMIT WORK;")
            db.execute("ROLLBACK;")
            assert not db.in_transaction
            assert db.execute("SELECT 1 FROM runtime_leases WHERE name='expired-commit'").fetchone() is None
