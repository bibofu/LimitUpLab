import json
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from app.agents.chat import answer_first_board_chat
from app.collectors import (
    HithinkHotStockFact,
    HithinkHotStockSnapshot,
    PopularityRankingItem,
    PopularityRankingSnapshot,
)
from app.models import (
    AgentChatRequest,
    FinanceNewsFacts,
    FinanceNewsItem,
    MarketIndexTrendFacts,
    MarketIndexTrendItem,
    MarketIndexTrendPoint,
    SectorPerformanceFacts,
    WebSearchFacts,
    WebSearchResult,
)
from app.services.llm_provider import DisabledLLMProvider, LLMProvider, LLMResult
from app.services.sample_data import SAMPLE_EVENTS


class ExternalToolProvider(LLMProvider):
    """Planner intentionally skips tools so policy repair is exercised."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "sector_move_reason",
                        "safety": "normal",
                        "tool_calls": [],
                        "answer_directly": "没有工具支撑的猜测",
                    }
                ),
                model="fake-planner",
                provider="fake",
            )
        return LLMResult(
            content=(
                "半导体板块当日下跌7.44%，上涨4家、下跌181家；"
                "公开报道中的原因需结合来源链接核对。"
            ),
            model="fake-answer",
            provider="fake",
        )


class HithinkToolProvider(LLMProvider):
    """Planner selects the structured Tonghuashun popularity tool."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "hot_stock_ranking",
                        "safety": "normal",
                        "tool_calls": [
                            {
                                "name": "hot_stock_ranking",
                                "arguments": {"period": "day", "limit": 5},
                            }
                        ],
                        "answer_directly": "",
                    }
                ),
                model="fake-planner",
                provider="fake",
            )
        return LLMResult(
            content="同花顺热股榜中，通鼎互联(002491)当前排名第3。",
            model="fake-answer",
            provider="fake",
        )


class Top100HotStockProvider(LLMProvider):
    """Planner under-requests rows and final answer intentionally truncates them."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "hot_stock_ranking",
                        "safety": "normal",
                        "tool_calls": [
                            {
                                "name": "hot_stock_ranking",
                                "arguments": {
                                    "period": "day",
                                    "limit": 30,
                                    "source": "auto",
                                },
                            }
                        ],
                        "answer_directly": "",
                    }
                ),
                model="fake-planner",
                provider="fake",
            )
        return LLMResult(
            content="东方财富热股榜第1名是测试股票1(600001)。",
            model="fake-answer",
            provider="fake",
        )


class HotStockFirstBoardIntersectionProvider(LLMProvider):
    """Planner omits the event pool while the final answer returns the wrong set."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "query_first_board_in_hot_ranking",
                        "safety": "normal",
                        "tool_calls": [
                            {
                                "name": "hot_stock_ranking",
                                "arguments": {
                                    "period": "day",
                                    "limit": 100,
                                    "source": "eastmoney",
                                },
                            },
                        ],
                        "answer_directly": "",
                    }
                ),
                model="fake-planner",
                provider="fake",
            )
        return LLMResult(
            content="热股榜中贵州茅台(600519)是首板票。",
            model="fake-answer",
            provider="fake",
        )


class FinanceNewsProvider(LLMProvider):
    """Planner skips the feed so the deterministic grounding policy repairs it."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "latest_finance_news",
                        "safety": "normal",
                        "tool_calls": [],
                        "answer_directly": "",
                    }
                ),
                model="fake-planner",
                provider="fake",
            )
        return LLMResult(
            content=(
                "截至北京时间 2026-08-24 09:20，央行公布人民币中间价；"
                "这可能影响外资流向和市场风险偏好，属于基于新闻事实的市场关联推断。"
            ),
            model="fake-answer",
            provider="fake",
        )


class AgentExternalToolsTest(unittest.TestCase):
    @patch("app.agents.tools.collect_market_index_trends")
    def test_weekly_market_trend_uses_major_index_history(self, collect_trends) -> None:
        def index_item(
            name: str,
            symbol: str,
            start_close: float,
            end_close: float,
            return_pct: float,
        ) -> MarketIndexTrendItem:
            return MarketIndexTrendItem(
                name=name,
                symbol=symbol,
                start_date=date(2026, 5, 11),
                end_date=date(2026, 5, 15),
                start_close=start_close,
                end_close=end_close,
                return_pct=return_pct,
                max_drawdown_pct=-1.2,
                positive_days=2,
                negative_days=2,
                points=[
                    MarketIndexTrendPoint(
                        trade_date=date(2026, 5, 15),
                        close=end_close,
                        change_pct=0.5,
                    )
                ],
                source="test-index",
            )

        collect_trends.return_value = MarketIndexTrendFacts(
            requested_days=5,
            requested_end_date=date(2026, 5, 15),
            data_as_of=date(2026, 5, 15),
            data_fresh=True,
            indices=[
                index_item("上证指数", "000001.SH", 3600, 3672, 2.0),
                index_item("深证成指", "399001.SZ", 11000, 11110, 1.0),
                index_item("创业板指", "399006.SZ", 2300, 2277, -1.0),
            ],
        )

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="weekly-market-index",
                message="近一周大盘走势怎么样",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=DisabledLLMProvider(),
        )

        self.assertEqual(response.intent, "market_index_trend")
        self.assertIn("market_index_trend", response.tool_calls)
        self.assertNotIn("first_board_ratings", response.tool_calls)
        self.assertIn("上证指数", response.answer)
        self.assertIn("深证成指", response.answer)
        self.assertIn("创业板指", response.answer)
        self.assertIn("+2.00%", response.answer)
        self.assertIn("-1.00%", response.answer)
        collect_trends.assert_called_once_with(
            days=5,
            end_date=date(2026, 5, 15),
        )

    @patch("app.agents.tools.search_web")
    @patch("app.agents.tools.build_sector_performance")
    def test_policy_repairs_sector_and_search_tools(
        self,
        sector_builder,
        search_builder,
    ) -> None:
        today = date.today()
        sector_builder.return_value = SectorPerformanceFacts(
            requested_sector="半导体",
            sector_name="半导体",
            trade_date=today,
            data_as_of=today,
            data_fresh=True,
            rank=87,
            sector_count=90,
            change_pct=-7.44,
            up_count=4,
            down_count=181,
            sources=["fake-sector"],
        )
        search_builder.return_value = WebSearchFacts(
            query="今天半导体板块为什么下跌",
            fetched_at=datetime.now(timezone.utc),
            provider="fake-search",
            results=[
                WebSearchResult(
                    title="半导体板块报道",
                    url="https://example.com/report",
                    domain="example.com",
                    snippet="板块下跌相关公开报道。",
                )
            ],
        )

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="external-tools",
                message="今天半导体板块为什么下跌",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=ExternalToolProvider(),
        )

        self.assertIn("sector_performance", response.tool_calls)
        self.assertIn("web_search", response.tool_calls)
        self.assertNotIn("rating_backtest", response.tool_calls)
        self.assertNotIn("rating_evaluation", response.tool_calls)
        self.assertIn("7.44%", response.answer)
        self.assertIn("https://example.com/report", response.references)

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
