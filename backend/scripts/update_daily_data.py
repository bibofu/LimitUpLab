"""Run the daily data update pipeline for the first-board Agent.

The pipeline keeps raw limit-up events, derived first-board features and
similar-case post-board bars in sync so Agent tools do not fail after a new
trading day is imported.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.agents.first_board import build_first_board_ratings
from app.collectors import collect_limit_up_events, collect_stock_kline, parse_akshare_trade_date
from app.models import LimitUpEvent, StockDailyBar
from app.repositories import SQLiteFirstBoardRepository, SQLiteLimitUpRepository
from app.services.first_board_features import (
    build_first_board_features,
    build_first_board_outcome,
)
from app.services.data_health import build_agent_data_health
from app.services.similar_cases import find_similar_first_board_cases


@dataclass
class DailyUpdateReport:
    """Summary of one daily data update run."""

    trade_date: str
    imported_events: int = 0
    closed_limit_events: int = 0
    failed_limit_events: int = 0
    synced_feature_dates: int = 0
    synced_features: int = 0
    target_candidates_checked: int = 0
    similar_case_references: int = 0
    backfilled_bars: int = 0
    backfilled_outcomes: int = 0
    top_candidate: dict[str, object] | None = None
    health: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def main() -> None:
    """Parse CLI arguments and run the daily pipeline."""

    parser = argparse.ArgumentParser(
        description="Update raw and derived first-board Agent data for one day.",
    )
    parser.add_argument(
        "--date",
        default=date.today().strftime("%Y%m%d"),
        help="Trading date in YYYYMMDD format. Defaults to local today.",
    )
    parser.add_argument(
        "--history-days",
        type=int,
        default=60,
        help="Number of latest local trading dates to rebuild feature rows for.",
    )
    parser.add_argument(
        "--top-targets",
        type=int,
        default=5,
        help="Number of latest-day top-rated candidates to backfill similar-case bars for.",
    )
    parser.add_argument(
        "--similar-limit",
        type=int,
        default=10,
        help="Number of similar cases to inspect per target candidate.",
    )
    parser.add_argument(
        "--max-kline-fetches",
        type=int,
        default=30,
        help="Maximum remote K-line fetches for similar cases. 0 means unlimited.",
    )
    parser.add_argument(
        "--skip-import",
        action="store_true",
        help="Skip AkShare limit-up import and only refresh derived data.",
    )
    parser.add_argument(
        "--replace-date",
        action="store_true",
        help="Delete existing raw events for --date before importing.",
    )
    args = parser.parse_args()

    report = run_daily_update(
        trade_date=parse_akshare_trade_date(args.date),
        history_days=args.history_days,
        top_targets=args.top_targets,
        similar_limit=args.similar_limit,
        max_kline_fetches=args.max_kline_fetches,
        skip_import=args.skip_import,
        replace_date=args.replace_date,
    )
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))


def run_daily_update(
    trade_date: date,
    history_days: int = 60,
    top_targets: int = 5,
    similar_limit: int = 10,
    max_kline_fetches: int = 30,
    skip_import: bool = False,
    replace_date: bool = False,
    limit_up_repository: SQLiteLimitUpRepository | None = None,
    first_board_repository: SQLiteFirstBoardRepository | None = None,
) -> DailyUpdateReport:
    """Update raw events, derived features, similar bars and health checks."""

    limit_repo = limit_up_repository or SQLiteLimitUpRepository()
    first_board_repo = first_board_repository or SQLiteFirstBoardRepository()
    report = DailyUpdateReport(trade_date=trade_date.isoformat())

    if not skip_import:
        imported_events = collect_limit_up_events(trade_date.strftime("%Y%m%d"))
        if replace_date:
            limit_repo.delete_events_for_date(trade_date)
        limit_repo.upsert_events(imported_events)
        report.imported_events = len(imported_events)
        report.closed_limit_events = sum(1 for item in imported_events if item.closed_limit)
        report.failed_limit_events = sum(1 for item in imported_events if not item.closed_limit)

    events = limit_repo.list_events()
    if not any(event.trade_date == trade_date for event in events):
        report.warnings.append(f"No raw limit-up events found for {trade_date.isoformat()}.")
        report.health = build_agent_data_health(
            events=events,
            first_board_repository=first_board_repo,
            trade_date=trade_date,
            top_limit=top_targets,
        ).model_dump(mode="json")
        return report

    report.synced_feature_dates, report.synced_features = sync_recent_features(
        events=events,
        first_board_repository=first_board_repo,
        history_days=history_days,
    )
    ratings = build_first_board_ratings(events=events, trade_date=trade_date)
    top_ratings = ratings.candidates[: max(top_targets, 0)]
    if top_ratings:
        top = top_ratings[0]
        report.top_candidate = {
            "symbol": top.facts.symbol,
            "name": top.facts.name,
            "score": top.score,
            "rating": top.rating,
        }

    backfill = backfill_top_candidate_similar_bars(
        trade_date=trade_date,
        target_symbols=[item.facts.symbol for item in top_ratings],
        events=events,
        first_board_repository=first_board_repo,
        similar_limit=similar_limit,
        max_kline_fetches=max_kline_fetches,
    )
    report.target_candidates_checked = len(top_ratings)
    report.similar_case_references = backfill["case_count"]
    report.backfilled_bars = backfill["bar_count"]
    report.backfilled_outcomes = backfill["outcome_count"]
    report.warnings.extend(backfill["warnings"])
    report.health = build_agent_data_health(
        events=events,
        first_board_repository=first_board_repo,
        trade_date=trade_date,
        top_limit=top_targets,
        similar_limit=similar_limit,
    ).model_dump(mode="json")
    return report


def sync_recent_features(
    events: list[LimitUpEvent],
    first_board_repository: SQLiteFirstBoardRepository,
    history_days: int,
) -> tuple[int, int]:
    """Rebuild first-board feature rows for recent local trading dates."""

    trade_dates = sorted({event.trade_date for event in events}, reverse=True)[:history_days]
    feature_count = 0
    for item in trade_dates:
        features = build_first_board_features(events, trade_date=item)
        first_board_repository.upsert_features(features)
        feature_count += len(features)
    return len(trade_dates), feature_count


def backfill_top_candidate_similar_bars(
    trade_date: date,
    target_symbols: list[str],
    events: list[LimitUpEvent],
    first_board_repository: SQLiteFirstBoardRepository,
    similar_limit: int,
    max_kline_fetches: int,
) -> dict[str, object]:
    """Backfill post-board bars for similar cases of selected target symbols."""

    fetch_count = 0
    bar_count = 0
    outcome_count = 0
    case_count = 0
    warnings: list[str] = []

    for symbol in target_symbols:
        try:
            response = find_similar_first_board_cases(
                symbol=symbol,
                trade_date=trade_date,
                repository=first_board_repository,
                limit=similar_limit,
            )
        except ValueError as error:
            warnings.append(f"{symbol}@{trade_date.isoformat()}: {error}")
            continue

        for item in response.cases:
            case_count += 1
            if first_board_repository.has_post_bars(item.symbol, item.trade_date):
                continue
            if max_kline_fetches > 0 and fetch_count >= max_kline_fetches:
                warnings.append(
                    f"Reached max_kline_fetches={max_kline_fetches}; remaining cases skipped."
                )
                return {
                    "case_count": case_count,
                    "bar_count": bar_count,
                    "outcome_count": outcome_count,
                    "warnings": warnings,
                }

            try:
                bars = collect_post_first_board_bars(item.symbol, item.trade_date)
            except Exception as error:  # noqa: BLE001
                warnings.append(f"{item.symbol}@{item.trade_date.isoformat()}: {error}")
                continue

            fetch_count += 1
            first_board_repository.upsert_daily_bars(bars)
            event = next(
                (
                    candidate
                    for candidate in events
                    if candidate.symbol == item.symbol
                    and candidate.trade_date == item.trade_date
                ),
                None,
            )
            if event is not None:
                outcome = build_first_board_outcome(
                    event=event,
                    bars=bars,
                    future_events=events,
                )
                first_board_repository.upsert_outcomes([outcome])
                outcome_count += 1
            bar_count += len(bars)

    return {
        "case_count": case_count,
        "bar_count": bar_count,
        "outcome_count": outcome_count,
        "warnings": warnings,
    }


def collect_post_first_board_bars(symbol: str, trade_date: date) -> list[StockDailyBar]:
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
