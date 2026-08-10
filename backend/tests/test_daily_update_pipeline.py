import os
import unittest
from datetime import date, time
from pathlib import Path
from uuid import uuid4

from app.models import LimitUpEvent
from app.repositories import SQLiteFirstBoardRepository, SQLiteLimitUpRepository
from scripts.update_daily_data import run_daily_update


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
                limit_up_repository=limit_repo,
                first_board_repository=first_board_repo,
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
                limit_up_repository=SQLiteLimitUpRepository(database_path=database_path),
                first_board_repository=SQLiteFirstBoardRepository(database_path=database_path),
            )

            self.assertFalse(report.health["raw_events_ready"])
            self.assertFalse(report.health["first_board_features_ready"])
            self.assertTrue(report.warnings)
        finally:
            self._cleanup_database(database_path)


if __name__ == "__main__":
    unittest.main()
