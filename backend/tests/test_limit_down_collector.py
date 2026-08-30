import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from app.collectors import limit_down_collector


class LimitDownCollectorTest(unittest.TestCase):
    def setUp(self) -> None:
        limit_down_collector._cache.clear()

    @patch("app.collectors.limit_down_collector.ak.stock_zt_pool_dtgc_em")
    def test_limit_down_pool_uses_requested_date_and_normalizes_rows(self, loader) -> None:
        loader.return_value = pd.DataFrame(
            [
                {
                    "代码": "002963",
                    "名称": "豪尔赛",
                    "涨跌幅": -10.01,
                    "所属行业": "装修装饰",
                }
            ]
        )

        snapshot = limit_down_collector.collect_limit_down_pool(date(2026, 8, 28))

        loader.assert_called_once_with(date="20260828")
        self.assertEqual(snapshot.trade_date, date(2026, 8, 28))
        self.assertEqual(snapshot.source, "akshare-eastmoney-limit-down-pool")
        self.assertEqual(snapshot.items[0].symbol, "002963")
        self.assertEqual(snapshot.items[0].change_pct, -10.01)

    @patch("app.collectors.limit_down_collector.ak.stock_zt_pool_dtgc_em")
    def test_empty_limit_down_pool_is_a_valid_zero_count(self, loader) -> None:
        loader.return_value = pd.DataFrame()

        snapshot = limit_down_collector.collect_limit_down_pool(date(2026, 8, 27))

        self.assertEqual(snapshot.items, [])


if __name__ == "__main__":
    unittest.main()
