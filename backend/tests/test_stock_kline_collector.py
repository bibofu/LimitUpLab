import unittest
from datetime import datetime

from app.collectors.stock_kline_collector import (
    _aggregate_intraday_rows,
    _normalize_stock_symbol,
    _parse_datetime,
)
from app.models import StockIntradayKLineBar


class StockKLineCollectorTest(unittest.TestCase):
    def test_normalize_stock_symbol(self) -> None:
        self.assertEqual(_normalize_stock_symbol("001259"), "sz001259")
        self.assertEqual(_normalize_stock_symbol("600519"), "sh600519")
        self.assertEqual(_normalize_stock_symbol("sz001259"), "sz001259")

    def test_normalize_stock_symbol_rejects_invalid_symbol(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_stock_symbol("abc")

    def test_parse_datetime(self) -> None:
        self.assertEqual(
            _parse_datetime("2026-05-20 09:35:00"),
            datetime(2026, 5, 20, 9, 35),
        )

    def test_aggregate_intraday_rows(self) -> None:
        rows = [
            StockIntradayKLineBar(
                timestamp=datetime(2026, 5, 20, 9, 31 + index),
                open=10 + index,
                close=11 + index,
                high=12 + index,
                low=9 + index,
                volume=100,
                amount=1_000,
            )
            for index in range(5)
        ]

        [bar] = _aggregate_intraday_rows(rows, period=5)

        self.assertEqual(bar.timestamp, datetime(2026, 5, 20, 9, 35))
        self.assertEqual(bar.open, 10)
        self.assertEqual(bar.close, 15)
        self.assertEqual(bar.high, 16)
        self.assertEqual(bar.low, 9)
        self.assertEqual(bar.volume, 500)
        self.assertEqual(bar.amount, 5_000)


if __name__ == "__main__":
    unittest.main()
