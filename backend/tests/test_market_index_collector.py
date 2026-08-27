import unittest
from datetime import date
from unittest.mock import patch

from app.collectors.market_index_collector import (
    _collect_market_index,
    _trend_from_rows,
)


class MarketIndexCollectorTest(unittest.TestCase):
    @staticmethod
    def _rows(*dates_and_closes: tuple[str, float]) -> list[dict[str, object]]:
        return [
            {"date": trade_date, "close": close}
            for trade_date, close in dates_and_closes
        ]

    @patch("app.collectors.market_index_collector._tencent_rows")
    def test_tencent_snapshot_matches_requested_trade_date(self, tencent_rows) -> None:
        tencent_rows.return_value = self._rows(
            ("2026-08-17", 3982.65),
            ("2026-08-18", 3990.30),
            ("2026-08-19", 3894.42),
        )

        snapshot = _collect_market_index(
            name="上证指数",
            display_symbol="000001.SH",
            akshare_symbol="sh000001",
            trade_date=date(2026, 8, 19),
        )

        self.assertEqual(snapshot.trade_date, date(2026, 8, 19))
        self.assertEqual(snapshot.close, 3894.42)
        self.assertEqual(snapshot.change_pct, -2.4)
        self.assertEqual(snapshot.source, "tencent_index_daily_spot")

    @patch("app.collectors.market_index_collector._sina_rows")
    @patch("app.collectors.market_index_collector._eastmoney_rows")
    @patch("app.collectors.market_index_collector._tencent_rows")
    def test_stale_sources_are_rejected(
        self,
        tencent_rows,
        eastmoney_rows,
        sina_rows,
    ) -> None:
        stale = self._rows(
            ("2026-08-17", 3982.65),
            ("2026-08-18", 3990.30),
        )
        tencent_rows.return_value = stale
        eastmoney_rows.return_value = stale
        sina_rows.return_value = stale

        with self.assertRaisesRegex(RuntimeError, "stale index data"):
            _collect_market_index(
                name="上证指数",
                display_symbol="000001.SH",
                akshare_symbol="sh000001",
                trade_date=date(2026, 8, 19),
            )

    def test_trend_uses_requested_trading_day_window(self) -> None:
        trend = _trend_from_rows(
            name="上证指数",
            display_symbol="000001.SH",
            rows=self._rows(
                ("2026-08-17", 100.0),
                ("2026-08-18", 102.0),
                ("2026-08-19", 101.0),
                ("2026-08-20", 104.0),
                ("2026-08-21", 103.0),
                ("2026-08-24", 105.0),
            ),
            requested_end_date=date(2026, 8, 24),
            days=5,
            source="test-index",
        )

        self.assertEqual(trend.start_date, date(2026, 8, 18))
        self.assertEqual(trend.end_date, date(2026, 8, 24))
        self.assertEqual(trend.return_pct, 2.94)
        self.assertEqual(trend.positive_days, 2)
        self.assertEqual(trend.negative_days, 2)
        self.assertEqual(trend.max_drawdown_pct, -0.98)
        self.assertEqual(len(trend.points), 5)

    def test_trend_rejects_stale_requested_date(self) -> None:
        with self.assertRaisesRegex(ValueError, "stale index data"):
            _trend_from_rows(
                name="上证指数",
                display_symbol="000001.SH",
                rows=self._rows(
                    ("2026-08-20", 104.0),
                    ("2026-08-21", 103.0),
                ),
                requested_end_date=date(2026, 8, 24),
                days=2,
                source="test-index",
            )


if __name__ == "__main__":
    unittest.main()
