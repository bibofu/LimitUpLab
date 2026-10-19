"""Inspect historical prediction time provenance; --apply adds audit annotations."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_local_env
from app.database import begin_write, connect, connect_readonly, get_database_path, initialize_database, is_postgres
from app.services.prediction_time_audit import audit_prediction_times
from scripts.backup_database import create_backup


def run_audit(database=None, *, apply=False, backup_directory=None):
    """Audit the selected backend; back it up before adding any annotations."""
    with closing(connect_readonly(database)) as reader:
        postgres = is_postgres(reader)
        if not apply:
            report = audit_prediction_times(reader)
            report.update(backup=None, database_backend="postgresql" if postgres else "sqlite")
            return report
    if postgres:
        from scripts.postgres_admin import backup_database, connection_environment
        _, environment = connection_environment(os.environ["LIMITUPLAB_DATABASE_URL"])
        directory = backup_directory or BACKEND_ROOT / "data/backups/prediction-time"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = backup_database(directory / f"prediction-time-{stamp}.dump", environment)
    else:
        path = database or get_database_path()
        saved = create_backup(path, backup_directory or path.parent / "backups/prediction-time")
        backup = {"backup": str(saved)}
    with closing(connect(database)) as connection, connection:
        initialize_database(connection)
        begin_write(connection)
        report = audit_prediction_times(connection, apply=True)
    report.update(backup=backup["backup"], database_backend="postgresql" if postgres else "sqlite")
    if "sha256" in backup:
        report["backup_sha256"] = backup["sha256"]
    return report


# Parse audit options and report prediction timing/provenance from the selected database.
def main() -> None:
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", "--db", type=Path, default=None,
                        help="Explicit SQLite file; otherwise use the configured database backend")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-directory", type=Path)
    parser.add_argument("--output", type=Path, default=BACKEND_ROOT / "data/prediction_time_audit_latest.json")
    args = parser.parse_args()
    report = run_audit(args.database, apply=args.apply, backup_directory=args.backup_directory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "batches"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
