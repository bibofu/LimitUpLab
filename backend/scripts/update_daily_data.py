"""Run the daily data update pipeline for the first-board Agent.

The pipeline keeps raw limit-up events, derived first-board features and
tracked Top10 post-board bars in sync after a new trading day is imported.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.agents.first_board import MIN_AMOUNT, build_first_board_ratings
from app.collectors import (
    HithinkFinanceCollector,
    HithinkLimitUpPoolSnapshot,
    collect_limit_up_events,
    collect_stock_kline,
    collect_stock_spot_klines,
    parse_akshare_trade_date,
)
from app.models import AgentPrediction, LimitUpEvent, StockDailyBar, StockKLineBar
from app.repositories import (
    SQLiteFirstBoardDiscoveryRepository,
    SQLiteFirstBoardRepository,
    SQLiteLimitUpRepository,
)
from app.services.first_board_features import (
    build_first_board_features,
    build_first_board_outcome,
)
from app.services.data_health import build_agent_data_health
from app.services.evaluation_agent import (
    persist_agent_predictions_for_dates,
    select_canonical_prediction_snapshots,
)
from app.services.first_board_enrichment import refresh_first_board_enrichment_snapshots
from app.services.outcome_completeness import build_top10_outcome_completeness
from app.services.limit_up_reason import merge_limit_up_reasons
from app.services.first_board_discovery import refresh_first_board_discovery


PostBarCollector = Callable[[str, date, date], list[StockDailyBar]]
SpotBarCollector = Callable[[list[str], date], dict[str, StockKLineBar]]
RemoteLimitUpCollector = Callable[[date], HithinkLimitUpPoolSnapshot]


@dataclass
class DailyUpdateReport:
    """Summary of one daily data update run."""

    trade_date: str
    imported_events: int = 0
    closed_limit_events: int = 0
    failed_limit_events: int = 0
    hithink_limit_up_count: int | None = None
    hithink_limit_up_source: str | None = None
    hithink_reason_enriched_count: int = 0
    limit_up_count_difference: int | None = None
    synced_feature_dates: int = 0
    synced_features: int = 0
    enrichment_snapshots: int = 0
    enrichment_technical_ready: int = 0
    enrichment_dragon_tiger: int = 0
    enrichment_popularity: int = 0
    enrichment_dragon_tiger_sources: list[str] = field(default_factory=list)
    enrichment_popularity_sources: list[str] = field(default_factory=list)
    persisted_top_predictions: int = 0
    persisted_live_predictions: int = 0
    persisted_historical_predictions: int = 0
    live_prediction_snapshot_ready: bool = False
    target_candidates_checked: int = 0
    tracked_candidate_references: int = 0
    tracked_cache_ready: int = 0
    tracked_cache_complete: int = 0
    tracked_cache_missing: int = 0
    tracked_next_day_outcomes_expected: int = 0
    tracked_next_day_outcomes_ready: int = 0
    tracked_three_day_outcomes_expected: int = 0
    tracked_three_day_outcomes_ready: int = 0
    tracked_five_day_paths_expected: int = 0
    tracked_five_day_paths_ready: int = 0
    backfilled_bars: int = 0
    backfilled_outcomes: int = 0
    outcome_completeness: dict[str, object] = field(default_factory=dict)
    top_candidate: dict[str, object] | None = None
    discovery_snapshot_ready: bool = False
    discovery_data_as_of: str | None = None
    discovery_target_trade_date: str | None = None
    discovery_candidate_count: int = 0
    discovery_generated_by: str | None = None
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
        default=10,
        help="Number of daily top-rated candidates to persist and track.",
    )
    parser.add_argument(
        "--max-tracked-kline-fetches",
        type=int,
        default=60,
        help="Maximum remote fetches reserved for recent daily Top10 tracking.",
    )
    parser.add_argument(
        "--skip-import",
        action="store_true",
        help="Skip AkShare limit-up import and only refresh derived data.",
    )
    parser.add_argument(
        "--skip-enrichment",
        action="store_true",
        help="Skip extended K-line, listing, Dragon-Tiger and popularity inputs.",
    )
    parser.add_argument(
        "--force-enrichment",
        action="store_true",
        help="Refresh enrichment even when a complete local snapshot exists.",
    )
    parser.add_argument(
        "--replace-date",
        action="store_true",
        help="Delete existing raw events for --date before importing.",
    )
    parser.add_argument(
        "--skip-discovery",
        action="store_true",
        help="Skip the full-market next-session first-board discovery scan.",
    )
    parser.add_argument(
        "--force-discovery",
        action="store_true",
        help="Replace the same-date discovery snapshot for the current strategy.",
    )
    args = parser.parse_args()

    report = run_daily_update(
        trade_date=parse_akshare_trade_date(args.date),
        history_days=args.history_days,
        top_targets=args.top_targets,
        max_tracked_kline_fetches=args.max_tracked_kline_fetches,
        skip_import=args.skip_import,
        refresh_enrichment=not args.skip_enrichment,
        force_enrichment=args.force_enrichment,
        replace_date=args.replace_date,
        refresh_discovery=not args.skip_discovery,
        force_discovery=args.force_discovery,
    )
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))


def run_daily_update(
    trade_date: date,
    history_days: int = 60,
    top_targets: int = 10,
    max_tracked_kline_fetches: int = 60,
    skip_import: bool = False,
    refresh_enrichment: bool = True,
    force_enrichment: bool = False,
    replace_date: bool = False,
    limit_up_repository: SQLiteLimitUpRepository | None = None,
    first_board_repository: SQLiteFirstBoardRepository | None = None,
    post_bar_collector: PostBarCollector | None = None,
    spot_bar_collector: SpotBarCollector | None = None,
    remote_limit_up_collector: RemoteLimitUpCollector | None = None,
    persist_live_prediction: bool | None = None,
    refresh_discovery: bool = False,
    force_discovery: bool = False,
) -> DailyUpdateReport:
    """Update raw events, scoring features, tracked bars and health checks."""

    limit_repo = limit_up_repository or SQLiteLimitUpRepository()
    first_board_repo = first_board_repository or SQLiteFirstBoardRepository()
    active_bar_collector = post_bar_collector or collect_post_first_board_bars
    active_spot_collector = spot_bar_collector or (
        collect_stock_spot_klines if post_bar_collector is None else None
    )
    report = DailyUpdateReport(trade_date=trade_date.isoformat())

    if not skip_import:
        imported_events = collect_limit_up_events(trade_date.strftime("%Y%m%d"))
        try:
            remote_snapshot = (
                remote_limit_up_collector(trade_date)
                if remote_limit_up_collector is not None
                else collect_hithink_limit_up_snapshot(trade_date)
            )
            report.hithink_limit_up_count = remote_snapshot.total
            report.hithink_limit_up_source = remote_snapshot.source
            imported_events, report.hithink_reason_enriched_count = (
                merge_limit_up_reasons(imported_events, remote_snapshot)
            )
        except Exception as error:  # noqa: BLE001
            report.warnings.append(f"Tonghuashun limit-up verification: {error}")
        if replace_date:
            limit_repo.delete_events_for_date(trade_date)
        limit_repo.upsert_events(imported_events)
        report.imported_events = len(imported_events)
        report.closed_limit_events = sum(1 for item in imported_events if item.closed_limit)
        report.failed_limit_events = sum(1 for item in imported_events if not item.closed_limit)
        if report.hithink_limit_up_count is not None:
            report.limit_up_count_difference = (
                report.closed_limit_events - report.hithink_limit_up_count
            )
            if report.limit_up_count_difference:
                report.warnings.append(
                    "Limit-up source count mismatch: "
                    f"AkShare closed={report.closed_limit_events}, "
                    f"Tonghuashun={remote_snapshot.total}."
                )

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
    eligible_count = sum(
        1
        for item in events
        if item.trade_date == trade_date
        and item.board_height == 1
        and item.closed_limit
        and item.amount >= MIN_AMOUNT
        and "ST" not in item.name.upper()
        and not item.name.startswith(("N", "C"))
        and not item.symbol.startswith(("4", "8", "920", "688", "689"))
    )
    existing_enrichment = first_board_repo.list_enrichment_for_date(trade_date)
    if refresh_enrichment and (
        force_enrichment
        or not skip_import
        or len(existing_enrichment) < eligible_count
    ):
        enrichment_report = refresh_first_board_enrichment_snapshots(
            events=events,
            trade_date=trade_date,
            repository=first_board_repo,
        )
        report.enrichment_snapshots = enrichment_report.snapshot_count
        report.enrichment_technical_ready = enrichment_report.technical_ready_count
        report.enrichment_dragon_tiger = enrichment_report.dragon_tiger_count
        report.enrichment_popularity = enrichment_report.popularity_count
        report.enrichment_dragon_tiger_sources = enrichment_report.dragon_tiger_sources
        report.enrichment_popularity_sources = enrichment_report.popularity_sources
        report.warnings.extend(enrichment_report.warnings)
    else:
        report.enrichment_snapshots = len(existing_enrichment)
        report.enrichment_technical_ready = sum(
            item.kline_bar_count >= 20 for item in existing_enrichment
        )
        report.enrichment_dragon_tiger = sum(
            item.dragon_tiger_on_list for item in existing_enrichment
        )
        report.enrichment_popularity = sum(
            item.popularity_rank is not None for item in existing_enrichment
        )
        report.enrichment_dragon_tiger_sources = sorted(
            {
                item.dragon_tiger_source
                for item in existing_enrichment
                if item.dragon_tiger_source
            }
        )
        report.enrichment_popularity_sources = sorted(
            {
                item.popularity_source
                for item in existing_enrichment
                if item.popularity_source
            }
        )
    ratings = build_first_board_ratings(
        events=events,
        trade_date=trade_date,
        first_board_repository=first_board_repo,
    )
    top_ratings = ratings.candidates[: max(top_targets, 0)]
    if top_ratings:
        top = top_ratings[0]
        report.top_candidate = {
            "symbol": top.facts.symbol,
            "name": top.facts.name,
            "score": top.score,
            "rating": top.rating,
        }

    recent_trade_dates = sorted(
        {event.trade_date for event in events if event.trade_date <= trade_date},
        reverse=True,
    )[:6]
    latest_available_date = max(event.trade_date for event in events)
    is_latest_available_date = trade_date == latest_available_date
    should_persist_live = is_latest_available_date and (
        persist_live_prediction is not False
    )
    target_has_live_prediction = (
        first_board_repo.get_live_prediction_snapshot(trade_date) is not None
    )
    historical_dates = sorted(
        item
        for item in recent_trade_dates
        if item < trade_date
        or (
            item == trade_date
            and not should_persist_live
            and not target_has_live_prediction
        )
    )
    historical_count = persist_agent_predictions_for_dates(
        events=events,
        trade_dates=historical_dates,
        repository=first_board_repo,
        top_per_day=top_targets,
        prediction_source="historical_backtest",
    )
    live_count = (
        persist_agent_predictions_for_dates(
            events=events,
            trade_dates=[trade_date],
            repository=first_board_repo,
            top_per_day=top_targets,
            prediction_source="live",
            data_as_of=trade_date,
        )
        if should_persist_live
        else 0
    )
    report.persisted_live_predictions = live_count
    report.persisted_historical_predictions = historical_count
    report.persisted_top_predictions = historical_count + live_count
    report.live_prediction_snapshot_ready = (
        first_board_repo.get_live_prediction_snapshot(trade_date) is not None
    )
    tracked_backfill = backfill_recent_daily_top_candidate_bars(
        events=events,
        first_board_repository=first_board_repo,
        trading_days=6,
        top_per_day=top_targets,
        max_kline_fetches=max_tracked_kline_fetches,
        as_of_date=trade_date,
        bar_collector=active_bar_collector,
        spot_bar_collector=active_spot_collector,
    )
    report.target_candidates_checked = len(top_ratings)
    report.tracked_candidate_references = int(tracked_backfill["case_count"])
    report.tracked_cache_ready = int(tracked_backfill["ready_count"])
    report.tracked_cache_complete = int(tracked_backfill["complete_count"])
    report.tracked_cache_missing = int(tracked_backfill["missing_count"])
    report.tracked_next_day_outcomes_expected = int(
        tracked_backfill["next_day_outcomes_expected"]
    )
    report.tracked_next_day_outcomes_ready = int(
        tracked_backfill["next_day_outcomes_ready"]
    )
    report.tracked_three_day_outcomes_expected = int(
        tracked_backfill["three_day_outcomes_expected"]
    )
    report.tracked_three_day_outcomes_ready = int(
        tracked_backfill["three_day_outcomes_ready"]
    )
    report.tracked_five_day_paths_expected = int(
        tracked_backfill["five_day_paths_expected"]
    )
    report.tracked_five_day_paths_ready = int(
        tracked_backfill["five_day_paths_ready"]
    )
    report.backfilled_bars = int(tracked_backfill["bar_count"])
    report.backfilled_outcomes = int(tracked_backfill["outcome_count"])
    report.outcome_completeness = dict(tracked_backfill["outcome_completeness"])
    report.warnings.extend(tracked_backfill["warnings"])
    if refresh_discovery and is_latest_available_date:
        try:
            discovery = refresh_first_board_discovery(
                first_board_repository=first_board_repo,
                snapshot_repository=SQLiteFirstBoardDiscoveryRepository(
                    first_board_repo.database_path
                ),
                top_k=30,
                force=force_discovery,
            )
            report.discovery_snapshot_ready = True
            report.discovery_data_as_of = discovery.data_as_of.isoformat()
            report.discovery_target_trade_date = (
                discovery.target_trade_date.isoformat()
                if discovery.target_trade_date
                else None
            )
            report.discovery_candidate_count = len(discovery.candidates)
            report.discovery_generated_by = discovery.generated_by
            if discovery.data_as_of != trade_date:
                report.warnings.append(
                    "First-board discovery market date differs from update date: "
                    f"market={discovery.data_as_of}, update={trade_date}."
                )
            report.warnings.extend(discovery.warnings)
        except Exception as error:  # noqa: BLE001
            report.warnings.append(f"First-board discovery: {error}")
    report.health = build_agent_data_health(
        events=events,
        first_board_repository=first_board_repo,
        trade_date=trade_date,
        top_limit=top_targets,
    ).model_dump(mode="json")
    return report


def collect_hithink_limit_up_snapshot(
    trade_date: date,
) -> HithinkLimitUpPoolSnapshot:
    """Fetch the complete bounded Tonghuashun limit-up pool for source audit."""

    return HithinkFinanceCollector().collect_limit_up_pool(
        trade_date=trade_date,
        page=1,
        size=200,
        sort_field="limit_up_time",
        sort_direction="asc",
    )


def backfill_recent_daily_top_candidate_bars(
    *,
    events: list[LimitUpEvent],
    first_board_repository: SQLiteFirstBoardRepository,
    trading_days: int,
    top_per_day: int,
    max_kline_fetches: int,
    as_of_date: date | None = None,
    bar_collector: PostBarCollector | None = None,
    spot_bar_collector: SpotBarCollector | None = None,
) -> dict[str, object]:
    """Ensure every recent daily Top-N pick has all currently available bars."""

    available_dates = sorted({event.trade_date for event in events})
    resolved_as_of = as_of_date or (available_dates[-1] if available_dates else date.today())
    recent_count = max(trading_days, 0)
    eligible_dates = [item for item in available_dates if item <= resolved_as_of]
    recent_dates = eligible_dates[-recent_count:] if recent_count else []
    retry_window_days = max(recent_count, 20)
    preflight = build_top10_outcome_completeness(
        events=events,
        repository=first_board_repository,
        as_of_date=resolved_as_of,
        tracking_days=retry_window_days,
        top_per_day=top_per_day,
    )
    unresolved_dates = {
        item.trade_date for item in preflight.dates if item.status == "partial"
    }
    trade_dates = sorted({*recent_dates, *unresolved_dates})
    active_collector = bar_collector or collect_post_first_board_bars
    events_by_case = {
        (event.trade_date, event.symbol): event
        for event in events
    }
    persisted_predictions = (
        select_canonical_prediction_snapshots(
            first_board_repository.list_predictions_between(
                trade_dates[0],
                trade_dates[-1],
            )
        )
        if trade_dates
        else []
    )
    predictions_by_date: dict[date, list[AgentPrediction]] = {}
    for prediction in persisted_predictions:
        predictions_by_date.setdefault(prediction.trade_date, []).append(prediction)
    targets: list[tuple[date, str]] = []
    for item_date in trade_dates:
        daily_predictions = sorted(
            predictions_by_date.get(item_date, []),
            key=lambda item: (-item.score, item.symbol),
        )[: max(top_per_day, 0)]
        targets.extend((item_date, item.symbol) for item in daily_predictions)

    fetch_count = 0
    bar_count = 0
    outcome_count = 0
    ready_count = 0
    complete_count = 0
    missing_count = 0
    skipped_count = 0
    warnings: list[str] = []

    tracked_symbols = sorted(
        {symbol for _item_date, symbol in targets}
    )
    if spot_bar_collector is not None and tracked_symbols:
        try:
            spot_bars = spot_bar_collector(tracked_symbols, resolved_as_of)
            normalized_spot_bars = [
                StockDailyBar(
                    symbol=symbol,
                    trade_date=bar.trade_date,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    amount=0,
                    change_pct=None,
                    source="tencent.qt.gtimg.cn",
                    created_at=datetime.now(timezone.utc),
                )
                for symbol, bar in spot_bars.items()
                if symbol in tracked_symbols and bar.trade_date == resolved_as_of
            ]
            first_board_repository.upsert_daily_bars(normalized_spot_bars)
            bar_count += len(normalized_spot_bars)
        except Exception as error:  # noqa: BLE001
            warnings.append(f"Latest-day spot K-line batch: {error}")

    for item_date, symbol in targets:
        expected_dates = [
            candidate_date
            for candidate_date in available_dates
            if item_date <= candidate_date <= resolved_as_of
        ][:6]
        cached_bars = [
            bar
            for bar in first_board_repository.list_post_bars(
                symbol,
                item_date,
                limit=6,
            )
            if bar.trade_date <= resolved_as_of
        ]
        cached_dates = {bar.trade_date for bar in cached_bars}
        if all(expected_date in cached_dates for expected_date in expected_dates):
            event = events_by_case.get((item_date, symbol))
            if event is not None:
                first_board_repository.upsert_outcomes(
                    [
                        build_first_board_outcome(
                            event=event,
                            bars=cached_bars,
                            future_events=events,
                            trading_dates=available_dates,
                        )
                    ]
                )
                outcome_count += 1
            ready_count += 1
            if len(expected_dates) >= 6:
                complete_count += 1
            continue

        if max_kline_fetches > 0 and fetch_count >= max_kline_fetches:
            skipped_count += 1
            continue

        fetch_count += 1
        bars: list[StockDailyBar] = []
        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                bars = active_collector(
                    symbol,
                    item_date,
                    resolved_as_of,
                )
                last_error = None
                if bars:
                    break
            except Exception as error:  # noqa: BLE001
                last_error = error

        if last_error is not None:
            warnings.append(f"{symbol}@{item_date.isoformat()}: {last_error}")
        elif not bars:
            warnings.append(
                f"{symbol}@{item_date.isoformat()}: remote K-line result was empty."
            )
        else:
            first_board_repository.upsert_daily_bars(bars)
            event = events_by_case.get((item_date, symbol))
            if event is not None:
                outcome = build_first_board_outcome(
                    event=event,
                    bars=bars,
                    future_events=events,
                    trading_dates=available_dates,
                )
                first_board_repository.upsert_outcomes([outcome])
                outcome_count += 1
            bar_count += len(bars)

        refreshed_bars = [
            bar
            for bar in first_board_repository.list_post_bars(
                symbol,
                item_date,
                limit=6,
            )
            if bar.trade_date <= resolved_as_of
        ]
        refreshed_dates = {bar.trade_date for bar in refreshed_bars}
        if all(expected_date in refreshed_dates for expected_date in expected_dates):
            ready_count += 1
            if len(expected_dates) >= 6:
                complete_count += 1
        else:
            missing_count += 1

    if skipped_count:
        missing_count += skipped_count
        warnings.append(
            f"Reached tracked Top10 fetch budget {max_kline_fetches}; "
            f"{skipped_count} candidates remain queued for the next update."
        )
    if missing_count:
        warnings.append(
            f"Tracked Top10 cache is incomplete for {missing_count}/{len(targets)} candidates."
        )

    completeness = build_top10_outcome_completeness(
        events=events,
        repository=first_board_repository,
        as_of_date=resolved_as_of,
        tracking_days=retry_window_days,
        top_per_day=top_per_day,
    )
    warnings.extend(completeness.warnings)

    return {
        "case_count": len(targets),
        "ready_count": ready_count,
        "complete_count": complete_count,
        "missing_count": missing_count,
        "fetch_count": fetch_count,
        "bar_count": bar_count,
        "outcome_count": outcome_count,
        "next_day_outcomes_expected": completeness.d1_expected_count,
        "next_day_outcomes_ready": completeness.d1_ready_count,
        "three_day_outcomes_expected": completeness.d3_expected_count,
        "three_day_outcomes_ready": completeness.d3_ready_count,
        "five_day_paths_expected": completeness.d5_expected_count,
        "five_day_paths_ready": completeness.d5_ready_count,
        "outcome_completeness": completeness.model_dump(mode="json"),
        "warnings": warnings,
    }


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


def collect_post_first_board_bars(
    symbol: str,
    trade_date: date,
    as_of_date: date,
) -> list[StockDailyBar]:
    """Fetch and normalize post-first-board daily bars for one stock."""

    requested_days = max(8, (as_of_date - trade_date).days + 3)
    raw_bars = collect_stock_kline(
        symbol,
        days=requested_days,
        end_date=as_of_date,
    )
    filtered_bars = [
        bar
        for bar in raw_bars
        if trade_date <= bar.trade_date <= as_of_date
    ][:6]
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
