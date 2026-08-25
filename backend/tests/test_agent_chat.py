import json
import unittest
from datetime import date, datetime, time, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.agents.chat import answer_first_board_chat
from app.models import (
    AgentChatRequest,
    AgentRun,
    ChatSessionMessage,
    FirstBoardEnrichmentSnapshot,
    LimitUpEvent,
    StockKLineBar,
    StockKLineFacts,
    StockPositionAssessment,
    StockPositionMatch,
)
from app.repositories import SQLiteFirstBoardRepository
from app.services.llm_provider import DisabledLLMProvider
from app.services.llm_provider import LLMProvider, LLMResult
from app.services.sample_data import SAMPLE_EVENTS


class FakeToolPlanningProvider(LLMProvider):
    """Fake LLM that first plans tools and then writes the final answer."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        self.calls.append((system_prompt, user_prompt))
        if "first job is to decide which tools are needed" in system_prompt:
            content = json.dumps(
                {
                    "intent_label": "first_board_top_candidates",
                    "safety": "normal",
                    "tool_calls": [
                        {
                            "name": "first_board_ratings",
                            "arguments": {"trade_date": "2026-05-15"},
                        }
                    ],
                    "answer_directly": "",
                }
            )
            return LLMResult(content=content, model="fake-planner", provider="fake")
        return LLMResult(
            content="\u6839\u636e\u5de5\u5177 facts\uff0c\u5019\u9009\u8bc4\u5206\u9760\u524d\u7684\u5305\u542b 301489\u3002",
            model="fake-answer",
            provider="fake",
        )


class FakeDirectFirstBoardProvider(FakeToolPlanningProvider):
    """Fake planner that incorrectly tries to answer a data question directly."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        self.calls.append((system_prompt, user_prompt))
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "today_summary",
                        "safety": "normal",
                        "tool_calls": [],
                        "answer_directly": "\u76f4\u63a5\u731c\u6d4b\u7684\u9996\u677f\u7b54\u6848",
                    }
                ),
                model="fake-planner",
                provider="fake",
            )
        return LLMResult(
            content="\u6839\u636e\u9996\u677f\u8bc4\u5206\u5de5\u5177\uff0c301489 \u8bc4\u5206\u9760\u524d\u3002",
            model="fake-answer",
            provider="fake",
        )


class FakeExhaustiveListProvider(FakeToolPlanningProvider):
    """Fake planner whose final answer intentionally truncates a full list."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        self.calls.append((system_prompt, user_prompt))
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "list_first_boards",
                        "safety": "normal",
                        "tool_calls": [
                            {
                                "name": "limit_up_events",
                                "arguments": {
                                    "trade_date": "2026-08-07",
                                    "limit": 100,
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
            content="只输出了第一只：测试股票1(002001)。",
            model="fake-answer",
            provider="fake",
        )


class FakeWrongPositionProvider(FakeToolPlanningProvider):
    """Fake planner and writer that mistake K-line position for seal time."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        self.calls.append((system_prompt, user_prompt))
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "first_board_position_groups",
                        "safety": "normal",
                        "tool_calls": [
                            {
                                "name": "limit_up_events",
                                "arguments": {"trade_date": "2026-05-15"},
                            }
                        ],
                        "answer_directly": "",
                    }
                ),
                model="fake-planner",
                provider="fake",
            )
        return LLMResult(
            content="按首封时间位置分类：思泉新材(301489)属于早盘板。",
            model="fake-answer",
            provider="fake",
        )


class FakeWrongPromotionProvider(FakeToolPlanningProvider):
    """Fake LLM that guesses a promotion rate without adjacent-day facts."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        self.calls.append((system_prompt, user_prompt))
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "daily_board_promotion",
                        "safety": "normal",
                        "tool_calls": [
                            {
                                "name": "limit_up_events",
                                "arguments": {"trade_date": "2026-05-15"},
                            }
                        ],
                        "answer_directly": "",
                    }
                ),
                model="fake-planner",
                provider="fake",
            )
        return LLMResult(
            content="今天连板晋级率很高。",
            model="fake-answer",
            provider="fake",
        )


class AgentChatTest(unittest.TestCase):
    def _make_event(
        self,
        symbol: str,
        name: str,
        industry: str,
        concept: str,
    ) -> LimitUpEvent:
        return LimitUpEvent(
            symbol=symbol,
            name=name,
            trade_date=date(2026, 8, 7),
            first_limit_time=time(10, 0),
            last_limit_time=time(10, 0),
            seal_count=1,
            break_count=0,
            closed_limit=True,
            board_height=1,
            amount=250_000_000,
            turnover_rate=7.5,
            industry=industry,
            concept=concept,
            next_open_pct=0,
            next_high_pct=0,
            next_close_pct=0,
            three_day_return_pct=0,
            five_day_return_pct=0,
            continued_next_day=False,
        )

    def test_greeting_does_not_route_to_market_tools(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(session_id="s1", message="\u4f60\u597d"),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "greeting")
        self.assertEqual(response.tool_calls, [])
        self.assertIn("LimitUpLab", response.answer)

    def test_capability_question_does_not_call_stock_tools(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u4f60\u80fd\u505a\u4ec0\u4e48",
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "capability_intro")
        self.assertEqual(response.tool_calls, [])
        self.assertIn("\u9996\u677f", response.answer)
        self.assertIn("K \u7ebf", response.answer)

    def test_retired_similar_case_question_does_not_call_tools(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="301489 有历史相似案例吗",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=DisabledLLMProvider(),
        )

        self.assertEqual(response.intent, "out_of_scope")
        self.assertEqual(response.tool_calls, [])
        self.assertIn("已经下线", response.answer)

    def test_prediction_quality_uses_audit_tool_when_llm_is_disabled(self) -> None:
        database_path = (
            Path(__file__).resolve().parents[1]
            / f"quality-chat-{uuid4().hex}.sqlite"
        )
        self.addCleanup(database_path.unlink, missing_ok=True)
        repository = SQLiteFirstBoardRepository(database_path=database_path)
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="quality-chat",
                message="目前预测质量怎么样，评分 v3 为什么还不能晋级？",
            ),
            events=SAMPLE_EVENTS,
            repository=repository,
            llm_provider=DisabledLLMProvider(),
        )

        self.assertEqual(response.intent, "prediction_quality_audit")
        self.assertIn("prediction_quality_audit", response.tool_calls)
        self.assertIn("60", response.answer)
        self.assertNotIn("首板候选评分靠前", response.answer)

    def test_out_of_scope_question_does_not_force_stock_facts(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u5e2e\u6211\u5199\u4e00\u9996\u8bd7",
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "out_of_scope")
        self.assertEqual(response.tool_calls, [])
        self.assertIn("\u8d85\u51fa", response.answer)

    def test_unsafe_investment_question_is_not_routed_to_recommendation(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="301489 \u80fd\u4e0d\u80fd\u4e70",
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "unsafe_investment_advice")
        self.assertEqual(response.tool_calls, [])
        self.assertIn("\u4e0d\u80fd", response.answer)
        self.assertNotIn("\u4e70\u5165", response.answer)

    def test_market_schedule_does_not_route_to_rating_tools(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="A\u80a1\u51e0\u70b9\u5f00\u76d8",
                intent_hint="market_schedule",
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "market_schedule")
        self.assertEqual(response.tool_calls, [])
        self.assertIn("09:30", response.answer)
        self.assertIn("15:00", response.answer)

    def test_market_sentiment_question_uses_market_context_tool(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u6700\u8fd1A\u80a1\u5e02\u573a\u7684\u60c5\u7eea\u600e\u4e48\u6837",
                intent_hint="market_context",
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "market_context")
        self.assertIn("market_summary", response.tool_calls)
        self.assertIn("2026-05-15", response.answer)

    def test_exhaustive_first_board_list_is_closed_only_and_complete(self) -> None:
        events = [
            self._make_event(
                f"0020{index:02d}",
                f"测试股票{index}",
                "测试行业",
                "测试题材",
            )
            for index in range(1, 11)
        ]
        failed = self._make_event("002099", "未封住样本", "测试行业", "测试题材")
        events.append(failed.model_copy(update={"closed_limit": False, "break_count": 2}))
        continued = self._make_event("002098", "二板样本", "测试行业", "测试题材")
        events.append(continued.model_copy(update={"board_height": 2}))

        response = answer_first_board_chat(
            AgentChatRequest(session_id="all-first-boards", message="列出今天所有首板"),
            events=events,
            llm_provider=FakeExhaustiveListProvider(),
        )

        self.assertIn("limit_up_events", response.tool_calls)
        self.assertNotIn("first_board_ratings", response.tool_calls)
        for index in range(1, 11):
            self.assertIn(f"0020{index:02d}", response.answer)
        self.assertNotIn("002099", response.answer)
        self.assertNotIn("002098", response.answer)
        trace = next(
            item for item in response.tool_results if item.name == "limit_up_events"
        )
        self.assertTrue(trace.input["closed_only"])
        self.assertEqual(trace.input["board_height"], 1)
        self.assertTrue(any("incomplete" in item for item in response.warnings))

    def test_today_summary_uses_first_board_tool(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u603b\u7ed3\u4e00\u4e0b\u4eca\u5929\u9996\u677f",
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "today_summary")
        self.assertIn("first_board_ratings", response.tool_calls)
        self.assertIn("2026-05-15", response.answer)
        self.assertTrue(response.warnings)

    def test_shorthand_date_can_select_historical_first_board_data(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="5.15\u65e5\u7684\u9996\u677f\u6570\u636e\u4f60\u6709\u5417",
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "today_summary")
        self.assertIn("2026-05-15", response.answer)

    def test_dated_first_board_data_question_uses_ratings_not_general_llm(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="5.15\u65e5\u7684\u9996\u677f\u6570\u636e\u4f60\u6709\u5417",
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "today_summary")
        self.assertIn("first_board_ratings", response.tool_calls)
        self.assertTrue(
            {"llm_general_answer", "template_general_answer"} & set(response.tool_calls)
        )

    def test_top_candidate_question_uses_unified_tool_grounded_answer(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u54ea\u4e9b\u5019\u9009\u8bc4\u5206\u9760\u524d",
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "today_summary")
        self.assertIn("first_board_ratings", response.tool_calls)
        self.assertTrue(
            {"llm_general_answer", "template_general_answer"} & set(response.tool_calls)
        )
        self.assertIn("301489", response.answer)

    def test_llm_planner_selects_tools_before_answering(self) -> None:
        provider = FakeToolPlanningProvider()

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u54ea\u4e9b\u5019\u9009\u8bc4\u5206\u9760\u524d",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=provider,
        )

        self.assertEqual(response.intent, "first_board_top_candidates")
        self.assertEqual(len(provider.calls), 2)
        self.assertIn("Available tools", provider.calls[0][0])
        self.assertEqual(response.tool_calls[0], "llm_tool_planner")
        self.assertIn("first_board_ratings", response.tool_calls)
        self.assertIn("llm_tool_answer", response.tool_calls)
        self.assertEqual(response.tool_results[0].name, "llm_tool_planner")
        self.assertGreater(response.performance.planner_prompt_chars, 0)
        self.assertGreater(response.performance.answer_prompt_chars, 0)
        self.assertIn("301489", response.answer)
        self.assertGreaterEqual(len(response.evidence_cards), 2)
        self.assertTrue(
            any(card.title == "首板候选池与评分" for card in response.evidence_cards)
        )

    def test_llm_prompts_receive_bounded_persisted_conversation_history(self) -> None:
        provider = FakeToolPlanningProvider()
        history = [
            ChatSessionMessage(
                message_id="history-user",
                session_id="history-session",
                role="user",
                content="先看一下中电鑫龙",
                created_at=datetime.now(timezone.utc),
            ),
            ChatSessionMessage(
                message_id="history-agent",
                session_id="history-session",
                role="assistant",
                content="已经读取该股票的首板评分。",
                created_at=datetime.now(timezone.utc),
            ),
        ]

        answer_first_board_chat(
            AgentChatRequest(
                session_id="history-session",
                message="那它的评分为什么高",
                trade_date=date(2026, 5, 15),
            ),
            events=SAMPLE_EVENTS,
            conversation_messages=history,
            llm_provider=provider,
        )

        planner_payload = json.loads(provider.calls[0][1])
        answer_payload = json.loads(provider.calls[1][1])
        self.assertEqual(len(planner_payload["conversation_history"]), 2)
        self.assertEqual(
            planner_payload["conversation_history"][0]["content"],
            "先看一下中电鑫龙",
        )
        self.assertEqual(
            answer_payload["conversation_history"],
            planner_payload["conversation_history"],
        )

    def test_llm_answer_reports_progress_and_streams_deltas(self) -> None:
        provider = FakeToolPlanningProvider()
        progress: list[tuple[str, str]] = []
        deltas: list[str] = []

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u54ea\u4e9b\u5019\u9009\u8bc4\u5206\u9760\u524d",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=provider,
            progress_callback=lambda stage, message: progress.append((stage, message)),
            answer_delta_callback=deltas.append,
        )

        self.assertEqual(
            [stage for stage, _ in progress],
            ["planning", "tools", "answering"],
        )
        self.assertEqual(len(deltas), 1)
        self.assertIn("301489", "".join(deltas))
        self.assertIn("".join(deltas), response.answer)

    def test_first_board_question_repairs_planner_direct_answer(self) -> None:
        provider = FakeDirectFirstBoardProvider()
        deltas: list[str] = []

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u603b\u7ed3\u4e00\u4e0b\u4eca\u5929\u9996\u677f\u8bc4\u5206\u9760\u524d\u4e09\u53ea",
                trade_date=date(2026, 5, 15),
            ),
            events=SAMPLE_EVENTS,
            llm_provider=provider,
            answer_delta_callback=deltas.append,
        )

        self.assertIn("first_board_ratings", response.tool_calls)
        self.assertIn("llm_tool_answer", response.tool_calls)
        self.assertNotIn("llm_planner_direct_answer", response.tool_calls)
        self.assertIn("301489", "".join(deltas))

    def test_first_board_position_question_uses_complete_kline_groups(self) -> None:
        database_path = (
            Path(__file__).resolve().parents[1]
            / f"position-chat-{uuid4().hex}.sqlite"
        )
        for suffix in ("", "-shm", "-wal"):
            self.addCleanup(Path(f"{database_path}{suffix}").unlink, missing_ok=True)
        repository = SQLiteFirstBoardRepository(database_path=database_path)
        repository.upsert_enrichment_snapshots(
            [
                FirstBoardEnrichmentSnapshot(
                    trade_date=date(2026, 5, 15),
                    symbol="301489",
                    kline_bar_count=125,
                    position=StockPositionAssessment(
                        primary=StockPositionMatch(
                            regime="low_base_breakout",
                            label="低位启动首板",
                            score=88,
                        ),
                        confidence=0.9,
                        tags=["120日低位", "MA20向上"],
                        evidence=["首板前位于 120 日价格区间的 22% 位置"],
                        bar_count=125,
                        classifier_version="position-test-v1",
                    ),
                    feature_version="enrichment-test-v1",
                    created_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
                )
            ]
        )

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="position-groups",
                message="今天首板按照位置分类一下",
            ),
            events=SAMPLE_EVENTS,
            repository=repository,
            llm_provider=FakeWrongPositionProvider(),
        )

        self.assertIn("first_board_ratings", response.tool_calls)
        self.assertNotIn("limit_up_events", response.tool_calls)
        self.assertIn("首板前 K 线位置", response.answer)
        self.assertIn("低位启动首板", response.answer)
        self.assertIn("301489", response.answer)
        self.assertNotIn("早盘板", response.answer)
        trace = next(
            item for item in response.tool_results if item.name == "first_board_ratings"
        )
        groups = trace.output["position_classification"]["groups"]
        self.assertEqual(groups[0]["label"], "低位启动首板")
        self.assertTrue(
            any("position classification" in item for item in response.warnings)
        )

        fallback_response = answer_first_board_chat(
            AgentChatRequest(
                session_id="position-groups-fallback",
                message="今天首板按照位置分类一下",
            ),
            events=SAMPLE_EVENTS,
            repository=repository,
            llm_provider=DisabledLLMProvider(),
        )
        self.assertIn("首板前 K 线位置", fallback_response.answer)
        self.assertIn("低位启动首板", fallback_response.answer)
        self.assertIn("301489", fallback_response.answer)

    def test_daily_promotion_question_uses_adjacent_close_statistics(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="daily-promotion",
                message="最近5个交易日连板晋级概率怎么样",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=FakeWrongPromotionProvider(),
        )

        self.assertIn("daily_board_promotion", response.tool_calls)
        self.assertNotIn("limit_up_events", response.tool_calls)
        self.assertIn("2026-05-15", response.answer)
        self.assertIn("0/1", response.answer)
        self.assertIn("首板→二板", response.answer)
        self.assertTrue(
            any("daily promotion" in item for item in response.warnings)
        )

        fallback_response = answer_first_board_chat(
            AgentChatRequest(
                session_id="daily-promotion-fallback",
                message="每天连板晋级率如何",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=DisabledLLMProvider(),
        )
        self.assertEqual(fallback_response.intent, "daily_board_promotion")
        self.assertIn("daily_board_promotion", fallback_response.tool_calls)
        self.assertIn("0/1", fallback_response.answer)

    @patch("app.agents.tools.build_stock_kline_facts")
    def test_stock_trend_question_repairs_to_kline_tool(self, build_facts) -> None:
        build_facts.return_value = StockKLineFacts(
            symbol="002298",
            requested_days=20,
            requested_end_date=date(2026, 8, 7),
            data_as_of=date(2026, 8, 7),
            data_fresh=True,
            trend="rising",
            latest_close=12.3,
            return_5d_pct=8.2,
            ma5=11.8,
            ma10=11.2,
            ma20=10.5,
            max_drawdown_pct=-4.1,
            sources=["test"],
            bars=[
                StockKLineBar(
                    trade_date=date(2026, 8, 7),
                    open=11.5,
                    high=12.5,
                    low=11.4,
                    close=12.3,
                    volume=1_000_000,
                )
            ],
        )
        events = [
            self._make_event(
                symbol="002298",
                name="中电鑫龙",
                industry="软件开发",
                concept="人工智能",
            )
        ]

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="中电鑫龙最近走势怎么样",
            ),
            events=events,
            llm_provider=FakeToolPlanningProvider(),
        )

        self.assertIn("stock_kline", response.tool_calls)
        self.assertTrue(any(trace.name == "stock_kline" for trace in response.tool_results))
        self.assertIn("symbol=002298", response.references)
        self.assertEqual(build_facts.call_args.kwargs["symbol"], "002298")

    def test_rating_backtest_question_repairs_missing_planner_tool_call(self) -> None:
        provider = FakeToolPlanningProvider()

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u6700\u8fd1\u9996\u677f\u8bc4\u5206\u51c6\u5417\uff0c\u505a\u4e2a\u56de\u6d4b",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=provider,
        )

        self.assertIn("rating_backtest", response.tool_calls)
        self.assertTrue(any(trace.name == "rating_backtest" for trace in response.tool_results))

    def test_critic_question_repairs_missing_planner_tool_call(self) -> None:
        provider = FakeToolPlanningProvider()

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="301489 \u8fd9\u4e2a\u8bc4\u5206\u9760\u8c31\u5417\uff0c\u5e2e\u6211\u53cd\u9a73\u4e00\u4e0b",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=provider,
        )

        self.assertIn("first_board_critic", response.tool_calls)
        self.assertTrue(any(trace.name == "first_board_critic" for trace in response.tool_results))

    def test_evaluation_question_repairs_missing_planner_tool_call(self) -> None:
        provider = FakeToolPlanningProvider()

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u590d\u76d8\u4e00\u4e0b\u6700\u8fd1\u54ea\u4e9b\u8bc4\u5206\u9519\u4e86",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=provider,
        )

        self.assertIn("rating_evaluation", response.tool_calls)
        self.assertTrue(any(trace.name == "rating_evaluation" for trace in response.tool_results))

    def test_review_question_repairs_to_review_agent_tool(self) -> None:
        provider = FakeToolPlanningProvider()

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u6700\u8fd1\u9ad8\u5206\u7968\u540e\u7eed\u8d70\u52bf\u600e\u4e48\u6837\uff0c\u5ba1\u7f8e\u9700\u8981\u600e\u4e48\u6539",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=provider,
        )

        self.assertIn("review_high_score_picks", response.tool_calls)
        self.assertTrue(any(trace.name == "review_high_score_picks" for trace in response.tool_results))

        promotion_response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1-promotion",
                message="那你选出的高分票1进2的成功率呢",
            ),
            events=SAMPLE_EVENTS,
            llm_provider=provider,
        )
        self.assertIn("review_high_score_picks", promotion_response.tool_calls)
        self.assertNotIn("daily_board_promotion", promotion_response.tool_calls)

    def test_broad_top_candidate_question_does_not_inherit_previous_symbol(self) -> None:
        previous = AgentRun(
            run_id="r1",
            session_id="s1",
            run_type="agent_chat",
            status="success",
            intent="llm_explanation",
            tool_calls=["first_board_ratings"],
            input_json={"message": "301489 \u8bc4\u5206\u4e3a\u4ec0\u4e48\u9ad8"},
            output_json={"references": ["symbol=301489", "trade_date=2026-05-15"]},
            started_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
            finished_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        )

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u54ea\u4e9b\u5019\u9009\u8bc4\u5206\u9760\u524d",
            ),
            events=SAMPLE_EVENTS,
            recent_runs=[previous],
        )

        self.assertEqual(response.intent, "today_summary")
        self.assertIn("first_board_ratings", response.tool_calls)
        self.assertNotEqual(response.intent, "symbol_not_found")

    def test_dated_first_board_topic_question_filters_candidate_pool(self) -> None:
        events = [
            self._make_event(
                symbol="002001",
                name="\u533b\u836f\u6837\u672c",
                industry="\u5316\u5b66\u5236\u836f",
                concept="\u533b\u836f\u751f\u7269",
            ),
            self._make_event(
                symbol="002002",
                name="\u79d1\u6280\u6837\u672c",
                industry="\u5143\u4ef6",
                concept="\u534a\u5bfc\u4f53",
            ),
        ]
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="8.7\u65e5\u9996\u677f\u7684\u80a1\u7968\uff0c\u662f\u533b\u836f\u76f8\u5173\u7684\u6709\u54ea\u4e9b",
            ),
            events=events,
        )

        self.assertEqual(response.intent, "limit_up_query")
        self.assertEqual(response.tool_calls, ["limit_up_events"])
        self.assertEqual(response.tool_results[0].name, "agent_plan")
        self.assertEqual(response.tool_results[0].input["intent"], "limit_up_query")
        self.assertIn("002001", response.answer)
        self.assertNotIn("002002", response.answer)

    def test_second_board_question_uses_general_limit_up_tool(self) -> None:
        events = [
            self._make_event(
                symbol="002101",
                name="\u4e8c\u8fde\u6837\u672c",
                industry="\u8f6f\u4ef6\u5f00\u53d1",
                concept="AI",
            ).model_copy(update={"board_height": 2}),
            self._make_event(
                symbol="002102",
                name="\u9996\u677f\u6837\u672c",
                industry="\u5143\u4ef6",
                concept="\u534a\u5bfc\u4f53",
            ),
        ]

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u4eca\u5929\u4e8c\u8fde\u677f\u7684\u7968\u6709\u54ea\u4e9b",
            ),
            events=events,
        )

        self.assertEqual(response.intent, "limit_up_query")
        self.assertEqual(response.tool_calls, ["limit_up_events"])
        self.assertIn("002101", response.answer)
        self.assertNotIn("002102", response.answer)
        tool_trace = next(trace for trace in response.tool_results if trace.name == "limit_up_events")
        self.assertEqual(tool_trace.input["board_height"], 2)

    def test_chinext_limit_up_question_filters_market_before_llm_answer(self) -> None:
        events = [
            self._make_event("002101", "主板样本", "软件开发", "AI"),
            self._make_event("300642", "创业板样本一", "医疗器械", "医疗"),
            self._make_event("301001", "创业板样本二", "专用设备", "机器人"),
            self._make_event("688169", "科创板样本", "小家电", "消费"),
            self._make_event("830001", "北交所样本", "机械", "设备"),
        ]

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="chinext-limit-up",
                message="今天创业板有哪些股票涨停",
            ),
            events=events,
            llm_provider=FakeExhaustiveListProvider(),
        )

        trace = next(
            item for item in response.tool_results if item.name == "limit_up_events"
        )
        symbols = {item["symbol"] for item in trace.output["events"]}
        self.assertEqual(trace.input["market"], "chinext")
        self.assertEqual(trace.output["market_label"], "创业板")
        self.assertEqual(symbols, {"300642", "301001"})
        self.assertNotIn("002101", response.answer)
        self.assertNotIn("688169", response.answer)

    def test_limit_up_topic_question_does_not_route_to_first_board_filter(self) -> None:
        events = [
            self._make_event(
                symbol="002201",
                name="\u533b\u836f\u8fde\u677f",
                industry="\u5316\u5b66\u5236\u836f",
                concept="\u533b\u836f\u751f\u7269",
            ).model_copy(update={"board_height": 2}),
            self._make_event(
                symbol="002202",
                name="\u79d1\u6280\u6da8\u505c",
                industry="\u5143\u4ef6",
                concept="\u534a\u5bfc\u4f53",
            ),
        ]

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u4eca\u5929\u6da8\u505c\u7684\u7968\u91cc\u533b\u836f\u76f8\u5173\u7684\u6709\u54ea\u4e9b",
            ),
            events=events,
        )

        self.assertEqual(response.intent, "limit_up_query")
        self.assertEqual(response.tool_calls, ["limit_up_events"])
        self.assertIn("002201", response.answer)
        self.assertNotIn("first_board_filter", response.tool_calls)

    def test_dated_first_board_sector_question_summarizes_industries(self) -> None:
        events = [
            self._make_event(
                symbol="002001",
                name="\u533b\u836f\u6837\u672c",
                industry="\u5316\u5b66\u5236\u836f",
                concept="\u533b\u836f\u751f\u7269",
            ),
            self._make_event(
                symbol="002003",
                name="\u5236\u836f\u6837\u672c",
                industry="\u5316\u5b66\u5236\u836f",
                concept="\u533b\u836f\u751f\u7269",
            ),
            self._make_event(
                symbol="002002",
                name="\u79d1\u6280\u6837\u672c",
                industry="\u5143\u4ef6",
                concept="\u534a\u5bfc\u4f53",
            ),
        ]
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="8.7\u65e5\u7684\u9996\u677f\u7968\u4e3b\u8981\u677f\u5757\u6709\u54ea\u4e9b",
            ),
            events=events,
        )

        self.assertEqual(response.intent, "first_board_sector_summary")
        self.assertIn("first_board_ratings", response.tool_calls)
        self.assertTrue(
            {"llm_general_answer", "template_general_answer"} & set(response.tool_calls)
        )
        self.assertIn("\u5316\u5b66\u5236\u836f", response.answer)
        self.assertIn("\u5143\u4ef6", response.answer)

    def test_agent_plan_trace_is_returned_for_tool_answers(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u603b\u7ed3\u4e00\u4e0b\u4eca\u5929\u9996\u677f",
            ),
            events=SAMPLE_EVENTS,
        )

        plan_trace = response.tool_results[0]
        self.assertEqual(plan_trace.name, "agent_plan")
        self.assertEqual(plan_trace.input["intent"], "today_summary")
        self.assertEqual(
            plan_trace.input["tool_steps"][0]["name"],
            "first_board_ratings",
        )

    def test_filter_and_top_question_is_planned_as_multi_tool_flow(self) -> None:
        events = [
            self._make_event(
                symbol="002001",
                name="\u533b\u836f\u6837\u672c",
                industry="\u5316\u5b66\u5236\u836f",
                concept="\u533b\u836f\u751f\u7269",
            ),
            self._make_event(
                symbol="002002",
                name="\u79d1\u6280\u6837\u672c",
                industry="\u5143\u4ef6",
                concept="\u534a\u5bfc\u4f53",
            ),
        ]
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message=(
                    "8.7\u65e5\u9996\u677f\u91cc\u533b\u836f\u76f8\u5173"
                    "\u8bc4\u5206\u6700\u9ad8\u7684\u662f\u8c01"
                ),
            ),
            events=events,
        )

        self.assertEqual(response.intent, "first_board_filter")
        self.assertEqual(
            response.tool_calls,
            ["first_board_ratings", "first_board_filter"],
        )
        self.assertEqual(response.tool_results[0].name, "agent_plan")
        self.assertEqual(
            [step["name"] for step in response.tool_results[0].input["tool_steps"]],
            ["first_board_ratings", "first_board_filter"],
        )
        self.assertIn("002001", response.answer)

    def test_follow_up_can_ask_top_stock_in_previous_filtered_pool(self) -> None:
        events = [
            self._make_event(
                symbol="002001",
                name="\u533b\u836f\u6837\u672c",
                industry="\u5316\u5b66\u5236\u836f",
                concept="\u533b\u836f\u751f\u7269",
            ),
            self._make_event(
                symbol="002002",
                name="\u79d1\u6280\u6837\u672c",
                industry="\u5143\u4ef6",
                concept="\u534a\u5bfc\u4f53",
            ),
        ]
        first_response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="8.7\u65e5\u9996\u677f\u7684\u80a1\u7968\uff0c\u533b\u836f\u76f8\u5173\u7684\u6709\u54ea\u4e9b",
            ),
            events=events,
        )
        previous_run = AgentRun(
            run_id="run_filter",
            session_id="s1",
            run_type="agent_chat",
            status="success",
            intent=first_response.intent,
            tool_calls=first_response.tool_calls,
            input_json={"message": first_response.answer},
            output_json=first_response.model_dump(mode="json"),
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u90a3\u91cc\u9762\u8bc4\u5206\u6700\u9ad8\u7684\u662f\u8c01",
            ),
            events=events,
            recent_runs=[previous_run],
        )

        self.assertEqual(response.intent, "first_board_context_top")
        self.assertIn("002001", response.answer)
        self.assertIn("symbol=002001", response.references)
        self.assertEqual(response.tool_results[0].input["filter"], "\u533b\u836f")

    def test_follow_up_risk_question_can_use_previous_selected_symbol(self) -> None:
        previous_run = AgentRun(
            run_id="run_top",
            session_id="s1",
            run_type="agent_chat",
            status="success",
            intent="first_board_context_top",
            tool_calls=["first_board_ratings", "first_board_filter"],
            input_json={"message": "\u90a3\u91cc\u9762\u8bc4\u5206\u6700\u9ad8\u7684\u662f\u8c01"},
            output_json={
                "references": [
                    "trade_date=2026-05-15",
                    "filter=\u533b\u836f",
                    "symbol=301489",
                ],
                "tool_results": [],
            },
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u5b83\u7684\u4e3b\u8981\u98ce\u9669\u662f\u4ec0\u4e48",
            ),
            events=SAMPLE_EVENTS,
            recent_runs=[previous_run],
        )

        self.assertEqual(response.intent, "risk_summary")
        self.assertTrue(any("301489" in item for item in response.references))
        self.assertIn("first_board_ratings", response.tool_calls)

    def test_missing_shorthand_date_reports_data_availability(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="8.6\u65e5\u7684\u9996\u677f\u6570\u636e\u4f60\u6709\u5417",
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "data_availability")
        self.assertIn("limit_up_event_dates", response.tool_calls)
        self.assertIn("2026-08-06", response.answer)

    def test_symbol_question_uses_context_symbol(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u4e3a\u4ec0\u4e48\u8bc4\u5206\u9ad8",
                trade_date=date(2026, 5, 15),
                symbol="301489",
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "rating_explain")
        self.assertIn("301489", response.answer)
        self.assertIn("first_board_ratings", response.tool_calls)

    def test_unknown_symbol_is_not_hallucinated(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u4e3a\u4ec0\u4e48 000001 \u8bc4\u5206\u9ad8",
                trade_date=date(2026, 5, 15),
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "symbol_not_found")
        self.assertIn("000001", response.answer)

    def test_follow_up_can_use_recent_run_context_symbol(self) -> None:
        previous_run = AgentRun(
            run_id="run_previous",
            session_id="s1",
            run_type="agent_chat",
            status="success",
            intent="rating_explain",
            tool_calls=["first_board_ratings"],
            input_json={"symbol": "301489", "trade_date": "2026-05-15"},
            output_json={"answer": "previous"},
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u4e3b\u8981\u98ce\u9669\u662f\u4ec0\u4e48",
                intent_hint="risk_summary",
            ),
            events=SAMPLE_EVENTS,
            recent_runs=[previous_run],
        )

        self.assertEqual(response.intent, "risk_summary")
        self.assertIn("301489", response.answer)

    def test_llm_explanation_intent_uses_explanation_tool(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="\u8be6\u7ec6\u89e3\u91ca\u4e00\u4e0b",
                intent_hint="llm_explanation",
                trade_date=date(2026, 5, 15),
                symbol="301489",
            ),
            events=SAMPLE_EVENTS,
        )

        self.assertEqual(response.intent, "llm_explanation")
        self.assertTrue(
            {"llm_explanation", "template_explanation"} & set(response.tool_calls)
        )
        self.assertIn("301489", response.answer)

    def test_output_avoids_investment_advice_terms(self) -> None:
        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="s1",
                message="301489 \u6709\u4ec0\u4e48\u98ce\u9669",
                trade_date=date(2026, 5, 15),
            ),
            events=SAMPLE_EVENTS,
        )
        rendered = response.model_dump_json()

        forbidden_terms = [
            "\u4e70\u5165",
            "\u5356\u51fa",
            "\u4ed3\u4f4d",
            "\u76ee\u6807\u4ef7",
            "\u6536\u76ca\u627f\u8bfa",
        ]
        for term in forbidden_terms:
            self.assertNotIn(term, rendered)


if __name__ == "__main__":
    unittest.main()
