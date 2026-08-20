import unittest
from pathlib import Path
from uuid import uuid4

from app.agents.tool_policy import (
    AgentToolPolicyEngine,
    QuestionSignals,
    ToolExecution,
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

    def test_scoring_policy_question_has_one_policy_scope(self) -> None:
        signals = QuestionSignals.from_message("评分权重有没有自动优化")

        self.assertTrue(signals.scoring_policy)
        self.assertFalse(signals.review)
        self.assertFalse(signals.evaluation)
        self.assertFalse(signals.rating_explanation)

    def test_exhaustive_first_board_list_is_not_a_rating_question(self) -> None:
        signals = QuestionSignals.from_message("列出今天所有首板")

        self.assertFalse(signals.first_board_facts)
        self.assertFalse(signals.rating_explanation)

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
