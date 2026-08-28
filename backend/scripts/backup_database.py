"""Create and retain consistent SQLite backups for production deployment."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


CN_TZ = ZoneInfo("Asia/Shanghai")
BACKUP_PATTERN = "limituplab-*.sqlite"


def create_backup(
    database_path: Path,
    output_dir: Path,
    *,
    now: datetime | None = None,
) -> Path:
    """Create a transactionally consistent SQLite backup with restricted permissions."""
    if not database_path.is_file():
        raise FileNotFoundError(f"Database does not exist: {database_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(CN_TZ)).astimezone(CN_TZ)
    backup_path = output_dir / f"limituplab-{timestamp:%Y%m%d-%H%M%S}.sqlite"

    source_uri = f"file:{database_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source:
        with sqlite3.connect(backup_path) as destination:
            source.backup(destination)
            integrity = destination.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise RuntimeError(f"Backup integrity check failed: {integrity}")

    backup_path.chmod(0o600)
    return backup_path


def prune_backups(output_dir: Path, *, retain_count: int) -> list[Path]:
    """Delete older managed backup files while retaining the newest snapshots."""
    if retain_count < 1:
        raise ValueError("retain_count must be at least 1")

    backups = sorted(output_dir.glob(BACKUP_PATTERN), reverse=True)
    removed = backups[retain_count:]
    for backup_path in removed:
        backup_path.unlink()
    return removed


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the production backup job."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--retain-count", type=int, default=14)
    return parser.parse_args()


def main() -> int:
    """Create one backup, prune stale snapshots, and emit a cron-friendly summary."""
    args = parse_args()
    backup_path = create_backup(args.database, args.output_dir)
    removed = prune_backups(args.output_dir, retain_count=args.retain_count)
    print(f"backup={backup_path} removed={len(removed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
