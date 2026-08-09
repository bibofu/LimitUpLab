"""Tool registry for the first-board Agent."""

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.agents.first_board import build_first_board_ratings
from app.models import (
    AgentToolTrace,
    FirstBoardRatingsResponse,
    LimitUpEvent,
    MarketSummary,
    SimilarFirstBoardCasesResponse,
)
from app.repositories import SQLiteFirstBoardRepository
from app.services.analysis import summarize_market
from app.services.similar_cases import find_similar_first_board_cases


@dataclass(frozen=True)
class ToolResult:
    """Internal tool result with full output and compact trace."""

    name: str
    input: dict[str, Any]
    output: Any
    summary: str

    def trace(self) -> AgentToolTrace:
        """Return the compact trace sent to frontend and saved in runs."""

        return AgentToolTrace(name=self.name, input=self.input, summary=self.summary)


class AgentToolRegistry:
    """Typed registry of tools available to the chat Agent."""

    def __init__(
        self,
        events: list[LimitUpEvent],
        first_board_repository: SQLiteFirstBoardRepository | None = None,
    ):
        """Create a registry bound to current request data dependencies."""

        self.events = events
        self.first_board_repository = first_board_repository or SQLiteFirstBoardRepository()

    def market_summary(self) -> ToolResult:
        """Return latest local market sentiment facts."""

        summary = summarize_market(self.events)
        label = {
            "heating": "升温",
            "diverging": "分歧",
            "cooling": "退潮",
        }.get(summary.sentiment, summary.sentiment)
        return ToolResult(
            name="market_summary",
            input={},
            output=summary,
            summary=(
                f"{summary.trade_date.isoformat()} 情绪{label}，涨停{summary.limit_up_count}只，"
                f"首板{summary.first_board_count}只，连板{summary.continued_board_count}只，"
                f"炸板率{summary.failed_limit_up_rate:.0%}。"
            ),
        )

    def first_board_ratings(self, trade_date: date | None = None) -> ToolResult:
        """Return explainable first-board ratings."""

        ratings = build_first_board_ratings(events=self.events, trade_date=trade_date)
        top = ratings.candidates[0] if ratings.candidates else None
        top_summary = (
            f"最高分 {top.facts.name}({top.facts.symbol}) {top.rating}/{top.score:.1f}"
            if top
            else "暂无入池候选"
        )
        return ToolResult(
            name="first_board_ratings",
            input={
                "trade_date": trade_date.isoformat() if trade_date else None,
            },
            output=ratings,
            summary=(
                f"{ratings.trade_date.isoformat()} 首板评级入池{len(ratings.candidates)}只，"
                f"{top_summary}。"
            ),
        )

    def similar_cases(
        self,
        symbol: str,
        trade_date: date,
        limit: int = 5,
    ) -> ToolResult:
        """Return historical first-board similar cases."""

        response = find_similar_first_board_cases(
            symbol=symbol,
            trade_date=trade_date,
            repository=self.first_board_repository,
            limit=limit,
        )
        names = "、".join(
            f"{item.name}({item.symbol})" for item in response.cases[:3]
        ) or "暂无"
        return ToolResult(
            name="first_board_similar_cases",
            input={
                "symbol": symbol,
                "trade_date": trade_date.isoformat(),
                "limit": limit,
            },
            output=response,
            summary=(
                f"召回{response.recall_count}条，窗口{response.window_days}日，"
                f"Top案例：{names}。"
            ),
        )
