"""Parameter and transaction contracts shared with existing repositories."""

import pytest
from app.database import begin_write, connect, connect_readonly, initialize_database, is_postgres, table_exists
from app.storage.postgres import Record, bind_qmarks, get_engine


def test_parameters_do_not_change_literals_comments_or_quoted_identifiers():
    sql = '''SELECT ?, '?' AS "?", 'it''s ? 5%', $$?$$, $body$?$body$ /* ? /* ? */ */ -- ?
        WHERE name LIKE ?'''
    compiled, count = bind_qmarks(sql)
    assert count == 2
    assert "SELECT %s, '?'" in compiled
    assert "5%%" in compiled
    assert "$$?$$" in compiled and "/* ? /* ? */ */" in compiled
    assert compiled.endswith("LIKE %s")


def test_row_keeps_numeric_named_and_mapping_access():
    row = Record(("id", "name"), (7, "测试"))
    assert row[0] == row["id"] == 7
    assert tuple(row) == (7, "测试")
    assert dict(row) == {"id": 7, "name": "测试"}


def test_explicit_sqlite_path_wins_over_postgres_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_DATABASE_URL", "postgresql://unreachable.invalid/test")
    db = connect(tmp_path / "isolated.sqlite")
    try:
        initialize_database(db)
        assert not is_postgres(db)
        assert table_exists(db, "chat_sessions")
    finally:
        db.close()


def test_missing_readonly_sqlite_is_not_created(tmp_path):
    path = tmp_path / "missing.sqlite"
    with pytest.raises(FileNotFoundError):
        connect_readonly(path)
    assert not path.exists()


def test_postgres_transaction_rollback_and_readonly(postgresql_database):
    db = connect()
    try:
        initialize_database(db)
        assert is_postgres(db) and table_exists(db, "chat_sessions")
        assert not db.in_transaction
        begin_write(db)
        db.execute("INSERT INTO runtime_leases VALUES (?, ?, ?, ?)", ("rollback", "token", 1, 1.0))
        db.rollback()
        assert db.execute("SELECT 1 FROM runtime_leases WHERE name=?", ("rollback",)).fetchone() is None
        with db:
            db.execute("INSERT INTO runtime_leases VALUES (?, ?, ?, ?)", ("committed", "token", 1, 1.0))
        with pytest.raises(ValueError, match="SQLite-specific"):
            db.execute("BEGIN IMMEDIATE")
    finally:
        db.close()
    reader = connect_readonly()
    try:
        assert reader.execute("SELECT name FROM runtime_leases").fetchone()[0] == "committed"
        with pytest.raises(PermissionError):
            reader.execute("DELETE FROM runtime_leases")
    finally:
        reader.close()


def test_engine_transactions_are_not_autocommit_after_adapter_checkout(postgresql_database):
    db = connect()
    db.close()
    engine = get_engine()
    with pytest.raises(RuntimeError):
        with engine.begin() as connection:
            connection.exec_driver_sql("INSERT INTO runtime_leases VALUES ('rollback-engine','x',1,1)")
            raise RuntimeError("abort")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM runtime_leases").scalar() == 0
