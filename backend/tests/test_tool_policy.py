import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.agents.tool_policy import (
    AgentToolPolicyEngine,
    QuestionSignals,
    ToolExecution,
    extract_market_segment,
)
from app.agents.tools import AgentToolRegistry, ToolResult
from app.models import (
    AgentChatRequest,
    AgentToolTrace,
    MarketIndexTrendFacts,
    MarketIndexTrendItem,
    MarketIndexTrendPoint,
    MarketSummary,
    SectorPerformanceFacts,
    SectorRankingItem,
    build_agent_tool_policy_audit,
)
from app.services.sample_data import SAMPLE_EVENTS
from app.services.scoring_policy import DEFAULT_SCORING_POLICY_VERSION
from app.repositories import SQLiteFirstBoardRepository


class AgentToolPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.database_path = (
            Path(__file__).resolve().parents[1]
            / f"tool-policy-{uuid4().hex}.sqlite"
        )
        self.addCleanup(self._cleanup_database)
        repository = SQLiteFirstBoardRepository(self.database_path)
        self.tools = AgentToolRegistry(
            events=SAMPLE_EVENTS,
            first_board_repository=repository,
            profile="extended",
        )
        self.policy = AgentToolPolicyEngine(self.tools)

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

    def test_high_score_review_has_one_policy_scope(self) -> None:
        signals = QuestionSignals.from_message(
            "最近高分票后续走势怎么样，评分审美怎么改"
        )

        self.assertTrue(signals.review)
        self.assertFalse(signals.stock_kline)
        self.assertFalse(signals.rating_explanation)
        self.assertFalse(signals.evaluation)
        self.assertFalse(signals.scoring_policy)

        promotion_signals = QuestionSignals.from_message(
            "那你选出的高分票1进2的成功率呢"
        )
        self.assertTrue(promotion_signals.review)
        self.assertFalse(promotion_signals.daily_board_promotion)
        self.assertFalse(promotion_signals.rating_explanation)

    def test_explicit_capability_disables_conflicting_keyword_routing(self) -> None:
        signals = QuestionSignals.from_message(
            "最近首板评分准吗，顺便做个回测",
            ("first_board_rating",),
        )

        self.assertTrue(signals.first_board_facts)
        self.assertTrue(signals.rating_explanation)
        self.assertFalse(signals.rating_backtest)

    def test_scoring_policy_question_has_one_policy_scope(self) -> None:
        signals = QuestionSignals.from_message("评分权重有没有自动优化")

        self.assertTrue(signals.scoring_policy)
        self.assertFalse(signals.review)
        self.assertFalse(signals.evaluation)
        self.assertFalse(signals.rating_explanation)

    def test_prediction_quality_has_one_policy_scope(self) -> None:
        signals = QuestionSignals.from_message(
            "做一次预测质量审计，评分 v3 准备好了吗"
        )

        self.assertTrue(signals.prediction_quality)
        self.assertFalse(signals.rating_backtest)
        self.assertFalse(signals.evaluation)
        self.assertFalse(signals.review)
        self.assertFalse(signals.scoring_policy)
        self.assertFalse(signals.rating_explanation)

    def test_exhaustive_first_board_list_is_not_a_rating_question(self) -> None:
        signals = QuestionSignals.from_message("列出今天所有首板")

        self.assertFalse(signals.first_board_facts)
        self.assertFalse(signals.rating_explanation)

    def test_hot_stock_top100_repair_preserves_explicit_limit(self) -> None:
        signals = QuestionSignals.from_message("热股榜前100名")
        self.assertTrue(signals.hot_stock_ranking)

        payload = {
            "source": "eastmoney",
            "captured_at": "2026-08-26T14:00:00+00:00",
            "data_fresh": True,
            "requested_count": 100,
            "count": 100,
            "complete": True,
            "items": [],
        }
        execution = self._empty_execution()
        with patch.object(
            self.tools,
            "hot_stock_ranking",
            return_value=ToolResult(
                name="hot_stock_ranking",
                input={"period": "day", "limit": 100, "source": "auto"},
                output=payload,
                summary="东方财富热股榜返回 100/100 只。",
                trace_output=payload,
            ),
        ) as ranking:
            repaired = self.policy.reconcile(
                request=AgentChatRequest(
                    session_id="policy-hot-stock-top100",
                    message="热股榜前100名",
                ),
                execution=execution,
            )

        self.assertEqual(repaired, ["hot_stock_ranking"])
        ranking.assert_called_once_with(period="day", limit=100, source="auto")
        self.assertEqual(execution["facts"]["hot_stock_ranking"]["count"], 100)

    def test_market_segment_extraction_uses_explicit_board_names(self) -> None:
        self.assertEqual(extract_market_segment("今天创业板有哪些股票涨停"), "chinext")
        self.assertEqual(extract_market_segment("科创板今天涨停股"), "star_market")
        self.assertEqual(extract_market_segment("北交所有哪些涨停"), "beijing")
        self.assertEqual(extract_market_segment("沪深主板涨停名单"), "main_board")

    def test_first_board_position_classification_uses_rating_facts(self) -> None:
        signals = QuestionSignals.from_message("今天首板按照位置分类一下")

        self.assertTrue(signals.first_board_facts)
        self.assertFalse(signals.limit_up_events)

        execution = self._empty_execution()
        repaired = self.policy.reconcile(
            request=AgentChatRequest(
                session_id="policy-position",
                message="今天首板按照位置分类一下",
            ),
            execution=execution,
        )

        self.assertEqual(repaired, ["first_board_ratings"])
        classification = execution["facts"]["first_board_ratings"][
            "position_classification"
        ]
        self.assertEqual(classification["candidate_count"], 1)
        self.assertEqual(classification["missing_count"], 1)

    def test_daily_board_promotion_uses_adjacent_close_facts(self) -> None:
        signals = QuestionSignals.from_message("最近5个交易日连板晋级概率怎么样")

        self.assertTrue(signals.daily_board_promotion)
        self.assertFalse(signals.limit_up_events)
        self.assertFalse(signals.scoring_policy)

        execution = self._empty_execution()
        repaired = self.policy.reconcile(
            request=AgentChatRequest(
                session_id="policy-promotion",
                message="最近5个交易日连板晋级概率怎么样",
            ),
            execution=execution,
        )

        self.assertEqual(repaired, ["daily_board_promotion"])
        payload = execution["facts"]["daily_board_promotion"]
        self.assertEqual(payload["observed_days"], 2)
        self.assertEqual(len(payload["items"]), 2)

        detail_signals = QuestionSignals.from_message("今天哪些票晋级成功")
        self.assertTrue(detail_signals.daily_board_promotion)
        self.assertFalse(detail_signals.limit_up_events)

    def test_sector_performance_does_not_trigger_rating_review(self) -> None:
        signals = QuestionSignals.from_message("今天半导体板块表现怎么样")

        self.assertTrue(signals.sector_performance)
        self.assertFalse(signals.rating_backtest)
        self.assertFalse(signals.evaluation)
        self.assertFalse(signals.review)

    def test_market_index_trend_uses_dedicated_grounding(self) -> None:
        signals = QuestionSignals.from_message("近一周大盘走势怎么样")

        self.assertTrue(signals.market_index_trend)
        self.assertFalse(signals.stock_kline)
        execution = self._empty_execution()
        facts = MarketIndexTrendFacts(
            requested_days=5,
            requested_end_date=date(2026, 5, 15),
            data_as_of=date(2026, 5, 15),
            data_fresh=True,
            indices=[
                MarketIndexTrendItem(
                    name="上证指数",
                    symbol="000001.SH",
                    start_date=date(2026, 5, 11),
                    end_date=date(2026, 5, 15),
                    start_close=3600,
                    end_close=3672,
                    return_pct=2.0,
                    max_drawdown_pct=-0.5,
                    positive_days=3,
                    negative_days=1,
                    points=[
                        MarketIndexTrendPoint(
                            trade_date=date(2026, 5, 15),
                            close=3672,
                            change_pct=0.8,
                        )
                    ],
                    source="test-index",
                )
            ],
        )
        with patch.object(
            self.tools,
            "market_index_trend",
            return_value=ToolResult(
                name="market_index_trend",
                input={"days": 5, "end_date": "2026-05-15"},
                output=facts,
                summary="近5日指数走势。",
                trace_output=facts.model_dump(mode="json"),
            ),
        ) as index_trend:
            repaired = self.policy.reconcile(
                request=AgentChatRequest(
                    session_id="policy-index-trend",
                    message="近一周大盘走势怎么样",
                ),
                execution=execution,
            )

        self.assertEqual(repaired, ["market_index_trend"])
        index_trend.assert_called_once_with(days=5, end_date=None)
        self.assertIn("market_index_trend", execution["facts"])

    def test_sector_move_reason_does_not_trigger_rating_explanation(self) -> None:
        signals = QuestionSignals.from_message("今天半导体板块为什么下跌")

        self.assertTrue(signals.sector_performance)
        self.assertTrue(signals.web_search)
        self.assertFalse(signals.rating_explanation)
        self.assertFalse(signals.first_board_facts)

    def test_broad_finance_news_uses_specialized_feed(self) -> None:
        signals = QuestionSignals.from_message("有什么最新财经新闻")

        self.assertTrue(signals.finance_news)
        self.assertFalse(signals.web_search)

    def test_market_environment_repairs_all_required_evidence_groups(self) -> None:
        request = AgentChatRequest(
            session_id="policy-market-environment",
            message="今天市场环境如何",
        )
        signals = QuestionSignals.from_message(request.message)
        self.assertTrue(signals.market_environment)
        self.assertTrue(signals.market_index_trend)
        self.assertTrue(signals.sector_performance)
        self.assertTrue(signals.hot_stock_ranking)

        market_summary = MarketSummary(
            trade_date=date(2026, 5, 15),
            limit_up_count=42,
            first_board_count=30,
            continued_board_count=12,
            failed_count=8,
            unsealed_count=5,
            unsealed_rate=0.1064,
            limit_down_count=2,
            limit_down_source="akshare-eastmoney-limit-down-pool",
            failed_limit_up_rate=0.17,
            max_board_height=5,
            total_amount=20_000_000_000,
            hot_industries=["半导体"],
            hot_concepts=[],
            indices=[],
        )
        index_facts = MarketIndexTrendFacts(
            requested_days=5,
            requested_end_date=date(2026, 5, 15),
            data_as_of=date(2026, 5, 15),
            data_fresh=True,
            indices=[],
        )
        sector_facts = SectorPerformanceFacts(
            trade_date=date(2026, 5, 15),
            data_as_of=date(2026, 5, 15),
            data_fresh=True,
            sector_count=2,
            top_sectors=[
                SectorRankingItem(
                    sector_name="半导体",
                    rank=1,
                    change_pct=3.2,
                    leader_name="测试芯片",
                )
            ],
            bottom_sectors=[
                SectorRankingItem(
                    sector_name="煤炭",
                    rank=2,
                    change_pct=-1.8,
                    leader_name="测试煤炭",
                )
            ],
            sources=["test-sector-source"],
        )
        hot_payload = {
            "source": "hithink-finance",
            "captured_at": "2026-05-15T07:10:00+00:00",
            "items": [
                {
                    "rank": 1,
                    "name": "测试热门股",
                    "symbol": "000001",
                    "change_pct": 6.5,
                }
            ],
        }
        execution = self._empty_execution()
        with (
            patch.object(
                self.tools,
                "market_summary",
                return_value=ToolResult(
                    name="market_summary",
                    input={"include_limit_down": True},
                    output=market_summary,
                    summary="市场概况",
                    trace_output=market_summary.model_dump(mode="json"),
                ),
            ) as summary_tool,
            patch.object(
                self.tools,
                "market_index_trend",
                return_value=ToolResult(
                    name="market_index_trend",
                    input={"days": 5, "end_date": None},
                    output=index_facts,
                    summary="指数走势",
                    trace_output=index_facts.model_dump(mode="json"),
                ),
            ) as index_tool,
            patch.object(
                self.tools,
                "sector_performance",
                return_value=ToolResult(
                    name="sector_performance",
                    input={"sector": None, "trade_date": None},
                    output=sector_facts,
                    summary="板块强弱",
                    trace_output=sector_facts.model_dump(mode="json"),
                ),
            ) as sector_tool,
            patch.object(
                self.tools,
                "hot_stock_ranking",
                return_value=ToolResult(
                    name="hot_stock_ranking",
                    input={
                        "period": "day",
                        "limit": 20,
                        "source": "auto",
                        "enrich_performance": True,
                    },
                    output=hot_payload,
                    summary="热门个股",
                    trace_output=hot_payload,
                ),
            ) as hot_tool,
        ):
            repaired = self.policy.reconcile(request=request, execution=execution)

        self.assertEqual(
            repaired,
            [
                "market_summary",
                "market_index_trend",
                "hot_stock_ranking",
                "sector_performance",
            ],
        )
        self.assertEqual(
            set(execution["facts"]),
            {
                "market_summary",
                "market_index_trend",
                "hot_stock_ranking",
                "sector_performance",
            },
        )
        summary_tool.assert_called_once_with(include_limit_down=True)
        index_tool.assert_called_once_with(days=5, end_date=None)
        sector_tool.assert_called_once_with(sector=None, trade_date=None)
        hot_tool.assert_called_once_with(
            period="day",
            limit=20,
            source="auto",
            enrich_performance=True,
        )

    def test_company_news_still_uses_generic_web_search(self) -> None:
        signals = QuestionSignals.from_message("中电鑫龙有什么最新消息")

        self.assertFalse(signals.finance_news)
        self.assertTrue(signals.web_search)

    def test_scoring_policy_repair_returns_champion_status(self) -> None:
        request = AgentChatRequest(
            session_id="policy-test",
            message="现在是哪个评分策略版本，会自动学习吗",
        )
        execution = self._empty_execution()

        repaired = self.policy.reconcile(request=request, execution=execution)

        self.assertEqual(repaired, ["scoring_policy_status"])
        payload = execution["facts"]["scoring_policy_status"]
        self.assertEqual(payload["champion"]["status"], "champion")
        self.assertIn("scoring_version=", execution["references"][0])

    def test_rating_repair_records_rule_and_audit_reason(self) -> None:
        request = AgentChatRequest(
            session_id="policy-test",
            message="哪些候选评分靠前",
        )
        execution = self._empty_execution()

        repaired = self.policy.reconcile(request=request, execution=execution)

        self.assertEqual(repaired, ["first_board_ratings"])
        self.assertIn("first_board_ratings", execution["facts"])
        repair = execution["tool_results"][0].output["policy_repair"]
        self.assertEqual(repair["rule"], "rating-facts-required")

        planner_trace = AgentToolTrace(
            name="llm_tool_planner",
            input={"tool_calls": []},
            summary="Planner returned no tools.",
        )
        audit = build_agent_tool_policy_audit(
            tool_calls=["first_board_ratings"],
            tool_results=[planner_trace, *execution["tool_results"]],
        )
        self.assertEqual(audit.backend_repaired_tools, ["first_board_ratings"])
        self.assertEqual(audit.repair_reasons, [repair["reason"]])

    def test_prediction_quality_repair_returns_coverage_facts(self) -> None:
        request = AgentChatRequest(
            session_id="policy-test",
            message="评分 v3 准备好了吗，检查预测质量",
        )
        execution = self._empty_execution()

        repaired = self.policy.reconcile(request=request, execution=execution)

        self.assertEqual(repaired, ["prediction_quality_audit"])
        payload = execution["facts"]["prediction_quality_audit"]
        self.assertEqual(
            payload["audited_scoring_version"],
            DEFAULT_SCORING_POLICY_VERSION,
        )
        self.assertEqual(payload["policy_status"]["required_trade_dates"], 60)

    def test_missing_date_short_circuits_domain_tools(self) -> None:
        request = AgentChatRequest(
            session_id="policy-test",
            message="8.8日的首板数据你有吗",
        )
        execution = self._empty_execution()

        repaired = self.policy.reconcile(request=request, execution=execution)

        self.assertEqual(repaired, ["limit_up_event_dates"])
        self.assertEqual(execution["tool_call_names"], ["limit_up_event_dates"])
        self.assertNotIn("first_board_ratings", execution["facts"])
        self.assertIn("limit_up_event_dates", execution["facts"])


if __name__ == "__main__":
    unittest.main()
