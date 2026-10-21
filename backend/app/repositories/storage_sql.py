"""PostgreSQL expressions for persisted ordering, timestamps and aggregation."""


def insertion_order(db, alias=None):
    """Preserve insertion order, including records imported from SQLite."""
    return f"{alias}.legacy_order" if alias else "legacy_order"


def timestamp_value(db, expression):
    """Compare ISO timestamps by instant while preserving their original text."""
    return f"CAST({expression} AS TIMESTAMPTZ)"


def distinct_text_list(db, expression):
    """Aggregate distinct text using PostgreSQL's native comma separator."""
    return f"string_agg(DISTINCT {expression}, ',')"


def database_clock(db):
    """Use one authoritative clock for leases shared across workers."""
    return float(db.execute("SELECT EXTRACT(EPOCH FROM clock_timestamp())").fetchone()[0])
