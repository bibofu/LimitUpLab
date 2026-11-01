"""PostgreSQL expression for persisted insertion order."""


def insertion_order(db, alias=None):
    """Preserve insertion order, including records imported from SQLite."""
    return f"{alias}.legacy_order" if alias else "legacy_order"
