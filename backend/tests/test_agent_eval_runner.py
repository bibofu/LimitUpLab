import json
import unittest
from pathlib import Path

from app.agents.eval_runner import (
    AgentEvalCase,
    load_eval_cases,
    run_agent_eval_suite,
)
from app.routers.agents import get_agent_eval_report
from app.services.llm_provider import LLMProvider, LLMResult
from app.services.sample_data import SAMPLE_EVENTS


class IntermittentPlannerProvider(LLMProvider):
    """Return a valid plan twice and simulate one provider outage."""

    def __init__(self) -> None:
        self.calls = 0

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
    def test_fixture_eval_suite_passes_against_deterministic_agent(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / "agent_eval_cases.json"
        suite = run_agent_eval_suite(
            cases=load_eval_cases(fixture_path),
            events=SAMPLE_EVENTS,
        )

        failure_report = {
            result.case_id: result.failures
            for result in suite.results
            if not result.passed
        }
        self.assertTrue(suite.ok, failure_report)
        self.assertEqual(suite.total, 18)

    def test_eval_report_route_returns_quality_summary(self) -> None:
        report = get_agent_eval_report()

        self.assertEqual(report.mode, "offline")
        self.assertEqual(report.total, 18)
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.pass_rate, 1.0)
        self.assertTrue(report.results)

    def test_repeated_live_trials_expose_provider_flakiness(self) -> None:
        case = AgentEvalCase(
            case_id="top_candidates_live",
            message="哪些候选评分靠前",
            required_tools=["first_board_ratings"],
        )

        suite = run_agent_eval_suite(
            cases=[case],
            events=SAMPLE_EVENTS,
            llm_provider=IntermittentPlannerProvider(),
            check_intent=False,
            trials_per_case=3,
            minimum_pass_rate=2 / 3,
            require_llm_planner=True,
        )

        result = suite.results[0]
        self.assertTrue(result.passed)
        self.assertFalse(result.stable)
        self.assertEqual(result.passed_trials, 2)
        self.assertEqual(result.provider_failure_trials, 1)
        self.assertEqual(suite.llm_planner_trials, 2)
        self.assertEqual(suite.llm_coverage_rate, 0.6667)

    def test_live_eval_rejects_silent_template_fallback(self) -> None:
        case = AgentEvalCase(
            case_id="top_candidates_no_llm",
            message="哪些候选评分靠前",
            required_tools=["first_board_ratings"],
            answer_contains=["301489"],
        )

        suite = run_agent_eval_suite(
            cases=[case],
            events=SAMPLE_EVENTS,
            require_llm_planner=True,
        )

        self.assertFalse(suite.ok)
        self.assertIn(
            "configured live LLM planner was not observed",
            suite.results[0].failures,
        )

    def test_live_eval_separates_backend_success_from_planner_quality(self) -> None:
        case = AgentEvalCase(
            case_id="top_candidates_repaired",
            message="哪些候选评分靠前",
            required_tools=["first_board_ratings"],
        )

        suite = run_agent_eval_suite(
            cases=[case],
            events=SAMPLE_EVENTS,
            llm_provider=EmptyPlannerProvider(),
            check_intent=False,
            require_llm_planner=True,
        )

        self.assertTrue(suite.ok)
        self.assertEqual(suite.planner_tool_success_rate, 0.0)
        self.assertEqual(suite.backend_repair_rate, 1.0)
        self.assertEqual(
            suite.results[0].backend_repaired_tools,
            ["first_board_ratings"],
        )


if __name__ == "__main__":
    unittest.main()
