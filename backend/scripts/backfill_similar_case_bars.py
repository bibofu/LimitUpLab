"""Backfill post-board daily bars for target stocks' similar cases."""

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.collectors import collect_stock_kline
from app.models import StockDailyBar
from app.repositories import SQLiteFirstBoardRepository, get_limit_up_repository
from app.services.first_board_features import build_first_board_outcome
from app.services.similar_cases import find_similar_first_board_cases


def main() -> None:
    """Backfill post bars for Top-K similar cases of one or many targets."""

    parser = argparse.ArgumentParser(
        description="Backfill post-board daily bars for target stocks' similar cases.",
    )
    parser.add_argument("--trade-date", required=True, help="Target date in YYYY-MM-DD.")
    parser.add_argument("--symbol", help="Target stock symbol.")
    parser.add_argument(
        "--all-targets-for-date",
        action="store_true",
        help="Backfill similar cases for every persisted target on the date.",
    )
    parser.add_argument(
        "--target-limit",
        type=int,
        default=0,
        help="Maximum target stocks to process when using --all-targets-for-date.",
    )
    parser.add_argument("--limit", type=int, default=10, help="Number of similar cases.")
    parser.add_argument(
        "--max-fetches",
        type=int,
        default=0,
        help="Maximum remote K-line fetches. 0 means unlimited.",
    )
    args = parser.parse_args()

    if not args.symbol and not args.all_targets_for_date:
        parser.error("Either --symbol or --all-targets-for-date is required.")

    target_date = date.fromisoformat(args.trade_date)
    repository = SQLiteFirstBoardRepository()
    limit_up_events = get_limit_up_repository().list_events()

    target_symbols = [args.symbol] if args.symbol else [
        feature.symbol for feature in repository.list_features_for_date(target_date)
    ]
    if args.target_limit > 0:
        target_symbols = target_symbols[: args.target_limit]

    fetch_count = 0
    bar_count = 0
    outcome_count = 0
    case_count = 0
    skipped: list[str] = []

    for target_symbol in target_symbols:
        response = find_similar_first_board_cases(
            symbol=target_symbol,
            trade_date=target_date,
            repository=repository,
            limit=args.limit,
        )

        for item in response.cases:
            case_count += 1
            if repository.has_post_bars(item.symbol, item.trade_date):
                continue
            if args.max_fetches > 0 and fetch_count >= args.max_fetches:
                print(
                    f"Reached max fetches. Backfilled {bar_count} daily bars and "
                    f"{outcome_count} outcomes for {case_count} similar case references "
                    f"across {len(target_symbols)} targets."
                )
                return

            try:
                bars = _collect_post_first_board_bars(item.symbol, item.trade_date)
            except Exception as exc:  # noqa: BLE001
                skipped.append(f"{item.symbol}@{item.trade_date}: {exc}")
                continue

            fetch_count += 1
            repository.upsert_daily_bars(bars)
            event = next(
                (
                    candidate
                    for candidate in limit_up_events
                    if candidate.symbol == item.symbol
                    and candidate.trade_date == item.trade_date
                ),
                None,
            )
            if event is not None:
                outcome = build_first_board_outcome(
                    event=event,
                    bars=bars,
                    future_events=limit_up_events,
                )
                repository.upsert_outcomes([outcome])
                outcome_count += 1
            bar_count += len(bars)

    print(
        f"Backfilled {bar_count} daily bars and {outcome_count} outcomes "
        f"for {case_count} similar case references across {len(target_symbols)} targets."
    )
    if skipped:
        print(f"Skipped {len(skipped)} failed K-line fetches:")
        for message in skipped[:20]:
            print(f"- {message}")
        if len(skipped) > 20:
            print(f"- ... {len(skipped) - 20} more")


def _collect_post_first_board_bars(symbol: str, trade_date: date) -> list[StockDailyBar]:
    """Fetch and normalize post-first-board daily bars for one stock."""

    raw_bars = collect_stock_kline(
        symbol,
        days=8,
        end_date=trade_date + timedelta(days=14),
    )
    filtered_bars = [bar for bar in raw_bars if bar.trade_date >= trade_date][:6]
    return [
        StockDailyBar(
            symbol=symbol,
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


if __name__ == "__main__":
    main()
