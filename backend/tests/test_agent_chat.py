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

























if __name__ == "__main__":
    unittest.main()
