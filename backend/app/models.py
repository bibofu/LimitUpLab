from datetime import date, datetime, time
from math import isfinite
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.agent_output_sanitizer import sanitize_agent_answer
from app.review_digest_models import ReviewDigest
from app.review_research_models import ReviewFeatureResearch, ReviewInsight
from app.agent_evidence_cards import (
    build_agent_evidence_cards,
    build_agent_tool_policy_audit,
)
from app.agent_presentation import (
    build_agent_suggested_questions,
    extract_agent_stock_mentions,
)

class LimitUpEvent(BaseModel):
    """One stock's daily limit-up or failed limit-up event."""

    symbol: str = Field(examples=["600519"], description="股票代码")
    name: str = Field(examples=["贵州茅台"], description="股票名称")
    trade_date: date = Field(description="涨停事件交易日")
    first_limit_time: time = Field(description="首次封板时间")
    last_limit_time: time = Field(description="最后封板时间")
    seal_count: int = Field(description="封板次数")
    break_count: int = Field(description="开板/炸板次数；不是连板高度")
    closed_limit: bool = Field(description="收盘是否封住涨停")
    board_height: int = Field(description="连板高度，1为首板")
    amount: float = Field(description="成交金额（元）")
    turnover_rate: float = Field(description="换手率（%）")
    industry: str = Field(description="所属行业")
    concept: str = Field(description="所属概念/题材")
    next_open_pct: float = Field(description="次日开盘涨跌幅（%）")
    next_high_pct: float = Field(description="次日最高价涨跌幅（%）")
    next_close_pct: float = Field(description="次日收盘涨跌幅（%）")
    three_day_return_pct: float = Field(description="后续3日累计涨跌幅（%）")
    five_day_return_pct: float = Field(description="后续5日累计涨跌幅（%）")
    continued_next_day: bool = Field(description="次日是否继续涨停")


class MarketIndexSnapshot(BaseModel):
    """Compact index snapshot used by the dashboard."""

    name: str
    symbol: str
    trade_date: date
    close: float
    change_pct: float
    trend: list[float]
    source: str


class MarketIndexTrendPoint(BaseModel):
    """One completed close in a major-index trend window."""

    trade_date: date
    close: float
    change_pct: float | None = None


class MarketIndexTrendItem(BaseModel):
    """One major index's objective performance over a requested window."""

    name: str = Field(description="指数名称")
    symbol: str = Field(description="指数代码")
    start_date: date = Field(description="区间第一个交易日")
    end_date: date = Field(description="区间最后一个交易日")
    start_close: float = Field(description="区间首个收盘点位")
    end_close: float = Field(description="区间最后收盘点位")
    change_pct: float | None = Field(default=None,
        description="截止交易日相对上一交易日的点位变化（%）；不是区间收益")
    return_pct: float = Field(description="区间累计点位涨跌幅（%）")
    max_drawdown_pct: float = Field(description="区间内峰谷最大回撤（%）")
    positive_days: int = Field(description="区间上涨交易日数")
    negative_days: int = Field(description="区间下跌交易日数")
    points: list[MarketIndexTrendPoint] = Field(description="逐日收盘序列，每项含trade_date、close、change_pct")
    source: str = Field(description="指数数据来源")


class MarketIndexTrendFacts(BaseModel):
    """Tool-ready trend facts for the main A-share indices."""

    requested_days: int = Field(description="请求的交易日窗口长度")
    requested_end_date: date = Field(description="请求的截止交易日")
    data_as_of: date = Field(description="实际数据截止交易日")
    data_fresh: bool = Field(description="数据是否覆盖请求的截止交易日")
    indices: list[MarketIndexTrendItem] = Field(description="三大指数走势")


class MarketCalendarFacts(BaseModel):
    """Current A-share trading-calendar status for Agent tools."""

    as_of: datetime = Field(description="上海时区当前时间（ISO 8601，含时区）")
    today: date = Field(description="上海时区当前日期")
    latest_closed_trade_date: date | None = Field(
        description=(
            "最新已收盘交易日；交易日历覆盖不足时为今天之前最近一个已知交易日，"
            "并需结合calendar_verified和data_missing解释"
        )
    )
    previous_trade_date: date | None = Field(
        default=None, description="latest_closed_trade_date之前的最近交易日"
    )
    next_trade_date: date | None = Field(
        default=None,
        description="latest_closed_trade_date之后的最近交易日；日历未验证未来日期时为null",
    )
    session_status: Literal[
        "pre_open", "open", "closed", "non_trading_day", "unverified"
    ] = Field(description="今天状态：未开盘、开市中、已收盘、非交易日或无法验证")
    close_time: str = Field(description="收盘时间，固定为15:00")
    calendar_source: str = Field(description="交易日历数据来源")
    calendar_verified: bool = Field(
        description="今天是否为交易日以及是否已收盘的结论是否已由日历验证"
    )
    calendar_coverage_start: date | None = Field(
        default=None, description="本次交易日历覆盖的首个日期"
    )
    calendar_coverage_end: date | None = Field(
        default=None, description="本次交易日历覆盖的最后一个日期"
    )
    local_data_through: date | None = Field(
        default=None,
        description="本地涨停事件数据最新日期；仅表示数据覆盖，不是交易日历结论",
    )
    data_missing: list[str] = Field(
        default_factory=list, description="交易日历缺口或验证警告"
    )


class SectorHistoryPoint(BaseModel):
    """One completed daily point for an industry sector."""

    trade_date: date
    close: float
    change_pct: float | None = None


class SectorRankingItem(BaseModel):
    """Compact sector ranking row from the latest market snapshot."""

    sector_name: str = Field(description="板块名称")
    rank: int = Field(description="当前目录中的涨跌幅排名")
    change_pct: float = Field(description="板块涨跌幅（%）")
    amount_yi: float | None = Field(default=None, description="板块成交额（亿元）")
    up_count: int | None = Field(default=None, description="板块内上涨家数")
    down_count: int | None = Field(default=None, description="板块内下跌家数")
    leader_name: str | None = Field(default=None, description="领涨股名称")
    leader_change_pct: float | None = Field(default=None, description="领涨股涨跌幅（%）")


class SectorPerformanceFacts(BaseModel):
    """Tool-ready industry performance, breadth, ranking and recent trend facts."""

    requested_sector: str | None = Field(default=None, description="请求的板块名")
    sector_name: str | None = Field(default=None, description="解析后的板块名")
    sector_type: Literal["industry", "concept", "industry_group"] = Field(default="industry", description="板块类型：industry、concept或industry_group")
    trade_date: date = Field(description="请求的观察交易日")
    data_as_of: date = Field(description="实际数据截止交易日")
    data_fresh: bool = Field(description="数据是否覆盖请求日期")
    previous_trade_date: date | None = Field(default=None, description="用于涨跌对比的前一交易日")
    covered_sector_count: int | None = Field(default=None, description="本次排名覆盖的板块数")
    ranking_complete: bool | None = Field(default=None, description="排名目录是否完整覆盖目标日")
    ranking_universe: str | None = Field(default=None, description="排名目录口径说明")
    data_missing: list[str] = Field(default_factory=list, description="数据缺口")
    rank: int | None = Field(default=None, description="指定板块的排名")
    sector_count: int = Field(description="排名目录板块总数")
    change_pct: float | None = Field(default=None, description="指定板块涨跌幅（%）")
    amount_yi: float | None = Field(default=None, description="指定板块成交额（亿元）")
    net_inflow_yi: float | None = Field(default=None, description="指定板块资金净流入（亿元）")
    up_count: int | None = Field(default=None, description="指定板块上涨家数")
    down_count: int | None = Field(default=None, description="指定板块下跌家数")
    leader_name: str | None = Field(default=None, description="指定板块领涨股")
    leader_price: float | None = Field(default=None, description="领涨股价格（元）")
    leader_change_pct: float | None = Field(default=None, description="领涨股涨跌幅（%）")
    return_5d_pct: float | None = Field(default=None, description="指定板块5日区间收益（%）")
    return_20d_pct: float | None = Field(default=None, description="指定板块20日区间收益（%）")
    top_sectors: list[SectorRankingItem] = Field(default_factory=list, description="涨跌幅靠前板块")
    bottom_sectors: list[SectorRankingItem] = Field(default_factory=list, description="涨跌幅靠后板块")
    matched_sectors: list[SectorRankingItem] = Field(default_factory=list, description="名称匹配的板块")
    history: list[SectorHistoryPoint] = Field(default_factory=list, description="指定板块逐日历史，每项含trade_date、close、change_pct")
    sources: list[str] = Field(default_factory=list, description="数据来源")


class SectorStockTrendItem(BaseModel):
    """One sector constituent ranked by deterministic close-based trend facts."""

    rank: int = Field(description="本次趋势比较排名")
    symbol: str = Field(description="股票代码")
    name: str = Field(description="股票名称")
    trend_score: float = Field(description="趋势综合分")
    trend: Literal["rising", "oscillating", "falling", "insufficient"] = Field(description="趋势标签：rising、oscillating、falling或insufficient")
    data_as_of: date = Field(description="该股数据截止交易日")
    latest_close: float = Field(description="最新收盘价（元）")
    return_5d_pct: float | None = Field(default=None, description="5日区间涨跌幅（%）")
    return_20d_pct: float | None = Field(default=None, description="20日区间涨跌幅（%）")
    ma5: float | None = Field(default=None, description="5日均线（元）")
    ma10: float | None = Field(default=None, description="10日均线（元）")
    ma20: float | None = Field(default=None, description="20日均线（元）")
    volume_ratio_5d: float | None = Field(default=None, description="近5日量比")
    max_drawdown_pct: float | None = Field(default=None, description="区间最大回撤（%）")


class SectorStockRankingFacts(BaseModel):
    """A bounded, auditable trend ranking for one industry or concept."""

    requested_sector: str = Field(description="请求的板块名")
    sector_name: str = Field(description="解析后的板块名")
    sector_category: Literal["industry", "concept"] = Field(description="板块类别：industry或concept")
    sector_thscode: str = Field(description="板块数据源标识")
    requested_days: int = Field(description="趋势比较窗口")
    requested_limit: int = Field(description="请求返回数量")
    data_as_of: date = Field(description="数据截止交易日")
    member_count: int = Field(description="板块成分股总数")
    analyzed_count: int = Field(description="完成趋势比较的股票数")
    missing_count: int = Field(description="因数据缺失未比较的股票数")
    truncated_count: int = Field(description="因数量上限未返回的股票数")
    items: list[SectorStockTrendItem] = Field(default_factory=list, description="趋势排名")
    sources: list[str] = Field(default_factory=list, description="数据来源")
    warnings: list[str] = Field(default_factory=list, description="覆盖范围说明")


class FinanceNewsItem(BaseModel):
    """One timestamped item from a structured financial-news feed."""

    title: str = Field(description="新闻标题")
    summary: str = Field(description="新闻摘要")
    published_at: datetime = Field(description="发布时间")
    source: str = Field(description="来源")
    url: str = Field(description="原文链接")
    category: str = Field(description="新闻类别")
    relevance_score: float = Field(description="与查询主题的相关性分数")


class FinanceNewsFacts(BaseModel):
    """Recent financial-news facts aggregated for an Agent answer."""

    query: str | None = Field(default=None, description="主题词；仅用于排序加权")
    fetched_at: datetime = Field(description="抓取时间")
    window_hours: int = Field(description="按抓取时间回溯的小时数")
    sources: list[str] = Field(default_factory=list, description="聚合来源")
    items: list[FinanceNewsItem] = Field(default_factory=list, description="去重后的财经快讯")


class FinanceNewsPage(BaseModel):
    """One page from a structured financial-news feed."""

    fetched_at: datetime
    window_hours: int = 24
    sources: list[str] = Field(default_factory=list)
    page: int
    page_size: int
    total: int
    total_pages: int
    items: list[FinanceNewsItem] = Field(default_factory=list)
    refreshing: bool = False
    stale: bool = False
    refresh_error: str | None = None


class StockNewsItem(BaseModel):
    """One stock-specific article normalized from a structured provider."""

    symbol: str = Field(description="股票代码")
    name: str = Field(description="股票名称")
    title: str = Field(description="新闻标题")
    summary: str = Field(description="新闻摘要")
    published_at: datetime = Field(description="发布时间")
    source: str = Field(description="来源")
    url: str = Field(description="原文链接")
    item_type: str = Field(description="条目类型，如新闻或公告")
    relevance_score: float = Field(description="相关性分数")
    fetched_at: datetime = Field(description="抓取时间")


class StockNewsFacts(BaseModel):
    """Recent news evidence for one resolved A-share stock."""

    symbol: str = Field(description="股票代码")
    name: str = Field(description="股票名称")
    fetched_at: datetime = Field(description="抓取时间")
    window_days: int = Field(description="回溯自然日数")
    cache_status: str = Field(description="缓存状态")
    sources: list[str] = Field(default_factory=list, description="新闻来源")
    items: list[StockNewsItem] = Field(default_factory=list, description="个股新闻")
    data_missing: list[str] = Field(default_factory=list, description="数据缺口")


class RecommendationFinancialReport(BaseModel):
    """Latest available quarterly report attached to one recommendation."""

    fiscal_year: int
    fiscal_period: str
    report_date: date
    period_end: date
    operating_income: float | None = None
    net_profit: float | None = None
    parent_holder_net_profit: float | None = None
    basic_eps: float | None = None
    operating_income_yoy_pct: float | None = None
    net_profit_yoy_pct: float | None = None
    fetched_at: datetime
    source: str = "hithink-finance"


class RecommendationIntelligenceItem(BaseModel):
    """Latest mutable evidence and bounded adjustments for one candidate."""

    strategy: Literal["relay"] = "relay"
    base_trade_date: date
    symbol: str
    name: str
    sector: str = ""
    position_label: str | None = None
    rule_rank: int = 0
    rule_score: float = 0
    base_rank: int = 0
    rank: int
    base_score: float
    draft_score: float = 0
    facts_cutoff_at: datetime | None = None
    close_information_adjustment: float = 0
    close_information_reasons: list[str] = Field(default_factory=list)
    news_adjustment: float = 0
    financial_adjustment: float = 0
    dragon_tiger_adjustment: float = 0
    popularity_adjustment: float = 0
    dynamic_adjustment: float = 0
    dragon_tiger_on_list: bool = False
    dragon_tiger_is_new: bool = False
    dragon_tiger_net_buy_amount: float | None = None
    dragon_tiger_source: str | None = None
    popularity_base_rank: int | None = None
    popularity_rank: int | None = None
    popularity_rank_change: int | None = None
    popularity_snapshot_at: datetime | None = None
    popularity_source: str | None = None
    update_reasons: list[str] = Field(default_factory=list)
    current_price: float | None = None
    change_pct: float | None = None
    turnover: float | None = None
    quote_captured_at: datetime | None = None
    latest_news: list[StockNewsItem] = Field(default_factory=list)
    financial_report: RecommendationFinancialReport | None = None
    refreshed_at: datetime
    data_missing: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def populate_legacy_draft_fields(cls, value: Any) -> Any:
        """Map pre-draft snapshots onto the current ranking contract."""

        if isinstance(value, dict):
            payload = dict(value)
            payload.setdefault("base_rank", payload.get("rank", 0))
            payload.setdefault("draft_score", payload.get("base_score", 0))
            payload.setdefault("rule_rank", payload.get("base_rank", 0))
            payload.setdefault("rule_score", payload.get("base_score", 0))
            payload.setdefault(
                "dynamic_adjustment",
                round(
                    float(payload.get("draft_score", 0))
                    - float(payload.get("base_score", 0)),
                    1,
                ),
            )
            return payload
        return value


class RecommendationDisplayContext(BaseModel):
    """Describe the latest refresh when showing an older candidate snapshot."""

    is_history_fallback: bool
    latest_target_trade_date: date | None
    latest_stage: Literal["draft", "final", "missed_cutoff"]
    latest_refreshed_at: datetime
    latest_warnings: list[str] = Field(default_factory=list)


class RecommendationIntelligenceResponse(BaseModel):
    """Current draft, pre-open final, or explicit missed-cutoff state."""

    refresh_id: str
    refreshed_at: datetime
    interval_minutes: int
    stage: Literal["draft", "final", "missed_cutoff"] = "draft"
    target_trade_date: date | None = None
    finalized_at: datetime | None = None
    prediction_provenance: dict[str, Any] = Field(default_factory=dict)
    relay_pool_size: int = 0
    relay_display_limit: int = 10
    popularity_coverage_count: int = 0
    status: Literal["complete", "partial"]
    relay_base_date: date | None = None
    items: list[RecommendationIntelligenceItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    display_context: RecommendationDisplayContext | None = None


class StockKLineBar(BaseModel):
    """Daily OHLCV bar for stock detail review."""

    trade_date: date = Field(description="交易日")
    open: float = Field(description="开盘价（元）")
    close: float = Field(description="收盘价（元）")
    high: float = Field(description="最高价（元）")
    low: float = Field(description="最低价（元）")
    volume: float = Field(description="成交量，沿用来源单位；不是成交金额")
    amount: float = Field(default=0, description="成交额（元）")
    source: str = Field(default="unknown", description="该条K线的数据来源")
    change_pct: float | None = Field(default=None, description="来源返回的单日涨跌幅；缺失时不得用未复权收盘价变动率代替（%）")
    previous_close: float | None = Field(default=None, description="前一交易日收盘价（元）")
    previous_trade_date: date | None = Field(default=None, description="前一交易日")
    change_pct_basis: Literal["source"] | None = Field(default=None, description="change_pct的来源口径")
    close_to_close_pct: float | None = Field(default=None,
        description="相邻有效记录的未复权收盘价变动率（%）；基准见previous_close，不等于来源日涨跌幅change_pct")


class StockKLineFacts(BaseModel):
    """Tool-ready K-line facts and derived trend indicators for one stock."""

    symbol: str = Field(description="股票代码")
    requested_days: int = Field(description="请求的观察交易日数")
    requested_end_date: date = Field(description="请求的截止交易日")
    data_as_of: date = Field(description="实际数据截止交易日")
    data_fresh: bool = Field(description="数据是否覆盖请求截止日")
    trend: Literal["rising", "falling", "oscillating", "insufficient"] = Field(description="均线排列标签：rising、falling、oscillating或insufficient")
    latest_close: float = Field(description="最新收盘价（元）")
    return_5d_pct: float | None = Field(default=None, description="5个交易日区间涨跌幅（%）")
    return_10d_pct: float | None = Field(default=None, description="10个交易日区间涨跌幅（%）")
    return_20d_pct: float | None = Field(default=None, description="20个交易日区间涨跌幅（%）")
    ma5: float | None = Field(default=None, description="5日均线（元）")
    ma10: float | None = Field(default=None, description="10日均线（元）")
    ma20: float | None = Field(default=None, description="20日均线（元）")
    volume_ratio_5d: float | None = Field(default=None, description="近5日量比")
    max_drawdown_pct: float | None = Field(default=None, description="展示窗口内峰谷最大回撤（%）")
    sources: list[str] = Field(default_factory=list, description="K线来源")
    bars: list[StockKLineBar] = Field(default_factory=list, description="日K线")
    data_missing: list[str] = Field(default_factory=list, description="数据缺口")


class StockActivityFacts(BaseModel):
    """After-close activity evidence assembled for one A-share stock."""

    symbol: str = Field(description="股票代码")
    name: str = Field(description="股票名称")
    fetched_at: datetime = Field(description="汇总抓取时间")
    data_as_of: date | None = Field(default=None, description="K线数据截止交易日")
    kline: StockKLineFacts | None = Field(default=None, description="K线事实，字段含义同stock_kline")
    recent_limit_up_events: list[dict[str, Any]] = Field(default_factory=list, description="近期涨停记录，每项含trade_date、board_height、closed_limit、first_limit_time、break_count、industry、concept")
    rating_context: dict[str, Any] = Field(default_factory=dict, description="首板评分补充事实，含流通市值、量比、20日涨幅、龙虎榜和人氣排名")
    news: StockNewsFacts = Field(description="个股新闻，字段含义同stock_news")
    data_missing: list[str] = Field(default_factory=list, description="数据缺口")


class StockIntradayKLineBar(BaseModel):
    """Intraday OHLCV bar for after-close trading-day review."""

    timestamp: datetime
    open: float
    close: float
    high: float
    low: float
    volume: float
    amount: float


class StockIntradayHistoryDay(BaseModel):
    """One requested trading day's intraday series and completeness state."""

    trade_date: date
    previous_close: float | None = None
    status: Literal["complete", "missing", "error"]
    bars: list[StockIntradayKLineBar] = Field(default_factory=list)
    error: str | None = None


class StockIntradayHistoryResponse(BaseModel):
    """A bounded multi-day intraday series for stock-detail visualization."""

    symbol: str
    requested_days: int
    period_minutes: int
    start_date: date
    end_date: date
    data_as_of: date | None = None
    complete: bool
    missing_trade_dates: list[date] = Field(default_factory=list)
    days: list[StockIntradayHistoryDay] = Field(default_factory=list)


class StockCloseSnapshot(BaseModel):
    """Latest available daily close snapshot for stock detail display."""

    symbol: str
    trade_date: date
    close: float
    previous_close: float | None = None
    change: float | None = None
    change_pct: float | None = None
    volume: float
    source: str


class ConceptHeat(BaseModel):
    """Limit-up and failed-count summary for one concept or topic."""

    name: str
    limit_up_count: int
    failed_count: int


class MarketSummary(BaseModel):
    """Objective dashboard facts for the latest persisted trading day."""

    trade_date: date
    limit_up_count: int
    first_board_count: int
    continued_board_count: int
    failed_count: int
    unsealed_count: int = 0
    unsealed_rate: float = 0.0
    limit_down_count: int | None = None
    limit_down_source: str | None = None
    failed_limit_up_rate: float
    max_board_height: int
    total_amount: float
    hot_industries: list[str]
    hot_concepts: list[ConceptHeat]
    indices: list[MarketIndexSnapshot]


class DragonTigerReviewItem(BaseModel):
    """One deduplicated stock row for the post-close Dragon-Tiger review."""

    symbol: str
    name: str
    change_pct: float | None = None
    buy_amount: float | None = None
    sell_amount: float | None = None
    net_buy_amount: float | None = None
    net_rate: float | None = None
    organization_net_buy_amount: float | None = None
    hot_money_net_buy_amount: float | None = None
    hot_rank: int | None = None
    range_days: int | None = None
    limit_reason: str | None = None
    concepts: list[str] = Field(default_factory=list)
    detail_trade_date: date | None = None


class DragonTigerReviewResponse(BaseModel):
    """Post-close Dragon-Tiger list summary shown in the review workspace."""

    trade_date: date
    source: str
    stock_count: int
    net_inflow_count: int
    net_outflow_count: int
    organization_count: int
    hot_money_count: int
    items: list[DragonTigerReviewItem] = Field(default_factory=list)


class ContinuationStat(BaseModel):
    """Continuation probability bucket grouped by board height."""

    board_height: int
    sample_size: int
    continued_count: int
    probability: float


class BoardPromotionBucket(BaseModel):
    """One board-height cohort observed across two adjacent trading dates."""

    from_board_height: int
    to_board_height: int
    sample_size: int
    promoted_count: int
    probability: float


class BoardPromotionStock(BaseModel):
    """One stock that closed at limit-up on two dates and advanced one board."""

    symbol: str
    name: str
    industry: str
    concept: str
    from_board_height: int
    to_board_height: int
    first_limit_time: time
    break_count: int
    previous_close: float | None = None
    open_price: float | None = None
    open_gap_pct: float | None = None
    open_type: Literal["high", "flat", "low"] | None = None


class DailyBoardPromotionStat(BaseModel):
    """Daily empirical promotion rates based on two adjacent trading dates."""

    trade_date: date = Field(description="本次晋级观察交易日")
    previous_trade_date: date = Field(description="用于分母的前一交易日")
    sample_size: int = Field(description="前一交易日收盘涨停股票数")
    promoted_count: int = Field(description="本次连板高度增加一板的股票数")
    probability: float = Field(description="总晋级率，0至1比例；展示百分数时乘100")
    first_board_sample_size: int = Field(description="前一交易日首板样本数")
    first_board_promoted_count: int = Field(description="首板晋级二板股票数")
    first_board_probability: float | None = Field(default=None, description="首板到二板晋级率，0至1比例；展示百分数时乘100")
    continued_board_sample_size: int = Field(description="前一交易日连板样本数")
    continued_board_promoted_count: int = Field(description="连板继续晋级股票数")
    continued_board_probability: float | None = Field(default=None, description="连板梯队晋级率，0至1比例；展示百分数时乘100")
    buckets: list[BoardPromotionBucket] = Field(description="按板数分组的晋级桶，每项含from_board_height、to_board_height、sample_size、promoted_count、probability")
    promoted_stocks: list[BoardPromotionStock] = Field(description="晋级成功股票，每项含symbol、name、industry、concept、from_board_height、to_board_height、first_limit_time、break_count及开盘表现")
    first_board_opening_evaluable_count: int = Field(default=0, description="首板晋级中可评价次日开盘类型的样本数")
    first_board_high_open_count: int = Field(default=0, description="首板晋级中高开样本数")
    first_board_flat_open_count: int = Field(default=0, description="首板晋级中平开样本数")
    first_board_low_open_count: int = Field(default=0, description="首板晋级中低开样本数")
    first_board_opening_missing_symbols: list[str] = Field(default_factory=list, description="缺少次日开盘数据、未参与开盘类型统计的股票代码")


class DailyBoardPromotionReport(BaseModel):
    """Promotion observations and explicit calendar/data coverage limits."""

    items: list[DailyBoardPromotionStat] = Field(default_factory=list)
    latest_event_date: date | None = None
    warnings: list[str] = Field(default_factory=list)


class FailedRateStat(BaseModel):
    """Intraday break-rate bucket grouped by board height."""

    board_height: int
    sample_size: int
    failed_count: int
    failed_rate: float


class PostPerformanceStat(BaseModel):
    """Average post-limit-up performance bucket grouped by board height."""

    board_height: int
    sample_size: int
    avg_next_open_pct: float
    avg_next_high_pct: float
    avg_next_close_pct: float
    avg_five_day_return_pct: float

class FirstBoardFeature(BaseModel):
    """Persisted first-board feature row used by scoring and backtesting."""

    trade_date: date
    symbol: str
    name: str
    first_limit_minutes: int
    first_limit_bucket: str
    break_count: int
    seal_count: int
    turnover_rate: float
    turnover_bucket: str
    amount: float
    amount_log: float
    amount_bucket: str
    industry: str
    concept: str
    same_industry_limit_up_count: int
    same_concept_limit_up_count: int
    market_limit_up_count: int
    market_first_board_count: int
    market_failed_limit_up_rate: float
    market_failed_rate_bucket: str
    market_max_board_height: int
    closed_limit: bool
    feature_version: str
    created_at: datetime


class StockDailyBar(BaseModel):
    """Persisted daily K-line bar used for post-first-board outcome review."""

    symbol: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    change_pct: float | None = None
    source: str
    created_at: datetime


class FirstBoardOutcome(BaseModel):
    """Derived post-first-board outcome summary for one historical case."""

    base_trade_date: date
    symbol: str
    next_trade_date: date | None = None
    next_open_pct: float | None = None
    next_high_pct: float | None = None
    next_close_pct: float | None = None
    next_open_to_high_pct: float | None = None
    next_open_to_low_pct: float | None = None
    next_open_to_close_pct: float | None = None
    three_day_high_pct: float | None = None
    three_day_close_pct: float | None = None
    max_drawdown_3d: float | None = None
    three_day_open_to_high_pct: float | None = None
    three_day_open_to_close_pct: float | None = None
    max_drawdown_from_next_open_3d: float | None = None
    promoted_to_second_board: bool
    next_day_ready: bool = False
    three_day_ready: bool = False
    outcome_ready: bool
    outcome_version: str
    created_at: datetime


class OutcomeCompletenessDate(BaseModel):
    """Maturity and exact-date cache coverage for one prediction batch."""

    trade_date: date
    prediction_source: Literal["live", "historical_backtest"]
    candidate_count: int
    elapsed_post_trade_days: int
    d1_mature: bool
    d1_expected_count: int
    d1_ready_count: int
    d1_missing_symbols: list[str] = Field(default_factory=list)
    d3_mature: bool
    d3_expected_count: int
    d3_ready_count: int
    d3_missing_symbols: list[str] = Field(default_factory=list)
    d5_mature: bool
    d5_expected_count: int
    d5_ready_count: int
    d5_missing_symbols: list[str] = Field(default_factory=list)
    status: Literal["complete", "partial", "pending"]


class OutcomeCompletenessReport(BaseModel):
    """Unified acceptance report for recent immutable Top10 outcomes."""

    as_of_date: date | None
    status: Literal["healthy", "partial", "missing", "pending"]
    prediction_trade_date_count: int
    tracked_prediction_count: int
    d1_expected_count: int
    d1_ready_count: int
    d3_expected_count: int
    d3_ready_count: int
    d5_expected_count: int
    d5_ready_count: int
    missing_case_count: int
    dates: list[OutcomeCompletenessDate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_by: str


class FirstBoardFilterResult(BaseModel):
    """Candidate-pool filter audit for one latest-day first-board event."""

    symbol: str
    name: str
    included: bool
    position_label: str | None = None
    excluded_reasons: list[str]
    data_missing: list[str]


StockPositionRegime = Literal[
    "oversold_rebound",
    "v_reversal",
    "low_base_breakout",
    "mid_base_breakout",
    "trend_acceleration",
    "high_consolidation",
    "high_breakout",
    "second_wave",
    "unclassified",
]


class StockPositionMatch(BaseModel):
    """One candidate regime and its deterministic match score."""

    regime: StockPositionRegime
    label: str
    score: float


class StockPositionAssessment(BaseModel):
    """Point-in-time position classification derived from pre-board K-lines."""

    primary: StockPositionMatch
    alternatives: list[StockPositionMatch] = Field(default_factory=list)
    confidence: float
    tags: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    metrics: dict[str, float | None] = Field(default_factory=dict)
    bar_count: int
    classifier_version: str


class StockDetailMarketData(BaseModel):
    """Consolidated local-first market data for one stock detail page."""

    symbol: str
    requested_days: int
    data_as_of: date
    kline: list[StockKLineBar] = Field(default_factory=list)
    latest_close: StockCloseSnapshot
    position_trade_date: date | None = None
    position: StockPositionAssessment | None = None


class FirstBoardEnrichmentSnapshot(BaseModel):
    """Point-in-time enrichment facts available after the first-board close."""

    trade_date: date
    symbol: str
    kline_bar_count: int = 0
    return_5d_pct: float | None = None
    return_20d_pct: float | None = None
    return_60d_pct: float | None = None
    distance_20d_high_pct: float | None = None
    distance_60d_high_pct: float | None = None
    volume_ratio_5d: float | None = None
    volatility_20d: float | None = None
    close_above_ma20: bool | None = None
    ma_alignment: str = "unknown"
    listing_date: date | None = None
    listing_age_days: int | None = None
    float_market_cap: float | None = None
    float_market_cap_source: str | None = None
    recent_limit_up_count_20d: int = 0
    recent_limit_up_count_60d: int = 0
    industry_first_board_count: int = 0
    industry_continued_board_count: int = 0
    industry_failed_count: int = 0
    industry_max_board_height: int = 0
    industry_first_limit_rank: int | None = None
    previous_first_board_promotion_rate: float | None = None
    market_first_board_seal_rate: float | None = None
    dragon_tiger_on_list: bool = False
    dragon_tiger_net_buy_amount: float | None = None
    dragon_tiger_buy_amount: float | None = None
    dragon_tiger_sell_amount: float | None = None
    dragon_tiger_reason: str | None = None
    dragon_tiger_source: str | None = None
    popularity_rank: int | None = None
    popularity_rank_change: int | None = None
    popularity_snapshot_at: datetime | None = None
    popularity_source: str | None = None
    position: StockPositionAssessment | None = None
    data_missing: list[str] = Field(default_factory=list)
    feature_version: str
    created_at: datetime


class FirstBoardCandidateFacts(BaseModel):
    """Structured facts used to rate one first-board limit-up candidate."""

    symbol: str
    name: str
    trade_date: date
    first_limit_time: time
    last_limit_time: time
    seal_count: int
    break_count: int
    closed_limit: bool
    is_one_word_board: bool = False
    board_height: int
    amount: float
    turnover_rate: float
    industry: str
    concept: str
    same_industry_limit_up_count: int
    same_concept_limit_up_count: int
    market_limit_up_count: int
    market_first_board_count: int
    market_failed_limit_up_rate: float
    market_max_board_height: int
    enrichment: FirstBoardEnrichmentSnapshot | None = None
    data_missing: list[str] = Field(default_factory=list)


class ScoreBreakdownItem(BaseModel):
    """One explainable scoring dimension in the first-board rating engine."""

    name: str
    score: float
    max_score: float
    evidence: list[str]


class FirstBoardRating(BaseModel):
    """Explainable first-board quality rating for one candidate."""

    facts: FirstBoardCandidateFacts
    score: float
    rating: Literal["A", "B", "C", "D"]
    confidence: float
    score_breakdown: list[ScoreBreakdownItem]
    reasons: list[str]
    risks: list[str]


class FirstBoardRatingsResponse(BaseModel):
    """API response for a trade date's first-board candidate ratings."""

    trade_date: date
    candidates: list[FirstBoardRating]
    filtered_out: list[FirstBoardFilterResult]
    universe_count: int
    generated_by: str
    snapshot_source: Literal["live", "historical_backtest", "calculated"] = "calculated"
    data_as_of: date | None = None
    snapshot_created_at: datetime | None = None
    snapshot_top_limit: int | None = Field(default=None, description="固化批次记录的候选保留上限；从快照表读取，不根据候选数量猜测")
    prediction_provenance: dict[str, Any] = Field(default_factory=dict)


class ScoringPolicy(BaseModel):
    """Versioned first-board scoring weights and promotion metadata."""

    version: str = Field(description="策略版本")
    parent_version: str | None = Field(default=None, description="父策略版本")
    status: Literal["champion", "challenger", "archived"] = Field(description="策略状态：champion、challenger或archived")
    factor_weights: dict[str, float] = Field(description="评分因子权重，总和为100")
    source: Literal["default", "optimizer", "manual"] = Field(default="default", description="策略来源：default、optimizer或manual")
    rationale: list[str] = Field(default_factory=list, description="策略说明")
    training_start_date: date | None = Field(default=None, description="训练起始日")
    training_end_date: date | None = Field(default=None, description="训练截止日")
    created_at: datetime = Field(description="创建时间")
    activated_at: datetime | None = Field(default=None, description="启用时间")

    @model_validator(mode="after")
    def validate_weights(self) -> "ScoringPolicy":
        """Require finite non-negative weights whose total is 100 points."""

        if not self.factor_weights:
            raise ValueError("factor_weights cannot be empty")
        if any(
            not isfinite(value) or value < 0
            for value in self.factor_weights.values()
        ):
            raise ValueError("factor weights must be finite and non-negative")
        if abs(sum(self.factor_weights.values()) - 100.0) > 0.05:
            raise ValueError("factor weights must sum to 100")
        return self


class ScoringPolicyMetrics(BaseModel):
    """Out-of-sample Top-K ranking metrics for one scoring policy."""

    policy_version: str
    trade_date_count: int
    pool_sample_size: int
    top_sample_size: int
    pool_return_sample_size: int = 0
    top_return_sample_size: int = 0
    top_k: int
    avg_next_open_to_close_pct: float | None = None
    positive_rate: float | None = None
    avg_three_day_open_to_close_pct: float | None = None
    avg_max_drawdown_from_next_open_3d: float | None = None
    promoted_to_second_board_rate: float | None = None
    pool_promoted_to_second_board_rate: float | None = None
    promotion_rate_lift: float | None = None
    large_loss_rate: float | None = None
    avg_next_open_to_low_pct: float | None = None
    pool_avg_next_open_to_close_pct: float | None = None
    excess_next_open_to_close_pct: float | None = None
    objective_score: float | None = None


class ScoringPolicyComparison(BaseModel):
    """Champion-versus-challenger validation and promotion decision."""

    champion: ScoringPolicyMetrics
    challenger: ScoringPolicyMetrics
    objective_delta: float | None = None
    positive_rate_delta: float | None = None
    drawdown_delta: float | None = None
    promotion_rate_delta: float | None = None
    promotion_lift_delta: float | None = None
    large_loss_rate_delta: float | None = None
    promotion_eligible: bool
    gate_reasons: list[str] = Field(default_factory=list)


class ScoringWalkForwardFold(BaseModel):
    """One expanding-window validation and out-of-sample test fold."""

    fold_index: int
    train_dates: list[date]
    validation_dates: list[date]
    test_dates: list[date]
    selected_strength: float
    champion_metrics: ScoringPolicyMetrics
    challenger_metrics: ScoringPolicyMetrics


class ScoringPolicyOptimizationResponse(BaseModel):
    """One constrained scoring-policy optimization run."""

    run_id: str
    champion_policy: ScoringPolicy
    challenger_policy: ScoringPolicy
    train_dates: list[date]
    validation_dates: list[date]
    test_dates: list[date]
    factor_correlations: dict[str, float]
    target_correlations: dict[str, dict[str, float]] = Field(default_factory=dict)
    target_weights: dict[str, float] = Field(default_factory=dict)
    walk_forward_folds: list[ScoringWalkForwardFold] = Field(default_factory=list)
    comparison: ScoringPolicyComparison
    activated: bool = False
    warnings: list[str] = Field(default_factory=list)
    generated_by: str


class ScoringPolicyRegistryResponse(BaseModel):
    """Current champion and recent registered scoring policies."""

    champion: ScoringPolicy
    policies: list[ScoringPolicy]
    generated_by: str


class PredictionQualityCohort(BaseModel):
    """Prediction coverage for one source or scoring-version cohort."""

    dimension: Literal["prediction_source", "scoring_version"]
    value: str
    row_count: int
    unique_stock_date_count: int
    trade_date_count: int
    next_day_ready_count: int
    next_day_coverage_rate: float


class PredictionDateCoverage(BaseModel):
    """Outcome maturity and cache coverage for one prediction date."""

    trade_date: date
    candidate_count: int
    top_count: int
    next_day_ready_count: int
    three_day_ready_count: int
    next_day_coverage_rate: float
    three_day_coverage_rate: float
    next_day_mature: bool
    three_day_mature: bool
    status: Literal["complete", "partial", "pending", "not_mature"]


class PredictionBenchmarkMetrics(BaseModel):
    """Comparable performance metrics for one deterministic ranking baseline."""

    benchmark: str
    label: str
    trade_date_count: int
    sample_size: int
    avg_next_open_to_close_pct: float | None = None
    positive_rate: float | None = None
    promoted_to_second_board_rate: float | None = None
    large_loss_rate: float | None = None
    avg_three_day_open_to_close_pct: float | None = None
    avg_max_drawdown_from_next_open_3d: float | None = None
    excess_vs_ready_pool_pct: float | None = None


class PredictionQualityPolicyStatus(BaseModel):
    """Current policy-governance readiness summarized for the audit panel."""

    champion_version: str
    latest_challenger_version: str | None = None
    latest_optimizer_version: str | None = None
    promotion_eligible: bool | None = None
    outcome_ready_trade_dates: int
    required_trade_dates: int
    readiness_rate: float
    gate_reasons: list[str] = Field(default_factory=list)


class PredictionQualityAuditResponse(BaseModel):
    """Auditable prediction coverage, benchmark and policy-readiness report."""

    start_date: date = Field(description="审计起始日")
    end_date: date = Field(description="审计截止日")
    latest_trade_date: date = Field(description="本地最新交易日")
    audited_scoring_version: str = Field(description="被审计评分版本")
    top_k: int = Field(description="Top K口径")
    raw_prediction_rows: int = Field(description="原始预测行数")
    audited_prediction_rows: int = Field(description="纳入审计的预测行数")
    canonical_prediction_count: int = Field(description="去重后的预测数")
    cross_cohort_duplicate_rows: int = Field(description="跨来源/版本重复行数")
    data_as_of_violation_count: int = Field(description="数据时点违规行数")
    prediction_trade_date_count: int = Field(description="预测交易日数")
    next_day_mature_trade_date_count: int = Field(description="次日结果成熟交易日数")
    complete_next_day_trade_date_count: int = Field(description="次日结果完整交易日数")
    next_day_outcome_coverage_rate: float = Field(description="次日结果覆盖率（%）")
    three_day_outcome_coverage_rate: float = Field(description="3日结果覆盖率（%）")
    cohorts: list[PredictionQualityCohort] = Field(description="按来源或评分版本分组的覆盖")
    date_coverage: list[PredictionDateCoverage] = Field(description="逐预测日的结果成熟度")
    benchmarks: list[PredictionBenchmarkMetrics] = Field(description="确定性基线表现")
    benchmark_cohorts: dict[str, list[PredictionBenchmarkMetrics]] = Field(default_factory=dict, description="按分组拆分的基线")
    time_cohort_counts: dict[str, int] = Field(default_factory=dict, description="时间分组计数")
    excluded_time_prediction_count: int = Field(default=0, description="因时间不可验证被排除的预测数")
    strict_forward_prediction_count: int = Field(default=0, description="严格前向预测数")
    policy_status: PredictionQualityPolicyStatus = Field(description="Champion/Challenger晋级状态")
    findings: list[str] = Field(description="审计发现")
    recommendations: list[str] = Field(description="改进建议")
    warnings: list[str] = Field(default_factory=list, description="边界警告")
    generated_by: str = Field(description="生成来源")


class RatingBacktestBucket(BaseModel):
    """Aggregated post-board outcome metrics for one rating bucket."""

    rating: str
    sample_size: int
    outcome_ready_count: int
    avg_next_open_pct: float | None = None
    avg_next_high_pct: float | None = None
    avg_next_close_pct: float | None = None
    avg_next_open_to_high_pct: float | None = None
    avg_next_open_to_close_pct: float | None = None
    avg_next_open_to_low_pct: float | None = None
    avg_three_day_high_pct: float | None = None
    avg_three_day_close_pct: float | None = None
    avg_three_day_open_to_close_pct: float | None = None
    avg_max_drawdown_from_next_open_3d: float | None = None
    next_open_to_close_positive_rate: float | None = None
    next_open_to_close_large_loss_rate: float | None = None
    promoted_to_second_board_rate: float | None = None


class RatingBacktestFailureSample(BaseModel):
    """High-rated candidate whose post-board outcome was weak."""

    symbol: str
    name: str
    trade_date: date
    rating: str
    score: float
    next_close_pct: float | None = None
    next_open_to_close_pct: float | None = None
    next_open_to_low_pct: float | None = None
    three_day_close_pct: float | None = None
    three_day_open_to_close_pct: float | None = None
    promoted_to_second_board: bool
    reasons: list[str]
    risks: list[str]


class RatingBacktestResponse(BaseModel):
    """Self-evaluation summary for first-board ratings over a date range."""

    start_date: date = Field(description="回测起始日")
    end_date: date = Field(description="回测截止日")
    trade_dates: list[date] = Field(description="回测交易日")
    sample_size: int = Field(description="样本数")
    outcome_ready_count: int = Field(description="后续结果可用样本数")
    buckets: list[RatingBacktestBucket] = Field(description="按A/B/C/D分桶的表现，含样本数、次日和3日收益、晋级率")
    failure_samples: list[RatingBacktestFailureSample] = Field(description="高评分但后续表现较弱的样本，含symbol、name、trade_date、rating、score、收益和原因")
    observations: list[str] = Field(description="评分自我评价")
    warnings: list[str] = Field(default_factory=list, description="边界警告")
    generated_by: str = Field(description="生成来源")


class FactorSignalDiagnosticRow(BaseModel):
    """One factor's date-aware cross-sectional signal diagnostic."""

    factor_key: str
    factor_name: str
    sample_size: int
    trade_date_count: int
    mean_daily_ic: float | None = None
    median_daily_ic: float | None = None
    daily_ic_positive_rate: float | None = None
    p_value: float | None = None
    significant_after_bonferroni: bool
    tercile_trade_date_count: int
    top_tercile_count: int
    bottom_tercile_count: int
    top_tercile_mean_outcome: float | None = None
    bottom_tercile_mean_outcome: float | None = None
    tercile_spread_pct: float | None = None
    direction: str


class FactorSignalLassoSummary(BaseModel):
    """Date-blocked joint signal summary across the scoring factors."""

    sample_size: int
    lasso_alpha: float
    alpha_max: float
    retained_factor_count: int
    retained_factor_keys: list[str]
    ols_r2: float | None = None
    ols_adjusted_r2: float | None = None
    blocked_oos_r2: float | None = None
    blocked_oos_trade_date_count: int = 0
    blocked_oos_mean_daily_ic: float | None = None
    blocked_oos_ic_p_value: float | None = None
    joint_signal_detected: bool = False
    bootstrap_iterations: int
    bootstrap_max_retention_rate: float | None = None
    coefficients: dict[str, float] = Field(default_factory=dict)
    bootstrap_retention_rates: dict[str, float] = Field(default_factory=dict)
    note: str


class FactorSignalDiagnosticResponse(BaseModel):
    """Date-aware falsification diagnostic for the 14 scoring factors."""

    start_date: date
    end_date: date
    scoring_version: str
    outcome_measure: str
    trade_date_count: int
    sample_size: int
    bonferroni_alpha: float
    factors: list[FactorSignalDiagnosticRow]
    lasso: FactorSignalLassoSummary
    strongest_factor_key: str | None = None
    verdict_status: Literal[
        "insufficient_sample",
        "no_robust_signal",
        "signal_requires_validation",
    ]
    verdict: str
    caveats: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_by: str


class ScoringErrorCase(BaseModel):
    """One promoted miss or non-promoted Top-K selection used for diagnosis."""

    trade_date: date
    symbol: str
    name: str
    rank: int
    score: float
    promoted_to_second_board: bool
    next_open_to_close_pct: float | None = None
    leading_factors: list[str] = Field(default_factory=list)


class ScoringFactorErrorDiagnostic(BaseModel):
    """Error-slice and ablation evidence for one scoring factor."""

    factor_key: str
    factor_name: str
    false_positive_mean_score: float | None = None
    false_negative_mean_score: float | None = None
    false_negative_minus_false_positive: float | None = None
    ablation_top_promotion_rate: float | None = None
    ablation_delta: float | None = None
    recommendation: Literal["increase", "decrease", "neutral"]
    evidence: str


class ScoringErrorDiagnosticResponse(BaseModel):
    """Promotion-first diagnosis of Top-K mistakes and factor ablations."""

    start_date: date
    end_date: date
    scoring_version: str
    top_k: int
    trade_date_count: int
    pool_sample_size: int
    top_sample_size: int
    top_promoted_count: int
    top_promotion_rate: float | None = None
    market_promoted_count: int
    market_promotion_rate: float | None = None
    promotion_rate_delta: float | None = None
    false_positive_count: int
    false_negative_count: int
    false_positive_samples: list[ScoringErrorCase] = Field(default_factory=list)
    false_negative_samples: list[ScoringErrorCase] = Field(default_factory=list)
    factors: list[ScoringFactorErrorDiagnostic] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_by: str


class FirstBoardCriticResponse(BaseModel):
    """Critic review for one first-board rating without changing the score."""

    symbol: str = Field(description="股票代码")
    name: str = Field(description="股票名称")
    trade_date: date = Field(description="首板交易日")
    rating: str = Field(description="原评级")
    score: float = Field(description="原评分")
    original_confidence: float = Field(description="原置信度")
    suggested_confidence: float = Field(description="建议置信度")
    confidence_delta: float = Field(description="建议置信度变化")
    verdict: Literal["supportive", "cautious", "fragile"] = Field(description="复核结论：supportive、cautious或fragile")
    support_evidence: list[str] = Field(description="支持证据")
    counter_evidence: list[str] = Field(description="反证")
    missing_data: list[str] = Field(description="缺失数据")
    critic_warnings: list[str] = Field(description="复核警告")
    review_questions: list[str] = Field(description="需要继续核实的问题")
    generated_by: str = Field(description="生成来源")


class AgentPrediction(BaseModel):
    """Persisted first-board rating snapshot for later evaluation."""

    prediction_id: str
    trade_date: date
    symbol: str
    name: str
    score: float
    rating: str
    confidence: float
    scoring_version: str
    prediction_source: Literal["live", "historical_backtest"]
    data_as_of: date
    facts_json: dict[str, Any]
    reasons: list[str]
    risks: list[str]
    created_at: datetime
    prediction_provenance: dict[str, Any] = Field(default_factory=dict)


class AgentEvaluationItem(BaseModel):
    """Post-outcome evaluation for one persisted first-board prediction."""

    prediction_id: str = Field(description="预测记录ID")
    trade_date: date = Field(description="预测交易日")
    symbol: str = Field(description="股票代码")
    name: str = Field(description="股票名称")
    score: float = Field(description="首板评分")
    rating: str = Field(description="首板评级")
    confidence: float = Field(description="评级置信度")
    prediction_source: Literal["live", "historical_backtest"] = Field(description="预测来源：live或historical_backtest")
    data_as_of: date = Field(description="预测数据截止日")
    time_cohort: str = Field(default="unverified", description="时间分组")
    scoring_version: str | None = Field(default=None, description="评分版本")
    evaluation_label: Literal[
        "success",
        "partial",
        "miss",
        "avoid_success",
        "false_negative",
        "pending",
    ] = Field(description="评价标签：success、partial、miss、avoid_success、false_negative或pending")
    outcome_ready: bool = Field(description="后续结果是否可用")
    promoted_to_second_board: bool = Field(description="是否晋级二板")
    next_high_pct: float | None = Field(default=None, description="次日最高价涨跌幅（%）")
    next_close_pct: float | None = Field(default=None, description="次日收盘涨跌幅（%）")
    next_open_to_high_pct: float | None = Field(default=None, description="次日开盘到最高涨跌幅（%）")
    next_open_to_low_pct: float | None = Field(default=None, description="次日开盘到最低涨跌幅（%）")
    next_open_to_close_pct: float | None = Field(default=None, description="次日开盘到收盘涨跌幅（%）")
    three_day_high_pct: float | None = Field(default=None, description="3日最高价涨跌幅（%）")
    three_day_close_pct: float | None = Field(default=None, description="3日收盘涨跌幅（%）")
    three_day_open_to_close_pct: float | None = Field(default=None, description="3日开盘到收盘涨跌幅（%）")
    max_drawdown_from_next_open_3d: float | None = Field(default=None, description="次日开盘后3日最大回撤（%）")
    lesson: str = Field(description="该样本的经验总结")
    scoring_suggestion: str = Field(description="评分改进建议")


class AgentEvaluationResponse(BaseModel):
    """Evaluation Agent summary for persisted first-board predictions."""

    start_date: date = Field(description="评价起始日")
    end_date: date = Field(description="评价截止日")
    prediction_count: int = Field(description="预测记录数")
    outcome_ready_count: int = Field(description="结果可用记录数")
    source_counts: dict[str, int] = Field(description="按预测来源计数")
    time_cohort_counts: dict[str, int] = Field(default_factory=dict, description="按时间分组计数")
    excluded_time_prediction_count: int = Field(default=0, description="因时间不可验证被排除的记录数")
    label_counts: dict[str, int] = Field(description="按评价标签计数")
    evaluations: list[AgentEvaluationItem] = Field(description="逐条评价")
    summary: list[str] = Field(description="评价摘要")
    warnings: list[str] = Field(default_factory=list, description="边界警告")
    generated_by: str = Field(description="生成来源")


class ReviewAgentPick(BaseModel):
    """One high-score first-board pick reviewed by the Review Agent."""

    trade_date: date
    symbol: str
    name: str
    concept: str = ""
    position_label: str | None = None
    score: float
    rating: str
    confidence: float
    prediction_source: Literal["live", "historical_backtest"] = Field(description="记录生成来源：live为日常运行固化，不能据此认定盘前终选；historical_backtest为历史补算。前向资格单独看time_cohort。")
    data_as_of: date
    time_cohort: str = Field(default="unverified", description="独立时间审计分组；只有premarket_final具有盘前终选前向验证资格，close_baseline仅为收盘基线。")
    scoring_version: str | None = None
    evaluation_label: str
    outcome_ready: bool
    promoted_to_second_board: bool
    next_high_pct: float | None = None
    next_close_pct: float | None = None
    next_open_to_high_pct: float | None = None
    next_open_to_low_pct: float | None = None
    next_open_to_close_pct: float | None = None
    three_day_high_pct: float | None = None
    three_day_close_pct: float | None = None
    three_day_open_to_close_pct: float | None = None
    max_drawdown_from_next_open_3d: float | None = None
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    post_bars: list["ReviewAgentPostBar"] = Field(default_factory=list)
    expected_post_bar_count: int = 0
    post_bar_cache_complete: bool = False


class ReviewAgentPostBar(BaseModel):
    """One cached daily bar after a reviewed first-board pick."""

    trade_date: date
    trading_day_offset: int | None = Field(default=None, ge=0)
    open: float
    high: float
    low: float
    close: float
    change_pct: float | None = None
    return_from_base_pct: float | None = None


class ReviewPromotionComparison(BaseModel):
    """One prediction day's Top10 first-to-second promotion benchmark."""

    trade_date: date
    next_trade_date: date | None = None
    outcome_ready: bool
    top_pick_sample_size: int
    top_pick_promoted_count: int
    top_pick_promotion_rate: float | None = None
    market_first_board_sample_size: int
    market_promoted_count: int
    market_promotion_rate: float | None = None
    promotion_rate_delta: float | None = None


class ReviewFeatureValue(BaseModel):
    """One group's observed feature value and its own denominator."""

    text: str
    detail: str = ""
    valid_count: int = Field(ge=0)
    sample_size: int = Field(ge=0)


class ReviewFeatureCard(BaseModel):
    """A deterministic comparison between positive and negative tracked returns."""

    key: Literal["position", "market_cap", "first_seal"]
    label: str
    positive: ReviewFeatureValue
    negative: ReviewFeatureValue
    observation: str


class ReviewFeatureSummary(BaseModel):
    positive_count: int = Field(ge=0)
    negative_count: int = Field(ge=0)
    cards: list[ReviewFeatureCard] = Field(default_factory=list)


class ReviewAgentReportResponse(BaseModel):
    """LLM tool-driven review report for high-score first-board picks."""

    generation_mode: Literal["llm", "deterministic", "legacy"] = Field(default="legacy", description="生成模式：llm、deterministic或legacy")
    llm_model: str | None = Field(default=None, description="生成使用的模型")
    generation_note: str | None = Field(default=None, description="生成方式说明")
    review_digest: ReviewDigest | None = Field(default=None, description="结构化复盘摘要")
    feature_summary: ReviewFeatureSummary | None = Field(default=None, description="确定性特征对比")
    feature_research: ReviewFeatureResearch | None = Field(default=None, description="候选与市场特征研究")
    summary_insights: list[ReviewInsight] = Field(default_factory=list, description="研究摘要要点")
    summary_headline: str | None = Field(default=None, description="研究摘要标题")
    start_date: date = Field(description="固化候选批次起始日，包含当日")
    end_date: date = Field(description="固化候选批次结束日，包含当日；后续观察截止另见as_of_date")
    as_of_date: date | None = Field(default=None, description="后续走势观察截止日，独立于候选批次范围；旧快照未保存时为null")
    sample_size: int = Field(description="复盘样本数")
    prediction_source_counts: dict[str, int] = Field(default_factory=dict, description="记录生成来源计数；live不等于盘前终选，与time_cohort_counts独立")
    evaluation_label_counts: dict[str, int] = Field(default_factory=dict, description="全部评价标签的互斥计数，合计等于sample_size；不与收益画像分组交叉相加")
    grouping_definitions: dict[str, str] = Field(default_factory=dict, description="评价标签、观察窗收益画像、摘要截止收益三套独立分组的定义，不得合并计数或相互套用阈值")
    horizon_coverage: dict[str, Any] = Field(default_factory=dict, description="固定候选范围内逐日及整体D+1/3/5覆盖、成熟/缺价/未成熟/未知日历数量与精确收盘收益；只包含requested_follow_days以内的期限，未请求的期限不计缺价")
    time_cohort_counts: dict[str, int] = Field(default_factory=dict, description="时间分组计数")
    time_audit_status: str = Field(default="unverified", description="时间审计状态")
    excluded_time_prediction_count: int = Field(default=0, description="因时间不可验证被排除的预测数")
    success_count: int = Field(description="评价标签success数量：A/B评级且次日开盘到收盘收益>0；不是摘要优秀组")
    failed_count: int = Field(description="评价标签miss数量：A/B评级且次日开盘到收盘收益<0；不是摘要较差组")
    partial_count: int = Field(default=0, description="评价标签partial数量：A/B评级且次日开盘到收盘收益=0；不与其他分组相加")
    pending_count: int = Field(description="结果待定样本数")
    promotion_ready_date_count: int = Field(default=0, description="可比较晋级率的交易日数")
    top_pick_promotion_sample_size: int = Field(default=0, description="Top票晋级率分母")
    top_pick_promoted_count: int = Field(default=0, description="Top票晋级数")
    top_pick_promotion_rate: float | None = Field(default=None, description="Top票晋级率（%）")
    market_promotion_sample_size: int = Field(default=0, description="全市场首板晋级率分母")
    market_promoted_count: int = Field(default=0, description="全市场首板晋级数")
    market_promotion_rate: float | None = Field(default=None, description="全市场首板晋级率（%）")
    promotion_rate_delta: float | None = Field(default=None, description="Top票相对全市场晋级率差（%）")
    promotion_comparisons: list[ReviewPromotionComparison] = Field(default_factory=list, description="逐日晋级率对照")
    main_findings: list[str] = Field(default_factory=list, description="主要发现")
    successful_patterns: list[str] = Field(default_factory=list, description="成功模式")
    failed_patterns: list[str] = Field(default_factory=list, description="失败模式")
    scoring_bias: list[str] = Field(default_factory=list, description="评分偏差")
    adjustment_suggestions: list[str] = Field(default_factory=list, description="评分调整建议")
    confidence: float = Field(description="复盘结论置信度")
    reviewed_picks: list[ReviewAgentPick] = Field(default_factory=list, description="逐票复盘明细")
    tool_results: list["AgentToolTrace"] = Field(default_factory=list, description="复盘工具调用轨迹")
    warnings: list[str] = Field(default_factory=list, description="边界警告")
    generated_by: str = Field(description="生成来源")


class DailyReviewSnapshot(BaseModel):
    """Immutable after-close review artifact for one market data date."""

    as_of_date: date
    start_date: date
    report: ReviewAgentReportResponse
    generated_by: str
    generated_at: datetime


class DailyReviewSnapshotSummary(BaseModel):
    """Compact history entry used by the review date selector."""

    as_of_date: date
    start_date: date
    sample_size: int
    time_audit_status: str = "unverified"
    outcome_ready_count: int
    top_pick_promotion_rate: float | None = None
    market_promotion_rate: float | None = None
    generated_by: str
    generated_at: datetime


class DailyReviewSnapshotsResponse(BaseModel):
    """Available persisted review dates ordered from newest to oldest."""

    snapshots: list[DailyReviewSnapshotSummary] = Field(default_factory=list)
    generated_by: str


class ChatSessionMessage(BaseModel):
    """Persisted user or assistant message inside one chat session."""

    message_id: str
    session_id: str
    role: Literal["user", "assistant"]
    content: str
    status: Literal["success", "error"] = "success"
    run_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ChatSessionMemory(BaseModel):
    """Persisted rolling context for one owner-scoped chat session."""

    session_id: str
    owner_id: str
    memory_version: str
    summary: str = ""
    research_goal: str = ""
    stock_symbols: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    date_scope: str | None = None
    constraints: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    summarized_message_count: int = 0
    last_message_id: str | None = None
    generation_mode: Literal[
        "llm_function_call",
        "prompt_json_fallback",
        "deterministic",
    ] = "deterministic"
    model: str | None = None
    created_at: datetime
    updated_at: datetime


class ChatSessionSummary(BaseModel):
    """List-friendly metadata for one resumable Agent conversation."""

    session_id: str
    owner_id: str
    title: str
    message_count: int = 0
    last_message_preview: str | None = None
    created_at: datetime
    updated_at: datetime


class ChatSessionDetail(ChatSessionSummary):
    """One chat session with messages ordered from oldest to newest."""

    messages: list[ChatSessionMessage] = Field(default_factory=list)
    active_run: dict[str, Any] | None = None


class ChatSessionsResponse(BaseModel):
    """Active chat sessions for the current local user."""

    sessions: list[ChatSessionSummary]
    generated_by: str


class ChatSessionCreateRequest(BaseModel):
    """Optional title supplied when creating a conversation."""

    title: str | None = Field(default=None, max_length=80)


class ChatSessionUpdateRequest(BaseModel):
    """Editable conversation metadata."""

    title: str = Field(min_length=1, max_length=80)


class AgentChatRequest(BaseModel):
    """User chat request with optional page context."""

    session_id: str
    message_id: str | None = None
    message: str
    intent_hint: str | None = None
    trade_date: date | None = None
    symbol: str | None = None
    page_context: dict[str, str] = Field(default_factory=dict)


class AgentChatPerformance(BaseModel):
    """Measured latency and prompt sizes for one LLM-backed chat turn."""

    planner_duration_ms: int = 0
    tool_duration_ms: int = 0
    answer_duration_ms: int = 0
    total_duration_ms: int = 0
    planner_prompt_chars: int = 0
    answer_prompt_chars: int = 0


class AgentEvidenceCard(BaseModel):
    """User-facing evidence extracted from raw Agent tool traces."""

    title: str
    kind: Literal[
        "execution",
        "market",
        "candidate_pool",
        "limit_up_events",
        "rating",
        "critic",
        "evaluation",
        "data_availability",
        "tool",
    ]
    status: Literal["success", "error", "skipped"] = "success"
    summary: str
    facts: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    source_tools: list[str] = Field(default_factory=list)


class AgentToolPolicyAudit(BaseModel):
    """Planner-vs-final audit for one Agent response."""

    planner_tool_calls: list[str] = Field(default_factory=list)
    final_tool_calls: list[str] = Field(default_factory=list)
    graph_compiled_tools: list[str] = Field(default_factory=list)
    backend_repaired_tools: list[str] = Field(default_factory=list)
    policy_repaired_tools: list[str] = Field(default_factory=list)
    repair_reasons: list[str] = Field(default_factory=list)
    safety_fallback_used: bool = False


class AgentStockMention(BaseModel):
    """A stock entity in an Agent answer, grounded by executed tool facts."""

    name: str
    symbol: str
    trade_date: date | None = None


class AgentDisplayBlock(BaseModel):
    """A deterministic table projection derived from tool evidence and memory."""

    kind: Literal["table"] = "table"
    source_tool: str
    source_tool_index: int
    columns: list[str] = Field(default_factory=list)
    column_labels: dict[str, str] = Field(default_factory=dict)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    displayed_count: int = 0
    ordered_symbols: list[str] = Field(default_factory=list)
    missing_columns: list[str] = Field(default_factory=list)
    truncated: bool = False
    display_policy_id: str | None = None


class AgentChatResponse(BaseModel):
    """Tool-grounded chat response from the first-board Agent."""

    session_id: str
    run_id: str | None = None
    trace_url: str | None = None
    memory_action: dict[str, Any] | None = None
    memory_owner_revision: int | None = None
    memory_context_fingerprint: str | None = None
    memory_dependencies: list[dict[str, Any]] = Field(default_factory=list)
    memory_context_used: bool = False
    intent: str
    answer: str
    task_status: Literal["complete", "partial", "empty", "clarify", "refuse", "error", "cancelled"] | None = None
    stop_reason: str | None = None
    stock_mentions: list[AgentStockMention] = Field(default_factory=list)
    tool_calls: list[str] = Field(default_factory=list)
    tool_results: list["AgentToolTrace"] = Field(default_factory=list)
    display_blocks: list[AgentDisplayBlock] = Field(default_factory=list)
    evidence_cards: list[AgentEvidenceCard] = Field(default_factory=list)
    tool_policy: AgentToolPolicyAudit = Field(default_factory=AgentToolPolicyAudit)
    references: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    performance: AgentChatPerformance = Field(default_factory=AgentChatPerformance)
    suggested_questions: list[str] = Field(default_factory=list)
    generated_by: str

    @model_validator(mode="after")
    def populate_agent_ui_fields(self) -> "AgentChatResponse":
        """Build UI evidence and planner audit fields when callers omit them."""

        # This validator runs when the response model is built, including template
        # and early-return paths. Derive UI metadata from the actual traces so all
        # paths share the same evidence cards, stock links and policy audit shape.

        self.answer = sanitize_agent_answer(self.answer)
        if not self.stock_mentions:
            self.stock_mentions = extract_agent_stock_mentions(
                self.answer,
                self.tool_results,
            )
        if not self.evidence_cards:
            self.evidence_cards = build_agent_evidence_cards(self.tool_results, self.warnings)
        if not self.tool_policy.final_tool_calls:
            self.tool_policy = build_agent_tool_policy_audit(
                tool_calls=self.tool_calls,
                tool_results=self.tool_results,
                warnings=self.warnings,
            )
        if not self.suggested_questions:
            self.suggested_questions = build_agent_suggested_questions(
                intent=self.intent,
                stock_mentions=self.stock_mentions,
            )
        return self


class AgentToolOutcome(BaseModel):
    """Uniform data outcome shared by every Agent tool trace."""

    status: Literal["ok", "empty", "partial", "error"]
    data_fresh: bool | None = None
    source_errors: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


AGENT_CONTROL_TRACE_NAMES = {
    "react_decision", "react_policy", "react_observe", "react_execution",
    "react_provider_error", "react_answer_check", "react_compute", "react_fallback_delivery",
    "task_plan", "task_completion", "task_replan", "task_answer", "task_execution",
    "task_answer_repair",
    "task_plan_error", "task_execute_error", "task_check_error", "task_replan_error", "task_answer_error",
    "agent_plan",
    "llm_tool_planner",
    "llm_tool_answer",
    "template_general_answer",
    "query_understanding",
    "routing_decision",
    "complex_graph_plan",
    "complex_graph_step",
    "complex_graph_completion",
    "complex_graph_replan",
}


class AgentToolTrace(BaseModel):
    """Compact trace for one tool execution inside an Agent run."""

    name: str
    input: dict[str, Any] = Field(default_factory=dict)
    summary: str
    status: Literal["success", "error", "skipped"] = "success"
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: int | None = None
    result: AgentToolOutcome | None = None

    @model_validator(mode="after")
    def normalize_result(self) -> "AgentToolTrace":
        """Populate the uniform result envelope for new and persisted traces."""

        if self.result is None and self.name in AGENT_CONTROL_TRACE_NAMES:
            return self
        if self.result is None:
            self.result = _infer_agent_tool_outcome(
                execution_status=self.status,
                output=self.output,
                error=self.error,
            )
        elif self.output != self.result.payload:
            self.output = self.result.payload
        return self


def _infer_agent_tool_outcome(
    *,
    execution_status: str,
    output: dict[str, Any],
    error: str | None,
) -> AgentToolOutcome:
    """Map legacy trace fields into the canonical data-result semantics."""

    if execution_status in {"error", "skipped"}:
        source_errors = [error] if error else []
        return AgentToolOutcome(
            status="error",
            data_fresh=None,
            source_errors=source_errors,
            payload=output,
        )

    source_errors = _agent_tool_source_errors(output)
    data_fresh = output.get("data_fresh")
    if not isinstance(data_fresh, bool):
        data_fresh = None
    empty = _agent_tool_payload_is_empty(output)
    partial = bool(
        source_errors
        or output.get("complete") is False
        or output.get("data_missing")
        or output.get("missing_trade_dates")
    )
    if source_errors and empty:
        result_status = "error"
    elif partial:
        result_status = "partial"
    elif empty:
        result_status = "empty"
    else:
        result_status = "ok"
    return AgentToolOutcome(
        status=result_status,
        data_fresh=data_fresh,
        source_errors=source_errors,
        payload=output,
    )


# Collect source-level errors embedded in a tool payload for the uniform outcome envelope.
def _agent_tool_source_errors(output: dict[str, Any]) -> list[str]:
    raw_errors = output.get("source_errors") or []
    if isinstance(raw_errors, str):
        return [raw_errors]
    if not isinstance(raw_errors, list):
        return []
    return [str(item) for item in raw_errors if str(item).strip()]


# Recognize tool-specific empty payloads so an empty result is not reported as successful
# evidence.
def _agent_tool_payload_is_empty(output: dict[str, Any]) -> bool:
    if not output:
        return True
    # Result-set counts describe the rows returned for this query.  They must win
    # over broader source metadata such as ``stock_count``: a Dragon-Tiger source
    # can contain stocks while the requested candidates still match zero rows.
    for key in ("matched_count", "returned_count"):
        value = output.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value <= 0
    collection_keys = (
        "items",
        "events",
        "candidates",
        "rows",
        "bars",
        "results",
        "promoted_stocks",
        "matches",
    )
    collections = [output[key] for key in collection_keys if key in output]
    if not collections:
        return False
    positive_counts = [
        value
        for key, value in output.items()
        if key.endswith("_count")
        and not key.startswith("requested_")
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value > 0
    ]
    if positive_counts:
        return False
    return all(isinstance(value, list) and not value for value in collections)


class AgentRunSummary(BaseModel):
    """Frontend-friendly summary of a persisted Agent execution."""

    run_id: str
    session_id: str
    status: Literal["success", "error"]
    intent: str | None = None
    message: str
    answer_preview: str | None = None
    tool_calls: list[str] = Field(default_factory=list)
    tool_results: list[AgentToolTrace] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None
    trace_url: str | None = None
    started_at: datetime
    finished_at: datetime
    duration_ms: int


class AgentRunsResponse(BaseModel):
    """Recent Agent execution summaries for observability UI."""

    runs: list[AgentRunSummary]
    generated_by: str


class AgentDataHealthTopCandidate(BaseModel):
    """Health status for one top-rated first-board candidate."""

    symbol: str
    name: str
    score: float
    rating: str
    feature_ready: bool
    enrichment_ready: bool = False


class AgentDataHealthResponse(BaseModel):
    """Health status for Agent data dependencies."""

    trade_date: date | None
    status: Literal["healthy", "partial", "missing"]
    raw_events_ready: bool
    raw_event_count: int
    first_board_features_ready: bool
    first_board_feature_count: int
    enrichment_ready: bool = False
    enrichment_count: int = 0
    top_candidates_checked: int
    top_candidates: list[AgentDataHealthTopCandidate] = Field(default_factory=list)
    outcome_completeness: OutcomeCompletenessReport | None = None
    post_limit_pool_count: int = 0
    post_limit_evaluable_count: int = 0
    post_limit_coverage_ratio: float = 1.0
    post_limit_missing_history_count: int = 0
    post_limit_source_consistent_count: int = 0
    post_limit_pending_symbol_count: int = 0
    post_limit_missing_reasons: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class AgentSystemHealthResponse(BaseModel):
    """System status used by local startup checks and the frontend."""

    status: Literal["healthy", "partial", "missing"]
    current_date: date
    current_time: str
    latest_local_trade_date: date | None
    expected_data_date: date | None
    data_fresh: bool
    data_update_recommended: bool
    data_update_reason: str
    llm_enabled: bool
    llm_provider_configured: bool
    llm_model: str | None = None
    proxy_configured: bool
    proxy_warning: str | None = None
    data_health: AgentDataHealthResponse
    warnings: list[str] = Field(default_factory=list)
    generated_by: str


class AgentRun(BaseModel):
    """Persisted trace for one Agent execution."""

    run_id: str
    session_id: str
    run_type: str
    status: Literal["success", "error"]
    intent: str | None = None
    tool_calls: list[str] = Field(default_factory=list)
    input_json: dict[str, Any]
    output_json: dict[str, Any] | None = None
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime


class AgentUsageRecord(BaseModel):
    """Cost and capacity accounting for one accepted Agent request."""

    usage_id: str
    run_id: str | None = None
    session_id: str
    owner_id: str
    ip_hash: str
    status: Literal["running", "success", "error", "rejected"] = "running"
    model: str | None = None
    llm_call_count: int = 0
    failed_llm_call_count: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    token_usage_complete: bool = False
    planner_prompt_chars: int = 0
    answer_prompt_chars: int = 0
    answer_chars: int = 0
    estimated_cost_usd: float = 0.0
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    error_message: str | None = None


class AgentUsageSummary(BaseModel):
    """Aggregate request and LLM usage for one accounting period."""

    period_started_at: datetime
    request_count: int = 0
    success_count: int = 0
    error_count: int = 0
    running_count: int = 0
    rejected_count: int = 0
    llm_call_count: int = 0
    failed_llm_call_count: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    token_measured_request_count: int = 0
    planner_prompt_chars: int = 0
    answer_prompt_chars: int = 0
    answer_chars: int = 0
    estimated_cost_usd: float = 0.0


class AgentUsageAdminResponse(BaseModel):
    """Administrator-facing usage totals and live concurrency state."""

    usage: AgentUsageSummary
    limits: dict[str, int | float | bool]
    concurrency: dict[str, int]
    generated_by: str


class DailyPipelineRun(BaseModel):
    """Persisted execution record for one automated after-close pipeline run."""

    run_id: str
    trade_date: date
    trigger: Literal["scheduled", "manual", "startup"]
    status: Literal["running", "success", "partial", "error", "skipped"]
    attempt_count: int = 0
    report: dict[str, Any] | None = None
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class DailyPipelineStatusResponse(BaseModel):
    """Latest and recent automated daily pipeline execution records."""

    latest: DailyPipelineRun | None = None
    recent: list[DailyPipelineRun] = Field(default_factory=list)
    generated_by: str
