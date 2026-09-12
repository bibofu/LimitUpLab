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
