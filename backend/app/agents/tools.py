"""Tool registry and schemas for the first-board Agent."""

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.agents.first_board import build_first_board_ratings
from app.models import (
    AgentToolTrace,
    FirstBoardCriticResponse,
    FirstBoardRatingsResponse,
    LimitUpEvent,
    MarketSummary,
    RatingBacktestResponse,
    SimilarFirstBoardCasesResponse,
)
from app.repositories import SQLiteFirstBoardRepository
from app.services.analysis import summarize_market
from app.services.first_board_critic import build_first_board_critic
from app.services.rating_backtest import build_rating_backtest
from app.services.similar_cases import find_similar_first_board_cases


@dataclass(frozen=True)
class AgentToolSchema:
    """LLM-facing metadata for one callable Agent tool."""

    name: str
    description: str
    args_schema: dict[str, Any]
    returns: str

    def model_dump(self) -> dict[str, Any]:
        """Serialize the schema into a prompt-friendly dictionary."""

        return {
            "name": self.name,
            "description": self.description,
            "args_schema": self.args_schema,
            "returns": self.returns,
        }


@dataclass(frozen=True)
class ToolResult:
    """Internal tool result with full output and compact trace."""

    name: str
    input: dict[str, Any]
    output: Any
    summary: str
    status: str = "success"
    trace_output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def trace(self) -> AgentToolTrace:
        """Return the compact trace sent to frontend and saved in runs."""

        return AgentToolTrace(
            name=self.name,
            input=self.input,
            summary=self.summary,
            status=self.status,  # type: ignore[arg-type]
            output=self.trace_output,
            error=self.error,
        )


TOOL_SCHEMAS = [
    AgentToolSchema(
        name="market_summary",
        description="读取本地最新市场情绪、涨停数量、首板数量、炸板率、最高连板和热门行业。",
        args_schema={"type": "object", "properties": {}, "required": []},
        returns="Market sentiment facts for the latest local trade date.",
    ),
    AgentToolSchema(
        name="first_board_ratings",
        description="读取某个交易日的首板候选池和可解释评分；未传 trade_date 时使用本地最新交易日。",
        args_schema={
            "type": "object",
            "properties": {
                "trade_date": {
                    "type": ["string", "null"],
                    "description": "YYYY-MM-DD; omit or null for latest local trade date.",
                }
            },
            "required": [],
        },
        returns="First-board candidate ratings, top candidates, filters and industry distribution.",
    ),
    AgentToolSchema(
        name="first_board_filter",
        description="在首板候选池中按行业、题材、概念或股票名称筛选候选。",
        args_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Topic, industry, concept or stock-name keyword.",
                }
            },
            "required": ["query"],
        },
        returns="Matched first-board candidates for the query.",
    ),
    AgentToolSchema(
        name="first_board_similar_cases",
        description="检索某只首板股票的历史相似首板案例和首板后走势缓存。",
        args_schema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Six-digit A-share symbol."},
                "trade_date": {"type": "string", "description": "YYYY-MM-DD base first-board date."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["symbol", "trade_date"],
        },
        returns="Similar first-board cases, similarity reasons and post-limit-up bars.",
    ),
    AgentToolSchema(
        name="rating_backtest",
        description="回测一段日期内首板评分 A/B/C/D 的后续表现，并输出评分自我评价。",
        args_schema={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD inclusive start date."},
                "end_date": {"type": "string", "description": "YYYY-MM-DD inclusive end date."},
                "failure_limit": {"type": "integer", "minimum": 0, "maximum": 30},
            },
            "required": [],
        },
        returns="Rating bucket performance, weak high-rated samples and self-evaluation observations.",
    ),
    AgentToolSchema(
        name="first_board_critic",
        description="Critique one first-board rating by checking support evidence, counter evidence, missing data and confidence adjustment.",
        args_schema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Six-digit A-share symbol."},
                "trade_date": {
                    "type": ["string", "null"],
                    "description": "YYYY-MM-DD first-board date; omit or null for latest local date.",
                },
                "similar_limit": {"type": "integer", "minimum": 0, "maximum": 10},
            },
            "required": ["symbol"],
        },
        returns="Critic verdict, supporting evidence, opposing evidence, missing data and suggested confidence.",
    ),
]


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

    def schemas(self) -> list[AgentToolSchema]:
        """Return the LLM-facing tool schemas."""

        return TOOL_SCHEMAS

    def schema_prompt(self) -> str:
        """Return a JSON tool description block for the planner prompt."""

        return json.dumps(
            [schema.model_dump() for schema in self.schemas()],
            ensure_ascii=False,
        )

    def market_summary(self) -> ToolResult:
        """Return latest local market sentiment facts."""

        summary = summarize_market(self.events)
        label = {
            "heating": "升温",
            "diverging": "分歧",
            "cooling": "退潮",
        }.get(summary.sentiment, summary.sentiment)
        trace_output = {
            "trade_date": summary.trade_date.isoformat(),
            "sentiment": summary.sentiment,
            "limit_up_count": summary.limit_up_count,
            "first_board_count": summary.first_board_count,
            "continued_board_count": summary.continued_board_count,
            "failed_limit_up_rate": summary.failed_limit_up_rate,
            "max_board_height": summary.max_board_height,
            "hot_industries": summary.hot_industries[:5],
        }
        return ToolResult(
            name="market_summary",
            input={},
            output=summary,
            summary=(
                f"{summary.trade_date.isoformat()} 情绪{label}，涨停{summary.limit_up_count}只，"
                f"首板{summary.first_board_count}只，连板{summary.continued_board_count}只，"
                f"炸板率{summary.failed_limit_up_rate:.0%}。"
            ),
            trace_output=trace_output,
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
        trace_output = {
            "trade_date": ratings.trade_date.isoformat(),
            "candidate_count": len(ratings.candidates),
            "filtered_out_count": len(ratings.filtered_out),
            "top_candidates": [
                {
                    "symbol": item.facts.symbol,
                    "name": item.facts.name,
                    "rating": item.rating,
                    "score": item.score,
                    "industry": item.facts.industry,
                }
                for item in ratings.candidates[:5]
            ],
        }
        return ToolResult(
            name="first_board_ratings",
            input={"trade_date": trade_date.isoformat() if trade_date else None},
            output=ratings,
            summary=(
                f"{ratings.trade_date.isoformat()} 首板评级入池{len(ratings.candidates)}只，"
                f"{top_summary}。"
            ),
            trace_output=trace_output,
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
        trace_output = {
            "target": {
                "symbol": response.target.symbol,
                "name": response.target.name,
                "trade_date": response.target.trade_date.isoformat(),
            },
            "recall_count": response.recall_count,
            "window_days": response.window_days,
            "top_cases": [
                {
                    "symbol": item.symbol,
                    "name": item.name,
                    "trade_date": item.trade_date.isoformat(),
                    "similarity": item.similarity,
                    "post_bar_count": len(item.post_bars),
                }
                for item in response.cases[:5]
            ],
        }
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
            trace_output=trace_output,
        )

    def rating_backtest(
        self,
        start_date: date,
        end_date: date,
        failure_limit: int = 8,
    ) -> ToolResult:
        """Return rating backtest and self-evaluation facts."""

        response = build_rating_backtest(
            events=self.events,
            start_date=start_date,
            end_date=end_date,
            first_board_repository=self.first_board_repository,
            failure_limit=failure_limit,
        )
        trace_output = {
            "start_date": response.start_date.isoformat(),
            "end_date": response.end_date.isoformat(),
            "sample_size": response.sample_size,
            "outcome_ready_count": response.outcome_ready_count,
            "buckets": [bucket.model_dump(mode="json") for bucket in response.buckets],
            "failure_sample_count": len(response.failure_samples),
        }
        return ToolResult(
            name="rating_backtest",
            input={
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "failure_limit": failure_limit,
            },
            output=response,
            summary=(
                f"{response.start_date.isoformat()} 至 {response.end_date.isoformat()} "
                f"回测 {response.sample_size} 个首板评分样本，"
                f"{response.outcome_ready_count} 个 outcome 可用。"
            ),
            trace_output=trace_output,
        )

    def first_board_critic(
        self,
        symbol: str,
        trade_date: date | None = None,
        similar_limit: int = 5,
    ) -> ToolResult:
        """Return critic review facts for one first-board rating."""

        response: FirstBoardCriticResponse = build_first_board_critic(
            events=self.events,
            symbol=symbol,
            trade_date=trade_date,
            first_board_repository=self.first_board_repository,
            similar_limit=similar_limit,
        )
        trace_output = {
            "symbol": response.symbol,
            "name": response.name,
            "trade_date": response.trade_date.isoformat(),
            "verdict": response.verdict,
            "rating": response.rating,
            "score": response.score,
            "original_confidence": response.original_confidence,
            "suggested_confidence": response.suggested_confidence,
            "counter_evidence_count": len(response.counter_evidence),
            "missing_data_count": len(response.missing_data),
        }
        return ToolResult(
            name="first_board_critic",
            input={
                "symbol": symbol,
                "trade_date": trade_date.isoformat() if trade_date else None,
                "similar_limit": similar_limit,
            },
            output=response,
            summary=(
                f"{response.name}({response.symbol}) Critic verdict={response.verdict}, "
                f"confidence {response.original_confidence:.0%}->{response.suggested_confidence:.0%}."
            ),
            trace_output=trace_output,
        )
