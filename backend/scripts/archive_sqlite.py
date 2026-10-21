"""Create an explicit, consistent archive of a retained SQLite migration source."""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
from uuid import uuid4
from zoneinfo import ZoneInfo


def create_backup(database_path: Path, output_dir: Path, *, now: datetime | None = None) -> Path:
    """Keep source reads pinned through SQLite's backup API, including committed WAL."""
    if not database_path.is_file():
        raise FileNotFoundError(f"Database does not exist: {database_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(ZoneInfo("Asia/Shanghai"))).astimezone(ZoneInfo("Asia/Shanghai"))
    destination_path = output_dir / f"limituplab-{timestamp:%Y%m%d-%H%M%S}-{uuid4().hex}.sqlite"
    # Exclusive creation prevents overwriting any previous historical archive.
    with destination_path.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(database_path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(destination_path)) as destination:
                source.backup(destination)
                integrity = destination.execute("PRAGMA integrity_check").fetchone()
                if not integrity or integrity[0] != "ok":
                    raise RuntimeError("SQLite archive integrity check failed")
        destination_path.chmod(0o600)
        return destination_path
    except Exception:
        destination_path.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(f"archive={create_backup(args.source, args.output_dir)}")


if __name__ == "__main__":
    main()
