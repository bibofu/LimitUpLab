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


def test_escaped_postgres_strings_keep_literal_question_marks():
    compiled, count = bind_qmarks(r"SELECT E'it\'s ? 50%', ?, e'\\?', '?' ")
    assert count == 1
    assert compiled == r"SELECT E'it\'s ? 50%%', %s, e'\\?', '?' "


def test_explicit_postgres_url_wins_over_environment(postgresql_database, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_DATABASE_URL", "postgresql://unreachable.invalid/test")
    db = connect(postgresql_database)
    try:
        initialize_database(db)
        assert is_postgres(db)
        assert table_exists(db, "chat_sessions")
    finally:
        db.close()


def test_readonly_sqlite_path_is_rejected_without_creating_file(tmp_path):
    path = tmp_path / "missing.sqlite"
    with pytest.raises((TypeError, ValueError)):
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


def test_postgres_escaped_string_and_sql_transaction_controls(postgresql_database):
    db = connect()
    try:
        assert tuple(db.execute(r"SELECT E'it\'s ? 50%', ?", (7,)).fetchone()) == ("it's ? 50%", 7)
        db.execute("INSERT INTO runtime_leases VALUES ('rolled-back','x',1,1)")
        for statement in ("COMMIT AND CHAIN", "/* outer /* nested */ */ COMMIT", "COMMIT; SELECT 1"):
            with pytest.raises(ValueError, match="transaction control"):
                db.execute(statement)
        db.execute("ROLLBACK;")
        assert not db.in_transaction and not db._write_locked
        db.execute("INSERT INTO runtime_leases VALUES ('sql-committed','x',1,1)")
        db.execute("COMMIT TRANSACTION;")
        assert not db.in_transaction and not db._write_locked
        assert [row[0] for row in db.execute("SELECT name FROM runtime_leases")] == ["sql-committed"]
    finally:
        db.close()


def test_readonly_ctes_and_comments_keep_database_write_protection(postgresql_database):
    from psycopg.errors import ReadOnlySqlTransaction
    reader = connect_readonly()
    try:
        assert reader.execute("WITH value AS (SELECT 7 AS n) SELECT n FROM value").fetchone()[0] == 7
        assert reader.execute("/* read /* nested comment */ */ SELECT ?", (9,)).fetchone()[0] == 9
        assert reader.execute("-- leading comment\nSELECT 11").fetchone()[0] == 11
        write_cte = """WITH changed AS (
            INSERT INTO runtime_leases VALUES ('readonly-cte','x',1,1) RETURNING name
        ) SELECT name FROM changed"""
        with pytest.raises(ReadOnlySqlTransaction):
            reader.execute(write_cte)
        reader.rollback()
        # Explicitly ending a snapshot must not make the next CTE writable.
        with pytest.raises(ReadOnlySqlTransaction):
            reader.execute(write_cte)
    finally:
        reader.close()
    db = connect()
    try:
        assert db.execute("SELECT 1 FROM runtime_leases WHERE name='readonly-cte'").fetchone() is None
    finally:
        db.close()
