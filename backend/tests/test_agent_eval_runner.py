import json
import os
import unittest
from unittest.mock import patch

from app.agents.chat import answer_first_board_chat
from app.agents.eval_runner import (
    AgentEvalCase,
    AgentProductEvalScenario,
    AgentProductEvalTurn,
    product_eval_failure_report,
    run_agent_eval_suite,
    run_agent_product_eval_suite,
)
from app.models import AgentChatRequest, AgentToolTrace
from app.routers.agents import get_agent_eval_report
from app.services.llm_provider import LLMProvider, LLMResult
from app.services.sample_data import SAMPLE_EVENTS






class AgentEvalRunnerTest(unittest.TestCase):


    # Regression scenario: eval report route returns quality summary.
    def test_eval_report_route_returns_quality_summary(self) -> None:
        from app.agents.chat_eval_dataset import load_dev_dataset
        from app.agents.chat_eval_runner_v2 import run_chat_eval_suite

        artifact = run_chat_eval_suite(
            load_dev_dataset().cases[:1],
            mode="offline",
            trials=1,
            seed="route-test",
        )
        with patch(
            "app.routers.agents.load_latest_completed_report",
            return_value=artifact,
        ) as load_report:
            report = get_agent_eval_report(_admin=None)

        load_report.assert_called_once_with()
        self.assertEqual(report.mode, "offline")
        self.assertEqual(report.status, "completed")
        self.assertEqual(report.case_count, 1)
        self.assertEqual(report.failed_cases, 0)
        self.assertEqual(len(report.results), 1)
        self.assertEqual(report.results[0]["case_id"], "CEV2-D001")





if __name__ == "__main__":
    unittest.main()
