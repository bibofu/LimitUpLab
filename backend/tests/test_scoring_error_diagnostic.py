import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.models import FirstBoardOutcome, LimitUpEvent
from app.repositories import SQLiteFirstBoardRepository
from app.services.scoring_error_diagnostic import build_scoring_error_diagnostic


class ScoringErrorDiagnosticTest(unittest.TestCase):
    def test_reports_top_false_positives_promoted_omissions_and_ablations(self) -> None:
        database_path = (
            Path(__file__).resolve().parents[1]
            / f"scoring-error-diagnostic-{uuid4().hex}.sqlite"
        )
        self.addCleanup(self._cleanup_database, database_path)
        repository = SQLiteFirstBoardRepository(database_path)
        events, outcomes = self._history(days=6)
        repository.upsert_outcomes(outcomes)

        report = build_scoring_error_diagnostic(
            events=events,
            start_date=events[0].trade_date,
            end_date=events[-1].trade_date,
            first_board_repository=repository,
            top_k=1,
        )

        self.assertEqual(report.trade_date_count, 6)
        self.assertEqual(report.top_sample_size, 6)
        self.assertEqual(report.false_positive_count, 6)
        self.assertEqual(report.false_negative_count, 6)
        self.assertEqual(report.top_promotion_rate, 0.0)
        self.assertEqual(report.market_promotion_rate, 0.25)
        self.assertEqual(report.promotion_rate_delta, -0.25)
        self.assertEqual(len(report.factors), 14)
        self.assertTrue(report.false_positive_samples[0].leading_factors)
        self.assertIn("影子假设", " ".join(report.warnings))

    @staticmethod
    def _cleanup_database(database_path: Path) -> None:
        for path in (
            database_path,
            database_path.with_name(f"{database_path.name}-wal"),
            database_path.with_name(f"{database_path.name}-shm"),
        ):
            path.unlink(missing_ok=True)

    @staticmethod
    def _history(days: int) -> tuple[list[LimitUpEvent], list[FirstBoardOutcome]]:
        events: list[LimitUpEvent] = []
        outcomes: list[FirstBoardOutcome] = []
        started = date(2026, 7, 1)
        created_at = datetime(2026, 8, 30, tzinfo=timezone.utc)
        first_limit_times = (time(9, 35), time(9, 55), time(10, 15), time(10, 35))
        for day_index in range(days):
            trade_date = started + timedelta(days=day_index * 7)
            for stock_index in range(4):
                symbol = f"00{day_index:02d}{stock_index:02d}"
                events.append(
                    LimitUpEvent(
                        symbol=symbol,
                        name=f"诊断{day_index}-{stock_index}",
                        trade_date=trade_date,
                        first_limit_time=first_limit_times[stock_index],
                        last_limit_time=time(14, 0),
                        seal_count=stock_index + 1,
                        break_count=stock_index,
                        closed_limit=True,
                        board_height=1,
                        amount=300_000_000 + stock_index * 100_000_000,
                        turnover_rate=5 + stock_index * 3,
                        industry="测试行业",
                        concept="测试题材",
                        next_open_pct=0,
                        next_high_pct=0,
                        next_close_pct=0,
                        three_day_return_pct=0,
                        five_day_return_pct=0,
                        continued_next_day=False,
                    )
                )
                promoted = stock_index == 3
                outcomes.append(
                    FirstBoardOutcome(
                        base_trade_date=trade_date,
                        symbol=symbol,
                        next_trade_date=trade_date + timedelta(days=1),
                        next_open_pct=0,
                        next_high_pct=10 if promoted else 1,
                        next_close_pct=10 if promoted else -1,
                        next_open_to_high_pct=10 if promoted else 1,
                        next_open_to_low_pct=0 if promoted else -2,
                        next_open_to_close_pct=10 if promoted else -1,
                        three_day_high_pct=10 if promoted else 1,
                        three_day_close_pct=10 if promoted else -1,
                        max_drawdown_3d=0 if promoted else -2,
                        three_day_open_to_high_pct=10 if promoted else 1,
                        three_day_open_to_close_pct=10 if promoted else -1,
                        max_drawdown_from_next_open_3d=0 if promoted else -2,
                        promoted_to_second_board=promoted,
                        next_day_ready=True,
                        three_day_ready=True,
                        outcome_ready=True,
                        outcome_version="test-v1",
                        created_at=created_at,
                    )
                )
        return events, outcomes


if __name__ == "__main__":
    unittest.main()
