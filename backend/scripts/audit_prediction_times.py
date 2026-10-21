"""Inspect historical prediction time provenance; --apply adds audit annotations."""

import argparse
from contextlib import closing
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_local_env
from app.database import begin_write, connect, connect_readonly, get_data_directory, initialize_database
from app.services.prediction_time_audit import audit_prediction_times
from scripts.backup_database import create_configured_backup


def run_audit(database_url=None, *, apply=False, backup_directory=None):
    """Audit the selected backend; back it up before adding any annotations."""
    with closing(connect_readonly(database_url)) as reader:
        if not apply:
            report = audit_prediction_times(reader)
            report.update(backup=None, database_backend="postgresql")
            return report
    directory = backup_directory or get_data_directory() / "backups/prediction-time"
    backup = create_configured_backup(directory, database_url=database_url)
    with closing(connect(database_url)) as connection, connection:
        initialize_database(connection)
        begin_write(connection)
        report = audit_prediction_times(connection, apply=True)
    report.update(backup=backup["backup"], database_backend="postgresql")
    if "sha256" in backup:
        report["backup_sha256"] = backup["sha256"]
    return report


# Parse audit options and report prediction timing/provenance from the selected database.
def main() -> None:
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-directory", type=Path)
    parser.add_argument("--output", type=Path, default=BACKEND_ROOT / "data/prediction_time_audit_latest.json")
    args = parser.parse_args()
    report = run_audit(apply=args.apply, backup_directory=args.backup_directory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "batches"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
