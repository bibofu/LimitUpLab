"""Back up the configured database, or an explicitly selected SQLite archive."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_local_env
from app.database import get_database_path


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


def create_configured_backup(
    output_dir: Path, *, database: Path | None = None, now: datetime | None = None,
) -> dict:
    """Honor the same backend selection as the application without a fallback."""
    raw_url = os.getenv("LIMITUPLAB_DATABASE_URL", "").strip()
    if database is None and raw_url:
        from scripts.postgres_admin import backup_database, connection_environment

        _, environment = connection_environment(raw_url)
        stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        filename = f"limituplab-pg-{stamp:%Y%m%dT%H%M%SZ}-{uuid4().hex}.dump"
        result = backup_database(output_dir / filename, environment)
        return {**result, "database_backend": "postgresql"}
    backup = create_backup(database or get_database_path(), output_dir, now=now)
    return {"backup": str(backup), "database_backend": "sqlite"}


def prune_postgres_backups(output_dir: Path, *, retain_count: int) -> list[Path]:
    """Retain only verified managed dump/checksum pairs; leave archives untouched."""
    from scripts.postgres_admin import file_hash

    if retain_count < 1:
        raise ValueError("retain_count must be at least 1")
    backups = []
    for path in output_dir.glob("limituplab-pg-*.dump"):
        parts = path.name.removeprefix("limituplab-pg-").removesuffix(".dump").split("-")
        if len(parts) != 2 or len(parts[1]) != 32 or any(c not in "0123456789abcdef" for c in parts[1]):
            continue
        try:
            stamp = datetime.strptime(parts[0], "%Y%m%dT%H%M%SZ")
        except ValueError:
            continue
        if stamp.strftime("%Y%m%dT%H%M%SZ") != parts[0]:
            continue
        checksum = path.with_suffix(".dump.sha256")
        if path.is_symlink() or checksum.is_symlink() or not path.is_file() or not checksum.is_file():
            continue
        try:
            if checksum.read_text(encoding="ascii").strip() == file_hash(path):
                backups.append(path)
        except (OSError, UnicodeError):
            continue
    removed = sorted(backups, reverse=True)[retain_count:]
    for path in removed:
        path.unlink()
        path.with_suffix(".dump.sha256").unlink()
    return removed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line options for the production backup job."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path,
                        help="Explicit historical SQLite file; otherwise back up the configured backend")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--retain-count", type=int, default=14)
    args = parser.parse_args(argv)
    if args.retain_count < 1:
        parser.error("--retain-count must be at least 1")
    return args


def main(argv: list[str] | None = None) -> int:
    """Create one backup, prune stale snapshots, and emit a cron-friendly summary."""
    load_local_env()
    args = parse_args(argv)
    result = create_configured_backup(args.output_dir, database=args.database)
    prune = prune_postgres_backups if result["database_backend"] == "postgresql" else prune_backups
    removed = prune(args.output_dir, retain_count=args.retain_count)
    print(f"backend={result['database_backend']} backup={result['backup']} removed={len(removed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
