"""Back up and remove the retired non-limit-up discovery data contract."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from scripts.backup_database import create_backup


PRESERVED_TABLES = (
    "agent_live_prediction_snapshots",
    "agent_predictions",
    "first_board_outcomes",
    "daily_review_snapshots",
    "scoring_policies",
)
RESET_TABLES = (
    "recommendation_intelligence_changes",
    "recommendation_intelligence_current",
    "recommendation_intelligence_snapshots",
    "recommendation_prediction_finals",
)


def retire_discovery(database_path: Path, backup_dir: Path) -> tuple[Path, dict[str, int]]:
    """Create a verified backup, then remove only discovery-coupled storage."""

    backup_path = create_backup(database_path, backup_dir)
    with sqlite3.connect(database_path) as connection:
        before = {
            table: _row_count(connection, table)
            for table in PRESERVED_TABLES
            if _table_exists(connection, table)
        }
        connection.execute("BEGIN IMMEDIATE")
        for table in RESET_TABLES:
            if _table_exists(connection, table):
                connection.execute(f"DELETE FROM {table}")
        connection.execute("DROP INDEX IF EXISTS idx_first_board_discovery_created")
        connection.execute("DROP TABLE IF EXISTS first_board_discovery_snapshots")
        after = {
            table: _row_count(connection, table)
            for table in before
        }
        if before != after:
            raise RuntimeError("Protected prediction or review records changed during cleanup.")
        connection.commit()
    return backup_path, before


# Check SQLite's catalog before querying an optional legacy table.
def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


# Count rows in the selected maintenance table for the retirement report.
def _row_count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


# Parse the maintenance options and retire the legacy discovery data through the script's guarded
# workflow.
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    args = parser.parse_args()
    backup, protected = retire_discovery(args.database, args.backup_dir)
    print(f"backup={backup} protected_rows={protected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
