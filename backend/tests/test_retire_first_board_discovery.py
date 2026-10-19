import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from app.database import connect, table_exists
from scripts import retire_first_board_discovery as maintenance
from scripts.retire_first_board_discovery import retire_discovery


# Regression scenario: cleanup is backed up idempotent and preserves prediction records.
def test_cleanup_is_backed_up_idempotent_and_preserves_prediction_records(tmp_path: Path) -> None:
    database = tmp_path / "test.sqlite"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(database) as connection:
        for table in (
            "agent_live_prediction_snapshots",
            "agent_predictions",
            "first_board_outcomes",
            "daily_review_snapshots",
            "scoring_policies",
        ):
            connection.execute(f"CREATE TABLE {table} (value TEXT)")
            connection.execute(f"INSERT INTO {table} VALUES ('protected')")
        connection.execute("CREATE TABLE first_board_discovery_snapshots (value TEXT)")
        connection.execute("INSERT INTO first_board_discovery_snapshots VALUES ('retired')")
        for table in (
            "recommendation_intelligence_changes",
            "recommendation_intelligence_current",
            "recommendation_intelligence_snapshots",
            "recommendation_prediction_finals",
        ):
            connection.execute(f"CREATE TABLE {table} (value TEXT)")
            connection.execute(f"INSERT INTO {table} VALUES ('mixed')")

    first_backup, counts = retire_discovery(database, backup_dir)
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO recommendation_intelligence_current VALUES ('new-current')")
    second_backup, second_counts = retire_discovery(database, backup_dir)

    assert first_backup.exists()
    assert second_backup is None
    assert counts == second_counts == {
        "agent_live_prediction_snapshots": 1,
        "agent_predictions": 1,
        "first_board_outcomes": 1,
        "daily_review_snapshots": 1,
        "scoring_policies": 1,
    }
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='first_board_discovery_snapshots'"
        ).fetchone() is None
        assert connection.execute("SELECT value FROM recommendation_intelligence_current").fetchone() == ("new-current",)
        for table in (
            "recommendation_intelligence_changes",
            "recommendation_intelligence_snapshots",
            "recommendation_prediction_finals",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


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


def test_postgres_without_legacy_table_keeps_all_recommendations(postgresql_database, tmp_path, monkeypatch):
    _seed_postgres_recommendations()
    missing = tmp_path / "no-sqlite.sqlite"
    monkeypatch.setenv("LIMITUPLAB_DATABASE_PATH", str(missing))

    def forbidden_backup(*args, **kwargs):
        raise AssertionError("Already retired databases must not need a backup client")

    monkeypatch.setattr(maintenance, "create_configured_backup", forbidden_backup)
    backup, counts = retire_discovery(None, tmp_path / "backups")
    assert backup is None
    assert counts["agent_predictions"] == 1
    assert not missing.exists()
    assert not (tmp_path / "backups").exists()
    with closing(connect()) as connection:
        for table in maintenance.RESET_TABLES:
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1


def _seed_postgres_legacy():
    _seed_postgres_recommendations()
    with closing(connect()) as connection, connection:
        connection.execute("CREATE TABLE first_board_discovery_snapshots (value TEXT)")
        connection.execute("INSERT INTO first_board_discovery_snapshots VALUES ('legacy')")
        return tuple(connection.execute("SELECT * FROM agent_predictions").fetchone())


def test_postgres_retirement_backs_up_before_changes_and_preserves_predictions(
    postgresql_database, tmp_path, monkeypatch,
):
    original = _seed_postgres_legacy()
    from scripts import postgres_admin

    def backup_database(output, environment):
        assert environment["PGDATABASE"]
        with closing(connect()) as reader:
            assert table_exists(reader, "first_board_discovery_snapshots")
            for table in maintenance.RESET_TABLES:
                assert reader.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"backup-created-before-mutation")
        return {"backup": str(output), "sha256": "test-sha256"}

    monkeypatch.setattr(postgres_admin, "backup_database", backup_database)
    backup, counts = retire_discovery(None, tmp_path / "backups")
    assert backup.is_file()
    assert counts["agent_predictions"] == 1
    with closing(connect()) as reader:
        assert not table_exists(reader, "first_board_discovery_snapshots")
        assert tuple(reader.execute("SELECT * FROM agent_predictions").fetchone()) == original
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
        assert tuple(reader.execute("SELECT * FROM agent_predictions").fetchone()) == original
        for table in maintenance.RESET_TABLES:
            assert reader.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1


def test_missing_explicit_archive_is_not_created(tmp_path):
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(FileNotFoundError):
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
