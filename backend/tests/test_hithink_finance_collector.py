import json
import os
import subprocess
import unittest
from datetime import date
from unittest.mock import patch

from app.collectors.hithink_finance_collector import (
    HithinkFinanceCollector,
    HithinkFinanceError,
)


class FakeRunner:
    # Prepare the init fixture or observation used by the surrounding regression scenario.
    def __init__(self, payload: dict, returncode: int = 0) -> None:
        self.payload = payload
        self.returncode = returncode
        self.commands: list[list[str]] = []

    # Build the subprocess.CompletedProcess fixture used by the surrounding regression scenario.
    def __call__(self, command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        return subprocess.CompletedProcess(
            args=command,
            returncode=self.returncode,
            stdout=json.dumps(self.payload),
            stderr="",
        )


class TimeoutRunner:
    # Simulate the collector terminating the CLI once its budget expires.
    def __call__(self, command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout", 0))


class SequenceRunner:
    # Prepare the init fixture or observation used by the surrounding regression scenario.
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.commands: list[list[str]] = []

    # Build the subprocess.CompletedProcess fixture used by the surrounding regression scenario.
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
    # Regression scenario: hot stock snapshot is normalized.
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

    # Regression scenario: a share symbol directory returns name mapping.
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

    # Regression scenario: index catalog snapshot and constituents are normalized.
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

    # Regression scenario: quarterly income statements are normalized.
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

    # Regression scenario: market snapshot preserves quote performance.
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

    # Regression scenario: stock history is unadjusted and end date is inclusive.
    def test_stock_history_is_unadjusted_and_end_date_is_inclusive(self) -> None:
        runner = FakeRunner(
            {
                "ok": True,
                "data": {
                    "item": [
                        {
                            "date_ms": 1_788_192_000_000,
                            "open_price": 10.1,
                            "high_price": 10.8,
                            "low_price": 10.0,
                            "close_price": 10.6,
                            "volume": 12_000_000,
                            "turnover": 126_000_000,
                        }
                    ]
                },
            }
        )
        collector = HithinkFinanceCollector(
            executable="hithink-finance",
            runner=runner,
        )

        history = collector.collect_stock_history(
            "002491.SZ",
            start_date=date(2026, 8, 31),
            end_date=date(2026, 9, 1),
            adjustment="none",
        )

        self.assertEqual(history.adjustment, "none")
        self.assertEqual(history.items[0].trade_date, date(2026, 9, 1))
        self.assertEqual(history.items[0].turnover, 126_000_000)
        command = runner.commands[0]
        self.assertEqual(command[1:3], ["--source", "remote"])
        self.assertEqual(command[command.index("--adjust") + 1], "none")
        self.assertEqual(
            command[command.index("--end-ms") + 1],
            "1788278400000",
        )

    # Regression scenario: full market snapshot pages and resolves names.
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

    # Regression scenario: dragon tiger snapshot preserves capital flow.
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

    def test_dragon_tiger_default_keeps_all_rows_and_explicit_limit_reports_truncation(self) -> None:
        rows = [{"ticker": "600001", "name": "测试", "range_days": 1 if i % 2 else 3}
                for i in range(210)]
        collector = HithinkFinanceCollector(executable="hithink-finance", runner=FakeRunner(
            {"ok": True, "data": {"trade_date": "2026-09-18", "stock_items": rows}}))
        complete = collector.collect_dragon_tiger(trade_date=date(2026, 9, 18))
        assert len(complete.items) == complete.matched_count == 210
        assert not complete.source_truncated
        limited = collector.collect_dragon_tiger(trade_date=date(2026, 9, 18), limit=30)
        assert len(limited.items) == 30
        assert limited.matched_count == 210
        assert limited.source_truncated

    # Regression scenario: limit up pool uses shanghai midnight and normalizes height.
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

    # Regression scenario: cli error envelope becomes typed error.
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

    # Regression scenario: batch runs raise the CLI budget so the CLI can finish its
    # own 4001/429 backoff instead of being killed mid-retry and reported as a timeout.
    def test_configured_timeout_follows_environment(self) -> None:
        runner = FakeRunner({"ok": True, "data": {"item": []}})
        with patch.dict(os.environ, {"LIMITUPLAB_HITHINK_TIMEOUT_SECONDS": "45"}):
            collector = HithinkFinanceCollector(
                executable="hithink-finance", runner=runner,
            )

        self.assertEqual(collector.timeout_seconds, 45.0)

    # Regression scenario: an unparsable budget keeps the documented 15 second default.
    def test_configured_timeout_falls_back_to_default_on_invalid_value(self) -> None:
        runner = FakeRunner({"ok": True, "data": {"item": []}})
        with patch.dict(os.environ, {"LIMITUPLAB_HITHINK_TIMEOUT_SECONDS": "not-a-number"}):
            collector = HithinkFinanceCollector(
                executable="hithink-finance", runner=runner,
            )

        self.assertEqual(collector.timeout_seconds, 15.0)

    # Regression scenario: a killed CLI surfaces as a retryable, budget-naming error.
    def test_timeout_error_is_retryable_and_reports_budget(self) -> None:
        collector = HithinkFinanceCollector(
            executable="hithink-finance", runner=TimeoutRunner(), timeout_seconds=45,
        )

        with self.assertRaises(HithinkFinanceError) as context:
            collector.collect_hot_stocks()

        self.assertEqual(context.exception.code, "timeout")
        self.assertTrue(context.exception.retryable)
        self.assertIn("45s", str(context.exception))


    # Regression scenario: financial report dates use Shanghai time, not UTC, and
    # the full income-statement payload survives normalization.
    def test_income_statements_use_shanghai_dates_and_full_fields(self) -> None:
        runner = FakeRunner(
            {
                "ok": True,
                "data": {
                    "item": [
                        {
                            "thscode": "600519.SH",
                            "fiscal_year": 2026,
                            "fiscal_period": "Q2",
                            "report_date_ms": 1_787_328_000_000,
                            "period_end_ms": 1_782_748_800_000,
                            "operating_income": 90_703_260_964.48,
                            "operating_costs": 9_473_762_565.88,
                            "operating_expenses": 30_946_044_878.08,
                            "sales_fee": 3_206_308_341.53,
                            "manage_fee": 3_635_355_263.82,
                            "research_and_development_expenses": 114_926_219.60,
                            "operating_profit": 61_411_291_686.27,
                            "interest_expenses": 76_587_662.57,
                            "profit_total": 61_438_419_177.29,
                            "income_tax_expense": 15_405_088_610.51,
                            "net_profit": 46_033_330_566.78,
                            "parent_holder_net_profit": 44_516_880_421.86,
                            "basic_eps": 35.57,
                        }
                    ]
                },
            }
        )
        collector = HithinkFinanceCollector(
            executable="hithink-finance",
            runner=runner,
        )

        statements = collector.collect_income_statements(
            "600519.SH", period="quarterly", limit=6,
        )

        statement = statements[0]
        self.assertEqual(statement.period_end, date(2026, 6, 30))
        self.assertEqual(statement.report_date, date(2026, 8, 22))
        self.assertEqual(statement.operating_costs, 9_473_762_565.88)
        self.assertEqual(statement.operating_profit, 61_411_291_686.27)
        self.assertEqual(statement.income_tax_expense, 15_405_088_610.51)
        self.assertIn("quarterly", runner.commands[0])
        self.assertIn("--limit", runner.commands[0])

    # Regression scenario: balance-sheet and cash-flow commands are normalized with
    # the same period identity and source fields.
    def test_balance_sheet_and_cash_flow_are_normalized(self) -> None:
        runner = SequenceRunner([
            {
                "ok": True,
                "data": {
                    "item": [
                        {
                            "thscode": "600519.SH",
                            "fiscal_year": 2026,
                            "fiscal_period": "Q2",
                            "report_date_ms": 1_787_328_000_000,
                            "period_end_ms": 1_782_748_800_000,
                            "assets_total": 309_050_784_569.31,
                            "total_current_assets": 260_724_668_103.40,
                            "non_current_nets_total": 48_326_116_465.91,
                            "cash": 53_518_798_979.08,
                            "accounts_receivable": 570_895.04,
                            "total_debt": 46_954_432_394.95,
                            "holder_equity_total": 262_096_352_174.36,
                        }
                    ]
                },
            },
            {
                "ok": True,
                "data": {
                    "item": [
                        {
                            "thscode": "600519.SH",
                            "fiscal_year": 2026,
                            "fiscal_period": "Q2",
                            "report_date_ms": 1_787_328_000_000,
                            "period_end_ms": 1_782_748_800_000,
                            "act_cash_flow_net": 70_690_750_119.06,
                            "invest_cash_flow_net": 25_640_543_520.60,
                            "financing_cash_flow_net": -37_944_297_802.12,
                            "pay_fixed_assets_etc_cash": 832_142_752.28,
                            "pay_dividends_profits_interest_cash": 35_044_145_842.75,
                            "cash_equivalents_net_addition": 58_385_486_034.90,
                        }
                    ]
                },
            },
        ])
        collector = HithinkFinanceCollector(
            executable="hithink-finance",
            runner=runner,
        )

        balance = collector.collect_balance_sheets("600519.SH")
        cash_flow = collector.collect_cash_flow_statements("600519.SH")

        self.assertEqual(balance[0].assets_total, 309_050_784_569.31)
        self.assertEqual(balance[0].holder_equity_total, 262_096_352_174.36)
        self.assertEqual(cash_flow[0].act_cash_flow_net, 70_690_750_119.06)
        self.assertEqual(cash_flow[0].financing_cash_flow_net, -37_944_297_802.12)
        self.assertIn("balance-sheet", runner.commands[0])
        self.assertIn("cash-flow", runner.commands[1])

    # Regression scenario: annual requests preserve the provider's FY label.
    def test_annual_income_statement_keeps_fy_period(self) -> None:
        runner = FakeRunner(
            {
                "ok": True,
                "data": {
                    "item": [
                        {
                            "thscode": "600519.SH",
                            "fiscal_year": 2025,
                            "fiscal_period": "FY",
                            "report_date_ms": 1_776_355_200_000,
                            "period_end_ms": 1_767_110_400_000,
                            "operating_income": 168_838_102_514.79,
                            "net_profit": 85_310_324_833.67,
                            "parent_holder_net_profit": 82_320_067_101.68,
                            "basic_eps": 65.66,
                        }
                    ]
                },
            }
        )
        collector = HithinkFinanceCollector(
            executable="hithink-finance",
            runner=runner,
        )

        statements = collector.collect_income_statements(
            "600519.SH", period="annual", limit=2,
        )

        self.assertEqual(statements[0].fiscal_period, "FY")
        self.assertIn("annual", runner.commands[0])


if __name__ == "__main__":
    unittest.main()
