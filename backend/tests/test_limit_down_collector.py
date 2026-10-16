import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from app.collectors import limit_down_collector
from app.collectors.hithink_finance_collector import HithinkLimitDownFact, HithinkLimitDownPoolSnapshot


class LimitDownCollectorTest(unittest.TestCase):
    # Prepare the isolated fixtures and dependencies shared by the tests in this class.
    def setUp(self) -> None:
        limit_down_collector._cache.clear()
        preferred = patch("app.collectors.limit_down_collector.HithinkFinanceCollector")
        self.cli = preferred.start().return_value.collect_all_limit_down_pool
        self.cli.side_effect = RuntimeError("offline")
        self.addCleanup(preferred.stop)
        worker = patch("app.collectors.limit_down_collector.run_in_killable_process")
        self.worker = worker.start()
        self.worker.side_effect = lambda fn, *args, **kwargs: fn(*args)
        self.addCleanup(worker.stop)

    # Regression scenario: limit down pool uses requested date and normalizes rows.
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

    # Regression scenario: empty limit down pool is a valid zero count.
    @patch("app.collectors.limit_down_collector.ak.stock_zt_pool_dtgc_em")
    def test_empty_limit_down_pool_is_a_valid_zero_count(self, loader) -> None:
        loader.return_value = pd.DataFrame()

        snapshot = limit_down_collector.collect_limit_down_pool(date(2026, 8, 27))

        self.assertEqual(snapshot.items, [])

    @patch("app.collectors.limit_down_collector.ak.stock_zt_pool_dtgc_em")
    def test_cli_membership_and_prices_win_while_industry_is_supplemented(self, loader) -> None:
        day = date(2026, 10, 15)
        self.cli.side_effect = None
        self.cli.return_value = HithinkLimitDownPoolSnapshot(day, [
            HithinkLimitDownFact("002963", "002963.SZ", "同花顺名称", -10.01),
        ])
        loader.return_value = pd.DataFrame([
            {"代码": "002963", "名称": "另一个名称", "涨跌幅": -9, "所属行业": "装修装饰"},
            {"代码": "000001", "名称": "额外成员", "涨跌幅": -10},
        ])
        snapshot = limit_down_collector.collect_limit_down_pool(day)
        self.assertEqual(snapshot.source, "hithink-finance+eastmoney-industry")
        self.assertEqual(len(snapshot.items), 1)
        self.assertEqual(snapshot.items[0].name, "同花顺名称")
        self.assertEqual(snapshot.items[0].change_pct, -10.01)
        self.assertEqual(snapshot.items[0].industry, "装修装饰")

    @patch("app.collectors.limit_down_collector.ak.stock_zt_pool_dtgc_em")
    def test_valid_cli_empty_pool_does_not_fallback(self, loader) -> None:
        day = date(2026, 10, 15)
        self.cli.side_effect = None
        self.cli.return_value = HithinkLimitDownPoolSnapshot(day, [])
        snapshot = limit_down_collector.collect_limit_down_pool(day)
        self.assertEqual(snapshot.items, [])
        self.assertEqual(snapshot.source, "hithink-finance")
        loader.assert_not_called()

    @patch("app.collectors.limit_down_collector.ak.stock_zt_pool_dtgc_em", side_effect=RuntimeError("offline"))
    def test_missing_industry_does_not_discard_valid_cli_pool(self, loader) -> None:
        day = date(2026, 10, 15)
        self.cli.side_effect = None
        self.cli.return_value = HithinkLimitDownPoolSnapshot(day, [
            HithinkLimitDownFact("002963", "002963.SZ", "豪尔赛", None),
        ])
        snapshot = limit_down_collector.collect_limit_down_pool(day)
        self.assertIsNone(snapshot.items[0].industry)
        self.assertIsNone(snapshot.items[0].change_pct)
        self.assertEqual(snapshot.source, "hithink-finance")

    def test_supplement_timeout_keeps_cli_pool_and_missing_industry(self) -> None:
        day = date(2026, 10, 15)
        self.cli.side_effect = None
        self.cli.return_value = HithinkLimitDownPoolSnapshot(day, [
            HithinkLimitDownFact("002963", "002963.SZ", "豪尔赛", -10.01),
        ])
        self.worker.side_effect = TimeoutError("supplement took too long")
        snapshot = limit_down_collector.collect_limit_down_pool(day)
        self.assertEqual(snapshot.items[0].symbol, "002963")
        self.assertIsNone(snapshot.items[0].industry)
        self.assertEqual(snapshot.source, "hithink-finance")
        self.worker.assert_called_once_with(
            limit_down_collector._eastmoney_items, day, timeout_seconds=5.0,
        )


if __name__ == "__main__":
    unittest.main()
