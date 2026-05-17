import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.collectors import collect_limit_up_events, parse_akshare_trade_date
from app.database import get_database_path
from app.repositories import SQLiteLimitUpRepository


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import A-share limit-up events from AKShare into SQLite.",
    )
    parser.add_argument("--date", required=True, help="Trading date in YYYYMMDD format.")
    parser.add_argument(
        "--replace-date",
        action="store_true",
        help="Delete existing events for this date before importing.",
    )
    args = parser.parse_args()

    events = collect_limit_up_events(args.date)
    repository = SQLiteLimitUpRepository()

    if args.replace_date:
        repository.delete_events_for_date(parse_akshare_trade_date(args.date))

    repository.upsert_events(events)
    closed_count = sum(1 for event in events if event.closed_limit)
    failed_count = sum(1 for event in events if not event.closed_limit)
    print(
        f"Imported {len(events)} events "
        f"({closed_count} closed, {failed_count} failed) "
        f"for {args.date} into {get_database_path()}"
    )


if __name__ == "__main__":
    main()
