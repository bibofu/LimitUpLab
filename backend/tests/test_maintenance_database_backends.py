"""Maintenance tools honor the selected PostgreSQL backend."""

from contextlib import closing

import pytest

from app.database import connect
from scripts.audit_prediction_times import run_audit


@pytest.fixture
def source(postgresql_database):
    return None


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
    from scripts import postgres_admin

    def backup(output, environment):
        assert environment["PGDATABASE"]
        with closing(connect()) as db:
            assert db.execute("SELECT count(*) FROM prediction_time_audits").fetchone()[0] == 0
        return {"backup": str(output), "sha256": "fixture-digest"}

    monkeypatch.setattr(postgres_admin, "backup_database", backup)
    report = run_audit(source, apply=True, backup_directory=tmp_path / "backups")
    assert report["backup"]
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
