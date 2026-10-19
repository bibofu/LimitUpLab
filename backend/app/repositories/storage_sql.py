"""Explicit dialect choices for persisted ordering and timestamp comparison."""

import time

from app.database import is_postgres


def insertion_order(db, alias=None):
    """Preserve SQLite insertion order after importing records into PostgreSQL."""
    column = "legacy_order" if is_postgres(db) else "rowid"
    return f"{alias}.{column}" if alias else column


def timestamp_value(db, expression):
    """Compare ISO timestamps by instant while preserving their original text."""
    if is_postgres(db):
        return f"CAST({expression} AS TIMESTAMPTZ)"
    return f"julianday({expression})"


def database_clock(db):
    """Use one authoritative clock for leases shared across PostgreSQL workers."""
    if is_postgres(db):
        return float(db.execute("SELECT EXTRACT(EPOCH FROM clock_timestamp())").fetchone()[0])
    return time.time()
