import os
import unittest
from datetime import date, datetime, time, timezone
from pathlib import Path
from uuid import uuid4

from app.models import FirstBoardOutcome, LimitUpEvent
from app.repositories import SQLiteFirstBoardRepository
from app.services.rating_backtest import build_rating_backtest


TEST_TMP_ROOT = Path(os.getenv("LIMITUPLAB_TEST_TMP", Path(__file__).resolve().parents[1]))


class RatingBacktestTest(unittest.TestCase):
    def setUp(self) -> None:
        TEST_TMP_ROOT.mkdir(exist_ok=True)

    def _database_path(self) -> Path:
        return TEST_TMP_ROOT / f"rating-backtest-{uuid4().hex}.sqlite"

    def _cleanup_database(self, database_path: Path) -> None:
        for path in (
            database_path,
            database_path.with_name(f"{database_path.name}-wal"),
            database_path.with_name(f"{database_path.name}-shm"),
        ):
            path.unlink(missing_ok=True)

    def _event(
        self,
        symbol: str,
        name: str,
        trade_date: date,
        first_limit_time: time,
        break_count: int,
    ) -> LimitUpEvent:
        return LimitUpEvent(
            symbol=symbol,
            name=name,
            trade_date=trade_date,
            first_limit_time=first_limit_time,
            last_limit_time=first_limit_time,
            seal_count=1,
            break_count=break_count,
            closed_limit=True,
            board_height=1,
            amount=350_000_000,
            turnover_rate=6.0,
            industry="\u5143\u4ef6",
            concept="\u667a\u80fd\u7535\u7f51",
            next_open_pct=0,
            next_high_pct=0,
            next_close_pct=0,
            three_day_return_pct=0,
            five_day_return_pct=0,
            continued_next_day=False,
        )

    def _make_outcome(
        self,
        symbol: str,
        base_trade_date: date,
        next_close_pct: float,
        three_day_close_pct: float,
        promoted: bool,
    ) -> FirstBoardOutcome:
        return FirstBoardOutcome(
            base_trade_date=base_trade_date,
            symbol=symbol,
            next_trade_date=date(2026, 8, 11),
            next_open_pct=1.0,
            next_high_pct=5.0,
            next_close_pct=next_close_pct,
            three_day_high_pct=8.0,
            three_day_close_pct=three_day_close_pct,
            max_drawdown_3d=-3.0,
            promoted_to_second_board=promoted,
            outcome_ready=True,
            outcome_version="test",
            created_at=datetime.now(timezone.utc),
        )

    def test_backtest_aggregates_rating_buckets_and_failures(self) -> None:
        database_path = self._database_path()
        try:
            repository = SQLiteFirstBoardRepository(database_path=database_path)
            trade_date = date(2026, 8, 10)
            events = [
                self._event("002001", "\u9ad8\u5206\u6837\u672c", trade_date, time(9, 35), 0),
                self._event("002002", "\u5931\u8d25\u6837\u672c", trade_date, time(9, 38), 0),
                self._event("002003", "\u4f4e\u5206\u6837\u672c", trade_date, time(14, 20), 3),
            ]
            repository.upsert_outcomes(
                [
                    self._make_outcome("002001", trade_date, 3.0, 6.0, True),
                    self._make_outcome("002002", trade_date, -2.0, -5.0, False),
                    self._make_outcome("002003", trade_date, -1.0, -3.0, False),
                ]
            )

            response = build_rating_backtest(
                events=events,
                start_date=trade_date,
                end_date=trade_date,
                first_board_repository=repository,
            )

            self.assertEqual(response.sample_size, 3)
            self.assertEqual(response.outcome_ready_count, 3)
            self.assertTrue(response.buckets)
            self.assertTrue(response.observations)
            self.assertTrue(
                any(item.symbol == "002002" for item in response.failure_samples)
            )
        finally:
            self._cleanup_database(database_path)


if __name__ == "__main__":
    unittest.main()
