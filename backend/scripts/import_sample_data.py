"""Import bundled sample events into the configured demonstration database."""

import argparse
from contextlib import closing
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_local_env
from app.database import begin_write, connect, initialize_database, is_postgres
from app.repositories import SQLiteLimitUpRepository
from app.services.sample_data import SAMPLE_EVENTS


def import_samples(*, replace_existing: bool = False) -> str:
    """Seed the selected database without silently replacing real market data."""

    with closing(connect()) as database:
        initialize_database(database)
        begin_write(database, "import-sample-events")
        if not replace_existing and database.execute("SELECT 1 FROM limit_up_events LIMIT 1").fetchone():
            raise RuntimeError("Selected database already has events; use a dedicated demo database "
                               "or explicitly pass --replace-existing.")
        SQLiteLimitUpRepository().replace_events(SAMPLE_EVENTS, connection=database)
        return "postgresql" if is_postgres(database) else "sqlite"


def main() -> None:
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replace-existing", action="store_true",
                        help="Explicitly replace all existing events in the configured demo database.")
    args = parser.parse_args()
    backend = import_samples(replace_existing=args.replace_existing)
    print(f"Imported {len(SAMPLE_EVENTS)} sample events into configured {backend} database")


if __name__ == "__main__":
    main()
