"""Import preservation checks; real PostgreSQL tests use isolated schemas."""

import hashlib
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.storage.migration import MigrationError, migrate_sqlite, read_source, source_rows, validate_source
from app.storage.schema import baseline_metadata, metadata, upgrade_schema


@pytest.fixture
def source_database(tmp_path):
    """Build a v12 source with original SQLite rowid, preserving literal text."""
    manifest_path = Path(__file__).resolve().parents[1] / "app/storage/schema_v0001.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    path = tmp_path / "source.sqlite"
    with sqlite3.connect(path) as db:
        for name, definition in manifest.items():
            columns = []
            for column, kind, required, default in definition["columns"]:
                if column == "legacy_order":
                    continue
                item = f'"{column}" {kind}' + (" NOT NULL" if required else "")
                if default is not None:
                    item += " DEFAULT " + default
                columns.append(item)
            columns.append("PRIMARY KEY (" + ",".join(definition["primary_key"]) + ")")
            db.execute(f'CREATE TABLE "{name}" ({",".join(columns)})')
        db.execute("PRAGMA user_version=12")
        db.execute("INSERT INTO chat_sessions VALUES('s','owner','研究','2026-10-19','2026-10-19',NULL)")
        for message_id in ("z", "a"):
            db.execute("INSERT INTO chat_messages VALUES(?, 's', 'user', '问题', 'success', NULL, ?, 'same-time')",
                       (message_id, '{ "z":1, "a":2 }'))
        db.execute("INSERT INTO react_runs(run_id,owner_id,session_id,message_key,request_json,request_hash,started_at) "
                   "VALUES('run','owner','s','message','{}','hash','2026-10-19')")
        db.execute("INSERT INTO react_calls VALUES('run','call-z','signature','{}')")
        db.execute("INSERT INTO react_calls VALUES('run','call-a','signature','{}')")
        snapshot = '{ "value": 1.00, "中文": "原文" }'
        db.execute("INSERT INTO agent_live_prediction_snapshots VALUES('2026-10-19','snap','v1','2026-10-19',1,1,?,?, 'now')",
                   (snapshot, hashlib.sha256(snapshot.encode()).hexdigest()))
    return path


@pytest.fixture
def pg_engine():
    url = os.getenv("LIMITUPLAB_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set LIMITUPLAB_TEST_DATABASE_URL for isolated PostgreSQL integration tests")
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    schema = "migration_test_" + uuid4().hex
    admin = sa.create_engine(url, poolclass=sa.pool.NullPool)
    with admin.begin() as db:
        db.execute(sa.schema.CreateSchema(schema))
    engine = sa.create_engine(url, poolclass=sa.pool.NullPool,
                              connect_args={"options": f"-csearch_path={schema}"})
    try:
        upgrade_schema(engine)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(sa.schema.DropSchema(schema, cascade=True))
        admin.dispose()


def test_readonly_source_and_legacy_table_acknowledgment(source_database):
    with sqlite3.connect(source_database) as db:
        db.execute("CREATE TABLE historical_extra(id TEXT)")
        db.execute("INSERT INTO historical_extra VALUES('preserved')")
    before = source_database.read_bytes()
    with read_source(source_database) as db:
        with pytest.raises(MigrationError, match="historical_extra"):
            validate_source(db)
        assert validate_source(db, ["historical_extra"])[1] == {"historical_extra"}
        with pytest.raises(sqlite3.OperationalError):
            db.execute("DELETE FROM chat_sessions")
    assert source_database.read_bytes() == before


def test_source_rowid_and_json_are_not_reconstructed(source_database):
    with read_source(source_database) as db:
        rows = list(source_rows(db, metadata.tables["chat_messages"]))
        assert [(row["message_id"], row["legacy_order"]) for row in rows] == [("a", 2), ("z", 1)]
        assert rows[0]["metadata_json"] == '{ "z":1, "a":2 }'


def test_source_missing_columns_fail_closed(source_database):
    with sqlite3.connect(source_database) as db:
        db.execute("ALTER TABLE chat_sessions ADD COLUMN unhandled TEXT")
    with read_source(source_database) as db, pytest.raises(MigrationError, match="extra=.*unhandled"):
        validate_source(db)


def test_source_multiple_champions_requires_explicit_repair(source_database):
    with sqlite3.connect(source_database) as db:
        for version in ("v1", "v2"):
            db.execute("INSERT INTO scoring_policies(version,status,factor_weights_json,source,rationale_json,created_at) "
                       "VALUES(?,'champion','{}','test','{}','now')", (version,))
    with read_source(source_database) as db, pytest.raises(MigrationError, match="multiple champion"):
        validate_source(db)


def test_import_preserves_rows_text_hashes_order_and_sequences(source_database, pg_engine):
    before = source_database.read_bytes()
    report = migrate_sqlite(source_database, pg_engine, batch_size=1)
    assert report["status"] == "verified_and_committed"
    assert report["tables"]["chat_messages"]["rows"] == 2
    assert source_database.read_bytes() == before
    with pg_engine.begin() as db:
        rows = db.execute(sa.text("SELECT message_id,metadata_json FROM chat_messages ORDER BY legacy_order")).all()
        assert [row[0] for row in rows] == ["z", "a"]
        assert rows[0][1] == '{ "z":1, "a":2 }'
        raw, digest = db.execute(sa.text("SELECT snapshot_json,content_hash FROM agent_live_prediction_snapshots")).one()
        assert hashlib.sha256(raw.encode()).hexdigest() == digest
        sequence = db.scalar(sa.text("INSERT INTO chat_messages(message_id,session_id,role,content,status,metadata_json,created_at) "
                                    "VALUES('next','s','user','new','success','{}','same-time') RETURNING legacy_order"))
        assert sequence == 3
    with pytest.raises(MigrationError, match="not empty"):
        migrate_sqlite(source_database, pg_engine)


def test_import_failure_rolls_back_all_destination_rows(source_database, pg_engine):
    with sqlite3.connect(source_database) as db:
        db.execute("UPDATE chat_messages SET session_id='missing'")
    with pytest.raises(sa.exc.IntegrityError):
        migrate_sqlite(source_database, pg_engine)
    with pg_engine.connect() as db:
        assert all(db.scalar(sa.select(sa.func.count()).select_from(table)) == 0
                   for table in baseline_metadata().tables.values())


def test_postgresql_triggers_block_evidence_update(pg_engine):
    with pg_engine.begin() as db:
        db.execute(sa.text("INSERT INTO chat_sessions VALUES('s','owner','title','now','now',NULL)"))
        db.execute(sa.text("INSERT INTO react_runs(run_id,owner_id,session_id,message_key,request_json,request_hash,started_at) "
                           "VALUES('r','owner','s','m','{}','hash','now')"))
        db.execute(sa.text("INSERT INTO quality_evidence_bundles VALUES('b','r','a','h','completion','{}','now')"))
    with pytest.raises(sa.exc.IntegrityError), pg_engine.begin() as db:
        db.execute(sa.text("UPDATE quality_evidence_bundles SET payload_json='changed' WHERE bundle_id='b'"))


def test_postgresql_enforces_single_champion(pg_engine):
    with pg_engine.begin() as db:
        db.execute(sa.text("INSERT INTO scoring_policies(version,status,factor_weights_json,source,rationale_json,created_at) "
                           "VALUES('v1','champion','{}','test','{}','now')"))
    with pytest.raises(sa.exc.IntegrityError), pg_engine.begin() as db:
        db.execute(sa.text("INSERT INTO scoring_policies(version,status,factor_weights_json,source,rationale_json,created_at) "
                           "VALUES('v2','champion','{}','test','{}','now')"))
