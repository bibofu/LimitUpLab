import os
import unittest
from datetime import date, time
from pathlib import Path
from uuid import uuid4

from app.models import LimitUpEvent
from app.repositories import SQLiteFirstBoardRepository
from app.services.data_health import build_agent_data_health
from app.services.first_board_features import build_first_board_features


TEST_TMP_ROOT = Path(
    os.getenv("LIMITUPLAB_TEST_TMP", Path(__file__).resolve().parents[1])
)


class AgentDataHealthTest(unittest.TestCase):
    def setUp(self) -> None:
        TEST_TMP_ROOT.mkdir(exist_ok=True)

    def _database_path(self) -> Path:
        return TEST_TMP_ROOT / f"data-health-test-{uuid4().hex}.sqlite"

    def _cleanup_database(self, database_path: Path) -> None:
        for path in (
            database_path,
            database_path.with_name(f"{database_path.name}-wal"),
            database_path.with_name(f"{database_path.name}-shm"),
        ):
            path.unlink(missing_ok=True)

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

    def test_missing_events_returns_missing_status(self) -> None:
        health = build_agent_data_health(events=[])

        self.assertEqual(health.status, "missing")
        self.assertFalse(health.raw_events_ready)
        self.assertTrue(health.warnings)

    def test_features_without_enrichment_returns_partial_status(self) -> None:
        database_path = self._database_path()
        try:
            repository = SQLiteFirstBoardRepository(database_path=database_path)
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
        finally:
            self._cleanup_database(database_path)


if __name__ == "__main__":
    unittest.main()
