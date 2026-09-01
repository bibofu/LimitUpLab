import json
import subprocess
import unittest
from datetime import date

from app.collectors.hithink_finance_collector import (
    HithinkFinanceCollector,
    HithinkFinanceError,
)


class FakeRunner:
    def __init__(self, payload: dict, returncode: int = 0) -> None:
        self.payload = payload
        self.returncode = returncode
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        return subprocess.CompletedProcess(
            args=command,
            returncode=self.returncode,
            stdout=json.dumps(self.payload),
            stderr="",
        )


class SequenceRunner:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        payload = self.payloads.pop(0)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )


class HithinkFinanceCollectorTest(unittest.TestCase):
    def test_hot_stock_snapshot_is_normalized(self) -> None:
        runner = FakeRunner(
            {
                "ok": True,
                "data": {
                    "timestamp": 1_787_356_800_000,
                    "item": [
                        {
                            "thscode": "002491.SZ",
                            "ticker": "002491",
                            "name": "通鼎互联",
                            "rank": 3,
                            "heat": "4382035",
                            "rank_change": 1,
                            "rank_trend": "up",
                        }
                    ],
                },
            }
        )
        collector = HithinkFinanceCollector(
            executable="hithink-finance",
            runner=runner,
        )

        snapshot = collector.collect_hot_stocks(period="day", limit=10)

        self.assertEqual(snapshot.source, "hithink-finance")
        self.assertEqual(snapshot.items[0].symbol, "002491")
        self.assertEqual(snapshot.items[0].heat, 4_382_035)
        self.assertIn("hot-stock", runner.commands[0])
        self.assertEqual(runner.commands[0][-2:], ["--format", "json"])

    def test_a_share_symbol_directory_returns_name_mapping(self) -> None:
        runner = FakeRunner(
            {
                "ok": True,
                "data": {
                    "item": [
                        {
                            "thscode": "603618.SH",
                            "ticker": "603618",
                            "name": "杭电股份",
                        }
                    ]
                },
            }
        )
        collector = HithinkFinanceCollector(
            executable="hithink-finance",
            runner=runner,
        )

        names = collector.collect_a_share_symbol_names()

        self.assertEqual(names, {"603618": "杭电股份"})
        self.assertIn("symbol", runner.commands[0])
        self.assertIn("10000", runner.commands[0])

    def test_index_catalog_snapshot_and_constituents_are_normalized(self) -> None:
        runner = SequenceRunner(
            [
                {
                    "ok": True,
                    "data": {
                        "item": [
                            {"thscode": "885001.TI", "name": "AI视频"},
                        ]
                    },
                },
                {
                    "ok": True,
                    "data": {
                        "item": [
                            {
                                "thscode": "885001.TI",
                                "price_change_ratio_pct": 5.74,
                                "turnover": 12_000_000_000,
                            }
                        ]
                    },
                },
                {
                    "ok": True,
                    "data": {
                        "item": [
                            {
                                "thscode": "300001.SZ",
                                "ticker": "300001",
                                "name": "主题样本",
                            }
                        ]
                    },
                },
            ]
        )
        collector = HithinkFinanceCollector(
            executable="hithink-finance",
            runner=runner,
        )

        catalog = collector.collect_index_catalog("cn_concept")
        snapshots = collector.collect_index_snapshots(catalog)
        constituents = collector.collect_index_constituents(snapshots[0])

        self.assertEqual(catalog[0].category, "cn_concept")
        self.assertEqual(snapshots[0].name, "AI视频")
        self.assertEqual(snapshots[0].change_pct, 5.74)
        self.assertEqual(constituents[0].symbol, "300001")
        self.assertIn("catalog", runner.commands[0])
        self.assertIn("snapshot", runner.commands[1])
        self.assertIn("constituents", runner.commands[2])

    def test_quarterly_income_statements_are_normalized(self) -> None:
        runner = FakeRunner(
            {
                "ok": True,
                "data": {
                    "item": [
                        {
                            "thscode": "600640.SH",
                            "fiscal_year": 2026,
                            "fiscal_period": "Q2",
                            "report_date_ms": 1_787_328_000_000,
                            "period_end_ms": 1_782_748_800_000,
                            "operating_income": 877_646_662.99,
                            "net_profit": 16_254_482.63,
                            "parent_holder_net_profit": 15_774_212.82,
                            "basic_eps": 0.0198,
                        }
                    ]
                },
            }
        )
        collector = HithinkFinanceCollector(
            executable="hithink-finance",
            runner=runner,
        )

        statements = collector.collect_income_statements("600640.SH", limit=6)

        self.assertEqual(statements[0].fiscal_period, "Q2")
        self.assertEqual(statements[0].operating_income, 877_646_662.99)
        self.assertIn("financials", runner.commands[0])
        self.assertIn("quarterly", runner.commands[0])
        self.assertIn("6", runner.commands[0])

    def test_market_snapshot_preserves_quote_performance(self) -> None:
        runner = FakeRunner(
            {
                "ok": True,
                "data": {
                    "timestamp": 1_787_356_800_000,
                    "item": [
                        {
                            "thscode": "002491.SZ",
                            "ticker": "002491",
                            "last_price": 12.34,
                            "price_change_ratio_pct": 6.78,
                            "turnover": 345_000_000,
                            "volume": 12_000_000,
                            "open_price": 11.8,
                            "high_price": 12.5,
                            "low_price": 11.7,
                            "prev_price": 11.56,
                        }
                    ],
                },
            }
        )
        collector = HithinkFinanceCollector(
            executable="hithink-finance",
            runner=runner,
        )

        snapshot = collector.collect_market_snapshots(["002491.SZ"])

        self.assertEqual(snapshot.items[0].symbol, "002491")
        self.assertEqual(snapshot.items[0].change_pct, 6.78)
        self.assertEqual(snapshot.items[0].turnover, 345_000_000)
        self.assertEqual(snapshot.items[0].volume, 12_000_000)
        self.assertEqual(snapshot.items[0].previous_close, 11.56)
        self.assertIn("snapshot", runner.commands[0])
        self.assertIn("002491.SZ", runner.commands[0])

    def test_full_market_snapshot_pages_and_resolves_names(self) -> None:
        runner = SequenceRunner(
            [
                {
                    "ok": True,
                    "data": {
                        "timestamp": 1_788_105_600_000,
                        "total": 1,
                        "item": [
                            {
                                "thscode": "002491.SZ",
                                "ticker": "002491",
                                "last_price": 12.34,
                                "price_change_ratio_pct": 6.78,
                                "turnover": 345_000_000,
                                "volume": 12_000_000,
                                "open_price": 11.8,
                                "high_price": 12.5,
                                "low_price": 11.7,
                                "prev_price": 11.56,
                            }
                        ],
                    },
                },
                {
                    "ok": True,
                    "data": {
                        "item": [
                            {
                                "thscode": "002491.SZ",
                                "ticker": "002491",
                                "name": "通鼎互联",
                            }
                        ]
                    },
                },
            ]
        )
        collector = HithinkFinanceCollector(
            executable="hithink-finance",
            runner=runner,
        )

        snapshot = collector.collect_full_market_snapshot()

        self.assertEqual(snapshot.total, 1)
        self.assertEqual(snapshot.items[0].name, "通鼎互联")
        self.assertIn("--offset", runner.commands[0])
        self.assertIn("symbol", runner.commands[1])

    def test_dragon_tiger_snapshot_preserves_capital_flow(self) -> None:
        runner = FakeRunner(
            {
                "ok": True,
                "data": {
                    "trade_date": "2026-08-21",
                    "stock_count": 1,
                    "stock_items": [
                        {
                            "thscode": "002491.SZ",
                            "ticker": "002491",
                            "name": "通鼎互联",
                            "change": 0.100156,
                            "net_value": 107_468_920.49,
                            "net_rate": 0.01479639,
                            "buy_value": 871_358_898.96,
                            "sell_value": 763_889_978.47,
                            "org_net_value": 167_141_952.47,
                            "hot_money_net_value": -89_957_240.89,
                            "range_days": 1,
                            "concept_list": [{"name": "5G"}],
                        }
                    ],
                },
            }
        )
        collector = HithinkFinanceCollector(
            executable="hithink-finance",
            runner=runner,
        )

        snapshot = collector.collect_dragon_tiger(
            trade_date=date(2026, 8, 21),
            query="通鼎",
        )

        self.assertEqual(snapshot.trade_date, date(2026, 8, 21))
        self.assertEqual(snapshot.items[0].change_pct, 10.0156)
        self.assertEqual(snapshot.items[0].organization_net_buy_amount, 167_141_952.47)
        self.assertEqual(snapshot.items[0].concepts, ["5G"])

    def test_limit_up_pool_uses_shanghai_midnight_and_normalizes_height(self) -> None:
        runner = FakeRunner(
            {
                "ok": True,
                "data": {
                    "item": [
                        {
                            "thscode": "002038.SZ",
                            "ticker": "002038",
                            "name": "双鹭药业",
                            "is_st": False,
                            "is_new": False,
                            "last_price": 7.08,
                            "price_change_ratio_pct": 9.9379,
                            "limit_up_time": "09:25",
                            "limit_up_reason": "创新药",
                            "continue_day_text": "2连板",
                            "continue_day_cnt": 2,
                            "seal_money": 127_471_619,
                        }
                    ],
                    "pagination": {"total": 54, "page": 1, "size": 100},
                },
            }
        )
        collector = HithinkFinanceCollector(
            executable="hithink-finance",
            runner=runner,
        )

        snapshot = collector.collect_limit_up_pool(trade_date=date(2026, 8, 21))

        self.assertEqual(snapshot.total, 54)
        self.assertEqual(snapshot.items[0].board_height, 2)
        date_ms_index = runner.commands[0].index("--date-ms") + 1
        self.assertEqual(runner.commands[0][date_ms_index], "1787241600000")

    def test_cli_error_envelope_becomes_typed_error(self) -> None:
        runner = FakeRunner(
            {
                "ok": False,
                "error": {
                    "code": "FUYAO_2003",
                    "message": "Invalid API key",
                    "retryable": False,
                },
            },
            returncode=1,
        )
        collector = HithinkFinanceCollector(
            executable="hithink-finance",
            runner=runner,
        )

        with self.assertRaises(HithinkFinanceError) as context:
            collector.collect_hot_stocks()

        self.assertEqual(context.exception.code, "FUYAO_2003")
        self.assertFalse(context.exception.retryable)


if __name__ == "__main__":
    unittest.main()
