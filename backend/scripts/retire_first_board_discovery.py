"""Back up and remove legacy discovery storage only when its retired table exists."""

from __future__ import annotations

import argparse
import sys
from contextlib import closing
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_local_env
from app.database import begin_write, connect, connect_readonly, table_exists
from scripts.backup_database import create_configured_backup


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


def retire_discovery(database_url: str | None, backup_dir: Path) -> tuple[Path | None, dict[str, int]]:
    """Use the selected backend; never reset current recommendations on a rerun."""

    with closing(connect_readonly(database_url)) as connection:
        before = {
            table: _row_count(connection, table)
            for table in PRESERVED_TABLES
            if table_exists(connection, table)
        }
        if not table_exists(connection, "first_board_discovery_snapshots"):
            return None, before
    backup = create_configured_backup(backup_dir, database_url=database_url)
    with closing(connect(database_url)) as connection, connection:
        begin_write(connection)
        # Another maintenance run may already have retired the table while the
        # backup was being created. Never clear subsequently generated results.
        if not table_exists(connection, "first_board_discovery_snapshots"):
            return Path(backup["backup"]), before
        before = {
            table: _row_count(connection, table)
            for table in PRESERVED_TABLES
            if table_exists(connection, table)
        }
        for table in RESET_TABLES:
            if table_exists(connection, table):
                connection.execute(f"DELETE FROM {table}")
        connection.execute("DROP INDEX IF EXISTS idx_first_board_discovery_created")
        connection.execute("DROP TABLE IF EXISTS first_board_discovery_snapshots")
        after = {
            table: _row_count(connection, table)
            for table in before
        }
        if before != after:
            raise RuntimeError("Protected prediction or review records changed during cleanup.")
    return Path(backup["backup"]), before


# Count rows in the selected maintenance table for the retirement report.
def _row_count(connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


# Parse the maintenance options and retire the legacy discovery data through the script's guarded
# workflow.
def main(argv: list[str] | None = None) -> int:
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    backup, protected = retire_discovery(None, args.backup_dir)
    print(f"status={'retired' if backup else 'already_retired'} backup={backup} protected_rows={protected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
