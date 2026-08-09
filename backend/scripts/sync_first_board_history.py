"""Build local first-board history tables for similar-case retrieval.

The script can either derive features from already-local limit-up events or
fetch recent Eastmoney/AKShare limit-up pools before deriving features.
"""

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.collectors import collect_limit_up_events, collect_stock_kline
from app.models import LimitUpEvent, StockDailyBar
from app.repositories import SQLiteFirstBoardRepository, SQLiteLimitUpRepository
from app.services.first_board_features import (
    build_first_board_features,
    build_first_board_outcome,
)


def main() -> None:
    """Parse CLI args and sync derived first-board history tables."""

    parser = argparse.ArgumentParser(
        description="Build local first-board features and post-board outcomes.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=60,
        help="Number of latest local trading dates to derive features for.",
    )
    parser.add_argument(
        "--fetch-limit-up",
        action="store_true",
        help="Fetch recent limit-up events before building features.",
    )
    parser.add_argument(
        "--lookback-calendar-days",
        type=int,
        default=120,
        help="Calendar days to scan when --fetch-limit-up is enabled.",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="End date in YYYYMMDD format. Defaults to today.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip AKShare fetches for dates that already exist locally.",
    )
    parser.add_argument(
        "--with-kline",
        action="store_true",
        help="Fetch daily K-line bars for first-board samples and build outcomes.",
    )
    parser.add_argument(
        "--missing-kline-only",
        action="store_true",
        help="Only fetch K-line bars for cases without local post-board bars.",
    )
    parser.add_argument(
        "--max-kline-fetches",
        type=int,
        default=None,
        help="Maximum number of first-board stocks to fetch K-line bars for.",
    )
    args = parser.parse_args()

    limit_up_repository = SQLiteLimitUpRepository()
    if args.fetch_limit_up:
        _fetch_recent_limit_up_events(
            repository=limit_up_repository,
            days=args.days,
            lookback_calendar_days=args.lookback_calendar_days,
            end_date=_parse_optional_yyyymmdd(args.end_date) or date.today(),
            skip_existing=args.skip_existing,
        )

    events = limit_up_repository.list_events()
    trade_dates = sorted({event.trade_date for event in events}, reverse=True)[: args.days]
    first_board_repository = SQLiteFirstBoardRepository()
    feature_count = 0
    bar_count = 0
    outcome_count = 0

    for trade_date in trade_dates:
        features = build_first_board_features(events, trade_date=trade_date)
        first_board_repository.upsert_features(features)
        feature_count += len(features)

        if not args.with_kline:
            continue

        first_board_events = [
            event
            for event in events
            if event.trade_date == trade_date
            and event.board_height == 1
            and event.closed_limit
        ]
        for event in first_board_events:
            if args.max_kline_fetches is not None and outcome_count >= args.max_kline_fetches:
                continue
            if args.missing_kline_only and first_board_repository.has_post_bars(
                event.symbol,
                event.trade_date,
            ):
                continue
            bars = _collect_post_first_board_bars(event)
            first_board_repository.upsert_daily_bars(bars)
            outcome = build_first_board_outcome(
                event=event,
                bars=bars,
                future_events=events,
            )
            first_board_repository.upsert_outcomes([outcome])
            bar_count += len(bars)
            outcome_count += 1

    print(
        "Synced "
        f"{feature_count} first-board features, "
        f"{bar_count} daily bars, "
        f"{outcome_count} outcomes "
        f"for {len(trade_dates)} local trading dates."
    )


def _fetch_recent_limit_up_events(
    repository: SQLiteLimitUpRepository,
    days: int,
    lookback_calendar_days: int,
    end_date: date,
    skip_existing: bool,
) -> None:
    """Fetch recent limit-up pools until enough trading dates are collected."""

    existing_dates = {event.trade_date for event in repository.list_events()}
    imported_dates: list[date] = []

    for offset in range(lookback_calendar_days + 1):
        current_date = end_date - timedelta(days=offset)
        if current_date.weekday() >= 5:
            continue
        if skip_existing and current_date in existing_dates:
            imported_dates.append(current_date)
            if len(set(imported_dates)) >= days:
                break
            continue

        trade_date_text = current_date.strftime("%Y%m%d")
        try:
            events = collect_limit_up_events(trade_date_text)
        except Exception as error:  # AKShare raises provider-specific exceptions.
            print(f"Skipped {trade_date_text}: {error}")
            continue

        if not events:
            print(f"Skipped {trade_date_text}: no events")
            continue

        repository.delete_events_for_date(current_date)
        repository.upsert_events(events)
        imported_dates.append(current_date)
        print(f"Imported {len(events)} events for {trade_date_text}")

        if len(set(imported_dates)) >= days:
            break


def _collect_post_first_board_bars(event: LimitUpEvent) -> list[StockDailyBar]:
    """Fetch latest daily bars around a first-board event for local persistence."""

    raw_bars = collect_stock_kline(
        event.symbol,
        days=8,
        end_date=event.trade_date + timedelta(days=14),
    )
    filtered_bars = [bar for bar in raw_bars if bar.trade_date >= event.trade_date][:6]
    return [
        StockDailyBar(
            symbol=event.symbol,
            trade_date=bar.trade_date,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            amount=bar.volume,
            change_pct=None,
            source="akshare.stock_zh_a_hist_tx",
            created_at=datetime.now(timezone.utc),
        )
        for bar in filtered_bars
    ]


def _parse_optional_yyyymmdd(value: str | None) -> date | None:
    """Parse optional YYYYMMDD date CLI values."""

    if value is None:
        return None
    if len(value) != 8 or not value.isdigit():
        raise ValueError("date must use YYYYMMDD format")
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


if __name__ == "__main__":
    main()

