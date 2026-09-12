import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.agents.capability_contract import capability_schema_prompt
from app.agents.chat import UNANSWERABLE_TEXT, answer_first_board_chat
from app.agents.tool_policy import AgentToolPolicyEngine, ToolExecution
from app.agents.tools import (
    EXTENDED_AGENT_PROFILE,
    V1_AGENT_PROFILE,
    V1_CLOSED_MARKET_TOOL_NAMES,
    V1_DEFERRED_REALTIME_TOOL_NAMES,
    AgentToolRegistry,
    ToolResult,
)
from app.models import (
    AgentChatRequest,
    FinanceNewsFacts,
    FinanceNewsItem,
    StockNewsFacts,
    StockNewsItem,
)
from app.repositories import SQLiteFirstBoardRepository
from app.services.llm_provider import LLMProvider, LLMResult
from app.services.sample_data import SAMPLE_EVENTS


class DeferredToolInjectionProvider(LLMProvider):
    """Request a hidden V2 tool to verify server-side enforcement."""

    # Prepare the init fixture or observation used by the surrounding regression scenario.
    def __init__(self) -> None:
        self.planner_system_prompt = ""
        self.calls = 0

    # Build the LLMResult fixture used by the surrounding regression scenario.
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        del user_prompt
        self.calls += 1
        if "first job is to decide which tools are needed" in system_prompt:
            self.planner_system_prompt = system_prompt
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "remote_limit_up_pool",
                        "capabilities": [],
                        "safety": "normal",
                        "tool_calls": [
                            {
                                "name": "remote_limit_up_pool",
                                "arguments": {},
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


class PopularityPolicyRepairProvider(LLMProvider):
    """Skip planning the popularity tool so the policy contract must repair it."""

    # Build the LLMResult fixture used by the surrounding regression scenario.
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        del user_prompt
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "hot_stock_ranking",
                        "capabilities": ["popularity"],
                        "safety": "normal",
                        "tool_calls": [],
                        "answer_directly": "",
                    }
                ),
                model="fake-v1-planner",
                provider="fake",
            )
        return LLMResult(
            content=(
                "截至 2026-08-30 16:00（北京时间），同花顺热股榜第 1 名为"
                "测试热门股(000001)。热度反映关注度，不代表推荐。"
            ),
            model="fake-v1-answer",
            provider="fake",
        )


class FinanceNewsPolicyRepairProvider(LLMProvider):
    """Skip planning the news tool so the policy contract must repair it."""

    # Build the LLMResult fixture used by the surrounding regression scenario.
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        del user_prompt
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "latest_finance_news",
                        "capabilities": ["finance_news"],
                        "safety": "normal",
                        "tool_calls": [],
                        "answer_directly": "",
                    }
                ),
                model="fake-v1-planner",
                provider="fake",
            )
        return LLMResult(
            content=(
                "截至北京时间 2026-08-30 16:00，东方财富报道央行发布"
                "公开市场操作公告。原文：https://example.com/macro"
            ),
            model="fake-v1-answer",
            provider="fake",
        )


class StockNewsPolicyRepairProvider(LLMProvider):
    """Declare the stock-news capability and let the contract add its evidence tool."""

    # Build the LLMResult fixture used by the surrounding regression scenario.
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        del user_prompt
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "stock_news",
                        "capabilities": ["stock_news"],
                        "safety": "normal",
                        "tool_calls": [],
                        "answer_directly": "",
                    }
                ),
                model="fake-v1-planner",
                provider="fake",
            )
        return LLMResult(
            content=(
                "思泉新材(301489)在 2026-08-31 发布一条公告类报道。"
                "来源：测试资讯源，原文：https://example.com/301489"
            ),
            model="fake-v1-answer",
            provider="fake",
        )


class AgentV1ProfileTest(unittest.TestCase):
    # Prepare the isolated fixtures and dependencies shared by the tests in this class.
    def setUp(self) -> None:
        self.database_path = (
            Path(__file__).resolve().parents[1]
            / f"agent-v1-profile-{uuid4().hex}.sqlite"
        )
        self.repository = SQLiteFirstBoardRepository(self.database_path)
        self.addCleanup(self._cleanup_database)

    # Release the temporary resources owned by this test fixture.
    def _cleanup_database(self) -> None:
        for suffix in ("", "-shm", "-wal"):
            Path(f"{self.database_path}{suffix}").unlink(missing_ok=True)

    # Prepare the empty execution fixture or observation used by the surrounding regression

    # Regression scenario: default profile exposes close tools and read only external facts.
    def test_default_profile_exposes_close_tools_and_read_only_external_facts(self) -> None:
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
        self.assertIn("hot_stock_ranking", registry.schema_prompt())
        self.assertIn("finance_news", registry.schema_prompt())
        self.assertIn("stock_news", registry.schema_prompt())
        self.assertIn("stock_activity", registry.schema_prompt())
        self.assertIn("sector_performance", registry.schema_prompt())
        self.assertNotIn("web_search", schema_names)

    # Regression scenario: extended profile preserves deferred v2 tools.
    def test_extended_profile_preserves_deferred_v2_tools(self) -> None:
        registry = AgentToolRegistry(
            events=SAMPLE_EVENTS,
            first_board_repository=self.repository,
            profile=EXTENDED_AGENT_PROFILE,
        )

        schema_names = {schema.name for schema in registry.schemas()}
        self.assertTrue(V1_DEFERRED_REALTIME_TOOL_NAMES.issubset(schema_names))
        self.assertIn("hot_stock_ranking", registry.schema_prompt())

    # Regression scenario: v1 capability catalog includes read only external workflows.
    def test_v1_capability_catalog_includes_read_only_external_workflows(self) -> None:
        catalog = capability_schema_prompt(V1_CLOSED_MARKET_TOOL_NAMES)

        self.assertIn("first_board_rating", catalog)
        self.assertIn("limit_up_pool", catalog)
        self.assertIn("popularity", catalog)
        self.assertIn("finance_news", catalog)
        self.assertIn("stock_news", catalog)








if __name__ == "__main__":
    unittest.main()
