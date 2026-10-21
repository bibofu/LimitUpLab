"""Back up the configured PostgreSQL database and retain verified dump pairs."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_local_env
from app.database import get_database_url


def create_configured_backup(
    output_dir: Path, *, database_url: str | None = None, now: datetime | None = None,
) -> dict:
    """Use the application's validated PostgreSQL configuration."""
    from scripts.postgres_admin import backup_database, connection_environment

    _, environment = connection_environment(get_database_url(database_url))
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    filename = f"limituplab-pg-{stamp:%Y%m%dT%H%M%SZ}-{uuid4().hex}.dump"
    result = backup_database(output_dir / filename, environment)
    return {**result, "database_backend": "postgresql"}


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
    result = create_configured_backup(args.output_dir)
    removed = prune_postgres_backups(args.output_dir, retain_count=args.retain_count)
    print(f"backend={result['database_backend']} backup={result['backup']} removed={len(removed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
