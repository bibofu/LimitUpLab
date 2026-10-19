from __future__ import annotations

import os
import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts import backup_database, postgres_admin
from scripts.backup_database import create_backup, create_configured_backup, prune_backups, prune_postgres_backups


# Regression scenario: create backup copies database and restricts permissions.
def test_create_backup_copies_database_and_restricts_permissions(tmp_path: Path) -> None:
    source_path = tmp_path / "source.sqlite"
    with sqlite3.connect(source_path) as connection:
        connection.execute("CREATE TABLE samples (value TEXT NOT NULL)")
        connection.execute("INSERT INTO samples VALUES ('kept')")

    backup_path = create_backup(
        source_path,
        tmp_path / "backups",
        now=datetime(2026, 8, 29, 3, 25, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert backup_path.name == "limituplab-20260829-032500.sqlite"
    with sqlite3.connect(backup_path) as connection:
        assert connection.execute("SELECT value FROM samples").fetchone() == ("kept",)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    if os.name == "posix":
        assert backup_path.stat().st_mode & 0o777 == 0o600


# Regression scenario: prune backups only removes older managed snapshots.
def test_prune_backups_only_removes_older_managed_snapshots(tmp_path: Path) -> None:
    names = [
        "limituplab-20260827-032500.sqlite",
        "limituplab-20260828-032500.sqlite",
        "limituplab-20260829-032500.sqlite",
    ]
    for name in names:
        (tmp_path / name).touch()
    unrelated = tmp_path / "manual.sqlite"
    unrelated.touch()

    removed = prune_backups(tmp_path, retain_count=2)

    assert [path.name for path in removed] == [names[0]]
    assert sorted(path.name for path in tmp_path.glob("limituplab-*.sqlite")) == names[1:]
    assert unrelated.exists()


def test_configured_postgres_backup_does_not_open_sqlite(monkeypatch, tmp_path):
    missing = tmp_path / "missing.sqlite"
    monkeypatch.setenv("LIMITUPLAB_DATABASE_URL", "postgresql://research:private@localhost/research")
    monkeypatch.setenv("LIMITUPLAB_DATABASE_PATH", str(missing))
    calls = []

    def capture(output, environment):
        calls.append((output, environment))
        return {"backup": str(output), "sha256": "test-digest", "archive_readable": True}

    monkeypatch.setattr(postgres_admin, "backup_database", capture)
    result = create_configured_backup(tmp_path / "backups")
    assert result["database_backend"] == "postgresql"
    assert result["sha256"] == "test-digest"
    assert calls[0][1]["PGDATABASE"] == "research"
    assert calls[0][0].suffix == ".dump"
    assert "private" not in str(result)
    assert not missing.exists()


def test_explicit_archive_overrides_postgres_for_historical_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_DATABASE_URL", "postgresql://unavailable/never-connect")
    source = tmp_path / "archive.sqlite"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE retired (value TEXT)")
        connection.execute("INSERT INTO retired VALUES ('kept')")
    result = create_configured_backup(tmp_path / "backups", database=source)
    assert result["database_backend"] == "sqlite"
    with sqlite3.connect(result["backup"]) as connection:
        assert connection.execute("SELECT value FROM retired").fetchone() == ("kept",)


def test_invalid_configured_url_fails_without_sqlite_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_DATABASE_URL", "sqlite:///do-not-fallback.sqlite")
    monkeypatch.setenv("LIMITUPLAB_DATABASE_PATH", str(tmp_path / "missing.sqlite"))
    with pytest.raises(postgres_admin.AdminError, match="must select PostgreSQL"):
        create_configured_backup(tmp_path / "backups")
    assert not (tmp_path / "backups").exists()
    assert not (tmp_path / "missing.sqlite").exists()


def _managed_dump(directory, day, *, digest=True):
    path = directory / f"limituplab-pg-202610{day:02d}T000000Z-{'a' * 32}.dump"
    path.write_bytes(f"snapshot-{day}".encode())
    path.with_suffix(".dump.sha256").write_text(
        hashlib.sha256(path.read_bytes()).hexdigest() if digest else "bad-digest", encoding="ascii")
    return path


def test_postgres_retention_preserves_sqlite_foreign_and_incomplete_backups(tmp_path):
    old, latest = (_managed_dump(tmp_path, day) for day in (17, 19))
    corrupt = _managed_dump(tmp_path, 16, digest=False)
    foreign = tmp_path / "manual.dump"
    incomplete = tmp_path / f"limituplab-pg-20261015T000000Z-{'b' * 32}.dump"
    sqlite_archive = tmp_path / "limituplab-20261018-032500.sqlite"
    for path in (foreign, incomplete, sqlite_archive):
        path.write_bytes(b"untouched")
    removed = prune_postgres_backups(tmp_path, retain_count=1)
    assert removed == [old]
    assert not old.exists()
    assert not old.with_suffix(".dump.sha256").exists()
    assert latest.is_file() and latest.with_suffix(".dump.sha256").is_file()
    assert all(path.is_file() for path in (corrupt, foreign, incomplete, sqlite_archive))


def test_backup_cli_loads_configuration_before_backend_selection(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("LIMITUPLAB_DATABASE_URL", raising=False)
    monkeypatch.setattr(backup_database, "load_local_env", lambda: monkeypatch.setenv(
        "LIMITUPLAB_DATABASE_URL", "postgresql://research@localhost/research"))
    monkeypatch.setattr(postgres_admin, "backup_database", lambda output, environment: {
        "backup": str(output), "archive_readable": True})
    assert backup_database.main(["--output-dir", str(tmp_path)]) == 0
    assert "backend=postgresql" in capsys.readouterr().out
    assert not list(tmp_path.glob("*.sqlite"))


def test_backup_cli_validates_retention_before_creating_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(backup_database, "create_configured_backup", lambda *args, **kwargs: pytest.fail("No writes expected"))
    with pytest.raises(SystemExit):
        backup_database.main(["--output-dir", str(tmp_path), "--retain-count", "0"])
    assert not list(tmp_path.iterdir())
