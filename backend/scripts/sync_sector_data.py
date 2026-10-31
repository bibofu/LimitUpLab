"""Sync the sector catalog and daily board ranking into PostgreSQL."""

from __future__ import annotations

import argparse
import json
from datetime import date

from app.repositories import SectorRepository, get_limit_up_repository
from app.services.sector_sync import sync_sector_daily


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync sector market data")
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to the latest local limit-up date")
    parser.add_argument("--no-concepts", action="store_true", help="skip the concept ranking")
    parser.add_argument("--force-catalog", action="store_true", help="refresh the 710-sector directory")
    args = parser.parse_args()
    trade_date = date.fromisoformat(args.date) if args.date else _latest_local_date()
    result = sync_sector_daily(
        SectorRepository(), trade_date,
        include_concepts=not args.no_concepts,
        force_catalog=args.force_catalog,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def _latest_local_date() -> date:
    events = get_limit_up_repository().list_events()
    latest = max((event.trade_date for event in events), default=None)
    if latest is None:
        raise SystemExit("No local limit-up events; pass --date explicitly.")
    return latest


if __name__ == "__main__":
    main()
