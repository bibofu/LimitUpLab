import json
import os
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from app.agents.chat import _template_answer_from_tool_facts, answer_first_board_chat
from app.agents.chat_templates import _format_capital_flow_amount
from app.collectors import (
    HithinkDragonTigerFact,
    HithinkDragonTigerSnapshot,
    HithinkHotStockFact,
    HithinkHotStockSnapshot,
    PopularityRankingItem,
    PopularityRankingSnapshot,
)
from app.collectors.limit_down_collector import LimitDownItem, LimitDownSnapshot
from app.models import (
    AgentChatRequest,
    FinanceNewsFacts,
    FinanceNewsItem,
    MarketIndexTrendFacts,
    MarketIndexTrendItem,
    MarketIndexTrendPoint,
    SectorPerformanceFacts,
    SectorRankingItem,
    SectorStockRankingFacts,
    SectorStockTrendItem,
    WebSearchFacts,
    WebSearchResult,
)
from app.services.llm_provider import DisabledLLMProvider, LLMProvider, LLMResult
from app.services.sample_data import SAMPLE_EVENTS


























@patch.dict(
    os.environ,
    {"LIMITUPLAB_AGENT_PROFILE": "extended"},
    clear=False,
)
class AgentExternalToolsTest(unittest.TestCase):
















    # Regression scenario: unspecified sector ranking is capped at top ten.
    @patch("app.agents.tools.build_sector_stock_ranking")
    def test_unspecified_sector_ranking_is_capped_at_top_ten(self, ranking_builder) -> None:
        today = date.today()
        ranking_builder.return_value = SectorStockRankingFacts(
            requested_sector="军工装备",
            sector_name="军工装备",
            sector_category="industry",
            sector_thscode="881166.TI",
            requested_days=20,
            requested_limit=10,
            data_as_of=today,
            member_count=82,
            analyzed_count=20,
            missing_count=0,
            truncated_count=62,
            items=[
                SectorStockTrendItem(
                    rank=1,
                    symbol="000001",
                    name="军工样本",
                    trend_score=80.0,
                    trend="rising",
                    data_as_of=today,
                    latest_close=10.0,
                    return_5d_pct=8.0,
                    return_20d_pct=16.0,
                )
            ],
            sources=["fake-sector-ranking"],
        )
        provider = SectorRankingPlannerProvider()

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="sector-stock-ranking-top10",
                message="军工装备哪些个股表现比较好",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=provider,
        )

        self.assertEqual(ranking_builder.call_args.kwargs["limit"], 10)
        self.assertEqual(provider.answer_calls, 0)
        self.assertIn("军工样本", response.answer)
        self.assertNotIn("趋势分", response.answer)
        self.assertNotIn("共 82 只成分股", response.answer)
        self.assertIn("template_general_answer", response.tool_calls)
        self.assertEqual(response.performance.answer_prompt_chars, 0)

    # Regression scenario: llm can call tonghuashun hot stock tool.
    @patch("app.collectors.hithink_finance_collector.HithinkFinanceCollector.collect_hot_stocks")
    def test_llm_can_call_tonghuashun_hot_stock_tool(self, collect_hot_stocks) -> None:
        collect_hot_stocks.return_value = HithinkHotStockSnapshot(
            captured_at=datetime(2026, 8, 21, 8, tzinfo=timezone.utc),
            period="day",
            items=[
                HithinkHotStockFact(
                    symbol="002491",
                    thscode="002491.SZ",
                    name="通鼎互联",
                    rank=3,
                    heat=4_382_035,
                    rank_change=1,
                    rank_trend="up",
                )
            ],
        )

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="hithink-hot-stock",
                message="同花顺当前热股榜里哪些股票靠前？",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=HithinkToolProvider(),
        )

        self.assertIn("hot_stock_ranking", response.tool_calls)
        self.assertTrue(
            any(trace.name == "hot_stock_ranking" for trace in response.tool_results)
        )
        self.assertIn("002491", response.answer)
        self.assertIn("source=hithink-finance", response.references)

    # Regression scenario: explicit top100 overrides planner limit and renders every stock.
    @patch("app.agents.tools.collect_eastmoney_hot_stock_ranking")
    def test_explicit_top100_overrides_planner_limit_and_renders_every_stock(
        self,
        collect_ranking,
    ) -> None:
        collect_ranking.return_value = PopularityRankingSnapshot(
            captured_at=datetime.now(timezone.utc),
            items=[
                PopularityRankingItem(
                    symbol=f"{600000 + index:06d}",
                    thscode=f"{600000 + index:06d}.SH",
                    name=f"测试股票{index}",
                    rank=index,
                    heat=None,
                    rank_change=0,
                    rank_trend="flat",
                )
                for index in range(1, 101)
            ],
        )

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="eastmoney-hot-stock-top100",
                message="热股榜前100名",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=Top100HotStockProvider(),
        )

        trace = next(
            item for item in response.tool_results if item.name == "hot_stock_ranking"
        )
        self.assertEqual(trace.output["requested_count"], 100)
        self.assertEqual(trace.output["count"], 100)
        self.assertTrue(trace.output["complete"])
        self.assertIn("600001", response.answer)
        self.assertIn("600100", response.answer)
        self.assertTrue(all(f"{600000 + index:06d}" in response.answer for index in range(1, 101)))
        self.assertTrue(any("incomplete" in warning for warning in response.warnings))
        self.assertIn("source=eastmoney", response.references)

    # Regression scenario: hot stock top100 first board question returns only intersection.
    @patch("app.agents.tools.collect_eastmoney_hot_stock_ranking")
    def test_hot_stock_top100_first_board_question_returns_only_intersection(
        self,
        collect_ranking,
    ) -> None:
        collect_ranking.return_value = PopularityRankingSnapshot(
            captured_at=datetime.now(timezone.utc),
            items=[
                PopularityRankingItem(
                    symbol="600519",
                    thscode="600519.SH",
                    name="贵州茅台",
                    rank=1,
                    heat=None,
                    rank_change=0,
                    rank_trend="flat",
                ),
                PopularityRankingItem(
                    symbol="301489",
                    thscode="301489.SZ",
                    name="思泉新材",
                    rank=2,
                    heat=None,
                    rank_change=0,
                    rank_trend="flat",
                ),
                PopularityRankingItem(
                    symbol="002230",
                    thscode="002230.SZ",
                    name="科大讯飞",
                    rank=3,
                    heat=None,
                    rank_change=0,
                    rank_trend="flat",
                ),
            ],
        )

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="hot-stock-first-board-intersection",
                message="热股榜前100中，首板票有哪些",
                trade_date=date(2026, 5, 15),
            ),
            events=SAMPLE_EVENTS,
            llm_provider=HotStockFirstBoardIntersectionProvider(),
        )

        self.assertIn("hot_stock_ranking", response.tool_calls)
        self.assertIn("limit_up_events", response.tool_calls)
        self.assertIn("思泉新材(301489)", response.answer)
        self.assertNotIn("600519", response.answer)
        self.assertNotIn("002230", response.answer)
        self.assertIn("共有 1 只首板票", response.answer)
        self.assertTrue(any("cross-list" in warning for warning in response.warnings))

    # Regression scenario: policy repairs broad finance news with structured feed.
    @patch("app.agents.tools.collect_finance_news")
    def test_policy_repairs_broad_finance_news_with_structured_feed(
        self,
        news_builder,
    ) -> None:
        published_at = datetime(2026, 8, 24, 9, 15, tzinfo=timezone.utc)
        news_builder.return_value = FinanceNewsFacts(
            fetched_at=datetime(2026, 8, 24, 9, 20, tzinfo=timezone.utc),
            window_hours=48,
            sources=["东方财富", "同花顺"],
            items=[
                FinanceNewsItem(
                    title="央行公布人民币中间价",
                    summary="央行公布当日人民币汇率中间价。",
                    published_at=published_at,
                    source="东方财富",
                    url="https://example.com/macro",
                    category="宏观",
                    relevance_score=8.5,
                )
            ],
        )

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="finance-news",
                message="有什么最新财经新闻",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=FinanceNewsProvider(),
        )

        self.assertIn("finance_news", response.tool_calls)
        self.assertNotIn("web_search", response.tool_calls)
        self.assertIn("人民币中间价", response.answer)
        self.assertIn("https://example.com/macro", response.references)


if __name__ == "__main__":
    unittest.main()
