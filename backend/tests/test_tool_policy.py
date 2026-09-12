import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.agents.tool_policy import (
    AgentToolPolicyEngine,
    QuestionSignals,
    ToolExecution,
    extract_market_segment,
    extract_sector_query,
    looks_like_broad_sector_ranking_question,
    looks_like_stock_news_question,
)
from app.agents.tools import AgentToolRegistry, ToolResult
from app.models import (
    AgentChatRequest,
    AgentToolTrace,
    MarketIndexTrendFacts,
    MarketIndexTrendItem,
    MarketIndexTrendPoint,
    MarketSummary,
    SectorPerformanceFacts,
    SectorRankingItem,
    StockNewsFacts,
    StockNewsItem,
    build_agent_tool_policy_audit,
)
from app.services.sample_data import SAMPLE_EVENTS
from app.services.scoring_policy import DEFAULT_SCORING_POLICY_VERSION
from app.repositories import SQLiteFirstBoardRepository


class AgentToolPolicyTest(unittest.TestCase):


    # Prepare the empty execution fixture or observation used by the surrounding regression

























    # Regression scenario: rating repair records rule and audit reason.
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
        self.assertEqual(audit.policy_repaired_tools, ["first_board_ratings"])
        self.assertEqual(audit.backend_repaired_tools, [])
        self.assertEqual(audit.repair_reasons, [repair["reason"]])


    # Regression scenario: missing date short circuits domain tools.
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
