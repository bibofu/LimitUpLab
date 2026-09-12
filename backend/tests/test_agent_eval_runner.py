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


class IntermittentPlannerProvider(LLMProvider):
    """Return a valid plan twice and simulate one provider outage."""

    # Prepare the init fixture or observation used by the surrounding regression scenario.
    def __init__(self) -> None:
        self.calls = 0

    # Build the LLMResult fixture used by the surrounding regression scenario.
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("temporary provider outage")
        return LLMResult(
            content=json.dumps(
                {
                    "intent_label": "today_summary",
                    "safety": "normal",
                    "tool_calls": [
                        {
                            "name": "first_board_ratings",
                            "arguments": {"trade_date": "2026-05-15"},
                        }
                    ],
                    "answer_directly": "",
                }
            ),
            model="fake-live-model",
            provider="fake-live-provider",
        )


class EmptyPlannerProvider(LLMProvider):
    """Return a valid but incomplete plan to exercise policy repair metrics."""

    # Build the LLMResult fixture used by the surrounding regression scenario.
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        return LLMResult(
            content=json.dumps(
                {
                    "intent_label": "today_summary",
                    "safety": "normal",
                    "tool_calls": [],
                    "answer_directly": "",
                }
            ),
            model="fake-live-model",
            provider="fake-live-provider",
        )


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
