"""Import a retained SQLite source into an empty Alembic-upgraded PostgreSQL DB."""

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa

from app.config import load_local_env
from app.storage.migration import MigrationError, migrate_sqlite
from app.storage.schema import upgrade_schema


def main():
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--retain-legacy-table", action="append", default=[],
                        help="Explicitly retain this legacy table only in the unchanged SQLite source")
    parser.add_argument("--upgrade", action="store_true", help="Apply Alembic migrations before importing")
    parser.add_argument("--batch-size", type=int, default=1000)
    args = parser.parse_args()
    url = os.environ.get("LIMITUPLAB_DATABASE_URL", "")
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    if not url.startswith("postgresql+psycopg://"):
        parser.error("LIMITUPLAB_DATABASE_URL must select PostgreSQL with psycopg")
    if args.report.resolve() == args.source.resolve():
        parser.error("The report cannot overwrite the SQLite source")
    if args.report.exists():
        parser.error("The report path already exists; use a new path to preserve prior evidence")
    engine = sa.create_engine(url, poolclass=sa.pool.NullPool)
    try:
        if args.upgrade:
            upgrade_schema(engine)
        report = migrate_sqlite(args.source, engine, allowed_legacy_tables=args.retain_legacy_table,
                                batch_size=args.batch_size)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
        print(f"Migration verified and committed; original SQLite retained. Report: {args.report}")
    except MigrationError as error:
        print(f"Migration stopped: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        # Database exception strings can contain credentials and raw private rows.
        print(f"Migration failed ({type(error).__name__}); inspect target state before retrying.", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
