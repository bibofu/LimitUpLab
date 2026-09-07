"""Back up and remove the retired non-limit-up discovery data contract."""

from __future__ import annotations

import argparse
import hashlib
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
        before_state = {
            table: _table_state(connection, table)
            for table in PRESERVED_TABLES
            if _table_exists(connection, table)
        }
        connection.execute("BEGIN IMMEDIATE")
        for table in RESET_TABLES:
            if _table_exists(connection, table):
                connection.execute(f"DELETE FROM {table}")
        connection.execute("DROP INDEX IF EXISTS idx_first_board_discovery_created")
        connection.execute("DROP TABLE IF EXISTS first_board_discovery_snapshots")
        after_state = {
            table: _table_state(connection, table)
            for table in before_state
        }
        if before_state != after_state:
            raise RuntimeError("Protected prediction or review records changed during cleanup.")
        connection.commit()
    return backup_path, {
        table: state["row_count"] for table, state in before_state.items()
    }


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _row_count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _table_state(connection: sqlite3.Connection, table: str) -> dict[str, int | str]:
    """Fingerprint complete protected rows so equal counts cannot hide rewrites."""

    rows = sorted(repr(tuple(row)) for row in connection.execute(f"SELECT * FROM {table}"))
    digest = hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()
    return {"row_count": len(rows), "sha256": digest}


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
