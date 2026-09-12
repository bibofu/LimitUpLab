import json
import unittest
from pathlib import Path
from uuid import uuid4

from app.agents.capability_contract import (
    CAPABILITY_BY_NAME,
    capability_answer_instruction,
    capability_schema_prompt,
    ensure_capability_tool_calls,
    infer_capabilities_from_facts,
    normalize_capabilities,
)
from app.agents.eval_runner import (
    AgentConversationEvalScenario,
    AgentConversationEvalTurn,
    AgentEvalCase,
    run_agent_conversation_planner_eval_suite,
    run_agent_planner_eval_suite,
)
from app.agents.chat import answer_first_board_chat, plan_agent_query
from app.agents.chat_prompts import (
    PLANNER_CONTRACT_VERSION,
    PLANNER_FIXED_INPUT_CHAR_BUDGET,
    PLANNER_SYSTEM_PROMPT_CHAR_BUDGET,
    planner_prompt_component_sizes,
)
from app.agents.tools import AgentToolRegistry
from app.models import AgentChatRequest
from app.repositories import SQLiteFirstBoardRepository
from app.services.llm_provider import (
    LLMProvider,
    LLMResult,
    NativeFunctionCallingError,
)
from app.services.sample_data import SAMPLE_EVENTS


class CapabilityOnlyProvider(LLMProvider):
    """Simulate semantic planning while intentionally omitting raw tool calls."""

    # Prepare the init fixture or observation used by the surrounding regression scenario.
    def __init__(self) -> None:
        self.answer_system_prompt = ""

    # Build the LLMResult fixture used by the surrounding regression scenario.
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "limit_up_query",
                        "capabilities": ["limit_up_pool"],
                        "safety": "normal",
                        "tool_calls": [],
                        "answer_directly": "",
                    }
                ),
                model="fake-capability-planner",
                provider="fake",
            )
        self.answer_system_prompt = system_prompt
        return LLMResult(
            content="已根据最新完整收盘事实整理。",
            model="fake-capability-answer",
            provider="fake",
        )


class SourceRefinementProvider(LLMProvider):
    """Select only the new capability and let the context contract merge sources."""

    # Build the LLMResult fixture used by the surrounding regression scenario.
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        return LLMResult(
            content=json.dumps(
                {
                    "intent_label": "limit_up_query",
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


class NativeFunctionPlanningProvider(LLMProvider):
    """Exercise the production planner without prompt-to-JSON compatibility."""

    # Prepare the init fixture or observation used by the surrounding regression scenario.
    def __init__(self) -> None:
        self.function_calls = 0
        self.parameters: dict = {}

    # Simulate the model response for this scenario; the controlled output lets the test inspect
    # planning, validation or fallback behavior.
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        raise AssertionError("native planner unexpectedly used text generation")

    # Build the LLMResult fixture used by the surrounding regression scenario.
    def generate_function_call(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        function_name: str,
        function_description: str,
        parameters: dict,
    ) -> LLMResult:
        self.function_calls += 1
        self.parameters = parameters
        self.assert_native_prompt = system_prompt
        return LLMResult(
            content=json.dumps(
                {
                    "intent_label": "limit_up_query",
                    "capabilities": ["limit_up_pool"],
                    "context_mode": "standalone",
                    "context_capabilities": [],
                    "safety": "normal",
                }
            ),
            model="fake-native-planner",
            provider="fake-native",
            response_mode="function_call",
            function_name=function_name,
        )


class MalformedNativeThenJsonProvider(CapabilityOnlyProvider):
    """Simulate a malformed native response followed by a valid JSON plan."""

    # Simulate the model response for this scenario; the controlled output lets the test inspect
    # planning, validation or fallback behavior.
    def generate_function_call(self, *args, **kwargs) -> LLMResult:
        raise NativeFunctionCallingError("malformed submit_agent_plan arguments")


class AgentCapabilityContractTest(unittest.TestCase):





    # Regression scenario: tool plans infer single tool capability.
    def test_tool_plans_infer_single_tool_capability(self) -> None:
        capabilities = normalize_capabilities(
            None,
            tool_calls=[{"name": "finance_news", "arguments": {}}],
        )

        self.assertEqual(capabilities, ("finance_news",))

    # Regression scenario: capability is the single workflow manifest.
    def test_capability_is_the_single_workflow_manifest(self) -> None:
        capability = CAPABILITY_BY_NAME["first_board_rating"]

        self.assertIn("首板评级前10名", capability.examples)
        self.assertEqual(capability.required_tools[0].name, "first_board_ratings")
        self.assertIn("研究评级", capability.answer_guidance)

    # Regression scenario: capability answer guidance is injected progressively.
    def test_capability_answer_guidance_is_injected_progressively(self) -> None:
        guidance = capability_answer_instruction(
            ("market_environment", "popularity")
        )

        self.assertIn("CAPABILITY_RESPONSE_CONTRACTS", guidance)
        self.assertIn("- market_environment:", guidance)
        self.assertNotIn("- popularity:", guidance)

    # Regression scenario: composite capability is recovered from executed facts.
    def test_composite_capability_is_recovered_from_executed_facts(self) -> None:
        capabilities = infer_capabilities_from_facts(
            (),
            {
                "market_summary": {},
                "market_index_trend": {},
                "sector_performance": {},
                "hot_stock_ranking": {},
            },
        )

        self.assertEqual(capabilities, ("market_environment",))

    # Regression scenario: compound capabilities merge required evidence.
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

    # Regression scenario: market environment contract requires four evidence groups.
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

    # Regression scenario: capability catalog only exposes available workflows.
    def test_capability_catalog_only_exposes_available_workflows(self) -> None:
        payload = json.loads(
            capability_schema_prompt({"hot_stock_ranking", "limit_up_events"})
        )
        names = {item["name"] for item in payload}

        self.assertEqual(names, {"popularity", "limit_up_pool"})





if __name__ == "__main__":
    unittest.main()
