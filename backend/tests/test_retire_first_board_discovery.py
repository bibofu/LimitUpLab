from contextlib import closing
from pathlib import Path

import pytest

from app.database import connect, table_exists
from scripts import retire_first_board_discovery as maintenance
from scripts.retire_first_board_discovery import retire_discovery


# Regression scenario: cleanup is backed up idempotent and preserves prediction records.
def test_cleanup_is_backed_up_idempotent_and_preserves_prediction_records(postgresql_database, tmp_path, monkeypatch):
    original = _seed_postgres_legacy()
    backup_dir = tmp_path / "backups"
    archive = backup_dir / "snapshot.dump"

    def backup(*args, **kwargs):
        backup_dir.mkdir()
        archive.write_bytes(b"isolated-test-archive")
        return {"backup": str(archive), "sha256": "test-only"}

    monkeypatch.setattr(maintenance, "create_configured_backup", backup)
    first_backup, counts = retire_discovery(None, backup_dir)
    with closing(connect()) as connection:
        assert _protected_rows(connection) == original
    with closing(connect()) as connection, connection:
        connection.execute("INSERT INTO recommendation_intelligence_current VALUES (1,'new','2026-10-20','ready','{}')")
    second_backup, second_counts = retire_discovery(None, backup_dir)
    assert first_backup == archive and archive.is_file()
    assert second_backup is None
    assert counts == second_counts == {table: 1 for table in maintenance.PRESERVED_TABLES}
    with closing(connect()) as connection:
        assert not table_exists(connection, "first_board_discovery_snapshots")
        assert connection.execute("SELECT refresh_id FROM recommendation_intelligence_current").fetchone()[0] == "new"
        assert _protected_rows(connection) == original


def _seed_postgres_recommendations():
    with closing(connect()) as connection, connection:
        connection.execute("""INSERT INTO recommendation_intelligence_current
            VALUES (1,'test-current','2026-10-19','ready','{}')""")
        connection.execute("""INSERT INTO recommendation_intelligence_snapshots
            VALUES ('test-current','2026-10-19','ready','{}')""")
        connection.execute("""INSERT INTO recommendation_intelligence_changes
            (refresh_id,changed_at,strategy,base_trade_date,symbol,changes_json)
            VALUES ('test-current','2026-10-19','test','2026-10-16','600000','[]')""")
        connection.execute("INSERT INTO recommendation_prediction_finals VALUES ('2026-10-19','2026-10-19','{}')")
        connection.execute("""INSERT INTO agent_predictions
            (prediction_id,trade_date,symbol,name,score,rating,confidence,scoring_version,
             prediction_source,data_as_of,facts_json,reasons_json,risks_json,created_at)
            VALUES ('protected','2026-10-16','600000','test',80,'A',0.8,'v5','live',
                    '2026-10-16','{}','[]','[]','2026-10-16T10:35:00+08:00')""")
        connection.execute("""INSERT INTO agent_live_prediction_snapshots
            (trade_date,snapshot_id,scoring_version,data_as_of,top_limit,prediction_count,
             snapshot_json,content_hash,created_at)
            VALUES ('2026-10-16','protected-snapshot','v5','2026-10-16',10,1,
                    '{"fixture":"immutable-live-snapshot"}','protected-content-hash',
                    '2026-10-16T16:10:00+08:00')""")
        connection.execute("""INSERT INTO first_board_outcomes
            (base_trade_date,symbol,next_trade_date,next_open_pct,next_high_pct,next_close_pct,
             promoted_to_second_board,next_day_ready,three_day_ready,outcome_ready,outcome_version,created_at)
            VALUES ('2026-10-16','600000','2026-10-19',1.2,2.5,-0.4,0,1,0,0,
                    'synthetic-outcome','2026-10-19T16:10:00+08:00')""")
        connection.execute("""INSERT INTO daily_review_snapshots
            (as_of_date,start_date,report_json,generated_by,generated_at)
            VALUES ('2026-10-19','2026-10-16','{"fixture":"immutable-review"}',
                    'synthetic-review','2026-10-19T16:10:00+08:00')""")
        connection.execute("""INSERT INTO scoring_policies
            (version,parent_version,status,factor_weights_json,source,rationale_json,
             training_start_date,training_end_date,created_at,activated_at)
            VALUES ('v5',NULL,'champion','{"seal_quality":0.25}','synthetic-policy',
                    '["preserve existing champion"]','2026-09-01','2026-10-15',
                    '2026-10-15T16:10:00+08:00','2026-10-16T08:00:00+08:00')""")


def _protected_rows(connection):
    """Compare all stored columns, including JSON, missing values and timestamps."""
    return {
        table: [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")]
        for table in maintenance.PRESERVED_TABLES
    }


def test_postgres_without_legacy_table_keeps_all_recommendations(postgresql_database, tmp_path, monkeypatch):
    _seed_postgres_recommendations()
    with closing(connect()) as connection:
        original = _protected_rows(connection)
    missing = tmp_path / "no-sqlite.sqlite"
    monkeypatch.setenv("LIMITUPLAB_DATABASE_PATH", str(missing))

    def forbidden_backup(*args, **kwargs):
        raise AssertionError("Already retired databases must not need a backup client")

    monkeypatch.setattr(maintenance, "create_configured_backup", forbidden_backup)
    backup, counts = retire_discovery(None, tmp_path / "backups")
    assert backup is None
    assert counts == {table: 1 for table in maintenance.PRESERVED_TABLES}
    assert not missing.exists()
    assert not (tmp_path / "backups").exists()
    with closing(connect()) as connection:
        assert _protected_rows(connection) == original
        for table in maintenance.RESET_TABLES:
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1


def _seed_postgres_legacy():
    _seed_postgres_recommendations()
    with closing(connect()) as connection, connection:
        connection.execute("CREATE TABLE first_board_discovery_snapshots (value TEXT)")
        connection.execute("INSERT INTO first_board_discovery_snapshots VALUES ('legacy')")
        return _protected_rows(connection)


def test_postgres_retirement_backs_up_before_changes_and_preserves_predictions(
    postgresql_database, tmp_path, monkeypatch,
):
    original = _seed_postgres_legacy()
    from scripts import postgres_admin

    def backup_database(output, environment):
        assert environment["PGDATABASE"]
        with closing(connect()) as reader:
            assert _protected_rows(reader) == original
            assert table_exists(reader, "first_board_discovery_snapshots")
            for table in maintenance.RESET_TABLES:
                assert reader.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"backup-created-before-mutation")
        return {"backup": str(output), "sha256": "test-sha256"}

    monkeypatch.setattr(postgres_admin, "backup_database", backup_database)
    backup, counts = retire_discovery(None, tmp_path / "backups")
    assert backup.is_file()
    assert counts == {table: 1 for table in maintenance.PRESERVED_TABLES}
    with closing(connect()) as reader:
        assert not table_exists(reader, "first_board_discovery_snapshots")
        assert _protected_rows(reader) == original
        for table in maintenance.RESET_TABLES:
            assert reader.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_postgres_retirement_backup_failure_prevents_cleanup(postgresql_database, tmp_path, monkeypatch):
    original = _seed_postgres_legacy()
    from scripts import postgres_admin

    def unavailable(*args, **kwargs):
        raise postgres_admin.AdminError("Backup unavailable")

    monkeypatch.setattr(postgres_admin, "backup_database", unavailable)
    with pytest.raises(postgres_admin.AdminError, match="unavailable"):
        retire_discovery(None, tmp_path / "backups")
    with closing(connect()) as reader:
        assert table_exists(reader, "first_board_discovery_snapshots")
        assert _protected_rows(reader) == original
        for table in maintenance.RESET_TABLES:
            assert reader.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1


def test_missing_explicit_archive_is_not_created(tmp_path):
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(ValueError, match="PostgreSQL"):
        retire_discovery(missing, tmp_path / "backups")
    assert not missing.exists()


def test_retirement_cli_loads_postgres_configuration(postgresql_database, tmp_path, monkeypatch, capsys):
    _seed_postgres_recommendations()
    monkeypatch.delenv("LIMITUPLAB_DATABASE_URL")
    monkeypatch.setattr(maintenance, "load_local_env", lambda: monkeypatch.setenv(
        "LIMITUPLAB_DATABASE_URL", postgresql_database))
    assert maintenance.main(["--backup-dir", str(tmp_path / "backups")]) == 0
    assert "status=already_retired backup=None" in capsys.readouterr().out
    with closing(connect()) as reader:
        assert reader.execute("SELECT count(*) FROM recommendation_intelligence_current").fetchone()[0] == 1
