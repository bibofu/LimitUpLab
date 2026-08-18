import os
import unittest
from datetime import date, datetime, time, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.models import LimitUpEvent, StockDailyBar, StockKLineBar
from app.repositories import SQLiteFirstBoardRepository, SQLiteLimitUpRepository
from scripts.update_daily_data import collect_post_first_board_bars, run_daily_update


TEST_TMP_ROOT = Path(
    os.getenv(
        "LIMITUPLAB_TEST_TMP",
        Path(__file__).resolve().parents[1],
    )
)


class DailyUpdatePipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        TEST_TMP_ROOT.mkdir(exist_ok=True)

    def _database_path(self) -> Path:
        return TEST_TMP_ROOT / f"daily-update-test-{uuid4().hex}.sqlite"

    def _cleanup_database(self, database_path: Path) -> None:
        for path in (
            database_path,
            database_path.with_name(f"{database_path.name}-wal"),
            database_path.with_name(f"{database_path.name}-shm"),
        ):
            path.unlink(missing_ok=True)

    def _make_event(
        self,
        symbol: str,
        name: str,
        trade_date: date,
        board_height: int = 1,
        closed_limit: bool = True,
    ) -> LimitUpEvent:
        return LimitUpEvent(
            symbol=symbol,
            name=name,
            trade_date=trade_date,
            first_limit_time=time(9, 35),
            last_limit_time=time(9, 40),
            seal_count=1,
            break_count=0,
            closed_limit=closed_limit,
            board_height=board_height,
            amount=250_000_000,
            turnover_rate=6.5,
            industry="\u7535\u7f51\u8bbe\u5907",
            concept="\u667a\u80fd\u7535\u7f51",
            next_open_pct=0,
            next_high_pct=0,
            next_close_pct=0,
            three_day_return_pct=0,
            five_day_return_pct=0,
            continued_next_day=False,
        )

    def test_skip_import_syncs_features_and_health_report(self) -> None:
        database_path = self._database_path()
        try:
            limit_repo = SQLiteLimitUpRepository(database_path=database_path)
            first_board_repo = SQLiteFirstBoardRepository(database_path=database_path)
            trade_date = date(2026, 8, 10)
            limit_repo.upsert_events(
                [
                    self._make_event("002298", "\u4e2d\u7535\u946b\u9f99", trade_date),
                ]
            )

            report = run_daily_update(
                trade_date=trade_date,
                history_days=60,
                top_targets=1,
                similar_limit=2,
                max_kline_fetches=0,
                skip_import=True,
                refresh_enrichment=False,
                limit_up_repository=limit_repo,
                first_board_repository=first_board_repo,
                post_bar_collector=lambda _symbol, _base_date, _as_of_date: [],
            )

            self.assertEqual(report.trade_date, "2026-08-10")
            self.assertEqual(report.synced_feature_dates, 1)
            self.assertEqual(report.synced_features, 1)
            self.assertEqual(report.target_candidates_checked, 1)
            self.assertTrue(report.health["raw_events_ready"])
            self.assertTrue(report.health["first_board_features_ready"])
            self.assertEqual(report.health["first_board_feature_count"], 1)
            self.assertEqual(report.health["status"], "partial")
            self.assertEqual(report.top_candidate["symbol"], "002298")
        finally:
            self._cleanup_database(database_path)

    def test_missing_raw_events_reports_unhealthy_state(self) -> None:
        database_path = self._database_path()
        try:
            report = run_daily_update(
                trade_date=date(2026, 8, 10),
                skip_import=True,
                refresh_enrichment=False,
                limit_up_repository=SQLiteLimitUpRepository(database_path=database_path),
                first_board_repository=SQLiteFirstBoardRepository(database_path=database_path),
            )

            self.assertFalse(report.health["raw_events_ready"])
            self.assertFalse(report.health["first_board_features_ready"])
            self.assertTrue(report.warnings)
        finally:
            self._cleanup_database(database_path)

    @patch("scripts.update_daily_data.collect_stock_kline")
    def test_post_bar_collection_includes_as_of_date(self, collect_kline) -> None:
        base_date = date(2026, 8, 17)
        as_of_date = date(2026, 8, 18)
        collect_kline.return_value = [
            StockKLineBar(
                trade_date=item_date,
                open=10,
                high=11,
                low=9.5,
                close=10.5,
                volume=1_000_000,
            )
            for item_date in (base_date, as_of_date, date(2026, 8, 19))
        ]

        bars = collect_post_first_board_bars("002165", base_date, as_of_date)

        self.assertEqual([item.trade_date for item in bars], [base_date, as_of_date])
        self.assertEqual(collect_kline.call_args.kwargs["end_date"], as_of_date)

    def test_recent_daily_top_picks_cache_all_available_follow_up_bars(self) -> None:
        database_path = self._database_path()
        try:
            limit_repo = SQLiteLimitUpRepository(database_path=database_path)
            first_board_repo = SQLiteFirstBoardRepository(database_path=database_path)
            trade_dates = [
                date(2026, 8, 6),
                date(2026, 8, 7),
                date(2026, 8, 10),
                date(2026, 8, 11),
                date(2026, 8, 12),
                date(2026, 8, 13),
            ]
            events = [
                self._make_event(
                    f"00{date_index + 1}{symbol_index + 1:03d}",
                    f"candidate-{date_index}-{symbol_index}",
                    item_date,
                )
                for date_index, item_date in enumerate(trade_dates)
                for symbol_index in range(2)
            ]
            limit_repo.upsert_events(events)

            def fake_bar_collector(
                symbol: str,
                base_date: date,
                as_of_date: date,
            ) -> list[StockDailyBar]:
                return [
                    StockDailyBar(
                        symbol=symbol,
                        trade_date=item_date,
                        open=10,
                        high=11,
                        low=9.5,
                        close=10.5,
                        volume=1_000_000,
                        amount=10_000_000,
                        change_pct=5,
                        source="test",
                        created_at=datetime.now(timezone.utc),
                    )
                    for item_date in trade_dates
                    if base_date <= item_date <= as_of_date
                ][:6]

            report = run_daily_update(
                trade_date=trade_dates[-1],
                history_days=60,
                top_targets=2,
                similar_limit=0,
                max_tracked_kline_fetches=20,
                skip_import=True,
                refresh_enrichment=False,
                limit_up_repository=limit_repo,
                first_board_repository=first_board_repo,
                post_bar_collector=fake_bar_collector,
            )

            self.assertEqual(report.persisted_top_predictions, 12)
            self.assertEqual(report.tracked_candidate_references, 12)
            self.assertEqual(report.tracked_cache_ready, 12)
            self.assertEqual(report.tracked_cache_complete, 2)
            self.assertEqual(report.tracked_cache_missing, 0)
            predictions = first_board_repo.list_predictions_between(
                trade_dates[0], trade_dates[-1]
            )
            self.assertEqual(len(predictions), 12)
            self.assertEqual(
                sum(item.prediction_source == "live" for item in predictions),
                2,
            )
            self.assertEqual(
                sum(item.prediction_source == "historical_backtest" for item in predictions),
                10,
            )
            self.assertEqual(
                len(first_board_repo.list_post_bars(events[0].symbol, trade_dates[0], limit=6)),
                6,
            )
            self.assertEqual(
                len(first_board_repo.list_post_bars(events[-1].symbol, trade_dates[-1], limit=6)),
                1,
            )
        finally:
            self._cleanup_database(database_path)


if __name__ == "__main__":
    unittest.main()
