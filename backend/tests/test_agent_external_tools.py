import json
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from app.agents.chat import answer_first_board_chat
from app.models import (
    AgentChatRequest,
    SectorPerformanceFacts,
    WebSearchFacts,
    WebSearchResult,
)
from app.services.llm_provider import LLMProvider, LLMResult
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


class AgentExternalToolsTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
