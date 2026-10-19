"""Maintenance tools honor the selected backend and retain immutable research inputs."""

from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import hashlib
import json

import pytest

from app.database import connect, initialize_database
from app.models import StockDailyBar
from app.repositories.first_board_repository import SQLiteFirstBoardRepository
from app.repositories.limit_up_repository import SQLiteLimitUpRepository
from app.services.sample_data import SAMPLE_EVENTS
from scripts.audit_prediction_times import run_audit
from scripts.research_limit_up_paths import analyze
from scripts.research_pullback_consolidation import load_frozen_bars


@pytest.fixture(params=["sqlite", "postgresql"])
def source(request, tmp_path):
    if request.param == "postgresql":
        request.getfixturevalue("postgresql_database")
        path = None
    else:
        path = tmp_path / "maintenance.sqlite"
    with closing(connect(path)) as db:
        initialize_database(db)
    return path


def test_research_scripts_preserve_frozen_input_fingerprint(source, tmp_path, capsys):
    dates = []
    day = date(2026, 9, 1)
    while len(dates) < 12:
        if day.weekday() < 5:
            dates.append(day)
        day += timedelta(days=1)
    events = [SAMPLE_EVENTS[0].model_copy(update={"trade_date": day}) for day in dates]
    SQLiteLimitUpRepository(source).upsert_events(events)
    bars = [StockDailyBar(
        symbol=events[0].symbol, trade_date=day, open=10, high=10.5, low=9.5, close=10, volume=100,
        amount=1000, source="synthetic-test", created_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
    ) for day in dates]
    SQLiteFirstBoardRepository(source).upsert_daily_bars(bars)
    before = run_audit(source)
    report = analyze(source, dates[0].isoformat(), tmp_path / "research")
    frozen = load_frozen_bars(source, report)
    assert len(frozen) == len(dates)
    assert report["database_backend"] == ("postgresql" if source is None else "sqlite")
    assert (report["database_path"] is None) == (source is None)
    assert report["kind"] == "historical_exploration_not_live"
    assert "postgresql://" not in json.dumps(report)
    with closing(connect(source)) as db:
        inputs = [[dict(row) for row in db.execute(query)] for query in report["queries"]]
    digest = hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    assert digest == report["input_sha256"]
    after = run_audit(source)
    assert after["row_count"] == before["row_count"] == 0
    assert after["backup"] is None
    assert not after["applied"]
    capsys.readouterr()


def _add_prediction(source):
    with closing(connect(source)) as db, db:
        db.execute("""INSERT INTO agent_predictions
            (prediction_id,trade_date,symbol,name,score,rating,confidence,scoring_version,
             prediction_source,data_as_of,facts_json,reasons_json,risks_json,created_at)
            VALUES ('audit','2026-09-02','600000','test',80,'A',0.8,'v5','live',
                    '2026-09-03','{}','[]','[]','2026-09-03T10:35:00+08:00')""")
        return tuple(db.execute("SELECT * FROM agent_predictions WHERE prediction_id='audit'").fetchone())


def test_audit_apply_backs_up_before_annotating_and_keeps_predictions(source, tmp_path, monkeypatch):
    original = _add_prediction(source)
    if source is None:
        from scripts import postgres_admin

        def backup(output, environment):
            assert environment["PGDATABASE"]
            with closing(connect()) as db:
                assert db.execute("SELECT count(*) FROM prediction_time_audits").fetchone()[0] == 0
            return {"backup": str(output), "sha256": "fixture-digest"}

        monkeypatch.setattr(postgres_admin, "backup_database", backup)
    report = run_audit(source, apply=True, backup_directory=tmp_path / "backups")
    assert report["backup"]
    if source is not None:
        assert list((tmp_path / "backups").glob("*.sqlite"))
    else:
        assert report["backup_sha256"] == "fixture-digest"
    with closing(connect(source)) as db:
        assert tuple(db.execute("SELECT * FROM agent_predictions WHERE prediction_id='audit'").fetchone()) == original
        assert db.execute("SELECT count(*) FROM prediction_time_audits").fetchone()[0] == 1


def test_postgres_audit_backup_failure_prevents_writes(postgresql_database, monkeypatch):
    from scripts import postgres_admin
    _add_prediction(None)

    def unavailable(*_args):
        raise postgres_admin.AdminError("Backup client unavailable")

    monkeypatch.setattr(postgres_admin, "backup_database", unavailable)
    with pytest.raises(postgres_admin.AdminError, match="unavailable"):
        run_audit(apply=True)
    with closing(connect()) as db:
        assert db.execute("SELECT count(*) FROM prediction_time_audits").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM agent_predictions").fetchone()[0] == 1
