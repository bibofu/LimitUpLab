import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.models import StockDailyBar, StockIntradayKLineBar, StockKLineBar
from app.repositories import SQLiteFirstBoardRepository
from app.services.stock_kline import (
    build_stock_kline_facts,
    load_stock_detail_market_data,
    load_stock_intraday_bars,
    load_stock_intraday_history,
    load_stock_kline_bars,
    load_stock_position_assessment,
)


TEST_TMP_ROOT = Path(os.getenv("LIMITUPLAB_TEST_TMP", Path(__file__).resolve().parents[1]))


class StockKLineServiceTest(unittest.TestCase):
    def test_intraday_history_groups_five_sessions_and_preserves_partial_errors(self) -> None:
        database_path = TEST_TMP_ROOT / f"stock-intraday-history-{uuid4().hex}.sqlite"
        repository = SQLiteFirstBoardRepository(database_path=database_path)
        trade_dates = [date(2026, 8, 24) + timedelta(days=index) for index in range(5)]
        daily_bars = [
            StockKLineBar(
                trade_date=date(2026, 8, 21) + timedelta(days=index),
                open=10 + index,
                high=10.5 + index,
                low=9.5 + index,
                close=10.2 + index,
                volume=1_000,
            )
            for index in range(9)
        ]

        def collector(
            _symbol: str,
            trade_date: date,
            _period: int,
        ) -> list[StockIntradayKLineBar]:
            if trade_date == trade_dates[2]:
                raise RuntimeError("upstream unavailable")
            return [
                StockIntradayKLineBar(
                    timestamp=datetime.combine(trade_date, datetime.min.time()).replace(
                        hour=9,
                        minute=31,
                    ),
                    open=10,
                    high=10.2,
                    low=9.9,
                    close=10.1,
                    volume=1_000,
                    amount=10_100,
                )
            ]

        try:
            response = load_stock_intraday_history(
                symbol="sz002624",
                trade_dates=trade_dates,
                period=1,
                daily_bars=daily_bars,
                repository=repository,
                collector=collector,
            )

            self.assertEqual(response.symbol, "002624")
            self.assertEqual(response.requested_days, 5)
            self.assertEqual(len(response.days), 5)
            self.assertFalse(response.complete)
            self.assertEqual(response.missing_trade_dates, [trade_dates[2]])
            self.assertEqual(response.days[2].status, "error")
            self.assertEqual(response.days[2].error, "RuntimeError")
            self.assertEqual(response.days[0].previous_close, 12.2)
            self.assertEqual(response.data_as_of, trade_dates[-1])
        finally:
            _remove_database_files(database_path)

    def test_intraday_cache_survives_repository_recreation(self) -> None:
        database_path = TEST_TMP_ROOT / f"stock-intraday-{uuid4().hex}.sqlite"
        trade_date = date(2026, 8, 31)
        calls = 0

        def collector(
            _symbol: str,
            _trade_date: date,
            _period: int,
        ) -> list[StockIntradayKLineBar]:
            nonlocal calls
            calls += 1
            return [
                StockIntradayKLineBar(
                    timestamp=datetime(2026, 8, 31, 9, 31),
                    open=10,
                    high=10.2,
                    low=9.9,
                    close=10.1,
                    volume=1_000,
                    amount=10_100,
                )
            ]

        try:
            first = load_stock_intraday_bars(
                symbol="sz002328",
                trade_date=trade_date,
                period=1,
                repository=SQLiteFirstBoardRepository(database_path=database_path),
                collector=collector,
            )
            second = load_stock_intraday_bars(
                symbol="002328",
                trade_date=trade_date,
                period=1,
                repository=SQLiteFirstBoardRepository(database_path=database_path),
                collector=collector,
            )

            self.assertEqual(calls, 1)
            self.assertEqual(first, second)
            self.assertEqual(second[0].timestamp, datetime(2026, 8, 31, 9, 31))
        finally:
            _remove_database_files(database_path)

    def test_concurrent_intraday_requests_share_one_cache_fill(self) -> None:
        database_path = TEST_TMP_ROOT / f"stock-intraday-lock-{uuid4().hex}.sqlite"
        trade_date = date(2026, 8, 31)
        collector_started = threading.Event()
        release_collector = threading.Event()
        calls = 0

        def collector(
            _symbol: str,
            _trade_date: date,
            _period: int,
        ) -> list[StockIntradayKLineBar]:
            nonlocal calls
            calls += 1
            collector_started.set()
            release_collector.wait(timeout=2)
            return [
                StockIntradayKLineBar(
                    timestamp=datetime(2026, 8, 31, 9, 31),
                    open=10,
                    high=10.2,
                    low=9.9,
                    close=10.1,
                    volume=1_000,
                    amount=10_100,
                )
            ]

        def load() -> list[StockIntradayKLineBar]:
            return load_stock_intraday_bars(
                symbol="002328",
                trade_date=trade_date,
                period=1,
                repository=SQLiteFirstBoardRepository(database_path=database_path),
                collector=collector,
            )

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(load)
                self.assertTrue(collector_started.wait(timeout=2))
                second = executor.submit(load)
                release_collector.set()
                results = [first.result(timeout=2), second.result(timeout=2)]

            self.assertEqual(calls, 1)
            self.assertEqual([len(result) for result in results], [1, 1])
        finally:
            release_collector.set()
            _remove_database_files(database_path)

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
