import unittest
from pathlib import Path
from uuid import uuid4

from app.agents.tool_policy import (
    AgentToolPolicyEngine,
    QuestionSignals,
    ToolExecution,
    extract_market_segment,
)
from app.agents.tools import AgentToolRegistry
from app.models import (
    AgentChatRequest,
    AgentToolTrace,
    build_agent_tool_policy_audit,
)
from app.services.sample_data import SAMPLE_EVENTS
from app.repositories import SQLiteFirstBoardRepository


class AgentToolPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.database_path = (
            Path(__file__).resolve().parents[1]
            / f"tool-policy-{uuid4().hex}.sqlite"
        )
        self.addCleanup(self._cleanup_database)
        repository = SQLiteFirstBoardRepository(self.database_path)
        self.tools = AgentToolRegistry(
            events=SAMPLE_EVENTS,
            first_board_repository=repository,
        )
        self.policy = AgentToolPolicyEngine(self.tools)

    def _cleanup_database(self) -> None:
        for suffix in ("", "-shm", "-wal"):
            Path(f"{self.database_path}{suffix}").unlink(missing_ok=True)

    @staticmethod
    def _empty_execution() -> ToolExecution:
        return {
            "facts": {},
            "tool_results": [],
            "tool_call_names": [],
            "references": [],
        }

    def test_high_score_review_has_one_policy_scope(self) -> None:
        signals = QuestionSignals.from_message(
            "最近高分票后续走势怎么样，评分审美怎么改"
        )

        self.assertTrue(signals.review)
        self.assertFalse(signals.stock_kline)
        self.assertFalse(signals.rating_explanation)
        self.assertFalse(signals.evaluation)
        self.assertFalse(signals.scoring_policy)

        promotion_signals = QuestionSignals.from_message(
            "那你选出的高分票1进2的成功率呢"
        )
        self.assertTrue(promotion_signals.review)
        self.assertFalse(promotion_signals.daily_board_promotion)
        self.assertFalse(promotion_signals.rating_explanation)

    def test_scoring_policy_question_has_one_policy_scope(self) -> None:
        signals = QuestionSignals.from_message("评分权重有没有自动优化")

        self.assertTrue(signals.scoring_policy)
        self.assertFalse(signals.review)
        self.assertFalse(signals.evaluation)
        self.assertFalse(signals.rating_explanation)

    def test_prediction_quality_has_one_policy_scope(self) -> None:
        signals = QuestionSignals.from_message(
            "做一次预测质量审计，评分 v3 准备好了吗"
        )

        self.assertTrue(signals.prediction_quality)
        self.assertFalse(signals.rating_backtest)
        self.assertFalse(signals.evaluation)
        self.assertFalse(signals.review)
        self.assertFalse(signals.scoring_policy)
        self.assertFalse(signals.rating_explanation)

    def test_exhaustive_first_board_list_is_not_a_rating_question(self) -> None:
        signals = QuestionSignals.from_message("列出今天所有首板")

        self.assertFalse(signals.first_board_facts)
        self.assertFalse(signals.rating_explanation)

    def test_market_segment_extraction_uses_explicit_board_names(self) -> None:
        self.assertEqual(extract_market_segment("今天创业板有哪些股票涨停"), "chinext")
        self.assertEqual(extract_market_segment("科创板今天涨停股"), "star_market")
        self.assertEqual(extract_market_segment("北交所有哪些涨停"), "beijing")
        self.assertEqual(extract_market_segment("沪深主板涨停名单"), "main_board")

    def test_first_board_position_classification_uses_rating_facts(self) -> None:
        signals = QuestionSignals.from_message("今天首板按照位置分类一下")

        self.assertTrue(signals.first_board_facts)
        self.assertFalse(signals.limit_up_events)

        execution = self._empty_execution()
        repaired = self.policy.reconcile(
            request=AgentChatRequest(
                session_id="policy-position",
                message="今天首板按照位置分类一下",
            ),
            execution=execution,
        )

        self.assertEqual(repaired, ["first_board_ratings"])
        classification = execution["facts"]["first_board_ratings"][
            "position_classification"
        ]
        self.assertEqual(classification["candidate_count"], 1)
        self.assertEqual(classification["missing_count"], 1)

    def test_daily_board_promotion_uses_adjacent_close_facts(self) -> None:
        signals = QuestionSignals.from_message("最近5个交易日连板晋级概率怎么样")

        self.assertTrue(signals.daily_board_promotion)
        self.assertFalse(signals.limit_up_events)
        self.assertFalse(signals.scoring_policy)

        execution = self._empty_execution()
        repaired = self.policy.reconcile(
            request=AgentChatRequest(
                session_id="policy-promotion",
                message="最近5个交易日连板晋级概率怎么样",
            ),
            execution=execution,
        )

        self.assertEqual(repaired, ["daily_board_promotion"])
        payload = execution["facts"]["daily_board_promotion"]
        self.assertEqual(payload["observed_days"], 2)
        self.assertEqual(len(payload["items"]), 2)

        detail_signals = QuestionSignals.from_message("今天哪些票晋级成功")
        self.assertTrue(detail_signals.daily_board_promotion)
        self.assertFalse(detail_signals.limit_up_events)

    def test_sector_performance_does_not_trigger_rating_review(self) -> None:
        signals = QuestionSignals.from_message("今天半导体板块表现怎么样")

        self.assertTrue(signals.sector_performance)
        self.assertFalse(signals.rating_backtest)
        self.assertFalse(signals.evaluation)
        self.assertFalse(signals.review)

    def test_sector_move_reason_does_not_trigger_rating_explanation(self) -> None:
        signals = QuestionSignals.from_message("今天半导体板块为什么下跌")

        self.assertTrue(signals.sector_performance)
        self.assertTrue(signals.web_search)
        self.assertFalse(signals.rating_explanation)
        self.assertFalse(signals.first_board_facts)

    def test_broad_finance_news_uses_specialized_feed(self) -> None:
        signals = QuestionSignals.from_message("有什么最新财经新闻")

        self.assertTrue(signals.finance_news)
        self.assertFalse(signals.web_search)

    def test_company_news_still_uses_generic_web_search(self) -> None:
        signals = QuestionSignals.from_message("中电鑫龙有什么最新消息")

        self.assertFalse(signals.finance_news)
        self.assertTrue(signals.web_search)

    def test_scoring_policy_repair_returns_champion_status(self) -> None:
        request = AgentChatRequest(
            session_id="policy-test",
            message="现在是哪个评分策略版本，会自动学习吗",
        )
        execution = self._empty_execution()

        repaired = self.policy.reconcile(request=request, execution=execution)

        self.assertEqual(repaired, ["scoring_policy_status"])
        payload = execution["facts"]["scoring_policy_status"]
        self.assertEqual(payload["champion"]["status"], "champion")
        self.assertIn("scoring_version=", execution["references"][0])

    def test_rating_repair_records_rule_and_audit_reason(self) -> None:
        request = AgentChatRequest(
            session_id="policy-test",
            message="哪些候选评分靠前",
        )
        execution = self._empty_execution()

        repaired = self.policy.reconcile(request=request, execution=execution)

        self.assertEqual(repaired, ["first_board_ratings"])
        self.assertIn("first_board_ratings", execution["facts"])
        repair = execution["tool_results"][0].output["policy_repair"]
        self.assertEqual(repair["rule"], "rating-facts-required")

        planner_trace = AgentToolTrace(
            name="llm_tool_planner",
            input={"tool_calls": []},
            summary="Planner returned no tools.",
        )
        audit = build_agent_tool_policy_audit(
            tool_calls=["first_board_ratings"],
            tool_results=[planner_trace, *execution["tool_results"]],
        )
        self.assertEqual(audit.backend_repaired_tools, ["first_board_ratings"])
        self.assertEqual(audit.repair_reasons, [repair["reason"]])

    def test_prediction_quality_repair_returns_coverage_facts(self) -> None:
        request = AgentChatRequest(
            session_id="policy-test",
            message="评分 v3 准备好了吗，检查预测质量",
        )
        execution = self._empty_execution()

        repaired = self.policy.reconcile(request=request, execution=execution)

        self.assertEqual(repaired, ["prediction_quality_audit"])
        payload = execution["facts"]["prediction_quality_audit"]
        self.assertEqual(
            payload["audited_scoring_version"],
            "first-board-rule-v4-opening-one-word-board",
        )
        self.assertEqual(payload["policy_status"]["required_trade_dates"], 60)

    def test_missing_date_short_circuits_domain_tools(self) -> None:
        request = AgentChatRequest(
            session_id="policy-test",
            message="8.8日的首板数据你有吗",
        )
        execution = self._empty_execution()

        repaired = self.policy.reconcile(request=request, execution=execution)

        self.assertEqual(repaired, ["limit_up_event_dates"])
        self.assertEqual(execution["tool_call_names"], ["limit_up_event_dates"])
        self.assertNotIn("first_board_ratings", execution["facts"])
        self.assertIn("limit_up_event_dates", execution["facts"])


if __name__ == "__main__":
    unittest.main()
