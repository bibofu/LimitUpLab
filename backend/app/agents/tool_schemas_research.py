"""Ordered contracts for event, rating and post-limit research queries."""

from app.agents.tool_contracts import AgentToolSchema


RESEARCH_TOOL_SCHEMAS = [
    AgentToolSchema(
        name="first_board_ratings",
        time_mode="historical",
        dates=("trade_date",),
        collection="top_candidates",
        notes="Rating candidate pool is not all limit-up stocks.",
        description=(
            "读取某个交易日的首板评级候选池、可解释评分、行业分布和基于首板前 K 线的"
            "位置分类（如低位启动、超跌反弹、V形反转、高位突破、二波启动）；"
            "未传 trade_date 时使用本地最新交易日，并返回由收盘基础分、最新新闻和"
            "季度财报持续重排的盘前研究排序。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "trade_date": {
                    "type": ["string", "null"],
                    "description": "YYYY-MM-DD; omit or null for latest local trade date.",
                },
                "symbols": {
                    "type": ["array", "null"],
                    "items": {"type": "string", "pattern": "^[0-9]{6}$"},
                    "maxItems": 20,
                    "description": "Optional dynamic stock set produced by a validated prior step.",
                },
            },
            "required": [],
        },
        returns=(
            "First-board candidate ratings, filters, industry distribution and complete "
            "K-line position groups for the rated candidate pool."
        ),
    ),
    AgentToolSchema(
        name="market_event_pool",
        time_mode="historical",
        dates=("trade_date",),
        collection="items",
        notes="Event identities/counts; amount may be unavailable. For amount-sorted local limit-up/failed events use limit_up_events.",
        description=(
            "查询完整交易日的市场价格限制事件，统一支持涨停、跌停和炸板名单。"
            "event_type 必须使用 limit_up、limit_down 或 broken_board；"
            "适合各种关于哪些股票涨停、跌停或炸板的口语化问法。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "event_type": {
                    "type": "string",
                    "enum": ["limit_up", "limit_down", "broken_board"],
                },
                "trade_date": {
                    "type": ["string", "null"],
                    "description": "YYYY-MM-DD; omit for the latest completed local trade date.",
                },
                "market": {
                    "type": ["string", "null"],
                    "enum": [
                        "main_board",
                        "chinext",
                        "star_market",
                        "beijing",
                        None,
                    ],
                },
                "query": {"type": ["string", "null"]},
                "result_mode": {
                    "type": ["string", "null"],
                    "enum": ["list", "count", "summary", "ranking", None],
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["event_type"],
        },
        returns=(
            "A normalized completed-day market event pool with event_type, date, count, "
            "stock names, symbols and available event fields."
        ),
    ),
    AgentToolSchema(
        name="limit_up_events",
        time_mode="historical",
        dates=("trade_date",),
        collection="events",
        notes="Includes amount (CNY), turnover_rate (%), closed_limit. failed means not sealed at close; broken_intraday also includes resealed stocks.",
        description=(
            "查询单日或最近多个交易日的涨停事件，可按市场板块、板数、首板/连板、炸板次数、"
            "行业、题材或股票名称过滤，也可按行业或题材聚合。用户提到主板、创业板、科创板、北交所时，"
            "分别设置 market=main_board、chinext、star_market、beijing。"
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
                "highest_only": {
                    "type": ["boolean", "null"],
                    "description": "Return every stock tied at the highest board height.",
                },
                "market": {
                    "type": ["string", "null"],
                    "enum": [
                        "main_board",
                        "chinext",
                        "star_market",
                        "beijing",
                        None,
                    ],
                    "description": "Exchange board segment; omit for all markets.",
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
                "event_status": {
                    "type": ["string", "null"],
                    "enum": ["closed", "failed", "broken_intraday", "all", None],
                    "description": (
                        "closed=closed limit-up, failed=did not close at limit-up, "
                        "broken_intraday=opened at least once, all=no status filter."
                    ),
                },
                "recent_trade_days": {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Number of latest local trading days to include; defaults to 1.",
                },
                "group_by": {
                    "type": ["string", "null"],
                    "enum": ["industry", "concept", None],
                    "description": "Aggregate matched stocks by industry or concept.",
                },
                "sort_by": {
                    "type": ["string", "null"],
                    "enum": [
                        "board_height",
                        "first_limit_time",
                        "amount",
                        "turnover_rate",
                        "break_count",
                        None,
                    ],
                },
                "sort_order": {
                    "type": ["string", "null"],
                    "enum": ["asc", "desc", None],
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": [],
        },
        returns="Filtered limit-up events with board height, industry, concept, first seal time and break count.",
    ),
    AgentToolSchema(
        name="first_board_filter",
        time_mode="historical",
        dates=("trade_date",),
        collection="items",
        notes="Literal case-insensitive keyword filter over rated name/symbol/industry/concept.",
        adapter="first_board_filter",
        description="在首板候选池中按行业、题材、概念或股票名称筛选候选。",
        args_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Topic, industry, concept or stock-name keyword.",
                },
                "trade_date": {
                    "type": ["string", "null"],
                    "description": "YYYY-MM-DD rating date.",
                },
            },
            "required": ["query"],
        },
        returns="Matched first-board candidates for the query.",
    ),
    AgentToolSchema(
        name="stock_kline",
        time_mode="historical",
        dates=("end_date",),
        collection="bars",
        notes="return_Nd_pct=(last close / close N trading intervals earlier - 1)*100. N displayed bars span N-1 intervals, not N intervals. trend is an MA-based label, not a claim that every recent day rose.",
        description="读取指定股票最近一段时间的日 K 线、均线、区间涨跌、量能和最大回撤，用于回答个股走势问题。",
        args_schema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "One code or exact name. Multiple stocks require independent calls.",
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
        name="post_limit_screen",
        time_mode="historical",
        dates=("data_as_of",),
        collection="candidates",
        adapter="post_limit",
        description=(
            "筛选沪深主板近期涨停后的形态，支持高位大幅回撤、横盘缩量、回撤企稳、"
            "强势不连板、断板修复和2进3观察。用于回答‘有哪些涨停后……的票’，"
            "不要用普通涨停名单替代。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "shape": {"type": "string", "enum": ["high_drawdown", "volume_consolidation", "pullback_stabilizing", "strong_nonconsecutive", "broken_board_repair", "second_to_third"]},
                "shapes": {"type": "array", "items": {"type": "string", "enum": ["high_drawdown", "volume_consolidation", "pullback_stabilizing", "strong_nonconsecutive", "broken_board_repair", "second_to_third"]}},
                "data_as_of": {"type": ["string", "null"]},
                "recent_limit_days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Explicit user-requested event lookback; high-drawdown and volume-consolidation default to 7 trading days.",
                },
                "min_peak_drawdown_pct": {"type": ["number", "null"]},
                "max_volume_ratio": {"type": ["number", "null"]},
                "max_range_pct": {"type": ["number", "null"]},
                "min_anchor_change_pct": {"type": ["number", "null"]},
                "max_anchor_change_pct": {"type": ["number", "null"]},
                "board_height": {"type": ["integer", "null"]},
                "query": {"type": ["string", "null"]},
                "sort_by": {"type": ["string", "null"], "enum": ["peak_drawdown_pct", "volume_ratio", "range_pct", "anchor_change_pct", "anchor_date", "symbol", None]},
                "sort_order": {"type": "string", "enum": ["asc", "desc"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": [],
        },
        returns="Post-limit candidates, applied rules, anchor-relative metrics, coverage, exclusions and missing data.",
    ),
    AgentToolSchema(
        name="post_limit_path",
        time_mode="historical",
        dates=("anchor_date", "data_as_of"),
        collection="path",
        adapter="post_limit",
        description=(
            "分析一只股票从指定或最近一次收盘涨停开始的逐日量价路径，返回涨停锚点、"
            "局部高点、回撤、相对涨停价变化和成交量变化。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "anchor_date": {"type": ["string", "null"]},
                "data_as_of": {"type": ["string", "null"]},
                "recent_limit_days": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["symbol"],
        },
        returns="Annotated daily post-limit path and event-relative metrics for one resolved stock.",
    ),
    AgentToolSchema(
        name="post_limit_statistics",
        time_mode="historical",
        dates=("data_as_of",),
        adapter="post_limit",
        description=(
            "重算涨停后形态的历史描述性统计或形态比较，默认使用最近7个已满足D+5的"
            "信号交易日，返回D+1/D+3/D+5、MAE、MFE、样本覆盖和成熟度。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "shapes": {"type": "array", "items": {"type": "string", "enum": ["high_drawdown", "volume_consolidation", "pullback_stabilizing", "strong_nonconsecutive", "broken_board_repair", "second_to_third"]}},
                "data_as_of": {"type": ["string", "null"]},
                "statistics_days": {"type": "integer", "minimum": 1, "maximum": 30},
                "recent_limit_days": {"type": "integer", "minimum": 1, "maximum": 20},
                "min_peak_drawdown_pct": {"type": ["number", "null"]},
                "max_volume_ratio": {"type": ["number", "null"]},
                "max_range_pct": {"type": ["number", "null"]},
                "min_anchor_change_pct": {"type": ["number", "null"]},
                "max_anchor_change_pct": {"type": ["number", "null"]},
                "board_height": {"type": ["integer", "null"]},
                "query": {"type": ["string", "null"]},
                "group_by": {"type": ["string", "null"], "enum": ["shape", "board_height", "anchor_age", "industry", "concept", "signal_date", None]},
            },
            "required": [],
        },
        returns="Versioned recomputed historical cohorts, outcome statistics, grouping, coverage and sample-quality warning.",
    ),
    AgentToolSchema(
        name="prediction_quality_audit",
        time_mode="historical",
        dates=("start_date", "end_date"),
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
            "required": ["start_date", "end_date"],
        },
        returns=(
            "Source-aware prediction coverage, date maturity, deterministic "
            "baselines, findings and v3 promotion readiness."
        ),
    ),
]
