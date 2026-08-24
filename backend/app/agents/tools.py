"""Tool registry and schemas for the first-board Agent."""

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from app.agents.first_board import build_first_board_ratings
from app.collectors import HithinkFinanceCollector
from app.models import (
    AgentEvaluationResponse,
    AgentToolTrace,
    FinanceNewsFacts,
    FirstBoardCriticResponse,
    FirstBoardRating,
    FirstBoardRatingsResponse,
    LimitUpEvent,
    MarketSummary,
    PredictionQualityAuditResponse,
    RatingBacktestResponse,
    SectorPerformanceFacts,
    SimilarFirstBoardCasesResponse,
    StockKLineFacts,
    WebSearchFacts,
)
from app.repositories import SQLiteFirstBoardRepository, SQLiteScoringPolicyRepository
from app.services.analysis import events_for_date, summarize_market
from app.services.evaluation_agent import build_agent_evaluation
from app.services.finance_news import collect_finance_news
from app.services.first_board_critic import build_first_board_critic
from app.services.prediction_quality_audit import build_prediction_quality_audit
from app.services.rating_backtest import build_rating_backtest
from app.services.sector_performance import build_sector_performance
from app.services.similar_cases import find_similar_first_board_cases
from app.services.stock_kline import build_stock_kline_facts
from app.services.scoring_policy_optimizer import build_scoring_policy_registry
from app.services.web_search import search_web
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
        name="sector_performance",
        description=(
            "按需获取A股行业板块行情。可查询指定板块的涨跌幅、行业排名、成交额、"
            "资金净流入、上涨/下跌家数、领涨股和近期趋势；sector 为空时返回行业强弱榜。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "sector": {
                    "type": ["string", "null"],
                    "description": "Industry sector name such as 半导体; omit for overall ranking.",
                },
                "trade_date": {
                    "type": ["string", "null"],
                    "description": "YYYY-MM-DD; omit or null for the latest live snapshot.",
                },
            },
            "required": [],
        },
        returns=(
            "Sector change, market rank, breadth, turnover, fund flow, leader, "
            "5/20-day returns and top/bottom sector rankings."
        ),
    ),
    AgentToolSchema(
        name="hot_stock_ranking",
        description=(
            "查询同花顺当前热股榜和热度排名变化。适合回答市场关注度、热门股票、"
            "某只股票当前人气排名等问题；榜单热度不代表投资价值。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["day", "hour"],
                    "description": "Ranking period; defaults to day.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 30},
            },
            "required": [],
        },
        returns="Tonghuashun hot-stock rank, heat, rank change and capture time.",
    ),
    AgentToolSchema(
        name="dragon_tiger_list",
        description=(
            "查询同花顺龙虎榜，可按交易日、机构/游资榜类型和股票名称或代码过滤，"
            "返回买卖额、净买额、机构净买、游资净买、热度排名和相关题材。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "trade_date": {
                    "type": ["string", "null"],
                    "description": "YYYY-MM-DD; omit for the latest available list.",
                },
                "board_type": {
                    "type": "string",
                    "enum": ["all", "org", "hot_money"],
                },
                "query": {
                    "type": ["string", "null"],
                    "description": "Optional exact/partial stock name or six-digit symbol.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": [],
        },
        returns="Tonghuashun Dragon-Tiger List rows and capital-flow evidence.",
    ),
    AgentToolSchema(
        name="remote_limit_up_pool",
        description=(
            "查询同花顺远端涨停池，包含首板/连板高度、封板时间、涨停原因、封单额、"
            "ST和新股标记。适合当前或指定交易日的实时/权威涨停池核验。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "trade_date": {
                    "type": ["string", "null"],
                    "description": "YYYY-MM-DD; omit for current upstream snapshot.",
                },
                "board_height": {
                    "type": ["integer", "null"],
                    "description": "1 for first-board, 2 for second-board, etc.",
                },
                "query": {
                    "type": ["string", "null"],
                    "description": "Optional stock name, symbol or limit-up reason keyword.",
                },
                "exclude_st": {"type": "boolean"},
                "exclude_new": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": [],
        },
        returns="Filtered Tonghuashun limit-up pool with board height and seal facts.",
    ),
    AgentToolSchema(
        name="first_board_ratings",
        description=(
            "读取某个交易日的首板评级候选池、可解释评分、行业分布和基于首板前 K 线的"
            "位置分类（如低位启动、超跌反弹、V形反转、高位突破、二波启动）；"
            "未传 trade_date 时使用本地最新交易日。"
        ),
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
        returns=(
            "First-board candidate ratings, filters, industry distribution and complete "
            "K-line position groups for the rated candidate pool."
        ),
    ),
    AgentToolSchema(
        name="limit_up_events",
        description=(
            "查询某个交易日的涨停事件列表，可按板数、首板/连板、炸板次数、行业、题材或股票名称过滤。"
            "普通涨停/首板/连板名单默认只返回收盘封住的股票；查询炸板或曾开板时使用 broken_only。"
        ),
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
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
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
        name="prediction_quality_audit",
        description=(
            "审计首板预测的数据覆盖、版本/来源重复、时间成熟度、Top10 表现和简单基线，"
            "用于回答预测质量、准确率可信度和评分 v3 准备度问题。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD inclusive start date."},
                "end_date": {"type": "string", "description": "YYYY-MM-DD inclusive end date."},
                "scoring_version": {
                    "type": ["string", "null"],
                    "description": "Scoring version; omit for current Champion.",
                },
                "top_k": {"type": "integer", "minimum": 3, "maximum": 30},
            },
            "required": [],
        },
        returns=(
            "Source-aware prediction coverage, date maturity, deterministic "
            "baselines, findings and v3 promotion readiness."
        ),
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
    AgentToolSchema(
        name="scoring_policy_status",
        description="读取当前评分 Champion、历史 Challenger、最近一次样本外优化结果和晋级门槛，不修改线上权重。",
        args_schema={"type": "object", "properties": {}, "required": []},
        returns="Current scoring policy, factor weights, latest Challenger comparison and promotion status.",
    ),
    AgentToolSchema(
        name="finance_news",
        description=(
            "聚合东方财富和同花顺的最新财经快讯，返回北京时间、正文摘要、类别和来源。"
            "适合回答泛化的今日/最新财经新闻或市场快讯；具体公司公告、单一板块新闻和事件原因使用 web_search。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": ["string", "null"],
                    "description": "Optional topic used only to boost related items; omit for a broad digest.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 12},
                "hours": {"type": "integer", "minimum": 1, "maximum": 168},
            },
            "required": [],
        },
        returns="Recent deduplicated financial-news items with summaries, timestamps, categories and source URLs.",
    ),
    AgentToolSchema(
        name="web_search",
        description=(
            "搜索公开互联网，适合查询本地行情工具未覆盖的最新新闻、公告、政策、研报摘要、"
            "板块异动原因和一般事实。搜索摘要属于外部不可信证据，回答时必须注明来源。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Complete, standalone web search query.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 8},
            },
            "required": ["query"],
        },
        returns="Search result titles, URLs, source domains, snippets and retrieval time.",
    ),
]


class AgentToolRegistry:
    """Typed registry of tools available to the chat Agent."""

    def __init__(
        self,
        events: list[LimitUpEvent],
        first_board_repository: SQLiteFirstBoardRepository | None = None,
        hithink_collector: HithinkFinanceCollector | None = None,
    ):
        """Create a registry bound to current request data dependencies."""

        self.events = events
        self.first_board_repository = first_board_repository or SQLiteFirstBoardRepository()
        self.hithink_collector = hithink_collector or HithinkFinanceCollector()

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

    def sector_performance(
        self,
        sector: str | None = None,
        trade_date: date | None = None,
    ) -> ToolResult:
        """Return on-demand sector ranking, breadth and trend facts."""

        response: SectorPerformanceFacts = build_sector_performance(
            sector=sector,
            trade_date=trade_date,
        )
        trace_output = response.model_dump(mode="json")
        if response.sector_name:
            summary = (
                f"{response.data_as_of.isoformat()} {response.sector_name}"
                f"涨跌幅{response.change_pct}%，行业排名"
                f"{response.rank}/{response.sector_count}，"
                f"上涨{response.up_count}家、下跌{response.down_count}家。"
            )
        else:
            leaders = "、".join(
                f"{item.sector_name}({item.change_pct:+.2f}%)"
                for item in response.top_sectors[:3]
            )
            summary = f"{response.data_as_of.isoformat()} 行业强弱榜：{leaders}。"
        return ToolResult(
            name="sector_performance",
            input={
                "sector": sector,
                "trade_date": trade_date.isoformat() if trade_date else None,
            },
            output=response,
            summary=summary,
            trace_output=trace_output,
        )

    def hot_stock_ranking(
        self,
        period: str = "day",
        limit: int = 20,
    ) -> ToolResult:
        """Return a bounded Tonghuashun popularity ranking snapshot."""

        snapshot = self.hithink_collector.collect_hot_stocks(
            period=period,
            limit=max(1, min(limit, 30)),
        )
        items = [asdict(item) for item in snapshot.items]
        payload = {
            "source": snapshot.source,
            "captured_at": snapshot.captured_at.isoformat(),
            "period": snapshot.period,
            "count": len(items),
            "items": items,
        }
        leaders = "、".join(
            f"{item.name}({item.symbol})第{item.rank}名"
            for item in snapshot.items[:5]
        )
        return ToolResult(
            name="hot_stock_ranking",
            input={"period": period, "limit": limit},
            output=payload,
            summary=f"同花顺热股榜返回 {len(items)} 只{f'：{leaders}' if leaders else '。'}",
            trace_output=payload,
        )

    def dragon_tiger_list(
        self,
        *,
        trade_date: date | None = None,
        board_type: str = "all",
        query: str | None = None,
        limit: int = 30,
    ) -> ToolResult:
        """Return bounded Tonghuashun Dragon-Tiger capital-flow facts."""

        snapshot = self.hithink_collector.collect_dragon_tiger(
            trade_date=trade_date,
            board_type=board_type,
            query=query,
            limit=max(1, min(limit, 100)),
        )
        items = [asdict(item) for item in snapshot.items]
        payload = {
            "source": snapshot.source,
            "trade_date": snapshot.trade_date.isoformat() if snapshot.trade_date else None,
            "board_type": snapshot.board_type,
            "stock_count": snapshot.stock_count,
            "matched_count": len(items),
            "items": items,
        }
        names = "、".join(
            f"{item.name}({item.symbol})"
            for item in snapshot.items[:5]
        )
        return ToolResult(
            name="dragon_tiger_list",
            input={
                "trade_date": trade_date.isoformat() if trade_date else None,
                "board_type": board_type,
                "query": query,
                "limit": limit,
            },
            output=payload,
            summary=(
                f"{payload['trade_date'] or '最新'} 同花顺龙虎榜命中 {len(items)} 条"
                f"{f'：{names}' if names else '。'}"
            ),
            trace_output=payload,
        )

    def remote_limit_up_pool(
        self,
        *,
        trade_date: date | None = None,
        board_height: int | None = None,
        query: str | None = None,
        exclude_st: bool = True,
        exclude_new: bool = True,
        limit: int = 100,
    ) -> ToolResult:
        """Return a filtered Tonghuashun remote limit-up pool."""

        snapshot = self.hithink_collector.collect_limit_up_pool(
            trade_date=trade_date,
            page=1,
            size=200,
            sort_field="limit_up_time",
            sort_direction="asc",
        )
        normalized_query = (query or "").strip().lower()
        items = [
            item
            for item in snapshot.items
            if (board_height is None or item.board_height == board_height)
            and (not exclude_st or not item.is_st)
            and (not exclude_new or not item.is_new)
            and (
                not normalized_query
                or normalized_query in item.symbol.lower()
                or normalized_query in item.name.lower()
                or normalized_query in (item.limit_up_reason or "").lower()
            )
        ][: max(1, min(limit, 100))]
        serialized = [asdict(item) for item in items]
        payload = {
            "source": snapshot.source,
            "trade_date": snapshot.trade_date.isoformat() if snapshot.trade_date else None,
            "upstream_total": snapshot.total,
            "matched_count": len(serialized),
            "board_height": board_height,
            "items": serialized,
        }
        names = "、".join(
            f"{item.name}({item.symbol})"
            for item in items[:5]
        )
        board_text = f"{board_height}板" if board_height else "涨停"
        return ToolResult(
            name="remote_limit_up_pool",
            input={
                "trade_date": trade_date.isoformat() if trade_date else None,
                "board_height": board_height,
                "query": query,
                "exclude_st": exclude_st,
                "exclude_new": exclude_new,
                "limit": limit,
            },
            output=payload,
            summary=(
                f"同花顺{board_text}池共 {snapshot.total} 只，筛选后 {len(items)} 只"
                f"{f'：{names}' if names else '。'}"
            ),
            trace_output=payload,
        )

    def web_search(self, query: str, limit: int = 5) -> ToolResult:
        """Return sanitized public-web search evidence."""

        response: WebSearchFacts = search_web(query=query, limit=limit)
        trace_output = response.model_dump(mode="json")
        domains = "、".join(
            dict.fromkeys(item.domain for item in response.results[:5])
        )
        return ToolResult(
            name="web_search",
            input={"query": response.query, "limit": limit},
            output=response,
            summary=(
                f"{response.provider} 搜索到 {len(response.results)} 条结果"
                f"{f'，来源：{domains}' if domains else ''}。"
            ),
            trace_output=trace_output,
        )

    def finance_news(
        self,
        query: str | None = None,
        limit: int = 8,
        hours: int = 48,
    ) -> ToolResult:
        """Return recent structured financial-news evidence."""

        response: FinanceNewsFacts = collect_finance_news(
            query=query,
            limit=limit,
            hours=hours,
        )
        trace_output = response.model_dump(mode="json")
        sources = "、".join(response.sources)
        return ToolResult(
            name="finance_news",
            input={"query": query, "limit": limit, "hours": hours},
            output=response,
            summary=(
                f"{sources or '财经数据源'} 聚合到 {len(response.items)} 条"
                f"近 {response.window_hours} 小时财经快讯。"
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
            "position_classification": compact_first_board_position_groups(
                ratings.candidates
            ),
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
        effective_closed_only = closed_only
        if effective_closed_only is None and not broken_only:
            effective_closed_only = True
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
        if effective_closed_only:
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
        )[: max(1, min(limit, 100))]
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
                "closed_only": effective_closed_only,
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

    def prediction_quality_audit(
        self,
        start_date: date,
        end_date: date,
        scoring_version: str | None = None,
        top_k: int = 10,
    ) -> ToolResult:
        """Return prediction coverage, baselines and v3 readiness facts."""

        response: PredictionQualityAuditResponse = build_prediction_quality_audit(
            events=self.events,
            start_date=start_date,
            end_date=end_date,
            first_board_repository=self.first_board_repository,
            scoring_version=scoring_version,
            top_k=top_k,
        )
        trace_output = {
            "start_date": response.start_date.isoformat(),
            "end_date": response.end_date.isoformat(),
            "audited_scoring_version": response.audited_scoring_version,
            "raw_prediction_rows": response.raw_prediction_rows,
            "canonical_prediction_count": response.canonical_prediction_count,
            "complete_next_day_trade_date_count": (
                response.complete_next_day_trade_date_count
            ),
            "next_day_outcome_coverage_rate": (
                response.next_day_outcome_coverage_rate
            ),
            "benchmarks": [
                item.model_dump(mode="json") for item in response.benchmarks
            ],
            "policy_status": response.policy_status.model_dump(mode="json"),
        }
        return ToolResult(
            name="prediction_quality_audit",
            input={
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "scoring_version": scoring_version,
                "top_k": top_k,
            },
            output=response,
            summary=(
                f"审计 {response.prediction_trade_date_count} 个预测日，"
                f"{response.complete_next_day_trade_date_count} 日 Top{response.top_k} "
                f"结果完整，次日覆盖率 {response.next_day_outcome_coverage_rate:.1%}。"
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

    def scoring_policy_status(self) -> ToolResult:
        """Return the current Champion and latest constrained optimization result."""

        repository = SQLiteScoringPolicyRepository(
            self.first_board_repository.database_path
        )
        registry = build_scoring_policy_registry(repository=repository, limit=10)
        latest = repository.get_latest_optimization_run()
        payload = {
            "champion": registry.champion.model_dump(mode="json"),
            "policy_count": len(registry.policies),
            "challengers": [
                item.model_dump(mode="json")
                for item in registry.policies
                if item.status == "challenger"
            ][:5],
            "latest_optimization": latest.model_dump(mode="json") if latest else None,
        }
        latest_comparison = latest.comparison if latest else None
        trace_output = {
            "champion_version": registry.champion.version,
            "policy_count": len(registry.policies),
            "challenger_count": len(payload["challengers"]),
            "latest_challenger": (
                latest.challenger_policy.version if latest else None
            ),
            "promotion_eligible": (
                latest_comparison.promotion_eligible if latest_comparison else None
            ),
            "activated": latest.activated if latest else None,
        }
        return ToolResult(
            name="scoring_policy_status",
            input={},
            output=payload,
            summary=(
                f"Champion {registry.champion.version}; "
                f"{len(payload['challengers'])} recent challengers registered."
            ),
            trace_output=trace_output,
        )


def compact_first_board_position_groups(
    candidates: list[FirstBoardRating],
) -> dict[str, Any]:
    """Group the complete rated candidate pool by pre-board K-line position."""

    grouped: dict[tuple[str, str], list[tuple[FirstBoardRating, Any]]] = {}
    missing: list[FirstBoardRating] = []
    for candidate in candidates:
        enrichment = candidate.facts.enrichment
        position = enrichment.position if enrichment else None
        if position is None:
            missing.append(candidate)
            continue
        key = (position.primary.regime, position.primary.label)
        grouped.setdefault(key, []).append((candidate, position))

    groups: list[dict[str, Any]] = []
    for (regime, label), entries in grouped.items():
        ordered = sorted(
            entries,
            key=lambda entry: (-entry[0].score, entry[0].facts.symbol),
        )
        groups.append(
            {
                "regime": regime,
                "label": label,
                "count": len(ordered),
                "avg_score": round(
                    sum(candidate.score for candidate, _ in ordered) / len(ordered),
                    1,
                ),
                "candidates": [
                    {
                        "symbol": candidate.facts.symbol,
                        "name": candidate.facts.name,
                        "industry": candidate.facts.industry,
                        "rating": candidate.rating,
                        "score": candidate.score,
                        "position_match_score": position.primary.score,
                        "position_confidence": position.confidence,
                        "tags": position.tags[:3],
                    }
                    for candidate, position in ordered
                ],
            }
        )

    groups.sort(
        key=lambda item: (-item["count"], -item["avg_score"], item["label"])
    )
    return {
        "scope": "rated_first_board_candidate_pool",
        "scope_note": (
            "仅统计通过 ST、板块、新股/次新和最低成交额过滤的首板评级候选；"
            "位置指首板前 K 线所处阶段，不是首封时间。"
        ),
        "candidate_count": len(candidates),
        "classified_count": len(candidates) - len(missing),
        "missing_count": len(missing),
        "groups": groups,
        "missing_candidates": [
            {
                "symbol": candidate.facts.symbol,
                "name": candidate.facts.name,
                "rating": candidate.rating,
                "score": candidate.score,
            }
            for candidate in sorted(
                missing,
                key=lambda item: (-item.score, item.facts.symbol),
            )
        ],
    }


def compact_prediction_quality_audit(
    response: PredictionQualityAuditResponse,
) -> dict[str, Any]:
    """Trim per-date details before placing an audit report in an LLM prompt."""

    return {
        "start_date": response.start_date.isoformat(),
        "end_date": response.end_date.isoformat(),
        "audited_scoring_version": response.audited_scoring_version,
        "top_k": response.top_k,
        "raw_prediction_rows": response.raw_prediction_rows,
        "canonical_prediction_count": response.canonical_prediction_count,
        "cross_cohort_duplicate_rows": response.cross_cohort_duplicate_rows,
        "data_as_of_violation_count": response.data_as_of_violation_count,
        "prediction_trade_date_count": response.prediction_trade_date_count,
        "next_day_mature_trade_date_count": (
            response.next_day_mature_trade_date_count
        ),
        "complete_next_day_trade_date_count": (
            response.complete_next_day_trade_date_count
        ),
        "next_day_outcome_coverage_rate": response.next_day_outcome_coverage_rate,
        "three_day_outcome_coverage_rate": response.three_day_outcome_coverage_rate,
        "cohorts": [item.model_dump(mode="json") for item in response.cohorts],
        "benchmarks": [
            item.model_dump(mode="json") for item in response.benchmarks
        ],
        "policy_status": response.policy_status.model_dump(mode="json"),
        "findings": response.findings,
        "recommendations": response.recommendations,
        "warnings": response.warnings,
    }
