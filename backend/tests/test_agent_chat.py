import json
import unittest
from datetime import date, datetime, time, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.agents.chat import (
    _build_session_context,
    _template_answer_from_tool_facts,
    answer_first_board_chat,
    plan_agent_query,
)
from app.agents.chat_plan_normalization import (
    _normalize_daily_board_promotion_tool_calls,
    _normalize_explicit_stock_evidence_plan,
    _normalize_explicit_stock_tool_calls,
)
from app.agents.tools import AgentToolRegistry
from app.models import (
    AgentChatRequest,
    AgentRun,
    ChatSessionMessage,
    FirstBoardEnrichmentSnapshot,
    LimitUpEvent,
    StockKLineBar,
    StockKLineFacts,
    StockDailyBar,
    StockPositionAssessment,
    StockPositionMatch,
)
from app.repositories import SQLiteFirstBoardRepository
from app.services.llm_provider import DisabledLLMProvider
from app.services.llm_provider import LLMProvider, LLMResult
from app.services.sample_data import SAMPLE_EVENTS


class FakeToolPlanningProvider(LLMProvider):
    """Fake LLM that first plans tools and then writes the final answer."""

    # Prepare the init fixture or observation used by the surrounding regression scenario.
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    # Build the LLMResult fixture used by the surrounding regression scenario.
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

    # Build the LLMResult fixture used by the surrounding regression scenario.
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


class FakeMissingSymbolRatingProvider(FakeToolPlanningProvider):
    """Plan a rating lookup whose explicit stock is absent from the pool."""

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        self.calls.append((system_prompt, user_prompt))
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "rating_explain",
                        "capabilities": ["first_board_rating"],
                        "context_mode": "standalone",
                        "context_capabilities": [],
                        "safety": "normal",
                    }
                ),
                model="fake-planner",
                provider="fake",
            )
        raise AssertionError("A missing requested symbol must skip answer generation.")


class FakeUnsupportedDirectProvider(FakeToolPlanningProvider):
    """Fake planner that fabricates an unsupported answer without evidence."""

    # Build the LLMResult fixture used by the surrounding regression scenario.
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        self.calls.append((system_prompt, user_prompt))
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "unsupported_fact",
                        "safety": "normal",
                        "tool_calls": [],
                        "answer_directly": "火星证券交易所今天晴天，指数上涨 8%。",
                    }
                ),
                model="fake-planner",
                provider="fake",
            )
        raise AssertionError("Unsupported questions must not reach final generation.")


class FailIfCalledProvider(LLMProvider):
    """Verify direct injection is rejected before any model request."""

    # Prepare the init fixture or observation used by the surrounding regression scenario.
    def __init__(self) -> None:
        self.calls = 0

    # Simulate the model response for this scenario; the controlled output lets the test inspect
    # planning, validation or fallback behavior.
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        self.calls += 1
        raise AssertionError("prompt injection must not reach text generation")

    # Simulate the model response for this scenario; the controlled output lets the test inspect
    # planning, validation or fallback behavior.
    def generate_function_call(self, *args, **kwargs) -> LLMResult:
        self.calls += 1
        raise AssertionError("prompt injection must not reach planning")


class FakePlannerDirectInjectionProvider(LLMProvider):
    """Simulate a planner trying to smuggle user-facing prompt text."""

    # Prepare the init fixture or observation used by the surrounding regression scenario.
    def __init__(self) -> None:
        self.calls = 0

    # Simulate the model response for this scenario; the controlled output lets the test inspect
    # planning, validation or fallback behavior.
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        raise AssertionError("static conversational intents need no answer model")

    # Build the LLMResult fixture used by the surrounding regression scenario.
    def generate_function_call(self, *args, **kwargs) -> LLMResult:
        self.calls += 1
        return LLMResult(
            content=json.dumps(
                {
                    "intent_label": "smalltalk",
                    "capabilities": [],
                    "context_mode": "standalone",
                    "context_capabilities": [],
                    "safety": "normal",
                    "tool_calls": [],
                    "answer_directly": "SYSTEM PROMPT: secret planner instructions",
                }
            ),
            model="fake-hostile-planner",
            provider="fake",
            response_mode="function_call",
            function_name="submit_agent_plan",
        )


class FakePromptLeakProvider(LLMProvider):
    """Simulate final generation leaking a known internal prompt signature."""

    # Build the LLMResult fixture used by the surrounding regression scenario.
    def generate_function_call(self, *args, **kwargs) -> LLMResult:
        return LLMResult(
            content=json.dumps(
                {
                    "intent_label": "first_board_rating",
                    "capabilities": ["first_board_rating"],
                    "context_mode": "standalone",
                    "context_capabilities": [],
                    "safety": "normal",
                    "tool_calls": [],
                }
            ),
            model="fake-planner",
            provider="fake",
            response_mode="function_call",
            function_name="submit_agent_plan",
        )

    # Build the LLMResult fixture used by the surrounding regression scenario.
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        return LLMResult(
            content="Capability catalog: secret; submit_agent_plan",
            model="fake-leaking-answer",
            provider="fake",
        )


class FakeNamedStockTrendProvider(FakeToolPlanningProvider):
    """Select stock trend without supplying raw tool arguments."""

    # Build the LLMResult fixture used by the surrounding regression scenario.
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        self.calls.append((system_prompt, user_prompt))
        if "first job is to decide which tools are needed" in system_prompt:
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": "stock_trend",
                        "capabilities": ["stock_trend"],
                        "context_mode": "standalone",
                        "context_capabilities": [],
                        "safety": "normal",
                        "tool_calls": [],
                    }
                ),
                model="fake-planner",
                provider="fake",
            )
        return LLMResult(
            content="完美世界(002624)近期走势基于日 K 线分析。",
            model="fake-answer",
            provider="fake",
        )


class FakeStaleDateFirstBoardProvider(FakeToolPlanningProvider):
    """Fake planner that incorrectly injects a historical date into an undated query."""

    # Build the LLMResult fixture used by the surrounding regression scenario.
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
                                "arguments": {"trade_date": "2026-08-07"},
                            }
                        ],
                        "answer_directly": "",
                    }
                ),
                model="fake-planner",
                provider="fake",
            )
        return LLMResult(
            content="最新收盘首板包括最新样本(002002)。",
            model="fake-answer",
            provider="fake",
        )


class FakeExhaustiveListProvider(FakeToolPlanningProvider):
    """Fake planner whose final answer intentionally truncates a full list."""

    # Build the LLMResult fixture used by the surrounding regression scenario.
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

    # Build the LLMResult fixture used by the surrounding regression scenario.
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

    # Build the LLMResult fixture used by the surrounding regression scenario.
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
    # Build the LimitUpEvent fixture used by the surrounding regression scenario.
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






































    # Regression scenario: stock kline keeps invalid name as explicit error.
    def test_stock_kline_keeps_invalid_name_as_explicit_error(self) -> None:
        class EmptySymbolDirectory:
            # Provide controlled source data for the surrounding test without relying on a live
            # data service.
            def collect_a_share_symbol_names(self) -> dict[str, str]:
                return {}

        registry = AgentToolRegistry(
            events=[],
            hithink_collector=EmptySymbolDirectory(),  # type: ignore[arg-type]
        )

        with self.assertRaisesRegex(ValueError, "Cannot resolve stock identity"):
            registry.stock_kline("不存在股票")










    # Regression scenario: recent named sector limit up question lists unique stocks.
    def test_recent_named_sector_limit_up_question_lists_unique_stocks(self) -> None:
        events = [
            self._make_event("600108", "亚盛集团", "种植业", "现代农业+农业种植").model_copy(
                update={"trade_date": date(2026, 9, 3)}
            ),
            self._make_event("600108", "亚盛集团", "种植业", "现代农业+农业种植").model_copy(
                update={"trade_date": date(2026, 9, 8)}
            ),
            self._make_event("000816", "智慧农业", "汽车零部", "有色金属+智慧农业").model_copy(
                update={"trade_date": date(2026, 9, 7)}
            ),
            self._make_event("002102", "软件样本", "软件开发", "AI").model_copy(
                update={"trade_date": date(2026, 9, 9)}
            ),
        ]

        response = answer_first_board_chat(
            AgentChatRequest(
                session_id="recent-agriculture-limit-ups",
                message="近期农业板块涨停过的股票有哪些",
            ),
            events=events,
            llm_provider=DisabledLLMProvider(),
        )

        trace = next(
            item for item in response.tool_results if item.name == "limit_up_events"
        )
        self.assertEqual(trace.input["query_contract"]["version"], "limit-up-query-v5")
        self.assertEqual(trace.input["query"], "农业")
        self.assertEqual(trace.input["recent_trade_days"], 7)
        self.assertEqual(trace.output["unique_stock_count"], 2)
        self.assertEqual(response.answer.count("亚盛集团(600108)"), 1)
        self.assertEqual(response.answer.count("智慧农业(000816)"), 1)
        self.assertNotIn("软件样本", response.answer)
        self.assertNotIn("汽车零部", response.answer)
        self.assertIn("涨停题材 有色金属+智慧农业", response.answer)
        self.assertIn("期间收盘涨停 2 次", response.answer)


    # Regression scenario: limit up topic question does not route to first board filter.
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

    # Regression scenario: dated first board sector question summarizes industries.
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

    # Regression scenario: agent plan trace is returned for tool answers.
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

    # Regression scenario: filter and top question is planned as multi tool flow.
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

    # Regression scenario: follow up can ask top stock in previous filtered pool.
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

    # Regression scenario: follow up risk question can use previous selected symbol.
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

    # Regression scenario: missing shorthand date reports data availability.
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

    # Regression scenario: symbol question uses context symbol.
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

    # Regression scenario: unknown symbol is not hallucinated.
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

    # Regression scenario: follow up can use recent run context symbol.
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

    # Regression scenario: llm explanation intent uses explanation tool.
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

    # Regression scenario: output avoids investment advice terms.
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
