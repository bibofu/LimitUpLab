import json
import unittest
from unittest.mock import patch

from app.agents.chat import answer_first_board_chat
from app.agents.skills import AGENT_SKILL_REGISTRY
from app.agents.tools import ToolResult
from app.models import AgentChatRequest
from app.services.llm_provider import LLMProvider, LLMResult
from app.services.sample_data import SAMPLE_EVENTS


class SkillSelectingProvider(LLMProvider):
    """Fake LLM that selects a skill but intentionally omits required tools."""

    def __init__(self, skill_name: str) -> None:
        self.skill_name = skill_name
        self.answer_system_prompt = ""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": self.skill_name,
                        "skill_name": self.skill_name,
                        "safety": "normal",
                        "tool_calls": [],
                        "answer_directly": "",
                    }
                ),
                model="fake-skill-planner",
                provider="fake",
            )
        self.answer_system_prompt = system_prompt
        return LLMResult(
            content="已根据最新可用事实完成回答。",
            model="fake-skill-answer",
            provider="fake",
        )


class AgentSkillRegistryTest(unittest.TestCase):
    def test_registry_exposes_three_initial_skills(self) -> None:
        self.assertEqual(
            [skill.name for skill in AGENT_SKILL_REGISTRY.skills],
            ["first-board-rating", "limit-up-pool", "popularity"],
        )
        catalog = AGENT_SKILL_REGISTRY.schema_prompt()
        self.assertIn("首板票有哪些", catalog)
        self.assertIn("有哪些票比较热门", catalog)
        self.assertIn("为什么这只股票评分高", catalog)
        for skill in AGENT_SKILL_REGISTRY.skills:
            self.assertTrue(skill.source_path.endswith("SKILL.md"))
            self.assertIn("## 工作流", skill.instructions)

    def test_legacy_underscore_names_remain_compatible(self) -> None:
        resolved = AGENT_SKILL_REGISTRY.get("first_board_rating")

        assert resolved is not None
        self.assertEqual(resolved.name, "first-board-rating")

    def test_required_tools_are_added_without_duplicates(self) -> None:
        popularity = AGENT_SKILL_REGISTRY.get("popularity")
        assert popularity is not None

        added = AGENT_SKILL_REGISTRY.ensure_required_tool_calls(popularity, [])
        preserved = AGENT_SKILL_REGISTRY.ensure_required_tool_calls(
            popularity,
            [{"name": "hot_stock_ranking", "arguments": {"limit": 50}}],
        )

        self.assertEqual(added[0]["name"], "hot_stock_ranking")
        self.assertEqual(added[0]["arguments"]["limit"], 20)
        self.assertEqual(len(preserved), 1)
        self.assertEqual(preserved[0]["arguments"]["limit"], 50)


class AgentSkillIntegrationTest(unittest.TestCase):
    def _assert_active_skill(
        self,
        response,
        provider: SkillSelectingProvider,
        skill_name: str,
        required_tool: str,
    ) -> None:
        self.assertIn(required_tool, response.tool_calls)
        self.assertIn(f"ACTIVE_SKILL: {skill_name}", provider.answer_system_prompt)
        planner_trace = next(
            trace for trace in response.tool_results if trace.name == "llm_tool_planner"
        )
        self.assertEqual(planner_trace.input["skill_name"], skill_name)

    def test_limit_up_pool_skill_fills_required_tool(self) -> None:
        provider = SkillSelectingProvider("limit-up-pool")

        response = answer_first_board_chat(
            AgentChatRequest(session_id="skill-limit-up", message="首板票有哪些"),
            events=SAMPLE_EVENTS,
            llm_provider=provider,
        )

        self._assert_active_skill(
            response,
            provider,
            "limit-up-pool",
            "limit_up_events",
        )

    @patch("app.agents.tools.AgentToolRegistry.hot_stock_ranking")
    def test_popularity_skill_fills_required_tool(self, hot_stock_ranking) -> None:
        payload = {
            "source": "hithink-finance",
            "source_label": "同花顺",
            "captured_at": "2026-08-28T08:00:00+00:00",
            "captured_at_beijing": "2026-08-28T16:00:00+08:00",
            "data_fresh": True,
            "requested_count": 20,
            "count": 1,
            "complete": False,
            "items": [
                {"rank": 1, "name": "测试热门股", "symbol": "000001"}
            ],
        }
        hot_stock_ranking.return_value = ToolResult(
            name="hot_stock_ranking",
            input={"period": "day", "limit": 20, "source": "auto"},
            output=payload,
            summary="最新热股榜返回 1 只。",
            trace_output=payload,
        )
        provider = SkillSelectingProvider("popularity")

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="skill-popularity",
                message="有哪些票比较热门",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=provider,
        )

        self._assert_active_skill(
            response,
            provider,
            "popularity",
            "hot_stock_ranking",
        )
        hot_stock_ranking.assert_called_once_with(
            period="day",
            limit=20,
            source="auto",
        )

    def test_first_board_rating_skill_fills_required_tool(self) -> None:
        provider = SkillSelectingProvider("first-board-rating")

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="skill-rating",
                message="哪些首板候选评分靠前",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=provider,
        )

        self._assert_active_skill(
            response,
            provider,
            "first-board-rating",
            "first_board_ratings",
        )


if __name__ == "__main__":
    unittest.main()
