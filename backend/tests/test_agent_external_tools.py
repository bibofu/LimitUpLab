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


def _dragon_tiger_fact() -> HithinkDragonTigerFact:
    return HithinkDragonTigerFact(
        symbol="000001",
        thscode="000001.SZ",
        name="平安银行",
        change_pct=1.2,
        buy_amount=200_000_000,
        sell_amount=100_000_000,
        net_buy_amount=100_000_000,
        net_rate=10.0,
        organization_net_buy_amount=50_000_000,
        hot_money_net_buy_amount=20_000_000,
        hot_rank=10,
        range_days=1,
        limit_reason="日涨幅偏离值达7%",
        concepts=["银行"],
    )


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


class SectorRankingPlannerProvider(LLMProvider):
    """Request too many rows so the backend default limit can be verified."""

    def __init__(self) -> None:
        self.answer_calls = 0

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "sector_stock_ranking",
                        "capabilities": ["sector_stock_ranking"],
                        "safety": "normal",
                        "tool_calls": [
                            {
                                "name": "sector_stock_ranking",
                                "arguments": {
                                    "sector": "军工装备",
                                    "days": 20,
                                    "limit": 20,
                                },
                            }
                        ],
                        "answer_directly": "",
                    }
                ),
                model="fake-planner",
                provider="fake",
            )
        self.answer_calls += 1
        raise AssertionError("simple Top10 ranking should not call the answer model")


class WrongBroadSectorPlannerProvider(LLMProvider):
    """Reproduce a planner that mistakes broad wording for a sector name."""

    def __init__(self) -> None:
        self.answer_calls = 0

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "market_environment",
                        "capabilities": ["market_environment"],
                        "safety": "normal",
                        "tool_calls": [
                            {
                                "name": "sector_performance",
                                "arguments": {"sector": "大盘哪些"},
                            }
                        ],
                        "answer_directly": "",
                    }
                ),
                model="fake-planner",
                provider="fake",
            )
        self.answer_calls += 1
        raise AssertionError("simple sector ranking should not call the answer model")


class WrongLimitDownPlannerProvider(LLMProvider):
    """Reproduce the live planner mistake that mapped limit-down to limit-up."""

    def __init__(self) -> None:
        self.answer_calls = 0

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "limit_up_pool_query",
                        "capabilities": ["limit_up_pool"],
                        "safety": "normal",
                        "tool_calls": [
                            {
                                "name": "limit_up_events",
                                "arguments": {
                                    "event_status": "limit_down",
                                    "closed_only": True,
                                },
                            }
                        ],
                        "answer_directly": "",
                    }
                ),
                model="fake-planner",
                provider="fake",
            )
        self.answer_calls += 1
        raise AssertionError("validated market-event facts should use the concise renderer")


class DragonTigerToolProvider(LLMProvider):
    """Planner selects the Dragon-Tiger tool with an intentionally stale date."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "dragon_tiger_list_query",
                        "safety": "normal",
                        "tool_calls": [
                            {
                                "name": "dragon_tiger_list",
                                "arguments": {
                                    "trade_date": "2026-05-14",
                                    "board_type": "all",
                                    "limit": 30,
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
            content="已按最新完整交易日汇总龙虎榜。",
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


class FailedMarketTrendProvider(LLMProvider):
    """Fake planner whose required market-data tool fails."""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        self.calls += 1
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "market_index_trend",
                        "safety": "normal",
                        "tool_calls": [
                            {
                                "name": "market_index_trend",
                                "arguments": {"days": 5},
                            }
                        ],
                        "answer_directly": "",
                    }
                ),
                model="fake-planner",
                provider="fake",
            )
        return LLMResult(
            content="工具虽然失败，但我猜上证指数近一周上涨。",
            model="fake-answer",
            provider="fake",
        )


class HotStockDirectGuessProvider(LLMProvider):
    """Fake planner that tries to guess an undated popularity answer."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "hot_stock_ranking",
                        "safety": "normal",
                        "tool_calls": [],
                        "answer_directly": "我猜贵州茅台最热门。",
                    }
                ),
                model="fake-planner",
                provider="fake",
            )
        return LLMResult(
            content="最新获取的人气榜中，通鼎互联(002491)排名第3。",
            model="fake-answer",
            provider="fake",
        )


@patch.dict(
    os.environ,
    {"LIMITUPLAB_AGENT_PROFILE": "extended"},
    clear=False,
)
class AgentExternalToolsTest(unittest.TestCase):
    @patch("app.agents.tools.collect_limit_down_pool")
    def test_limit_down_question_repairs_wrong_planner_semantics(self, collect_pool) -> None:
        trade_date = date(2026, 9, 2)
        collect_pool.return_value = LimitDownSnapshot(
            trade_date=trade_date,
            items=[
                LimitDownItem(
                    symbol="000001",
                    name="跌停样本一",
                    change_pct=-10.01,
                    industry="军工装备",
                ),
                LimitDownItem(
                    symbol="300001",
                    name="跌停样本二",
                    change_pct=-20.0,
                    industry="软件服务",
                ),
            ],
        )
        provider = WrongLimitDownPlannerProvider()

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="limit-down-semantic-repair",
                message="今天跌停的票有哪些",
                intent_hint="today_summary",
                trade_date=trade_date,
            ),
            events=SAMPLE_EVENTS,
            llm_provider=provider,
        )

        self.assertEqual(response.intent, "market_event_query")
        self.assertIn("market_event_pool", response.tool_calls)
        self.assertNotIn("limit_up_events", response.tool_calls)
        self.assertIn("跌停样本一（000001）", response.answer)
        self.assertIn("跌停样本二（300001）", response.answer)
        self.assertIn("跌停股票共 2 只", response.answer)
        self.assertNotIn("数据源", response.answer)
        self.assertEqual(provider.answer_calls, 0)
        event_trace = next(
            trace for trace in response.tool_results if trace.name == "market_event_pool"
        )
        self.assertEqual(event_trace.output["event_type"], "limit_down")
        self.assertEqual(response.performance.answer_prompt_chars, 0)

    @patch("app.agents.tools.collect_limit_down_pool")
    def test_empty_limit_down_pool_is_valid_without_llm(self, collect_pool) -> None:
        trade_date = date(2026, 9, 2)
        collect_pool.return_value = LimitDownSnapshot(
            trade_date=trade_date,
            items=[],
        )

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="empty-limit-down-no-llm",
                message="列一下今天的跌停名单",
                intent_hint="today_summary",
                trade_date=trade_date,
            ),
            events=SAMPLE_EVENTS,
            llm_provider=DisabledLLMProvider(),
        )

        self.assertIn("跌停股票共 0 只", response.answer)
        self.assertNotIn("无法回答", response.answer)
        self.assertEqual(
            response.tool_calls,
            ["market_event_pool", "template_general_answer"],
        )

    def test_dragon_tiger_fallback_formats_money_and_omits_missing_fields(
        self,
    ) -> None:
        answer = _template_answer_from_tool_facts(
            request=AgentChatRequest(
                session_id="dragon-tiger-money-format",
                message="龙虎榜的情况",
            ),
            intent="dragon_tiger_list_query",
            facts={
                "dragon_tiger_list": {
                    "trade_date": "2026-08-28",
                    "matched_count": 2,
                    "items": [
                        {
                            "symbol": "600613",
                            "name": "神奇制药",
                            "net_buy_amount": 41_000_000,
                            "organization_net_buy_amount": None,
                            "hot_money_net_buy_amount": 52_746_072.0,
                        },
                        {
                            "symbol": "000001",
                            "name": "平安银行",
                            "net_buy_amount": -125_000_000,
                            "organization_net_buy_amount": float("nan"),
                            "hot_money_net_buy_amount": None,
                        },
                    ],
                }
            },
        )

        self.assertIn("净买额 +4100.00 万元", answer)
        self.assertIn("游资净买 +5274.61 万元", answer)
        self.assertIn("净买额 -1.25 亿元", answer)
        self.assertNotIn("机构净买", answer)
        self.assertNotIn("None", answer)
        self.assertNotIn("52746072.0", answer)

    def test_sector_template_lists_every_matching_military_subsector(self) -> None:
        answer = _template_answer_from_tool_facts(
            request=AgentChatRequest(
                session_id="military-sector-group",
                message="今天军工板块表现如何？",
            ),
            intent="sector_performance",
            facts={
                "sector_performance": SectorPerformanceFacts(
                    requested_sector="国防军工",
                    sector_name="军工",
                    sector_type="industry_group",
                    trade_date=date(2026, 9, 3),
                    data_as_of=date(2026, 9, 3),
                    data_fresh=True,
                    sector_count=90,
                    matched_sectors=[
                        SectorRankingItem(
                            sector_name="军工装备",
                            rank=45,
                            change_pct=-0.59,
                            up_count=28,
                            down_count=53,
                        ),
                        SectorRankingItem(
                            sector_name="军工电子",
                            rank=66,
                            change_pct=-0.92,
                            up_count=14,
                            down_count=46,
                        ),
                    ],
                ).model_dump(mode="json")
            },
        )

        self.assertIn("对应多个行业", answer)
        self.assertIn("军工装备", answer)
        self.assertIn("军工电子", answer)
        self.assertNotIn("无法回答", answer)

    def test_sector_template_formats_history_only_concept_without_nulls(
        self,
    ) -> None:
        answer = _template_answer_from_tool_facts(
            request=AgentChatRequest(
                session_id="ai-concept-history",
                message="今天 AI 概念板块怎么样？",
            ),
            intent="sector_performance",
            facts={
                "sector_performance": SectorPerformanceFacts(
                    requested_sector="AI",
                    sector_name="人工智能",
                    sector_type="concept",
                    trade_date=date(2026, 9, 3),
                    data_as_of=date(2026, 9, 2),
                    data_fresh=False,
                    sector_count=0,
                    change_pct=-0.80,
                    return_5d_pct=2.97,
                    return_20d_pct=1.83,
                    sources=["fake-concept-history"],
                ).model_dump(mode="json")
            },
        )

        self.assertIn("人工智能概念板块涨跌幅 -0.80%", answer)
        self.assertIn("近5日 +2.97%", answer)
        self.assertIn("最近可用交易日", answer)
        self.assertNotIn("None", answer)
        self.assertNotIn("排名", answer)

    def test_capital_flow_formatter_rejects_non_numeric_values(self) -> None:
        self.assertIsNone(_format_capital_flow_amount(None))
        self.assertIsNone(_format_capital_flow_amount("41000000"))
        self.assertIsNone(_format_capital_flow_amount(float("inf")))

    @patch(
        "app.collectors.hithink_finance_collector."
        "HithinkFinanceCollector.collect_dragon_tiger"
    )
    def test_undated_dragon_tiger_question_uses_latest_local_trade_date(
        self,
        collect_dragon_tiger,
    ) -> None:
        latest_date = max(event.trade_date for event in SAMPLE_EVENTS)
        collect_dragon_tiger.return_value = HithinkDragonTigerSnapshot(
            trade_date=latest_date,
            board_type="all",
            stock_count=1,
            items=[_dragon_tiger_fact()],
        )

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="latest-dragon-tiger",
                message="龙虎榜的情况",
                trade_date=date(2026, 5, 14),
            ),
            events=SAMPLE_EVENTS,
            llm_provider=DragonTigerToolProvider(),
        )

        collect_dragon_tiger.assert_called_once_with(
            trade_date=latest_date,
            board_type="all",
            query=None,
            limit=30,
        )
        trace = next(
            item for item in response.tool_results if item.name == "dragon_tiger_list"
        )
        self.assertEqual(trace.input["trade_date"], latest_date.isoformat())
        self.assertEqual(trace.output["trade_date"], latest_date.isoformat())

    @patch(
        "app.collectors.hithink_finance_collector."
        "HithinkFinanceCollector.collect_dragon_tiger"
    )
    def test_explicit_dragon_tiger_date_overrides_latest_default(
        self,
        collect_dragon_tiger,
    ) -> None:
        requested_date = date(2026, 5, 14)
        collect_dragon_tiger.return_value = HithinkDragonTigerSnapshot(
            trade_date=requested_date,
            board_type="all",
            stock_count=1,
            items=[_dragon_tiger_fact()],
        )

        answer_first_board_chat(
            AgentChatRequest(
                session_id="dated-dragon-tiger",
                message="2026-05-14的龙虎榜情况",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=DragonTigerToolProvider(),
        )

        self.assertEqual(
            collect_dragon_tiger.call_args.kwargs["trade_date"],
            requested_date,
        )

    @patch("app.collectors.hithink_finance_collector.HithinkFinanceCollector.collect_hot_stocks")
    def test_natural_undated_popularity_question_uses_latest_snapshot(
        self,
        collect_hot_stocks,
    ) -> None:
        collect_hot_stocks.return_value = HithinkHotStockSnapshot(
            captured_at=datetime.now(timezone.utc),
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
                session_id="latest-hot-stock",
                message="有哪些票比较热门",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=HotStockDirectGuessProvider(),
        )

        self.assertIn("hot_stock_ranking", response.tool_calls)
        self.assertIn("002491", response.answer)
        self.assertNotIn("我猜", response.answer)
        collect_hot_stocks.assert_called_once_with(period="day", limit=20)

    @patch("app.agents.tools.collect_market_index_trends")
    def test_failed_required_tool_returns_fixed_unanswerable_text(
        self,
        collect_trends,
    ) -> None:
        collect_trends.side_effect = RuntimeError("upstream unavailable")
        provider = FailedMarketTrendProvider()

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="failed-market-index",
                message="近一周大盘走势怎么样",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=provider,
        )

        self.assertEqual(response.answer, "抱歉，该问题无法回答")
        self.assertIn("market_index_trend", response.tool_calls)
        self.assertEqual(provider.calls, 1)

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

    @patch("app.agents.tools.build_sector_performance")
    def test_broad_sector_ranking_overrides_wrong_planner_parameter(
        self,
        sector_builder,
    ) -> None:
        trade_date = date(2026, 9, 2)
        sector_builder.return_value = SectorPerformanceFacts(
            requested_sector=None,
            sector_name=None,
            trade_date=trade_date,
            data_as_of=trade_date,
            data_fresh=True,
            sector_count=90,
            top_sectors=[
                SectorRankingItem(
                    sector_name="军工装备", rank=1, change_pct=2.06
                ),
                SectorRankingItem(
                    sector_name="地面兵装", rank=2, change_pct=1.62
                ),
                SectorRankingItem(
                    sector_name="数据确权", rank=3, change_pct=1.52
                ),
            ],
            sources=["fake-sector"],
        )
        provider = WrongBroadSectorPlannerProvider()

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="broad-sector-semantic-repair",
                message="今天大盘哪些板块表现好",
                intent_hint="market_context",
                trade_date=trade_date,
            ),
            events=SAMPLE_EVENTS,
            llm_provider=provider,
        )

        self.assertEqual(response.intent, "sector_performance")
        self.assertEqual(response.tool_calls.count("sector_performance"), 1)
        self.assertNotIn("market_summary", response.tool_calls)
        self.assertIn("军工装备", response.answer)
        self.assertIn("地面兵装", response.answer)
        self.assertIn("数据确权", response.answer)
        self.assertNotIn("数据源", response.answer)
        self.assertEqual(provider.answer_calls, 0)
        self.assertEqual(response.performance.answer_prompt_chars, 0)
        sector_builder.assert_called_once_with(
            sector=None,
            trade_date=trade_date,
        )

    @patch("app.agents.tools.build_sector_performance")
    def test_broad_sector_ranking_works_without_llm(self, sector_builder) -> None:
        trade_date = date(2026, 9, 2)
        sector_builder.return_value = SectorPerformanceFacts(
            requested_sector=None,
            sector_name=None,
            trade_date=trade_date,
            data_as_of=trade_date,
            data_fresh=True,
            sector_count=90,
            top_sectors=[
                SectorRankingItem(
                    sector_name="军工装备", rank=1, change_pct=2.06
                )
            ],
            sources=["fake-sector"],
        )

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="broad-sector-no-llm",
                message="哪些行业今天涨得好",
                trade_date=trade_date,
            ),
            events=SAMPLE_EVENTS,
            llm_provider=DisabledLLMProvider(),
        )

        self.assertEqual(response.intent, "sector_performance")
        self.assertIn("军工装备", response.answer)
        self.assertIn("template_general_answer", response.tool_calls)
        sector_builder.assert_called_once_with(
            sector=None,
            trade_date=trade_date,
        )

    @patch("app.agents.tools.build_sector_performance")
    def test_broad_sector_laggard_question_uses_bottom_ranking(
        self,
        sector_builder,
    ) -> None:
        trade_date = date(2026, 9, 3)
        sector_builder.return_value = SectorPerformanceFacts(
            requested_sector=None,
            sector_name=None,
            trade_date=trade_date,
            data_as_of=trade_date,
            data_fresh=True,
            sector_count=90,
            top_sectors=[
                SectorRankingItem(
                    sector_name="保险", rank=1, change_pct=2.56
                )
            ],
            bottom_sectors=[
                SectorRankingItem(
                    sector_name="农产品加工", rank=90, change_pct=-1.94
                ),
                SectorRankingItem(
                    sector_name="教育", rank=89, change_pct=-1.90
                ),
            ],
            sources=["fake-sector"],
        )

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="broad-sector-laggards",
                message="今天哪个行业跌得最惨？",
                trade_date=trade_date,
            ),
            events=SAMPLE_EVENTS,
            llm_provider=DisabledLLMProvider(),
        )

        self.assertEqual(response.intent, "sector_performance")
        self.assertIn("行业板块跌幅靠前", response.answer)
        self.assertIn("农产品加工：-1.94%", response.answer)
        self.assertIn("教育：-1.90%", response.answer)
        self.assertNotIn("保险", response.answer)
        sector_builder.assert_called_once_with(
            sector=None,
            trade_date=trade_date,
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

    @patch("app.agents.tools.build_sector_stock_ranking")
    def test_policy_repairs_sector_constituent_ranking_tool(self, ranking_builder) -> None:
        today = date.today()
        ranking_builder.return_value = SectorStockRankingFacts(
            requested_sector="游戏",
            sector_name="游戏",
            sector_category="industry",
            sector_thscode="881275.TI",
            requested_days=20,
            requested_limit=10,
            data_as_of=today,
            member_count=23,
            analyzed_count=23,
            missing_count=0,
            truncated_count=0,
            items=[
                SectorStockTrendItem(
                    rank=1,
                    symbol="600892",
                    name="大晟文化",
                    trend_score=80.56,
                    trend="rising",
                    data_as_of=today,
                    latest_close=5.35,
                    return_5d_pct=32.1,
                    return_20d_pct=28.61,
                    volume_ratio_5d=0.31,
                    max_drawdown_pct=-20.52,
                )
            ],
            sources=["fake-sector-ranking"],
        )

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="sector-stock-ranking",
                message="游戏板块哪些股票走势好",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=ExternalToolProvider(),
        )

        self.assertIn("sector_stock_ranking", response.tool_calls)
        self.assertNotIn("sector_performance", response.tool_calls)
        ranking_builder.assert_called_once()

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
