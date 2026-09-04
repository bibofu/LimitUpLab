import os
import unittest
from datetime import date, datetime, time, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.collectors import (
    HithinkLimitUpFact,
    HithinkLimitUpPoolSnapshot,
    LimitUpCollectionResult,
)
from app.models import LimitUpEvent, StockDailyBar, StockKLineBar
from app.repositories import SQLiteFirstBoardRepository, SQLiteLimitUpRepository
from scripts.update_daily_data import (
    collect_post_first_board_bars,
    run_daily_update,
    warm_latest_intraday_cache,
)


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

    def test_intraday_warmup_covers_every_latest_pool_symbol(self) -> None:
        database_path = self._database_path()
        trade_date = date(2026, 8, 10)
        events = [
            self._make_event("002298", "first", trade_date),
            self._make_event("600001", "continued", trade_date, board_height=2),
            self._make_event("000001", "failed", trade_date, closed_limit=False),
            self._make_event("920001", "bse", trade_date),
            self._make_event("000002", "older", date(2026, 8, 7)),
        ]
        loaded: list[str] = []

        def loader(*, symbol: str, **_kwargs):
            loaded.append(symbol)
            return [] if symbol == "000001" else [object()]

        try:
            report = warm_latest_intraday_cache(
                events=events,
                trade_date=trade_date,
                repository=SQLiteFirstBoardRepository(database_path=database_path),
                max_workers=2,
                loader=loader,
            )

            self.assertEqual(sorted(loaded), ["000001", "002298", "600001"])
            self.assertEqual(report["target_count"], 3)
            self.assertEqual(report["ready_count"], 2)
            self.assertEqual(report["missing_count"], 1)
            self.assertTrue(report["warnings"])
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

    def test_late_target_is_persisted_as_historical_backtest(self) -> None:
        database_path = self._database_path()
        try:
            limit_repo = SQLiteLimitUpRepository(database_path=database_path)
            first_board_repo = SQLiteFirstBoardRepository(database_path=database_path)
            trade_date = date(2026, 8, 10)
            limit_repo.upsert_events(
                [self._make_event("002298", "\u4e2d\u7535\u946b\u9f99", trade_date)]
            )

            report = run_daily_update(
                trade_date=trade_date,
                top_targets=1,
                max_tracked_kline_fetches=0,
                skip_import=True,
                refresh_enrichment=False,
                persist_live_prediction=False,
                limit_up_repository=limit_repo,
                first_board_repository=first_board_repo,
                post_bar_collector=lambda _symbol, _base_date, _as_of_date: [],
            )

            predictions = first_board_repo.list_predictions_between(
                trade_date,
                trade_date,
            )
            self.assertEqual(report.persisted_live_predictions, 0)
            self.assertEqual(report.persisted_historical_predictions, 1)
            self.assertEqual(len(predictions), 1)
            self.assertEqual(predictions[0].prediction_source, "historical_backtest")
        finally:
            self._cleanup_database(database_path)

    def test_repeated_latest_update_does_not_rewrite_live_predictions(self) -> None:
        database_path = self._database_path()
        try:
            limit_repo = SQLiteLimitUpRepository(database_path=database_path)
            first_board_repo = SQLiteFirstBoardRepository(database_path=database_path)
            trade_date = date(2026, 8, 10)
            limit_repo.upsert_events(
                [self._make_event("002298", "candidate", trade_date)]
            )
            kwargs = {
                "trade_date": trade_date,
                "top_targets": 1,
                "max_tracked_kline_fetches": 0,
                "skip_import": True,
                "refresh_enrichment": False,
                "limit_up_repository": limit_repo,
                "first_board_repository": first_board_repo,
                "post_bar_collector": lambda _symbol, _base_date, _as_of_date: [],
            }

            first = run_daily_update(**kwargs)
            first_snapshot = first_board_repo.get_live_prediction_snapshot(trade_date)
            second = run_daily_update(**kwargs)

            self.assertEqual(first.persisted_live_predictions, 1)
            self.assertEqual(second.persisted_live_predictions, 0)
            self.assertTrue(first.live_prediction_snapshot_ready)
            self.assertTrue(second.live_prediction_snapshot_ready)
            self.assertEqual(
                len(first_board_repo.list_predictions_between(trade_date, trade_date)),
                1,
            )
            self.assertEqual(
                first_snapshot,
                first_board_repo.get_live_prediction_snapshot(trade_date),
            )
        finally:
            self._cleanup_database(database_path)

    @patch("scripts.update_daily_data.collect_limit_up_events")
    def test_import_reports_tonghuashun_limit_up_count_difference(
        self,
        collect_events,
    ) -> None:
        database_path = self._database_path()
        trade_date = date(2026, 8, 21)
        collect_events.return_value = LimitUpCollectionResult(
            status="ok",
            data_fresh=True,
            source_errors=(),
            payload=[self._make_event("002491", "通鼎互联", trade_date)],
        )
        try:
            report = run_daily_update(
                trade_date=trade_date,
                top_targets=0,
                max_tracked_kline_fetches=0,
                refresh_enrichment=False,
                limit_up_repository=SQLiteLimitUpRepository(database_path=database_path),
                first_board_repository=SQLiteFirstBoardRepository(database_path=database_path),
                post_bar_collector=lambda _symbol, _base_date, _as_of_date: [],
                remote_limit_up_collector=lambda _date: HithinkLimitUpPoolSnapshot(
                    trade_date=trade_date,
                    page=1,
                    page_size=200,
                    total=2,
                    items=[
                        HithinkLimitUpFact(
                            symbol="002491",
                            thscode="002491.SZ",
                            name="通鼎互联",
                            is_st=False,
                            is_new=False,
                            last_price=5.0,
                            change_pct=10.0,
                            limit_up_time="09:35:00",
                            limit_up_reason="通信设备+算力",
                            board_height=1,
                            board_height_text="首板",
                            seal_amount=50_000_000,
                            max_seal_amount=60_000_000,
                        )
                    ],
                ),
            )

            self.assertEqual(report.closed_limit_events, 1)
            self.assertEqual(report.hithink_limit_up_count, 2)
            self.assertEqual(report.hithink_limit_up_source, "hithink-finance")
            self.assertEqual(report.hithink_reason_enriched_count, 1)
            self.assertEqual(report.limit_up_count_difference, -1)
            stored = SQLiteLimitUpRepository(database_path=database_path).list_events()
            self.assertEqual(stored[0].concept, "通信设备+算力")
            self.assertTrue(
                any("source count mismatch" in item for item in report.warnings)
            )
        finally:
            self._cleanup_database(database_path)

    @patch("scripts.update_daily_data.collect_limit_up_events")
    def test_partial_import_does_not_delete_existing_date_rows(
        self,
        collect_events,
    ) -> None:
        database_path = self._database_path()
        trade_date = date(2026, 8, 21)
        limit_repo = SQLiteLimitUpRepository(database_path=database_path)
        first_board_repo = SQLiteFirstBoardRepository(database_path=database_path)
        existing = self._make_event(
            "002050",
            "三花智控",
            trade_date,
            closed_limit=False,
        )
        partial_event = self._make_event("002491", "通鼎互联", trade_date)
        limit_repo.upsert_events([existing])
        collect_events.return_value = LimitUpCollectionResult(
            status="partial",
            data_fresh=True,
            source_errors=("akshare.failed_limit_pool: provider timeout",),
            payload=[partial_event],
        )

        def unavailable_remote(_trade_date):
            raise RuntimeError("remote verification unavailable")

        try:
            report = run_daily_update(
                trade_date=trade_date,
                replace_date=True,
                top_targets=0,
                max_tracked_kline_fetches=0,
                refresh_enrichment=False,
                limit_up_repository=limit_repo,
                first_board_repository=first_board_repo,
                post_bar_collector=lambda _symbol, _base_date, _as_of_date: [],
                remote_limit_up_collector=unavailable_remote,
            )

            symbols = {item.symbol for item in limit_repo.list_events()}
            self.assertEqual(symbols, {"002050", "002491"})
            self.assertEqual(report.akshare_status, "partial")
            self.assertIn(
                "akshare.failed_limit_pool: provider timeout",
                report.akshare_source_errors,
            )
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
            self.assertEqual(report.tracked_next_day_outcomes_expected, 10)
            self.assertEqual(report.tracked_next_day_outcomes_ready, 10)
            self.assertEqual(report.tracked_three_day_outcomes_expected, 6)
            self.assertEqual(report.tracked_three_day_outcomes_ready, 6)
            self.assertEqual(report.tracked_five_day_paths_expected, 2)
            self.assertEqual(report.tracked_five_day_paths_ready, 2)
            self.assertEqual(report.outcome_completeness["status"], "healthy")
            self.assertEqual(report.outcome_completeness["missing_case_count"], 0)
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
