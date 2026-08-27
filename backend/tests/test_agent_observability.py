import unittest
from datetime import datetime, timedelta, timezone

from app.models import AgentRun
from app.routers.agents import _summarize_agent_run


class AgentObservabilityTest(unittest.TestCase):
    def test_summarizes_persisted_run_for_frontend(self) -> None:
        started_at = datetime.now(timezone.utc)
        run = AgentRun(
            run_id="run_observe",
            session_id="session-observe",
            run_type="agent_chat",
            status="success",
            intent="market_overview_query",
            tool_calls=["llm_tool_planner", "market_summary", "llm_tool_answer"],
            input_json={"message": "market mood"},
            output_json={
                "answer": "A-share limit-up count is 105.",
                "warnings": ["not investment advice"],
                "tool_results": [
                    {
                        "name": "market_summary",
                        "input": {"trade_date": "2026-08-12"},
                        "summary": "Market summary loaded.",
                        "status": "success",
                        "output": {"limit_up_count": 105},
                        "error": None,
                    }
                ],
            },
            started_at=started_at,
            finished_at=started_at + timedelta(milliseconds=123),
        )

        summary = _summarize_agent_run(run)

        self.assertEqual(summary.run_id, "run_observe")
        self.assertEqual(summary.message, "market mood")
        self.assertEqual(summary.duration_ms, 123)
        self.assertEqual(summary.warnings, ["not investment advice"])
        self.assertEqual(summary.tool_results[0].name, "market_summary")
        self.assertIn("limit-up count", summary.answer_preview or "")


if __name__ == "__main__":
    unittest.main()
