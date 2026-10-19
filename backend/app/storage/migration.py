"""Transactional, source-read-only SQLite to PostgreSQL import and verification."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import sqlalchemy as sa

from app.storage.schema import BASELINE_REVISION, HEAD_REVISION, assert_schema_current, baseline_metadata, metadata


class MigrationError(ValueError):
    """An actionable validation failure without connection secrets or row data."""


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


@contextmanager
def read_source(path: Path):
    """Pin one SQLite read transaction, including committed WAL contents."""
    path = path.resolve(strict=True)
    source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, isolation_level=None)
    source.row_factory = sqlite3.Row
    try:
        source.execute("PRAGMA query_only=ON")
        source.execute("BEGIN")
        # First read pins the snapshot before inspecting schema or copying rows.
        source.execute("SELECT count(*) FROM sqlite_master").fetchone()
        yield source
    finally:
        source.rollback()
        source.close()


def inventory(source):
    return {row["name"]: row["sql"] for row in source.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )}


def validate_source(source, allowed_legacy_tables=()):
    tables = inventory(source)
    required = set(baseline_metadata().tables)
    missing = required - set(tables)
    if missing:
        raise MigrationError("Source lacks required tables: " + ", ".join(sorted(missing)))
    legacy = set(tables) - set(metadata.tables)
    unacknowledged = legacy - set(allowed_legacy_tables)
    if unacknowledged:
        raise MigrationError("Legacy tables need explicit --retain-legacy-table acknowledgment: "
                             + ", ".join(sorted(unacknowledged)))
    unknown = set(allowed_legacy_tables) - legacy
    if unknown:
        raise MigrationError("Acknowledged legacy tables are not legacy source tables: " + ", ".join(sorted(unknown)))
    for name in set(tables) & set(metadata.tables):
        actual = {row["name"] for row in source.execute(f"PRAGMA table_info({quote_identifier(name)})")}
        expected = set(metadata.tables[name].c.keys())
        if name in {"chat_messages", "react_calls"}:
            expected.discard("legacy_order")
            actual.discard("legacy_order")
        if actual != expected:
            raise MigrationError(f"Source columns differ for {name}: missing={sorted(expected - actual)}, "
                                 f"extra={sorted(actual - expected)}")
    if source.execute("SELECT count(*) FROM scoring_policies WHERE status='champion'").fetchone()[0] > 1:
        raise MigrationError("Source has multiple champion policies; resolve explicitly before importing")
    return tables, legacy


def source_rows(source, table):
    columns = list(table.c)
    actual = {row["name"] for row in source.execute(f"PRAGMA table_info({quote_identifier(table.name)})")}
    expressions = ["rowid AS legacy_order" if column.name == "legacy_order" and column.name not in actual
                   else quote_identifier(column.name) for column in columns]
    order = [quote_identifier(column.name) + (" COLLATE BINARY" if isinstance(column.type, sa.Text) else "")
             for column in table.primary_key.columns]
    cursor = source.execute(f"SELECT {', '.join(expressions)} FROM {quote_identifier(table.name)} "
                            f"ORDER BY {', '.join(order)}")
    for row in cursor:
        result = {}
        for column in columns:
            value = row[column.name]
            if value is not None and isinstance(column.type, sa.Double):
                value = float(value)
            result[column.name] = value
        yield result


def fingerprint(rows, columns):
    digest, count = hashlib.sha256(), 0
    for row in rows:
        payload = json.dumps([row[column] for column in columns], ensure_ascii=False,
                             allow_nan=False, separators=(",", ":")).encode("utf-8")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        count += 1
    return {"rows": count, "sha256": digest.hexdigest()}


def legacy_inventory(source, names, definitions):
    result = {}
    for name in sorted(names):
        columns = source.execute(f"PRAGMA table_info({quote_identifier(name)})").fetchall()
        primary = sorted((column for column in columns if column["pk"]), key=lambda column: column["pk"])
        order = ", ".join(quote_identifier(column["name"]) for column in primary) if primary else "rowid"
        cursor = source.execute(f"SELECT * FROM {quote_identifier(name)} ORDER BY {order}")
        digest, count = hashlib.sha256(), 0
        for row in cursor:
            # Archive checksums cover blobs too; these records remain in SQLite.
            encoded = json.dumps(list(row), ensure_ascii=False, separators=(",", ":"),
                                 default=lambda value: {"bytes_hex": value.hex()}).encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
            count += 1
        result[name] = {"rows": count, "sha256": digest.hexdigest(), "schema_sql": definitions[name],
                        "disposition": "retained in original SQLite source; not imported"}
    return result


def target_fingerprint(connection, table):
    ordering = [column.collate("C") if isinstance(column.type, sa.Text) else column
                for column in table.primary_key.columns]
    rows = connection.execute(sa.select(table).order_by(*ordering)).mappings()
    return fingerprint(rows, table.c.keys())


def reset_identity_sequences(connection):
    for table in metadata.tables.values():
        for column in table.c:
            if column.identity is None:
                continue
            maximum = connection.scalar(sa.select(sa.func.max(column)))
            connection.execute(sa.text("SELECT setval(pg_get_serial_sequence(:table_name, :column_name), "
                                       ":value, :called)"),
                               {"table_name": quote_identifier(table.name), "column_name": column.name,
                                "value": maximum if maximum is not None else 1, "called": maximum is not None})


def migrate_sqlite(source_path, engine, *, allowed_legacy_tables=(), batch_size=1000):
    """Import into an empty, upgraded target in one transaction; never modify source."""
    if engine.dialect.name != "postgresql":
        raise MigrationError("Destination must be PostgreSQL")
    if not 1 <= batch_size <= 10_000:
        raise MigrationError("batch_size must be between 1 and 10000")
    source_path = Path(source_path).resolve(strict=True)
    with read_source(source_path) as source:
        definitions, legacy = validate_source(source, allowed_legacy_tables)
        report = {"source": str(source_path), "source_retained": True,
                  "source_schema_version": source.execute("PRAGMA user_version").fetchone()[0],
                  "target_revision": HEAD_REVISION, "baseline_revision": BASELINE_REVISION,
                  "created_at": datetime.now(timezone.utc).isoformat(), "tables": {},
                  "legacy_tables": legacy_inventory(source, legacy, definitions)}
        with engine.begin() as connection:
            assert_schema_current(connection)
            connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
            # Lock every destination table before checking emptiness, preventing
            # an app writer from racing the importer between check and insert.
            names = ", ".join(quote_identifier(name) for name in sorted(metadata.tables))
            connection.execute(sa.text(f"LOCK TABLE {names} IN ACCESS EXCLUSIVE MODE"))
            for table in metadata.tables.values():
                if connection.scalar(sa.select(sa.func.count()).select_from(table)):
                    raise MigrationError(f"Destination table is not empty: {table.name}")
            for table in metadata.sorted_tables:
                if table.name not in definitions:
                    report["tables"][table.name] = {"rows": 0, "status": "new coordination table"}
                    continue
                batch = []
                for row in source_rows(source, table):
                    batch.append(row)
                    if len(batch) == batch_size:
                        connection.execute(table.insert(), batch)
                        batch.clear()
                if batch:
                    connection.execute(table.insert(), batch)
                expected = fingerprint(source_rows(source, table), table.c.keys())
                actual = target_fingerprint(connection, table)
                if actual != expected:
                    raise MigrationError(f"Row count/content checksum mismatch: {table.name}")
                report["tables"][table.name] = {**actual, "status": "verified"}
            # Deferred FK constraints are verified before sequences and commit.
            connection.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
            reset_identity_sequences(connection)
        report["status"] = "verified_and_committed"
        return report
