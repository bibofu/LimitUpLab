"""Tests for the four-layer Agent golden-dataset evaluator."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents.golden_dataset import (
    REQUIRED_CASE_FIELDS,
    GoldenEvalCase,
    load_golden_cases,
)
from app.agents.golden_eval import (
    golden_suite_report,
    golden_panel_report,
    run_golden_eval_suite,
)
from app.models import AgentChatResponse, AgentToolTrace
from app.services.llm_provider import LLMProvider, LLMResult
from app.services.sample_data import SAMPLE_EVENTS


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "agent_golden_dataset.json"


class StockNewsPlanner(LLMProvider):
    # Build the LLMResult fixture used by the surrounding regression scenario.
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        return LLMResult(
            content=json.dumps(
                {
                    "intent_label": "stock_news",
                    "safety": "normal",
                    "capabilities": ["stock_news"],
                    "tool_calls": [
                        {
                            "name": "stock_news",
                            "arguments": {
                                "symbol": "300750",
                                "days": 7,
                                "limit": 10,
                            },
                        }
                    ],
                    "answer_directly": "",
                }
            ),
            model="golden-test",
            provider="fake",
        )


class AgentGoldenEvalTest(unittest.TestCase):
    # Regression scenario: dataset has fifty strictly versioned cases and required coverage.
    def test_dataset_has_fifty_strictly_versioned_cases_and_required_coverage(self) -> None:
        version, cases = load_golden_cases(FIXTURE_PATH)

        self.assertEqual(version, "agent-golden-v1")
        self.assertEqual(len(cases), 50)
        self.assertEqual(len({case.case_id for case in cases}), 50)
        self.assertTrue(
            {
                "intent_understanding",
                "parameter_extraction",
                "tool_selection",
                "aggregation",
                "comparison",
                "multi_turn",
                "context",
                "missing_data",
                "tool_failure",
                "anti_hallucination",
                "safety",
            }.issubset({case.category for case in cases})
        )
        raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        self.assertTrue(
            all(REQUIRED_CASE_FIELDS.issubset(item) for item in raw["cases"])
        )

    # Regression scenario: one run reports each layer without collapsing failures.
    def test_one_run_reports_each_layer_without_collapsing_failures(self) -> None:
        case = GoldenEvalCase(
            case_id="layer-isolation",
            category="parameter_extraction",
            question="看一下创业板首板",
            expected_capabilities=["limit_up_pool"],
            required_tools=["limit_up_events"],
            expected_parameters={"limit_up_events": {"market": "chinext"}},
            required_facts=["301489"],
            must_include=["301489"],
            must_not_include=["建议买入"],
            allow_refusal=False,
        )
        response = AgentChatResponse(
            session_id="golden",
            intent="limit_up_query",
            answer="思泉新材(301489)",
            tool_calls=["limit_up_events"],
            generated_by="golden-eval-test",
            tool_results=[
                AgentToolTrace(
                    name="llm_tool_planner",
                    input={
                        "capabilities": ["limit_up_pool"],
                        "tool_calls": [
                            {"name": "limit_up_events", "arguments": {}}
                        ],
                    },
                    summary="plan",
                ),
                AgentToolTrace(
                    name="limit_up_events",
                    input={"market": "main_board"},
                    summary="facts",
                    output={
                        "events": [{"symbol": "301489", "name": "思泉新材"}]
                    },
                ),
            ],
        )
        with patch(
            "app.agents.golden_eval.answer_first_board_chat",
            return_value=response,
        ):
            suite = run_golden_eval_suite(
                cases=[case],
                events=SAMPLE_EVENTS,
                repository=object(),  # type: ignore[arg-type]
            )

        result = suite.results[0]
        self.assertTrue(result.planner.passed)
        self.assertFalse(result.tool_execution.passed)
        self.assertTrue(result.grounding.passed)
        self.assertTrue(result.answer.passed)
        self.assertFalse(result.passed)
        panel = golden_panel_report(suite)
        self.assertEqual((panel.total, panel.passed, panel.failed), (1, 0, 1))
        self.assertEqual(panel.pass_rate, 0)
        self.assertEqual(panel.results[0].intent, response.intent)
        self.assertEqual(panel.results[0].planner_tool_calls, ["limit_up_events"])
        self.assertEqual(panel.results[0].trace_names, ["llm_tool_planner", "limit_up_events"])
        self.assertEqual(panel.results[0].answer_preview, response.answer)
        self.assertTrue(all(item.startswith("tool_execution:") for item in panel.results[0].failures))
        report = golden_suite_report(suite)
        self.assertEqual(
            set(report["results"][0]["layers"]),
            {"planner", "tool_execution", "grounding", "answer"},
        )

    # Regression scenario: expected tool failure is executed without hallucination.
    def test_expected_tool_failure_is_executed_without_hallucination(self) -> None:
        case = GoldenEvalCase(
            case_id="tool-failure",
            category="tool_failure",
            question="300750最近7天有什么新闻？",
            expected_capabilities=["stock_news"],
            required_tools=["stock_news"],
            expected_parameters={
                "stock_news": {"symbol": "300750", "days": 7, "limit": 10}
            },
            required_facts=[],
            must_include=["无法"],
            must_not_include=["重大利好"],
            allow_refusal=True,
            simulate_tool_failure=["stock_news"],
        )

        suite = run_golden_eval_suite(
            cases=[case],
            events=SAMPLE_EVENTS,
            llm_provider=StockNewsPlanner(),
            repository=object(),  # type: ignore[arg-type]
        )

        result = suite.results[0]
        self.assertTrue(result.passed, golden_suite_report(suite))
        observed_tools = result.tool_execution.observed["tools"]
        self.assertEqual(observed_tools["stock_news"]["status"], "error")
        self.assertFalse(
            result.grounding.observed["tool_failure_hallucination"]
        )


if __name__ == "__main__":
    unittest.main()
