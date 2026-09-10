import unittest
from datetime import date

from app.agents.query_contract import (
    build_conversation_query_understanding_view,
    build_query_understanding_view,
    build_limit_up_query_contract,
    build_market_event_query_contract,
    looks_like_limit_up_sector_summary_question,
    looks_like_named_limit_up_sector_list_question,
    looks_like_market_event_query,
    query_reference_date_override,
)


class QueryContractV2Test(unittest.TestCase):

    def test_multi_turn_query_view_retains_referenced_date_and_market(self) -> None:
        with query_reference_date_override(date(2026, 5, 15)):
            previous_day = build_conversation_query_understanding_view(
                ["今天创业板涨停股有哪些？", "换成前一个交易日呢？"]
            )
            same_day = build_conversation_query_understanding_view(
                ["今天创业板涨停股有哪些？", "同一天主板的呢？"]
            )

        self.assertEqual(previous_day["trade_date"], "2026-05-14")
        self.assertEqual(previous_day["market"], "chinext")
        self.assertEqual(same_day["trade_date"], "2026-05-15")
        self.assertEqual(same_day["market"], "main_board")
        self.assertEqual(same_day["context_reference"], "previous_date")

    # Regression scenario: relative dates are stable inside an eval request.
    def test_relative_dates_use_request_scoped_anchor(self) -> None:
        with query_reference_date_override(date(2026, 9, 14)):
            today = build_query_understanding_view("今天创业板涨停股")
            yesterday = build_query_understanding_view("昨天主板涨停股")

        self.assertEqual(today["reference_date"], "2026-09-14")
        self.assertEqual(today["trade_date"], "2026-09-14")
        self.assertEqual(today["market"], "chinext")
        self.assertEqual(yesterday["trade_date"], "2026-09-11")
        self.assertEqual(yesterday["market"], "main_board")

    # Regression scenario: executed canonical defaults complement explicit wording.
    def test_query_view_merges_executed_contract_with_explicit_fields(self) -> None:
        with query_reference_date_override(date(2026, 5, 15)):
            view = build_query_understanding_view(
                "今天301489三连板成交额前2名",
                executed_contract={
                    "trade_date": "2026-05-14",
                    "sort_by": "amount",
                    "sort_order": "desc",
                    "limit": 2,
                },
            )

        self.assertEqual(view["trade_date"], "2026-05-15")
        self.assertEqual(view["symbol"], "301489")
        self.assertEqual(view["board_height"], 3)
        self.assertEqual(view["sort_by"], "amount")
        self.assertEqual(view["limit"], 2)

    def test_query_view_exposes_event_sort_quantity_and_scope_constraints(self) -> None:
        with query_reference_date_override(date(2026, 5, 15)):
            ranked = build_query_understanding_view("今天涨停股成交额前2名")
            highest = build_query_understanding_view("列出今天所有最高板")

        self.assertEqual(ranked["event_type"], "limit_up")
        self.assertEqual(ranked["sort_by"], "amount")
        self.assertEqual(ranked["sort_order"], "desc")
        self.assertEqual(ranked["limit"], 2)
        self.assertEqual(highest["highest_only"], True)
        self.assertEqual(highest["exhaustive"], True)

    # Regression scenario: user filters override conflicting planner arguments.
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

    # Regression scenario: failed and intraday opened are distinct.
    def test_failed_and_intraday_opened_are_distinct(self) -> None:
        failed = build_limit_up_query_contract("今天炸板票有哪些")
        opened = build_limit_up_query_contract("今天有哪些涨停股曾开板")

        self.assertEqual(failed.event_status, "failed")
        self.assertEqual(opened.event_status, "broken_intraday")

    # Regression scenario: plain limit up scope overrides wrong planner status.
    def test_plain_limit_up_scope_overrides_wrong_planner_status(self) -> None:
        contract = build_limit_up_query_contract(
            "今天创业板有哪些股票涨停",
            planner_arguments={"event_status": "failed"},
        )

        self.assertEqual(contract.event_status, "closed")

    # Regression scenario: undated query ignores planner historical date.
    def test_undated_query_ignores_planner_historical_date(self) -> None:
        contract = build_limit_up_query_contract(
            "首板票有哪些",
            planner_arguments={"trade_date": "2026-08-07"},
        )

        self.assertIsNone(contract.trade_date)
        self.assertEqual(contract.board_height, 1)

    # Regression scenario: recent limit up sector summary defaults to seven trade days.
    def test_recent_limit_up_sector_summary_defaults_to_seven_trade_days(self) -> None:
        contract = build_limit_up_query_contract(
            "近期哪些板块涨停的股票比较多",
            planner_arguments={"recent_trade_days": 1, "group_by": "concept"},
        )

        self.assertEqual(contract.version, "limit-up-query-v5")
        self.assertEqual(contract.recent_trade_days, 7)
        self.assertEqual(contract.group_by, "concept")
        self.assertEqual(contract.result_mode, "summary")

    # Regression scenario: explicit recent window and concept group override defaults.
    def test_explicit_recent_window_and_concept_group_override_defaults(self) -> None:
        contract = build_limit_up_query_contract(
            "近10个交易日哪些题材的涨停股票最多"
        )

        self.assertEqual(contract.recent_trade_days, 10)
        self.assertEqual(contract.group_by, "concept")
        self.assertTrue(
            looks_like_limit_up_sector_summary_question(
                "近10个交易日哪些题材的涨停股票最多"
            )
        )

    # Regression scenario: explicit industry wording uses industry instead of limit reason.
    def test_explicit_industry_wording_uses_industry_instead_of_limit_reason(self) -> None:
        contract = build_limit_up_query_contract(
            "近期哪些行业的涨停股票比较多"
        )

        self.assertEqual(contract.group_by, "industry")

    # Regression scenario: recent named sector stock list uses clean query and seven days.
    def test_recent_named_sector_stock_list_uses_clean_query_and_seven_days(self) -> None:
        message = "近期农业板块涨停过的股票有哪些"
        contract = build_limit_up_query_contract(
            message,
            planner_arguments={"query": "近期农业", "recent_trade_days": 1},
        )

        self.assertTrue(looks_like_named_limit_up_sector_list_question(message))
        self.assertEqual(contract.version, "limit-up-query-v5")
        self.assertEqual(contract.query, "农业")
        self.assertEqual(contract.recent_trade_days, 7)
        self.assertEqual(contract.result_mode, "list")
        self.assertIsNone(contract.group_by)
        self.assertTrue(contract.exhaustive)
        self.assertEqual(contract.limit, 100)

    # Regression scenario: today named sector stock list keeps single day scope.
    def test_today_named_sector_stock_list_keeps_single_day_scope(self) -> None:
        contract = build_limit_up_query_contract("今天农业板块涨停的股票有哪些")

        self.assertEqual(contract.query, "农业")
        self.assertEqual(contract.recent_trade_days, 1)

    # Regression scenario: explicit named sector window overrides seven day default.
    def test_explicit_named_sector_window_overrides_seven_day_default(self) -> None:
        contract = build_limit_up_query_contract(
            "近10个交易日农业板块涨停过的股票有哪些",
            planner_arguments={"recent_trade_days": 1},
        )

        self.assertEqual(contract.query, "农业")
        self.assertEqual(contract.recent_trade_days, 10)

    # Regression scenario: limit down wording compiles to one market event type.
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

    # Regression scenario: explicit limit down overrides wrong planner event type.
    def test_explicit_limit_down_overrides_wrong_planner_event_type(self) -> None:
        contract = build_market_event_query_contract(
            "今天跌停的票有哪些",
            planner_arguments={"event_type": "limit_up", "limit": 20},
        )

        self.assertEqual(contract.event_type, "limit_down")
        self.assertEqual(contract.limit, 20)

    # Regression scenario: unknown planner event type is rejected without silent default.
    def test_unknown_planner_event_type_is_rejected_without_silent_default(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported market event type"):
            build_market_event_query_contract(
                "查询今天的价格限制事件",
                planner_arguments={"event_type": "mystery_event"},
            )

    # Regression scenario: market event list signal excludes rules and causes.
    def test_market_event_list_signal_excludes_rules_and_causes(self) -> None:
        self.assertTrue(looks_like_market_event_query("今天跌停的票有哪些"))
        self.assertFalse(looks_like_market_event_query("股票为什么会跌停"))
        self.assertFalse(looks_like_market_event_query("创业板跌停制度是什么"))


if __name__ == "__main__":
    unittest.main()
