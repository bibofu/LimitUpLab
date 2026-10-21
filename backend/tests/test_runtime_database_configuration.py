"""Runtime storage must never recreate a retired local database file."""

from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest

from app.database import connect, connect_readonly, get_database_url, initialize_database
from app.storage.schema import HEAD_REVISION


def test_missing_url_rejects_legacy_path_without_opening_sqlite(monkeypatch, tmp_path):
    legacy = tmp_path / "retired.sqlite"
    monkeypatch.setenv("LIMITUPLAB_DATABASE_URL", "")
    monkeypatch.setenv("LIMITUPLAB_DATABASE_PATH", str(legacy))
    with patch("sqlite3.connect", side_effect=AssertionError("runtime opened SQLite")):
        for operation in (connect, connect_readonly):
            with pytest.raises(ValueError, match="required"):
                operation()
    assert not legacy.exists()


@pytest.mark.parametrize("value", [Path("retired.sqlite"), "sqlite:///retired.sqlite", "", " "])
def test_explicit_non_postgresql_target_is_rejected(value):
    with pytest.raises(ValueError, match="PostgreSQL"):
        connect(value)


def test_invalid_url_errors_do_not_disclose_credentials():
    secret = "must-never-appear"
    with pytest.raises(ValueError) as failure:
        get_database_url(f"postgresql://user:{secret}@localhost:invalid/database")
    assert secret not in str(failure.value)


def test_readonly_connection_validates_migrated_schema_without_writes(postgresql_database):
    with patch("sqlite3.connect", side_effect=AssertionError("runtime opened SQLite")):
        with closing(connect_readonly(postgresql_database)) as db:
            initialize_database(db)
            assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD_REVISION
            with pytest.raises(PermissionError):
                db.execute("CREATE TABLE forbidden_probe(id TEXT)")


def test_stale_schema_is_rejected_without_migrating_it(postgresql_database):
    with closing(connect(postgresql_database)) as db:
        db.execute("UPDATE alembic_version SET version_num='retired_revision'")
        db.commit()
    with closing(connect(postgresql_database)) as db:
        with pytest.raises(RuntimeError, match="Alembic"):
            initialize_database(db)
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "retired_revision"
