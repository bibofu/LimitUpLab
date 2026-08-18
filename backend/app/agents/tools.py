"""Tool registry and schemas for the first-board Agent."""

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.agents.first_board import build_first_board_ratings
from app.models import (
    AgentEvaluationResponse,
    AgentToolTrace,
    FirstBoardCriticResponse,
    FirstBoardRatingsResponse,
    LimitUpEvent,
    MarketSummary,
    RatingBacktestResponse,
    SimilarFirstBoardCasesResponse,
    StockKLineFacts,
)
from app.repositories import SQLiteFirstBoardRepository
from app.services.analysis import events_for_date, summarize_market
from app.services.evaluation_agent import build_agent_evaluation
from app.services.first_board_critic import build_first_board_critic
from app.services.rating_backtest import build_rating_backtest
from app.services.similar_cases import find_similar_first_board_cases
from app.services.stock_kline import build_stock_kline_facts
from app.agents.review_agent import build_review_agent_report


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

    def planner_dump(self) -> dict[str, Any]:
        """Serialize only fields needed by the LLM to choose and call a tool."""

        properties = self.args_schema.get("properties", {})
        return {
            "name": self.name,
            "description": self.description,
            "arguments": {
                name: definition.get("type", "any")
                for name, definition in properties.items()
            },
            "required": self.args_schema.get("required", []),
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
        name="limit_up_events",
        description="查询某个交易日的涨停事件列表，可按板数、首板/连板、炸板次数、行业、题材或股票名称过滤。",
        args_schema={
            "type": "object",
            "properties": {
                "trade_date": {
                    "type": ["string", "null"],
                    "description": "YYYY-MM-DD; omit or null for latest local trade date.",
                },
                "board_height": {
                    "type": ["integer", "null"],
                    "description": "Limit-up board height, e.g. 1 for first-board, 2 for second-board.",
                },
                "min_board_height": {
                    "type": ["integer", "null"],
                    "description": "Minimum board height; use 2 for all continued-board stocks.",
                },
                "query": {
                    "type": ["string", "null"],
                    "description": "Optional industry, concept, stock name or symbol keyword.",
                },
                "broken_only": {
                    "type": ["boolean", "null"],
                    "description": "Only return stocks with intraday breaks when true.",
                },
                "closed_only": {
                    "type": ["boolean", "null"],
                    "description": "Only return stocks that closed at limit-up when true.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": [],
        },
        returns="Filtered limit-up events with board height, industry, concept, first seal time and break count.",
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
        name="stock_kline",
        description="读取指定股票最近一段时间的日 K 线、均线、区间涨跌、量能和最大回撤，用于回答个股走势问题。",
        args_schema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Six-digit A-share symbol or an exact stock name present in local data.",
                },
                "days": {"type": "integer", "minimum": 5, "maximum": 60},
                "end_date": {
                    "type": ["string", "null"],
                    "description": "YYYY-MM-DD; omit or null for latest local trade date.",
                },
            },
            "required": ["symbol"],
        },
        returns="Daily OHLCV bars, data freshness, trend, returns, moving averages, volume ratio and drawdown.",
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
    AgentToolSchema(
        name="rating_evaluation",
        description="Evaluate saved first-board rating predictions against later outcomes and summarize successes, misses and false negatives.",
        args_schema={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD inclusive start date."},
                "end_date": {"type": "string", "description": "YYYY-MM-DD inclusive end date."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": [],
        },
        returns="Prediction evaluation labels, lessons, scoring suggestions and summary counts.",
    ),
    AgentToolSchema(
        name="review_high_score_picks",
        description="Run the Review Agent to review high-score first-board picks, later outcomes, successful/failed patterns and scoring taste adjustments.",
        args_schema={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD inclusive start date."},
                "end_date": {"type": "string", "description": "YYYY-MM-DD inclusive end date."},
                "min_score": {"type": "number", "minimum": 0, "maximum": 100},
            },
            "required": [],
        },
        returns="Review Agent report with findings, successful patterns, failed patterns, scoring bias and adjustment suggestions.",
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
            [schema.planner_dump() for schema in self.schemas()],
            ensure_ascii=False,
            separators=(",", ":"),
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

        ratings = build_first_board_ratings(
            events=self.events,
            trade_date=trade_date,
            first_board_repository=self.first_board_repository,
        )
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

    def limit_up_events(
        self,
        trade_date: date | None = None,
        board_height: int | None = None,
        min_board_height: int | None = None,
        query: str | None = None,
        broken_only: bool | None = None,
        closed_only: bool | None = None,
        limit: int = 30,
    ) -> ToolResult:
        """Return filtered limit-up events for general limit-up questions."""

        target_events = events_for_date(self.events, trade_date)
        if board_height is not None:
            target_events = [
                event for event in target_events if event.board_height == board_height
            ]
        if min_board_height is not None:
            target_events = [
                event for event in target_events if event.board_height >= min_board_height
            ]
        if broken_only:
            target_events = [event for event in target_events if event.break_count > 0]
        if closed_only:
            target_events = [event for event in target_events if event.closed_limit]
        if query:
            normalized_query = query.strip().lower()
            target_events = [
                event
                for event in target_events
                if normalized_query in event.symbol.lower()
                or normalized_query in event.name.lower()
                or normalized_query in event.industry.lower()
                or normalized_query in event.concept.lower()
            ]

        target_events = sorted(
            target_events,
            key=lambda event: (-event.board_height, event.first_limit_time, event.symbol),
        )[: max(1, min(limit, 50))]
        if trade_date is not None:
            trade_date_text = trade_date.isoformat()
        elif self.events:
            trade_date_text = max(event.trade_date for event in self.events).isoformat()
        else:
            trade_date_text = ""
        board_text = f"{board_height}板" if board_height is not None else "涨停"
        names = "、".join(f"{event.name}({event.symbol})" for event in target_events[:5])
        trace_output = {
            "trade_date": trade_date_text,
            "matched_count": len(target_events),
            "events": [
                {
                    "symbol": event.symbol,
                    "name": event.name,
                    "board_height": event.board_height,
                    "industry": event.industry,
                    "concept": event.concept,
                    "first_limit_time": event.first_limit_time.strftime("%H:%M"),
                    "break_count": event.break_count,
                    "closed_limit": event.closed_limit,
                }
                for event in target_events
            ],
        }
        return ToolResult(
            name="limit_up_events",
            input={
                "trade_date": trade_date.isoformat() if trade_date else None,
                "board_height": board_height,
                "min_board_height": min_board_height,
                "query": query,
                "broken_only": broken_only,
                "closed_only": closed_only,
                "limit": limit,
            },
            output=target_events,
            summary=(
                f"{trade_date_text} {board_text}查询命中 {len(target_events)} 只"
                f"{f'：{names}' if names else '。'}"
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

    def stock_kline(
        self,
        symbol: str,
        days: int = 20,
        end_date: date | None = None,
    ) -> ToolResult:
        """Return local-first K-line facts for a stock trend question."""

        resolved_symbol = self.resolve_stock_symbol(symbol)
        available_dates = sorted({event.trade_date for event in self.events})
        resolved_end_date = end_date or (
            available_dates[-1] if available_dates else date.today()
        )
        response: StockKLineFacts = build_stock_kline_facts(
            symbol=resolved_symbol,
            days=max(5, min(days, 60)),
            end_date=resolved_end_date,
            repository=self.first_board_repository,
        )
        trace_output = {
            "symbol": response.symbol,
            "requested_days": response.requested_days,
            "requested_end_date": response.requested_end_date.isoformat(),
            "data_as_of": response.data_as_of.isoformat(),
            "data_fresh": response.data_fresh,
            "trend": response.trend,
            "latest_close": response.latest_close,
            "return_5d_pct": response.return_5d_pct,
            "return_10d_pct": response.return_10d_pct,
            "return_20d_pct": response.return_20d_pct,
            "bar_count": len(response.bars),
        }
        return ToolResult(
            name="stock_kline",
            input={
                "symbol": resolved_symbol,
                "days": response.requested_days,
                "end_date": resolved_end_date.isoformat(),
            },
            output=response,
            summary=(
                f"{resolved_symbol} K-line through {response.data_as_of.isoformat()}: "
                f"trend={response.trend}, close={response.latest_close}, "
                f"5d={response.return_5d_pct}."
            ),
            trace_output=trace_output,
        )

    def resolve_stock_symbol(self, value: str) -> str:
        """Resolve a six-digit symbol or local stock name to a symbol."""

        normalized = value.strip()
        lowered = normalized.lower()
        if lowered.startswith(("sh", "sz")):
            normalized = normalized[2:]
        if len(normalized) == 6 and normalized.isdigit():
            return normalized

        compact = normalized.replace(" ", "")
        exact = {
            event.symbol
            for event in self.events
            if event.name.replace(" ", "") == compact
        }
        if len(exact) == 1:
            return exact.pop()
        contained = {
            event.symbol
            for event in self.events
            if event.name.replace(" ", "") in compact
        }
        if len(contained) == 1:
            return contained.pop()
        raise ValueError(f"Cannot resolve stock symbol from: {value}")

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
                f"{response.outcome_ready_count} 个次日介入结果可用。"
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

    def rating_evaluation(
        self,
        start_date: date,
        end_date: date,
        limit: int = 30,
    ) -> ToolResult:
        """Return Evaluation Agent facts for persisted first-board predictions."""

        response: AgentEvaluationResponse = build_agent_evaluation(
            events=self.events,
            start_date=start_date,
            end_date=end_date,
            first_board_repository=self.first_board_repository,
            limit=limit,
        )
        trace_output = {
            "start_date": response.start_date.isoformat(),
            "end_date": response.end_date.isoformat(),
            "prediction_count": response.prediction_count,
            "outcome_ready_count": response.outcome_ready_count,
            "source_counts": response.source_counts,
            "label_counts": response.label_counts,
            "top_evaluations": [
                {
                    "symbol": item.symbol,
                    "name": item.name,
                    "trade_date": item.trade_date.isoformat(),
                    "rating": item.rating,
                    "score": item.score,
                    "prediction_source": item.prediction_source,
                    "evaluation_label": item.evaluation_label,
                    "next_open_to_close_pct": item.next_open_to_close_pct,
                    "next_open_to_low_pct": item.next_open_to_low_pct,
                }
                for item in response.evaluations[:5]
            ],
        }
        return ToolResult(
            name="rating_evaluation",
            input={
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "limit": limit,
            },
            output=response,
            summary=(
                f"Evaluation {response.start_date.isoformat()} to "
                f"{response.end_date.isoformat()}: {response.prediction_count} predictions, "
                f"{response.outcome_ready_count} ready outcomes."
            ),
            trace_output=trace_output,
        )

    def review_high_score_picks(
        self,
        start_date: date,
        end_date: date,
        min_score: float = 85,
    ) -> ToolResult:
        """Run Review Agent over high-score picks and post-board outcomes."""

        response = build_review_agent_report(
            events=self.events,
            start_date=start_date,
            end_date=end_date,
            repository=self.first_board_repository,
            min_score=min_score,
        )
        trace_output = {
            "start_date": response.start_date.isoformat(),
            "end_date": response.end_date.isoformat(),
            "sample_size": response.sample_size,
            "success_count": response.success_count,
            "failed_count": response.failed_count,
            "pending_count": response.pending_count,
            "main_findings": response.main_findings[:3],
            "adjustment_suggestions": response.adjustment_suggestions[:3],
        }
        return ToolResult(
            name="review_high_score_picks",
            input={
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "min_score": min_score,
            },
            output=response,
            summary=(
                f"Review Agent checked {response.sample_size} high-score picks "
                f"from {response.start_date.isoformat()} to {response.end_date.isoformat()}."
            ),
            trace_output=trace_output,
        )
