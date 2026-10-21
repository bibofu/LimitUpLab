
from database_support import isolated_database_url, release_database_url
import os
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.models import LimitUpEvent, StockDailyBar
from app.repositories import FirstBoardRepository
from app.services.data_health import build_agent_data_health
from app.services.first_board_features import build_first_board_features


TEST_TMP_ROOT = Path(
    os.getenv("LIMITUPLAB_TEST_TMP", Path(__file__).resolve().parents[1])
)


class AgentDataHealthTest(unittest.TestCase):
    # Prepare the isolated fixtures and dependencies shared by the tests in this class.
    def setUp(self) -> None:
        TEST_TMP_ROOT.mkdir(exist_ok=True)

    # Prepare the database path fixture or observation used by the surrounding regression
    # scenario.
    def _database_url(self) -> str:
        return isolated_database_url()

    # Release the temporary resources owned by this test fixture.
    def _cleanup_database(self, database_url: str) -> None:
        release_database_url(database_url)

    # Build the LimitUpEvent fixture used by the surrounding regression scenario.
    def _make_event(self, symbol: str, name: str, trade_date: date) -> LimitUpEvent:
        return LimitUpEvent(
            symbol=symbol,
            name=name,
            trade_date=trade_date,
            first_limit_time=time(9, 35),
            last_limit_time=time(9, 40),
            seal_count=1,
            break_count=0,
            closed_limit=True,
            board_height=1,
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

    # Regression scenario: missing events returns missing status.
    def test_missing_events_returns_missing_status(self) -> None:
        health = build_agent_data_health(events=[])

        self.assertEqual(health.status, "missing")
        self.assertFalse(health.raw_events_ready)
        self.assertTrue(health.warnings)

    def test_excluded_only_day_is_ready_for_an_empty_relay_result(self) -> None:
        database_url = self._database_url()
        try:
            repository = FirstBoardRepository(database_url=database_url)
            trade_date = date(2026, 10, 20)
            events = [
                self._make_event("000639", "ST西王", trade_date),
                self._make_event("600302", "*ST标准", trade_date),
                self._make_event("920096", "嘉晨智能", trade_date),
                self._make_event("830001", "北交样本", trade_date),
            ]
            health = build_agent_data_health(
                events=events, first_board_repository=repository, trade_date=trade_date,
            )
            self.assertTrue(health.raw_events_ready)
            self.assertTrue(health.first_board_features_ready)
            self.assertEqual(health.first_board_feature_count, 0)
            self.assertEqual(health.top_candidates_checked, 0)
            self.assertFalse(any("features are missing" in item for item in health.warnings))
        finally:
            self._cleanup_database(database_url)

    def test_partial_features_do_not_hide_a_missing_eligible_symbol(self) -> None:
        database_url = self._database_url()
        try:
            repository = FirstBoardRepository(database_url=database_url)
            trade_date = date(2026, 10, 20)
            events = [
                self._make_event("002491", "通鼎互联", trade_date),
                self._make_event("600001", "普通样本", trade_date),
            ]
            repository.upsert_features(build_first_board_features(events[:1], trade_date))
            health = build_agent_data_health(
                events=events, first_board_repository=repository, trade_date=trade_date,
            )
            self.assertFalse(health.first_board_features_ready)
            self.assertEqual(health.status, "missing")
        finally:
            self._cleanup_database(database_url)

    # Regression scenario: features without enrichment returns partial status.
    def test_features_without_enrichment_returns_partial_status(self) -> None:
        database_url = self._database_url()
        try:
            repository = FirstBoardRepository(database_url=database_url)
            trade_date = date(2026, 8, 10)
            events = [self._make_event("002298", "\u4e2d\u7535\u946b\u9f99", trade_date)]
            repository.upsert_features(build_first_board_features(events, trade_date))

            health = build_agent_data_health(
                events=events,
                first_board_repository=repository,
                trade_date=trade_date,
                top_limit=1,
            )

            self.assertEqual(health.status, "partial")
            self.assertTrue(health.raw_events_ready)
            self.assertTrue(health.first_board_features_ready)
            self.assertEqual(health.first_board_feature_count, 1)
            self.assertEqual(health.top_candidates[0].symbol, "002298")
            self.assertTrue(health.top_candidates[0].feature_ready)
            self.assertFalse(health.top_candidates[0].enrichment_ready)
            self.assertIsNotNone(health.outcome_completeness)
            self.assertEqual(health.outcome_completeness.status, "missing")
            self.assertEqual(health.post_limit_pool_count, 1)
            self.assertEqual(health.post_limit_evaluable_count, 0)
            self.assertEqual(health.post_limit_pending_symbol_count, 1)
            self.assertEqual(health.post_limit_missing_reasons, {"missing_history20": 1})
        finally:
            self._cleanup_database(database_url)

    def test_post_limit_coverage_separates_missing_dates_and_mixed_sources(self) -> None:
        database_url = self._database_url()
        try:
            repository = FirstBoardRepository(database_url=database_url)
            dates = [date(2026, 8, 3) + timedelta(days=index) for index in range(28)]
            dates = [day for day in dates if day.weekday() < 5]
            target_date = dates[-1]
            events = [
                self._make_event("600999", "日期样本", day).model_copy(update={"closed_limit": False})
                for day in dates
            ]
            events.extend(self._make_event(symbol, name, target_date) for symbol, name in [
                ("600001", "同源样本"), ("600002", "缺日样本"), ("600003", "混源样本"),
                ("600004", "ST排除样本"), ("300001", "创业板排除样本"),
            ])
            bars = []
            for symbol in ("600001", "600002", "600003"):
                for index, day in enumerate(dates):
                    if symbol == "600002" and index == 10:
                        continue
                    source = "akshare.stock_zh_a_hist_tx" if index % 2 else "tencent.daily"
                    if symbol == "600003" and index == 10:
                        source = "hithink-finance.market.history"
                    bars.append(StockDailyBar(
                        symbol=symbol, trade_date=day, open=10, high=11, low=9, close=10,
                        volume=100, amount=1000, source=source, created_at=datetime.now(timezone.utc),
                    ))
            # A future bar must not fill the gap in the requested history window.
            bars.append(bars[-1].model_copy(update={
                "symbol": "600002", "trade_date": target_date + timedelta(days=3),
            }))
            repository.upsert_daily_bars(bars)

            health = build_agent_data_health(events, repository, target_date, top_limit=0)

            self.assertEqual(health.post_limit_pool_count, 3)
            self.assertEqual(health.post_limit_evaluable_count, 1)
            self.assertEqual(health.post_limit_source_consistent_count, 1)
            self.assertEqual(health.post_limit_pending_symbol_count, 2)
            self.assertEqual(health.post_limit_coverage_ratio, 0.3333)
            self.assertEqual(health.post_limit_missing_history_count, 1)
            self.assertEqual(health.post_limit_missing_reasons, {
                "missing_history20": 1, "mixed_or_missing_source": 1,
            })
            self.assertIn("Recent post-limit research cache coverage is 1/3.", health.warnings)
        finally:
            self._cleanup_database(database_url)

    def test_empty_post_limit_pool_has_full_coverage_without_history_warning(self) -> None:
        database_url = self._database_url()
        try:
            repository = FirstBoardRepository(database_url=database_url)
            target_date = date(2026, 8, 10)
            event = self._make_event("600001", "未封板样本", target_date).model_copy(
                update={"closed_limit": False},
            )
            health = build_agent_data_health([event], repository, target_date, top_limit=0)

            self.assertEqual(health.post_limit_pool_count, 0)
            self.assertEqual(health.post_limit_coverage_ratio, 1.0)
            self.assertEqual(health.post_limit_pending_symbol_count, 0)
            self.assertEqual(health.post_limit_missing_reasons, {})
            self.assertFalse(any("research cache coverage" in warning for warning in health.warnings))
        finally:
            self._cleanup_database(database_url)


if __name__ == "__main__":
    unittest.main()
