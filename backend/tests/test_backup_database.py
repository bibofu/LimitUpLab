from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.backup_database import create_backup, prune_backups


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
