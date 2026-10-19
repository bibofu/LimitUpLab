"""Sample import selects PostgreSQL and never silently replaces existing data."""

from contextlib import closing
import sqlite3

import pytest

from app.database import connect_readonly
from app.repositories.limit_up_repository import SQLiteLimitUpRepository
from app.services.sample_data import SAMPLE_EVENTS
from scripts.import_sample_data import import_samples


def test_postgres_sample_import_needs_no_sqlite(postgresql_database, tmp_path, monkeypatch):
    absent = tmp_path / "no-sqlite" / "limituplab.sqlite"
    monkeypatch.setenv("LIMITUPLAB_DATABASE_PATH", str(absent))

    def forbid_sqlite(*args, **kwargs):
        raise AssertionError("Configured PostgreSQL must not open SQLite")

    monkeypatch.setattr(sqlite3, "connect", forbid_sqlite)
    assert import_samples() == "postgresql"
    assert {event.symbol for event in SQLiteLimitUpRepository().list_events()} == {
        event.symbol for event in SAMPLE_EVENTS
    }
    assert not absent.exists()


def test_sample_import_preserves_existing_events(postgresql_database):
    original = SAMPLE_EVENTS[0].model_copy(update={"name": "Existing research data"})
    repository = SQLiteLimitUpRepository()
    repository.upsert_events([original])
    with pytest.raises(RuntimeError, match="already has events"):
        import_samples()
    assert repository.list_events() == [original]
    assert import_samples(replace_existing=True) == "postgresql"
    with closing(connect_readonly()) as database:
        assert database.execute("SELECT COUNT(*) FROM limit_up_events").fetchone()[0] == len(SAMPLE_EVENTS)
    assert repository.list_events()[0].name != "Existing research data"
