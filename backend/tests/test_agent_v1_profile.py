import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.agents.chat import UNANSWERABLE_TEXT, answer_first_board_chat
from app.agents.skills import AGENT_SKILL_REGISTRY
from app.agents.tool_policy import AgentToolPolicyEngine, ToolExecution
from app.agents.tools import (
    EXTENDED_AGENT_PROFILE,
    V1_AGENT_PROFILE,
    V1_CLOSED_MARKET_TOOL_NAMES,
    V1_DEFERRED_REALTIME_TOOL_NAMES,
    AgentToolRegistry,
)
from app.models import AgentChatRequest
from app.repositories import SQLiteFirstBoardRepository
from app.services.llm_provider import LLMProvider, LLMResult
from app.services.sample_data import SAMPLE_EVENTS


class RealtimeToolInjectionProvider(LLMProvider):
    """Request a hidden real-time tool to verify server-side enforcement."""

    def __init__(self) -> None:
        self.planner_system_prompt = ""
        self.calls = 0

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        del user_prompt
        self.calls += 1
        if "first job is to decide which tools are needed" in system_prompt:
            self.planner_system_prompt = system_prompt
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "hot_stock_ranking",
                        "skill_name": "popularity",
                        "safety": "normal",
                        "tool_calls": [
                            {
                                "name": "hot_stock_ranking",
                                "arguments": {"limit": 100},
                            }
                        ],
                        "answer_directly": "我猜测这是当前热门股票。",
                    }
                ),
                model="fake-v1-planner",
                provider="fake",
            )
        return LLMResult(
            content="这段实时猜测不应被执行。",
            model="fake-v1-answer",
            provider="fake",
        )


class UnexpectedLLMProvider(LLMProvider):
    """Fail if a V1 scope rejection reaches the LLM."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        del system_prompt, user_prompt
        raise AssertionError("V1 deferred capability must be rejected before LLM")


class AgentV1ProfileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.database_path = (
            Path(__file__).resolve().parents[1]
            / f"agent-v1-profile-{uuid4().hex}.sqlite"
        )
        self.repository = SQLiteFirstBoardRepository(self.database_path)
        self.addCleanup(self._cleanup_database)

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

    def test_default_profile_exposes_only_completed_close_tools(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LIMITUPLAB_AGENT_PROFILE", None)
            registry = AgentToolRegistry(
                events=SAMPLE_EVENTS,
                first_board_repository=self.repository,
            )

        schema_names = {schema.name for schema in registry.schemas()}
        self.assertEqual(registry.profile, V1_AGENT_PROFILE)
        self.assertEqual(schema_names, set(V1_CLOSED_MARKET_TOOL_NAMES))
        self.assertTrue(schema_names.isdisjoint(V1_DEFERRED_REALTIME_TOOL_NAMES))
        self.assertNotIn("hot_stock_ranking", registry.schema_prompt())
        self.assertNotIn("sector_performance", registry.schema_prompt())
        self.assertNotIn("web_search", registry.schema_prompt())

    def test_extended_profile_preserves_deferred_v2_tools(self) -> None:
        registry = AgentToolRegistry(
            events=SAMPLE_EVENTS,
            first_board_repository=self.repository,
            profile=EXTENDED_AGENT_PROFILE,
        )

        schema_names = {schema.name for schema in registry.schemas()}
        self.assertTrue(V1_DEFERRED_REALTIME_TOOL_NAMES.issubset(schema_names))
        self.assertIn("hot_stock_ranking", registry.schema_prompt())

    def test_v1_skill_catalog_excludes_popularity_workflow(self) -> None:
        catalog = AGENT_SKILL_REGISTRY.schema_prompt(V1_CLOSED_MARKET_TOOL_NAMES)

        self.assertIn("first-board-rating", catalog)
        self.assertIn("limit-up-pool", catalog)
        self.assertNotIn("popularity", catalog)
        self.assertIsNone(
            AGENT_SKILL_REGISTRY.resolve(
                "popularity",
                [],
                V1_CLOSED_MARKET_TOOL_NAMES,
            )
        )

    def test_v1_policy_does_not_repair_a_deferred_realtime_tool(self) -> None:
        registry = AgentToolRegistry(
            events=SAMPLE_EVENTS,
            first_board_repository=self.repository,
            profile=V1_AGENT_PROFILE,
        )
        policy = AgentToolPolicyEngine(registry)
        request = AgentChatRequest(
            session_id="v1-realtime-policy",
            message="热股榜前100名",
        )

        execution = self._empty_execution()
        repaired = policy.reconcile(request=request, execution=execution)

        self.assertTrue(policy.requires_grounding(request))
        self.assertEqual(repaired, [])
        self.assertEqual(execution["tool_call_names"], [])

    @patch("app.agents.tools.AgentToolRegistry.hot_stock_ranking")
    def test_injected_realtime_tool_call_is_blocked(self, hot_stock_ranking) -> None:
        provider = RealtimeToolInjectionProvider()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LIMITUPLAB_AGENT_PROFILE", None)
            response = answer_first_board_chat(
                AgentChatRequest(
                    session_id="v1-realtime-injection",
                    message="请分析一下这个研究工具",
                ),
                events=SAMPLE_EVENTS,
                repository=self.repository,
                llm_provider=provider,
            )

        hot_stock_ranking.assert_not_called()
        self.assertEqual(response.answer, UNANSWERABLE_TEXT)
        self.assertEqual(provider.calls, 1)
        self.assertIn("v1_close_review", provider.planner_system_prompt)
        self.assertNotIn('"name":"hot_stock_ranking"', provider.planner_system_prompt)
        blocked_trace = next(
            trace for trace in response.tool_results if trace.name == "hot_stock_ranking"
        )
        self.assertEqual(blocked_trace.status, "error")

    def test_v1_scope_eval_cases_are_rejected_before_llm(self) -> None:
        fixture_path = (
            Path(__file__).parent
            / "fixtures"
            / "agent_v1_scope_cases.json"
        )
        cases = json.loads(fixture_path.read_text(encoding="utf-8"))["cases"]

        for case in cases:
            with self.subTest(case_id=case["case_id"]):
                response = answer_first_board_chat(
                    AgentChatRequest(
                        session_id=f"v1-scope-{case['case_id']}",
                        message=case["message"],
                    ),
                    events=SAMPLE_EVENTS,
                    repository=self.repository,
                    llm_provider=UnexpectedLLMProvider(),
                )

                self.assertEqual(response.intent, "out_of_scope")
                self.assertEqual(response.answer, UNANSWERABLE_TEXT)
                self.assertEqual(response.tool_calls, [])

    def test_capability_text_states_v1_close_only_scope(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="v1-capability",
                message="你能做什么",
            ),
            events=SAMPLE_EVENTS,
            repository=self.repository,
        )

        self.assertIn("V1", response.answer)
        self.assertIn("完整收盘数据", response.answer)
        self.assertIn("一进二", response.answer)
        self.assertIn("不提供盘中实时行情", response.answer)


if __name__ == "__main__":
    unittest.main()
