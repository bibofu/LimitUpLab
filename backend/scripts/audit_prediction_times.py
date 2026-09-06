"""Inspect historical prediction time provenance; --apply adds audit annotations."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import DEFAULT_DATABASE_PATH, initialize_database
from app.services.prediction_time_audit import audit_prediction_times
from scripts.backup_database import create_backup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path, default=BACKEND_ROOT / "data/prediction_time_audit_latest.json")
    args = parser.parse_args()
    backup = create_backup(args.database, args.database.parent / "backups/prediction-time") if args.apply else None
    uri = f"{args.database.resolve().as_uri()}?mode={'rw' if args.apply else 'ro'}"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        if args.apply:
            initialize_database(connection)
            connection.execute("BEGIN IMMEDIATE")
        report = audit_prediction_times(connection, apply=args.apply)
    report["backup"] = str(backup) if backup else None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "batches"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
