import unittest
from pathlib import Path

from app.agents.query_contract import (
    build_limit_up_query_contract,
    build_market_event_query_contract,
    looks_like_market_event_query,
)
from app.agents.query_contract_eval import (
    load_query_contract_eval_cases,
    run_query_contract_eval_suite,
)


class QueryContractV2Test(unittest.TestCase):
    def test_contract_fixture_passes(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / "query_contract_v2_cases.json"

        suite = run_query_contract_eval_suite(
            load_query_contract_eval_cases(fixture_path)
        )

        failures = {
            result.case_id: result.failures
            for result in suite.results
            if not result.passed
        }
        self.assertEqual(suite.total, 36)
        self.assertTrue(suite.ok, failures)

    def test_user_filters_override_conflicting_planner_arguments(self) -> None:
        contract = build_limit_up_query_contract(
            "2026-08-07 创业板二板股成交额前5名",
            planner_arguments={
                "trade_date": "2026-08-08",
                "market": "main_board",
                "board_height": 4,
                "sort_by": "first_limit_time",
                "limit": 30,
            },
        )

        self.assertEqual(contract.trade_date.isoformat(), "2026-08-07")
        self.assertEqual(contract.market, "chinext")
        self.assertEqual(contract.board_height, 2)
        self.assertEqual(contract.sort_by, "amount")
        self.assertEqual(contract.limit, 5)

    def test_failed_and_intraday_opened_are_distinct(self) -> None:
        failed = build_limit_up_query_contract("今天炸板票有哪些")
        opened = build_limit_up_query_contract("今天有哪些涨停股曾开板")

        self.assertEqual(failed.event_status, "failed")
        self.assertEqual(opened.event_status, "broken_intraday")

    def test_plain_limit_up_scope_overrides_wrong_planner_status(self) -> None:
        contract = build_limit_up_query_contract(
            "今天创业板有哪些股票涨停",
            planner_arguments={"event_status": "failed"},
        )

        self.assertEqual(contract.event_status, "closed")

    def test_undated_query_ignores_planner_historical_date(self) -> None:
        contract = build_limit_up_query_contract(
            "首板票有哪些",
            planner_arguments={"trade_date": "2026-08-07"},
        )

        self.assertIsNone(contract.trade_date)
        self.assertEqual(contract.board_height, 1)

    def test_limit_down_wording_compiles_to_one_market_event_type(self) -> None:
        for message in (
            "今天跌停的票有哪些",
            "列一下最新跌停名单",
            "今天谁封死跌停",
            "跌幅限制的股票有几只",
        ):
            with self.subTest(message=message):
                contract = build_market_event_query_contract(message)
                self.assertEqual(contract.event_type, "limit_down")

    def test_explicit_limit_down_overrides_wrong_planner_event_type(self) -> None:
        contract = build_market_event_query_contract(
            "今天跌停的票有哪些",
            planner_arguments={"event_type": "limit_up", "limit": 20},
        )

        self.assertEqual(contract.event_type, "limit_down")
        self.assertEqual(contract.limit, 20)

    def test_unknown_planner_event_type_is_rejected_without_silent_default(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported market event type"):
            build_market_event_query_contract(
                "查询今天的价格限制事件",
                planner_arguments={"event_type": "mystery_event"},
            )

    def test_market_event_list_signal_excludes_rules_and_causes(self) -> None:
        self.assertTrue(looks_like_market_event_query("今天跌停的票有哪些"))
        self.assertFalse(looks_like_market_event_query("股票为什么会跌停"))
        self.assertFalse(looks_like_market_event_query("创业板跌停制度是什么"))


if __name__ == "__main__":
    unittest.main()
