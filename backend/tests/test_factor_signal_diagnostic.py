import os
import unittest
from datetime import date, datetime, time, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np

from app.models import FirstBoardOutcome, LimitUpEvent
from app.repositories import SQLiteFirstBoardRepository
from app.services.factor_signal_diagnostic import (
    _average_ranks,
    _fit_lasso,
    _spearman_p_value,
    _spearman_rho,
    _standardize,
    _tercile_spread,
    build_factor_signal_diagnostic,
)


TEST_TMP_ROOT = Path(os.getenv("LIMITUPLAB_TEST_TMP", Path(__file__).resolve().parents[1]))


class FactorSignalStatsTest(unittest.TestCase):
    """Pure-statistics helpers, no DB and no rating engine."""

    def test_spearman_recovers_perfect_monotone(self) -> None:
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
        y = np.array([3.0, 5.0, 7.0, 9.0, 11.0, 13.0, 15.0, 17.0])
        rho = _spearman_rho(x, y)
        self.assertIsNotNone(rho)
        self.assertAlmostEqual(rho, 1.0, places=6)
        p = _spearman_p_value(rho, x.size)
        self.assertIsNotNone(p)
        self.assertLess(p, 0.01)

    def test_spearman_inverse_is_negative(self) -> None:
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        y = np.array([6.0, 5.0, 4.0, 3.0, 2.0, 1.0])
        rho = _spearman_rho(x, y)
        self.assertAlmostEqual(rho, -1.0, places=6)

    def test_spearman_constant_column_is_none(self) -> None:
        x = np.array([2.0, 2.0, 2.0, 2.0])
        y = np.array([1.0, 2.0, 3.0, 4.0])
        self.assertIsNone(_spearman_rho(x, y))
        self.assertIsNone(_spearman_p_value(None, 4))

    def test_average_ranks_handles_ties(self) -> None:
        ranks = _average_ranks(np.array([10.0, 10.0, 20.0, 10.0, 30.0]))
        # 10 appears 3 times -> ranks 1,2,3 average to 2.0; 20 -> 4; 30 -> 5.
        np.testing.assert_allclose(ranks, np.array([2.0, 2.0, 4.0, 2.0, 5.0]))

    def test_tercile_spread_direction(self) -> None:
        factor = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0])
        outcome = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0])
        top_n, bottom_n, top_mean, bottom_mean, spread = _tercile_spread(
            factor, outcome
        )
        self.assertEqual(top_n, 3)
        self.assertEqual(bottom_n, 3)
        self.assertAlmostEqual(top_mean, 8.0)
        self.assertAlmostEqual(bottom_mean, 2.0)
        self.assertAlmostEqual(spread, 6.0)

    def test_lasso_keeps_signal_drops_noise(self) -> None:
        rng = np.random.default_rng(7)
        signal = rng.normal(size=40)
        noise = rng.normal(size=40)
        y = 2.0 * signal + 0.05 * noise  # outcome driven by signal only
        X = np.column_stack([signal, noise])
        X_std, y_centered = _standardize(X, y)
        alpha = 0.05 * float(np.max(np.abs(X_std.T @ y_centered) / 40))
        beta = _fit_lasso(X_std, y_centered, alpha)
        self.assertGreater(abs(beta[0]), 1e-3)
        self.assertLess(abs(beta[1]), 1e-3)


class FactorSignalDiagnosticIntegrationTest(unittest.TestCase):
    """End-to-end over a temp SQLite DB with a planted monotone signal."""

    def setUp(self) -> None:
        TEST_TMP_ROOT.mkdir(exist_ok=True)

    def _database_path(self) -> Path:
        return TEST_TMP_ROOT / f"factor-signal-{uuid4().hex}.sqlite"

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
    ) -> LimitUpEvent:
        return LimitUpEvent(
            symbol=symbol,
            name=name,
            trade_date=trade_date,
            first_limit_time=first_limit_time,
            last_limit_time=first_limit_time,
            seal_count=1,
            break_count=0,
            closed_limit=True,
            board_height=1,
            amount=350_000_000,
            turnover_rate=6.0,
            industry="电子",
            concept="消费电子",
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
        next_open_to_close_pct: float,
    ) -> FirstBoardOutcome:
        return FirstBoardOutcome(
            base_trade_date=base_trade_date,
            symbol=symbol,
            next_trade_date=date(2026, 8, 11),
            next_open_pct=1.0,
            next_high_pct=5.0,
            next_close_pct=next_open_to_close_pct,
            next_open_to_high_pct=4.0,
            next_open_to_low_pct=-2.0,
            next_open_to_close_pct=next_open_to_close_pct,
            three_day_high_pct=8.0,
            three_day_close_pct=next_open_to_close_pct,
            max_drawdown_3d=-3.0,
            three_day_open_to_high_pct=7.0,
            three_day_open_to_close_pct=next_open_to_close_pct,
            max_drawdown_from_next_open_3d=-4.0,
            promoted_to_second_board=next_open_to_close_pct > 0,
            next_day_ready=True,
            three_day_ready=True,
            outcome_ready=True,
            outcome_version="test",
            created_at=datetime.now(timezone.utc),
        )

    def test_diagnostic_detects_planted_signal(self) -> None:
        database_path = self._database_path()
        try:
            repository = SQLiteFirstBoardRepository(database_path=database_path)
            # 4 trade dates x 10 main-board candidates; only first_limit_time
            # varies, and the outcome is a deterministic monotone function of it,
            # so the 首封时间 factor should dominate every other (constant) factor.
            trade_dates = [
                date(2026, 8, 5),
                date(2026, 8, 6),
                date(2026, 8, 7),
                date(2026, 8, 8),
            ]
            seal_times = [
                time(9, 35),
                time(9, 50),
                time(10, 15),
                time(10, 45),
                time(11, 15),
                time(13, 0),
                time(13, 30),
                time(14, 0),
                time(14, 30),
                time(14, 45),
            ]
            events: list[LimitUpEvent] = []
            outcomes: list[FirstBoardOutcome] = []
            for trade_date in trade_dates:
                for index, seal_time in enumerate(seal_times):
                    symbol = f"002{index + 1:03d}"
                    events.append(
                        self._event(
                            symbol=symbol,
                            name=f"样本{index + 1}",
                            trade_date=trade_date,
                            first_limit_time=seal_time,
                        )
                    )
                    minutes = seal_time.hour * 60 + seal_time.minute
                    outcomes.append(
                        self._make_outcome(
                            symbol=symbol,
                            base_trade_date=trade_date,
                            # earlier seal -> higher next-day entry return
                            next_open_to_close_pct=8.0 - 0.05 * minutes,
                        )
                    )
            repository.upsert_outcomes(outcomes)

            response = build_factor_signal_diagnostic(
                events=events,
                start_date=trade_dates[0],
                end_date=trade_dates[-1],
                first_board_repository=repository,
            )

            self.assertEqual(response.sample_size, len(outcomes))
            self.assertEqual(response.trade_date_count, len(trade_dates))
            self.assertEqual(len(response.factors), 14)
            self.assertTrue(response.verdict)
            self.assertTrue(response.caveats)
            self.assertEqual(response.strongest_factor_key, "first_limit_time")
            first_limit_row = next(
                row for row in response.factors if row.factor_key == "first_limit_time"
            )
            self.assertIsNotNone(first_limit_row.spearman_rho)
            self.assertGreater(first_limit_row.spearman_rho, 0.5)
            self.assertTrue(first_limit_row.significant_after_bonferroni)
        finally:
            self._cleanup_database(database_path)

    def test_diagnostic_too_few_samples_is_honest(self) -> None:
        database_path = self._database_path()
        try:
            repository = SQLiteFirstBoardRepository(database_path=database_path)
            trade_date = date(2026, 8, 5)
            events = [
                self._event("002001", "样本一", trade_date, time(9, 35)),
                self._event("002002", "样本二", trade_date, time(10, 15)),
            ]
            repository.upsert_outcomes(
                [
                    self._make_outcome("002001", trade_date, 3.0),
                    self._make_outcome("002002", trade_date, -1.0),
                ]
            )
            response = build_factor_signal_diagnostic(
                events=events,
                start_date=trade_date,
                end_date=trade_date,
                first_board_repository=repository,
            )
            self.assertLess(response.sample_size, 10)
            self.assertIn("样本量", response.verdict)
        finally:
            self._cleanup_database(database_path)


if __name__ == "__main__":
    unittest.main()
