import os
import unittest
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

from app.models import StockKLineBar
from app.repositories import SQLiteFirstBoardRepository
from app.services.stock_kline import (
    build_stock_kline_facts,
    load_stock_position_assessment,
)


TEST_TMP_ROOT = Path(os.getenv("LIMITUPLAB_TEST_TMP", Path(__file__).resolve().parents[1]))


class StockKLineServiceTest(unittest.TestCase):
    def test_merges_history_with_latest_spot_and_reuses_cache(self) -> None:
        database_path = TEST_TMP_ROOT / f"stock-kline-test-{uuid4().hex}.sqlite"
        repository = SQLiteFirstBoardRepository(database_path=database_path)
        end_date = date(2026, 8, 17)
        history_calls = 0
        spot_calls = 0

        def history_collector(
            _symbol: str,
            _days: int,
            _end_date: date | None,
        ) -> list[StockKLineBar]:
            nonlocal history_calls
            history_calls += 1
            return [
                StockKLineBar(
                    trade_date=end_date - timedelta(days=21 - index),
                    open=10 + index * 0.1,
                    high=10.5 + index * 0.1,
                    low=9.8 + index * 0.1,
                    close=10.2 + index * 0.1,
                    volume=1_000 + index * 10,
                )
                for index in range(21)
            ]

        def spot_collector(
            symbols: list[str],
            _trade_date: date,
        ) -> dict[str, StockKLineBar]:
            nonlocal spot_calls
            spot_calls += 1
            return {
                symbols[0]: StockKLineBar(
                    trade_date=end_date,
                    open=12.1,
                    high=12.8,
                    low=12,
                    close=12.7,
                    volume=2_000,
                )
            }

        try:
            facts = build_stock_kline_facts(
                symbol="002365",
                days=20,
                end_date=end_date,
                repository=repository,
                history_collector=history_collector,
                spot_collector=spot_collector,
            )
            cached = build_stock_kline_facts(
                symbol="002365",
                days=20,
                end_date=end_date,
                repository=repository,
                history_collector=history_collector,
                spot_collector=spot_collector,
            )

            self.assertTrue(facts.data_fresh)
            self.assertEqual(facts.data_as_of, end_date)
            self.assertEqual(facts.bars[-1].close, 12.7)
            self.assertEqual(len(facts.bars), 20)
            self.assertIsNotNone(facts.return_5d_pct)
            self.assertEqual(cached.data_as_of, end_date)
            self.assertEqual(history_calls, 1)
            self.assertEqual(spot_calls, 1)
        finally:
            for path in (
                database_path,
                database_path.with_name(f"{database_path.name}-wal"),
                database_path.with_name(f"{database_path.name}-shm"),
            ):
                path.unlink(missing_ok=True)

    def test_loads_125_point_in_time_bars_for_position_assessment(self) -> None:
        database_path = TEST_TMP_ROOT / f"stock-position-test-{uuid4().hex}.sqlite"
        repository = SQLiteFirstBoardRepository(database_path=database_path)
        end_date = date(2026, 8, 17)
        requested_days: list[int] = []

        def history_collector(
            symbol: str,
            days: int,
            _end_date: date | None,
        ) -> list[StockKLineBar]:
            requested_days.append(days)
            return [
                StockKLineBar(
                    trade_date=end_date - timedelta(days=124 - index),
                    open=10 + index * 0.02,
                    high=10.3 + index * 0.02,
                    low=9.8 + index * 0.02,
                    close=10.1 + index * 0.02,
                    volume=1_000 + index * 10,
                )
                for index in range(125)
            ]

        try:
            position = load_stock_position_assessment(
                symbol="002365",
                end_date=end_date,
                repository=repository,
                history_collector=history_collector,
                spot_collector=lambda _symbols, _trade_date: {},
            )

            self.assertEqual(requested_days, [125])
            self.assertEqual(position.bar_count, 125)
            self.assertNotEqual(position.primary.regime, "unclassified")
            self.assertTrue(position.evidence)
        finally:
            for path in (
                database_path,
                database_path.with_name(f"{database_path.name}-wal"),
                database_path.with_name(f"{database_path.name}-shm"),
            ):
                path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
