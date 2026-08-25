import unittest
from pathlib import Path

from app.agents.query_contract import build_limit_up_query_contract
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


if __name__ == "__main__":
    unittest.main()
