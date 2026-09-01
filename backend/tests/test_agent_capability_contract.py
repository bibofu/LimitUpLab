import json
import unittest
from pathlib import Path
from uuid import uuid4

from app.agents.capability_contract import (
    capability_schema_prompt,
    ensure_capability_tool_calls,
    normalize_capabilities,
)
from app.agents.eval_runner import (
    AgentConversationEvalScenario,
    AgentConversationEvalTurn,
    AgentEvalCase,
    load_conversation_eval_scenarios,
    load_eval_cases,
    run_agent_conversation_planner_eval_suite,
    run_agent_planner_eval_suite,
)
from app.agents.chat import answer_first_board_chat
from app.models import AgentChatRequest
from app.repositories import SQLiteFirstBoardRepository
from app.services.llm_provider import LLMProvider, LLMResult
from app.services.sample_data import SAMPLE_EVENTS


class CapabilityOnlyProvider(LLMProvider):
    """Simulate semantic planning while intentionally omitting raw tool calls."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "limit_up_query",
                        "skill_name": None,
                        "capabilities": ["limit_up_pool"],
                        "safety": "normal",
                        "tool_calls": [],
                        "answer_directly": "",
                    }
                ),
                model="fake-capability-planner",
                provider="fake",
            )
        return LLMResult(
            content="已根据最新完整收盘事实整理。",
            model="fake-capability-answer",
            provider="fake",
        )


class ContextAwareCapabilityProvider(LLMProvider):
    """Return semantic plans for follow-ups and verify history is present."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        payload = json.loads(user_prompt)
        if not payload["conversation_history"]:
            raise AssertionError("multi-turn planner did not receive conversation history")
        message = payload["message"]
        capability = "stock_trend" if "走" in message else "first_board_rating"
        return LLMResult(
            content=json.dumps(
                {
                    "intent_label": capability,
                    "skill_name": None,
                    "capabilities": [capability],
                    "safety": "normal",
                    "tool_calls": [],
                    "answer_directly": "",
                }
            ),
            model="fake-context-planner",
            provider="fake",
        )


class SourceRefinementProvider(LLMProvider):
    """Select only the new capability and let the context contract merge sources."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        return LLMResult(
            content=json.dumps(
                {
                    "intent_label": "limit_up_query",
                    "skill_name": None,
                    "capabilities": ["limit_up_pool"],
                    "context_mode": "source_refinement",
                    "context_capabilities": ["popularity"],
                    "safety": "normal",
                    "tool_calls": [],
                    "answer_directly": "",
                }
            ),
            model="fake-refinement-planner",
            provider="fake",
        )


class AgentCapabilityContractTest(unittest.TestCase):
    def test_skill_and_tool_plans_remain_backward_compatible(self) -> None:
        capabilities = normalize_capabilities(
            None,
            skill_name="market_environment",
            tool_calls=[{"name": "finance_news", "arguments": {}}],
        )

        self.assertEqual(capabilities, ("market_environment", "finance_news"))

    def test_compound_capabilities_merge_required_evidence(self) -> None:
        calls = ensure_capability_tool_calls(
            ("popularity", "limit_up_pool"),
            [{"name": "hot_stock_ranking", "arguments": {"limit": 100}}],
            allowed_tool_names={"hot_stock_ranking", "limit_up_events"},
        )

        self.assertEqual(
            [call["name"] for call in calls],
            ["hot_stock_ranking", "limit_up_events"],
        )
        self.assertEqual(calls[0]["arguments"]["limit"], 100)
        self.assertEqual(calls[0]["arguments"]["source"], "auto")

    def test_market_environment_contract_requires_four_evidence_groups(self) -> None:
        calls = ensure_capability_tool_calls(
            ("market_environment",),
            [],
            allowed_tool_names={
                "market_summary",
                "market_index_trend",
                "sector_performance",
                "hot_stock_ranking",
            },
        )

        self.assertEqual(
            [call["name"] for call in calls],
            [
                "market_summary",
                "market_index_trend",
                "sector_performance",
                "hot_stock_ranking",
            ],
        )
        self.assertTrue(calls[-1]["arguments"]["enrich_performance"])

    def test_capability_catalog_only_exposes_available_workflows(self) -> None:
        payload = json.loads(
            capability_schema_prompt({"hot_stock_ranking", "limit_up_events"})
        )
        names = {item["name"] for item in payload}

        self.assertEqual(names, {"popularity", "limit_up_pool"})

    def test_paraphrase_eval_fixture_covers_single_and_compound_requests(self) -> None:
        path = Path(__file__).parent / "fixtures" / "agent_paraphrase_eval_cases.json"
        cases = load_eval_cases(path)

        self.assertEqual(len(cases), 146)
        self.assertTrue(all(case.expected_capabilities for case in cases))
        self.assertTrue(any(len(case.expected_capabilities) > 1 for case in cases))

    def test_planner_eval_repeats_each_case_three_times(self) -> None:
        suite = run_agent_planner_eval_suite(
            cases=[
                AgentEvalCase(
                    case_id="semantic-limit-up",
                    message="把最近封死的票池给我扫一遍",
                    expected_capabilities=["limit_up_pool"],
                    required_tools=["limit_up_events"],
                )
            ],
            events=SAMPLE_EVENTS,
            llm_provider=CapabilityOnlyProvider(),
            trials_per_case=3,
        )

        self.assertTrue(suite.ok)
        self.assertEqual(suite.trials_per_case, 3)
        self.assertEqual(suite.stable_cases, 1)
        self.assertEqual(suite.capability_success_rate, 1.0)

    def test_conversation_eval_preserves_history_across_turns(self) -> None:
        path = (
            Path(__file__).parent
            / "fixtures"
            / "agent_conversation_eval_scenarios.json"
        )
        scenario = load_conversation_eval_scenarios(path)[0]
        suite = run_agent_conversation_planner_eval_suite(
            scenarios=[scenario],
            events=SAMPLE_EVENTS,
            llm_provider=ContextAwareCapabilityProvider(),
            trials_per_scenario=3,
        )

        self.assertTrue(suite.ok)
        self.assertEqual(suite.total_turns, 2)
        self.assertEqual(suite.stable_scenarios, 1)
        self.assertEqual(suite.turn_pass_rate, 1.0)

    def test_source_refinement_merges_previous_evidence_capability(self) -> None:
        scenario = AgentConversationEvalScenario(
            scenario_id="source-refinement",
            history=[
                {"role": "user", "content": "列出热门股票"},
                {
                    "role": "assistant",
                    "content": "最新人气榜已经列出。",
                    "capabilities": ["popularity"],
                },
            ],
            turns=[
                AgentConversationEvalTurn(
                    message="这些里面涨停的有哪些",
                    expected_capabilities=["popularity", "limit_up_pool"],
                    required_tools=["hot_stock_ranking", "limit_up_events"],
                    assistant_context="交集已经列出。",
                )
            ],
        )

        suite = run_agent_conversation_planner_eval_suite(
            scenarios=[scenario],
            events=SAMPLE_EVENTS,
            llm_provider=SourceRefinementProvider(),
            trials_per_scenario=3,
        )

        self.assertTrue(suite.ok)
        turn = suite.results[0]["trials"][0]["turns"][0]
        self.assertEqual(
            turn["capabilities"],
            ["popularity", "limit_up_pool"],
        )

    def test_capability_routes_a_paraphrase_without_keyword_policy_help(self) -> None:
        database_path = Path(__file__).resolve().parents[1] / (
            f"capability-contract-{uuid4().hex}.sqlite"
        )
        self.addCleanup(database_path.unlink, missing_ok=True)

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="capability-paraphrase",
                message="把最近封死的票池给我扫一遍",
            ),
            events=SAMPLE_EVENTS,
            repository=SQLiteFirstBoardRepository(database_path),
            llm_provider=CapabilityOnlyProvider(),
        )

        self.assertIn("limit_up_events", response.tool_calls)
        planner_trace = next(
            trace for trace in response.tool_results if trace.name == "llm_tool_planner"
        )
        self.assertEqual(planner_trace.input["capabilities"], ["limit_up_pool"])


if __name__ == "__main__":
    unittest.main()
