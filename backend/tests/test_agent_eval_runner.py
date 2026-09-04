import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents.chat import answer_first_board_chat
from app.agents.eval_runner import (
    AgentEvalCase,
    AgentProductEvalScenario,
    AgentProductEvalTurn,
    load_eval_cases,
    load_product_eval_scenarios,
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
    def test_eval_template_override_does_not_mutate_process_environment(self) -> None:
        case = AgentEvalCase(
            case_id="template_isolation",
            message="哪些候选评分靠前",
            required_tools=["first_board_ratings"],
        )
        observed_values: list[str | None] = []

        def observe_environment(**kwargs):
            observed_values.append(
                os.environ.get("LIMITUPLAB_FORCE_TEMPLATE_ANSWER")
            )
            return answer_first_board_chat(**kwargs)

        with patch.dict(
            os.environ,
            {"LIMITUPLAB_FORCE_TEMPLATE_ANSWER": "preserved"},
            clear=False,
        ):
            with patch(
                "app.agents.eval_runner.answer_first_board_chat",
                side_effect=observe_environment,
            ):
                suite = run_agent_eval_suite(
                    cases=[case],
                    events=SAMPLE_EVENTS,
                    force_template_answer=True,
                )
            self.assertEqual(
                os.environ.get("LIMITUPLAB_FORCE_TEMPLATE_ANSWER"),
                "preserved",
            )

        self.assertTrue(suite.ok)
        self.assertEqual(observed_values, ["preserved"])

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

    def test_product_fixture_covers_complete_multi_turn_answers(self) -> None:
        fixture_path = (
            Path(__file__).parent
            / "fixtures"
            / "agent_product_eval_scenarios.json"
        )
        suite = run_agent_product_eval_suite(
            scenarios=load_product_eval_scenarios(fixture_path),
            events=SAMPLE_EVENTS,
        )

        failures = {
            f"{result.scenario_id}/{result.turn_id}": result.failures
            for result in suite.results
            if not result.passed
        }
        self.assertTrue(suite.ok, failures)
        self.assertEqual(suite.total_scenarios, 10)
        self.assertEqual(suite.total_turns, 30)
        self.assertEqual(suite.metrics["claim_grounding_rate"], 1.0)
        self.assertEqual(suite.metrics["grounded_claim_rate"], 1.0)
        self.assertEqual(suite.metrics["context_continuity_rate"], 1.0)
        self.assertEqual(suite.metrics["presentation_compliance_rate"], 1.0)

    def test_product_failure_report_groups_actionable_dimensions(self) -> None:
        response = answer_first_board_chat(
            request=AgentChatRequest(session_id="broken", message="你好"),
            events=SAMPLE_EVENTS,
        ).model_copy(
            update={
                "answer": "幻觉股份(600000)上涨 99%。",
                "tool_results": [
                    AgentToolTrace(
                        name="stock_facts",
                        input={},
                        summary="grounding fixture",
                        output={
                            "symbol": "002050",
                            "name": "三花智控",
                            "change_pct": 9.98,
                        },
                    )
                ],
            }
        )
        with patch(
            "app.agents.eval_runner.answer_first_board_chat",
            return_value=response,
        ):
            suite = run_agent_product_eval_suite(
                scenarios=[
                    AgentProductEvalScenario(
                        scenario_id="broken_product_answer",
                        turns=[
                            AgentProductEvalTurn(
                                turn_id="broken_turn",
                                message="你好",
                                expected_intent="greeting",
                                max_answer_chars=2,
                                max_total_duration_ms=-1,
                            )
                        ],
                    )
                ],
                events=SAMPLE_EVENTS,
            )

        report = product_eval_failure_report(suite)

        self.assertFalse(suite.ok)
        self.assertEqual(report["failed_turns"], 1)
        self.assertEqual(len(report["results"]), 1)
        self.assertNotIn("fact_completeness", report["failure_categories"])
        self.assertEqual(report["failure_categories"]["claim_grounding"], 2)
        self.assertEqual(report["failure_categories"]["latency_sla"], 1)
        self.assertEqual(report["failure_categories"]["presentation_compliance"], 1)
        grounding = report["results"][0]["evaluation_details"]["grounding"]
        self.assertEqual(grounding["unsupported_claim_count"], 2)

    def test_eval_report_route_returns_quality_summary(self) -> None:
        report = get_agent_eval_report(_admin=None)

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
