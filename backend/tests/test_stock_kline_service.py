import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.models import StockDailyBar, StockKLineBar
from app.repositories import SQLiteFirstBoardRepository
from app.services.stock_kline import (
    build_stock_kline_facts,
    load_stock_detail_market_data,
    load_stock_kline_bars,
    load_stock_position_assessment,
)


TEST_TMP_ROOT = Path(os.getenv("LIMITUPLAB_TEST_TMP", Path(__file__).resolve().parents[1]))


class StockKLineServiceTest(unittest.TestCase):
    def test_complete_current_day_cache_skips_external_collectors(self) -> None:
        database_path = TEST_TMP_ROOT / f"stock-kline-current-{uuid4().hex}.sqlite"
        repository = SQLiteFirstBoardRepository(database_path=database_path)
        end_date = date.today()
        now = datetime.now(timezone.utc)
        repository.upsert_daily_bars(
            [
                StockDailyBar(
                    symbol="600001",
                    trade_date=end_date - timedelta(days=20 - index),
                    open=10 + index * 0.1,
                    high=10.4 + index * 0.1,
                    low=9.8 + index * 0.1,
                    close=10.2 + index * 0.1,
                    volume=1_000 + index,
                    amount=0,
                    change_pct=None,
                    source="test-cache",
                    created_at=now,
                )
                for index in range(21)
            ]
        )
        history_calls = 0
        spot_calls = 0

        def history_collector(
            _symbol: str,
            _days: int,
            _end_date: date | None,
        ) -> list[StockKLineBar]:
            nonlocal history_calls
            history_calls += 1
            return []

        def spot_collector(
            _symbols: list[str],
            _trade_date: date,
        ) -> dict[str, StockKLineBar]:
            nonlocal spot_calls
            spot_calls += 1
            return {}

        try:
            bars = load_stock_kline_bars(
                symbol="600001",
                days=21,
                end_date=end_date,
                repository=repository,
                history_collector=history_collector,
                spot_collector=spot_collector,
            )

            self.assertEqual(len(bars), 21)
            self.assertEqual(history_calls, 0)
            self.assertEqual(spot_calls, 0)
        finally:
            _remove_database_files(database_path)

    def test_partial_history_refresh_is_not_repeated_within_ttl(self) -> None:
        database_path = TEST_TMP_ROOT / f"stock-kline-partial-{uuid4().hex}.sqlite"
        repository = SQLiteFirstBoardRepository(database_path=database_path)
        end_date = date(2026, 8, 18)
        history_calls = 0

        def history_collector(
            _symbol: str,
            _days: int,
            _end_date: date | None,
        ) -> list[StockKLineBar]:
            nonlocal history_calls
            history_calls += 1
            return [
                StockKLineBar(
                    trade_date=end_date - timedelta(days=4 - index),
                    open=10,
                    high=10.5,
                    low=9.8,
                    close=10.2,
                    volume=1_000,
                )
                for index in range(5)
            ]

        try:
            first = load_stock_kline_bars(
                symbol="600002",
                days=21,
                end_date=end_date,
                repository=repository,
                history_collector=history_collector,
                spot_collector=lambda _symbols, _trade_date: {},
            )
            second = load_stock_kline_bars(
                symbol="600002",
                days=21,
                end_date=end_date,
                repository=repository,
                history_collector=history_collector,
                spot_collector=lambda _symbols, _trade_date: {},
            )

            self.assertEqual(len(first), 5)
            self.assertEqual(len(second), 5)
            self.assertEqual(history_calls, 1)
        finally:
            _remove_database_files(database_path)

    def test_concurrent_history_requests_are_coalesced(self) -> None:
        database_path = TEST_TMP_ROOT / f"stock-kline-concurrent-{uuid4().hex}.sqlite"
        repository = SQLiteFirstBoardRepository(database_path=database_path)
        end_date = date(2026, 8, 19)
        history_calls = 0
        calls_lock = threading.Lock()
        collector_started = threading.Event()
        release_collector = threading.Event()

        def history_collector(
            _symbol: str,
            _days: int,
            _end_date: date | None,
        ) -> list[StockKLineBar]:
            nonlocal history_calls
            with calls_lock:
                history_calls += 1
            collector_started.set()
            release_collector.wait(timeout=2)
            return [
                StockKLineBar(
                    trade_date=end_date - timedelta(days=20 - index),
                    open=10,
                    high=10.5,
                    low=9.8,
                    close=10.2,
                    volume=1_000,
                )
                for index in range(21)
            ]

        def load() -> list[StockKLineBar]:
            return load_stock_kline_bars(
                symbol="600004",
                days=21,
                end_date=end_date,
                repository=repository,
                history_collector=history_collector,
                spot_collector=lambda _symbols, _trade_date: {},
            )

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(load)
                self.assertTrue(collector_started.wait(timeout=2))
                second = executor.submit(load)
                release_collector.set()
                results = [first.result(timeout=2), second.result(timeout=2)]

            self.assertEqual([len(item) for item in results], [21, 21])
            self.assertEqual(history_calls, 1)
        finally:
            release_collector.set()
            _remove_database_files(database_path)

    def test_detail_bundle_reuses_one_history_load_for_position_and_chart(self) -> None:
        database_path = TEST_TMP_ROOT / f"stock-market-data-{uuid4().hex}.sqlite"
        repository = SQLiteFirstBoardRepository(database_path=database_path)
        end_date = date(2026, 8, 18)
        requested_days: list[int] = []

        def history_collector(
            _symbol: str,
            days: int,
            _end_date: date | None,
        ) -> list[StockKLineBar]:
            requested_days.append(days)
            return [
                StockKLineBar(
                    trade_date=end_date - timedelta(days=124 - index),
                    open=10 + index * 0.02,
                    high=10.4 + index * 0.02,
                    low=9.8 + index * 0.02,
                    close=10.2 + index * 0.02,
                    volume=1_000 + index,
                )
                for index in range(125)
            ]

        try:
            result = load_stock_detail_market_data(
                symbol="600003",
                days=60,
                end_date=end_date,
                position_trade_date=end_date,
                repository=repository,
                history_collector=history_collector,
                spot_collector=lambda _symbols, _trade_date: {},
            )

            self.assertEqual(requested_days, [125])
            self.assertEqual(len(result.kline), 60)
            self.assertEqual(result.latest_close.trade_date, end_date)
            self.assertIsNotNone(result.position)
            self.assertEqual(result.position.bar_count, 125)
        finally:
            _remove_database_files(database_path)

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
            _remove_database_files(database_path)

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
            _remove_database_files(database_path)


def _remove_database_files(database_path: Path) -> None:
    for path in (
        database_path,
        database_path.with_name(f"{database_path.name}-wal"),
        database_path.with_name(f"{database_path.name}-shm"),
    ):
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
