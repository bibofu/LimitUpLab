import json
import os
import unittest
from datetime import datetime, timezone
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
    ToolResult,
)
from app.models import AgentChatRequest, FinanceNewsFacts, FinanceNewsItem
from app.repositories import SQLiteFirstBoardRepository
from app.services.llm_provider import LLMProvider, LLMResult
from app.services.sample_data import SAMPLE_EVENTS


class DeferredToolInjectionProvider(LLMProvider):
    """Request a hidden V2 tool to verify server-side enforcement."""

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
                        "intent_label": "remote_limit_up_pool",
                        "skill_name": None,
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

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        del user_prompt
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "hot_stock_ranking",
                        "skill_name": "popularity",
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

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        del user_prompt
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "latest_finance_news",
                        "skill_name": "finance-news",
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
        self.assertIn("sector_performance", registry.schema_prompt())
        self.assertNotIn("web_search", schema_names)

    def test_extended_profile_preserves_deferred_v2_tools(self) -> None:
        registry = AgentToolRegistry(
            events=SAMPLE_EVENTS,
            first_board_repository=self.repository,
            profile=EXTENDED_AGENT_PROFILE,
        )

        schema_names = {schema.name for schema in registry.schemas()}
        self.assertTrue(V1_DEFERRED_REALTIME_TOOL_NAMES.issubset(schema_names))
        self.assertIn("hot_stock_ranking", registry.schema_prompt())

    def test_v1_skill_catalog_includes_read_only_external_workflows(self) -> None:
        catalog = AGENT_SKILL_REGISTRY.schema_prompt(V1_CLOSED_MARKET_TOOL_NAMES)

        self.assertIn("first-board-rating", catalog)
        self.assertIn("limit-up-pool", catalog)
        self.assertIn("popularity", catalog)
        self.assertIn("finance-news", catalog)
        self.assertIsNotNone(
            AGENT_SKILL_REGISTRY.resolve(
                "popularity",
                [],
                V1_CLOSED_MARKET_TOOL_NAMES,
            )
        )
        self.assertIsNotNone(
            AGENT_SKILL_REGISTRY.resolve(
                "finance-news",
                [],
                V1_CLOSED_MARKET_TOOL_NAMES,
            )
        )

    def test_v1_policy_repairs_current_popularity_query(self) -> None:
        registry = AgentToolRegistry(
            events=SAMPLE_EVENTS,
            first_board_repository=self.repository,
            profile=V1_AGENT_PROFILE,
        )
        policy = AgentToolPolicyEngine(registry)
        request = AgentChatRequest(session_id="v1-popularity-policy", message="现在的热门股票有哪些")

        execution = self._empty_execution()
        payload = {
            "source": "hithink-finance",
            "source_label": "同花顺",
            "captured_at": "2026-08-30T08:00:00+00:00",
            "captured_at_beijing": "2026-08-30T16:00:00+08:00",
            "data_fresh": True,
            "requested_count": 20,
            "count": 1,
            "complete": False,
            "items": [{"rank": 1, "name": "测试热门股", "symbol": "000001"}],
        }
        with patch.object(
            registry,
            "hot_stock_ranking",
            return_value=ToolResult(
                name="hot_stock_ranking",
                input={"period": "day", "limit": 20, "source": "auto"},
                output=payload,
                summary="同花顺热股榜返回 1/20 只。",
                trace_output=payload,
            ),
        ) as ranking:
            repaired = policy.reconcile(request=request, execution=execution)

        self.assertTrue(policy.requires_grounding(request))
        self.assertEqual(repaired, ["hot_stock_ranking"])
        self.assertEqual(execution["tool_call_names"], ["hot_stock_ranking"])
        ranking.assert_called_once_with(period="day", limit=20, source="auto")

    @patch("app.agents.tools.AgentToolRegistry.remote_limit_up_pool")
    def test_injected_deferred_tool_call_is_blocked(self, remote_limit_up_pool) -> None:
        provider = DeferredToolInjectionProvider()
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

        remote_limit_up_pool.assert_not_called()
        self.assertEqual(response.answer, UNANSWERABLE_TEXT)
        self.assertEqual(provider.calls, 1)
        self.assertIn("v1_close_review", provider.planner_system_prompt)
        self.assertNotIn('"name":"remote_limit_up_pool"', provider.planner_system_prompt)
        blocked_trace = next(
            trace for trace in response.tool_results if trace.name == "remote_limit_up_pool"
        )
        self.assertEqual(blocked_trace.status, "error")

    @patch("app.agents.tools.AgentToolRegistry.hot_stock_ranking")
    def test_current_popularity_question_is_grounded_in_v1(self, hot_stock_ranking) -> None:
        payload = {
            "source": "hithink-finance",
            "source_label": "同花顺",
            "captured_at": "2026-08-30T08:00:00+00:00",
            "captured_at_beijing": "2026-08-30T16:00:00+08:00",
            "data_fresh": True,
            "requested_count": 20,
            "count": 1,
            "complete": False,
            "items": [{"rank": 1, "name": "测试热门股", "symbol": "000001"}],
        }
        hot_stock_ranking.return_value = ToolResult(
            name="hot_stock_ranking",
            input={"period": "day", "limit": 20, "source": "auto"},
            output=payload,
            summary="同花顺热股榜返回 1/20 只。",
            trace_output=payload,
        )

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="v1-current-popularity",
                message="现在的热门股票有哪些",
            ),
            events=SAMPLE_EVENTS,
            repository=self.repository,
            llm_provider=PopularityPolicyRepairProvider(),
        )

        self.assertIn("hot_stock_ranking", response.tool_calls)
        self.assertIn("测试热门股", response.answer)
        self.assertIn("北京时间", response.answer)
        hot_stock_ranking.assert_called_once_with(
            period="day",
            limit=20,
            source="auto",
        )

    @patch("app.agents.tools.AgentToolRegistry.finance_news")
    def test_latest_finance_news_question_is_grounded_in_v1(self, finance_news) -> None:
        facts = FinanceNewsFacts(
            fetched_at=datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc),
            window_hours=48,
            sources=["东方财富", "同花顺"],
            items=[
                FinanceNewsItem(
                    title="央行发布公开市场操作公告",
                    summary="央行公布当日公开市场操作情况。",
                    published_at=datetime(2026, 8, 30, 7, 50, tzinfo=timezone.utc),
                    source="东方财富",
                    url="https://example.com/macro",
                    category="宏观",
                    relevance_score=8.0,
                )
            ],
        )
        finance_news.return_value = ToolResult(
            name="finance_news",
            input={"query": None, "limit": 8, "hours": 48},
            output=facts,
            summary="东方财富、同花顺聚合到 1 条近 48 小时财经快讯。",
            trace_output=facts.model_dump(mode="json"),
        )

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="v1-latest-finance-news",
                message="最新的财经新闻",
            ),
            events=SAMPLE_EVENTS,
            repository=self.repository,
            llm_provider=FinanceNewsPolicyRepairProvider(),
        )

        self.assertIn("finance_news", response.tool_calls)
        self.assertNotIn("web_search", response.tool_calls)
        self.assertIn("央行", response.answer)
        self.assertIn("https://example.com/macro", response.references)
        finance_news.assert_called_once_with(query=None, limit=8, hours=48)

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
