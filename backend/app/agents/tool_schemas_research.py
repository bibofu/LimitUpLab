"""Ordered contracts for event, rating and post-limit research queries."""

from app.agents.tool_contracts import AgentToolSchema


RESEARCH_TOOL_SCHEMAS = [
    AgentToolSchema(
        name="first_board_ratings",
        time_mode="historical",
        dates=("trade_date",),
        collection="top_candidates",
        notes="Prefer persisted prediction snapshot; its saved candidates are not necessarily the full eligible rating pool. Read selection_scope and candidate_pool_complete.",
        description=(
            "读取某个交易日的首板评级候选池、可解释评分、行业分布和基于首板前 K 线的"
            "位置分类（如低位启动、超跌反弹、V形反转、高位突破、二波启动）；"
            "优先返回历史固化批次，可能仅保存Top候选而非完整合格池。返回数、硬筛排除数与未入快照数分别说明；"
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
            "First-board ratings and K-line position groups for returned candidates, with explicit "
            "saved snapshot scope, recorded eligibility counts, source completeness and symbol-filter boundaries."
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
                        "symbol",
                        "board_height",
                        "first_limit_time",
                        "amount",
                        "turnover_rate",
                        "break_count",
                        None,
                    ],
                    "description": "Sort key; symbol sorts by the six-digit stock code. Omit for board_height. Existing numeric and time keys retain their meanings.",
                },
                "sort_order": {
                    "type": ["string", "null"],
                    "enum": ["asc", "desc", None],
                    "description": "Omit for ascending symbol/first_limit_time, descending other keys. Explicit asc/desc overrides that default.",
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
        name="recent_limit_up_returns",
        time_mode="historical",
        dates=("end_date",),
        collection="items",
        description=(
            "批量计算本地近期收盘涨停候选池每只股票的近N个交易日收盘价累计涨幅。"
            "候选期默认近7个本地交易日，完整去重后计算，不受涨停名单100条或逐股调用次数限制。"
            "用于近期股票累计涨幅/跌幅筛选和排名；未指定股票池时可提供此明确标注范围的本地结果，"
            "不能将任意行业的小样本当作全部可用候选，也不能声称这是全市场名单。"
        ),
        notes=(
            "All computable candidates are returned; filter and sort return_pct directly in the answer, "
            "without a limit unless requested. N intervals require N+1 shared daily closes. "
            "Preserve cohort scope, price basis and missing coverage in the answer."
        ),
        args_schema={
            "type": "object",
            "properties": {
                "end_date": {"type": ["string", "null"],
                             "description": "YYYY-MM-DD as-of close; omit for latest local event date."},
                "recent_trade_days": {"type": "integer", "minimum": 1, "maximum": 20,
                                      "description": "Candidate event lookback, default 7; distinct from return horizon."},
                "return_trade_days": {"type": "integer", "minimum": 1, "maximum": 60,
                                      "description": "Number of trading-day return intervals, default 5, needing N+1 closes."},
            },
            "required": [],
        },
        returns="All calculable stock rows, explicit price windows, local cohort/calendar coverage and missing candidates.",
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
        name="stock_financials",
        time_mode="retrieved_now",
        collection="rows",
        notes=(
            "Provider quarterly values are year-to-date cumulative, not single-quarter. "
            "single_quarter fields are derived server-side and absent when the previous quarter is missing. "
            "report_date is the provider's report/update date and may differ from the original announcement. "
            "Returns the most recent disclosed reports; it is not an arbitrary historical as-of query."
        ),
        description=(
            "查询单只A股最近若干期已披露的利润表、资产负债表或现金流量表。"
            "适合回答营收、净利润、归母净利润、费用、营业利润、资产总额、负债、现金流等问题。"
            "一次只查一只股票；多只需要分别调用。"
            "quarterly报表同时返回年初至今累计值和单季推算值，回答“某季度”时必须先确认用户指单季还是累计。"
            "返回最近N期，不承诺任意历史截止日；data_as_of为最近报告期截止日。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "单只A股代码或名称，如600519或贵州茅台；多只需要分别调用。",
                },
                "statement": {
                    "type": "string",
                    "enum": ["income", "balance", "cash_flow"],
                    "description": "报表类型：income利润表、balance资产负债表、cash_flow现金流量表。",
                },
                "period": {
                    "type": "string",
                    "enum": ["quarterly", "annual"],
                    "description": "报告频率，默认quarterly；年度请显式传annual。",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "返回最近N期，默认6期。",
                },
            },
            "required": ["symbol", "statement"],
        },
        returns=(
            "Symbol identity, statement type, period basis, currency/unit, source and fetch time, "
            "latest report period, and rows with fiscal period identity plus statement-specific "
            "cumulative and derived single-quarter fields."
        ),
    ),
    AgentToolSchema(
        name="index_search",
        time_mode="retrieved_now",
        collection="rows",
        notes=(
            "Tonghuashun category indices come from index catalog; broad exchange indices "
            "come from symbol search. 沪深300 has both 000300.SH and 399300.SZ; read resolved "
            "together with variants. Constituents never include weights."
        ),
        description=(
            "把指数名称或代码解析为可查询的 thscode，支持宽基、行业、概念、地域和特色指数。"
            "当用户问沪深300、上证50、中证500、科创50、创业板指、半导体、新能源汽车、安徽等指数时，先用本工具确认代码。"
            "返回 resolved、同名 variants 和候选列表；存在多个同等匹配时必须结合 category 或 thscode 再查行情或成分股。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "指数名称或代码，如沪深300、000300.SH、半导体。",
                },
                "category": {
                    "type": ["string", "null"],
                    "enum": [
                        "industry", "cn_concept", "region", "tszs", "broad", "all", None,
                    ],
                    "description": "可选过滤：同花顺行业/概念/地域/特色，或宽基交易所指数。",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "返回候选数，默认20。",
                },
            },
            "required": ["query"],
        },
        returns="Resolved index identity, same-name variants, candidate rows and ambiguity warnings.",
    ),
    AgentToolSchema(
        name="index_quote",
        time_mode="historical_or_live",
        collection="rows",
        notes=(
            "days returns the last N trading days with daily bars; omit days for snapshot only. "
            "data_as_of may be earlier than requested_end_date when that date has no bar. "
            "return_pct and max_drawdown are computed from the returned close series."
        ),
        description=(
            "查询一个指数的当前快照和/或最近若干交易日日线，返回点位、涨跌幅、成交额、区间涨跌、"
            "上涨/下跌天数和最大回撤。适合回答沪深300今天涨跌、上证50最近一个月走势、半导体指数近期表现等问题。"
            "days省略时只返回当前快照；指数不明确时先用 index_search。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "index": {
                    "type": "string",
                    "description": "指数名称或thscode。",
                },
                "days": {
                    "type": ["integer", "null"],
                    "minimum": 2,
                    "maximum": 250,
                    "description": "最近N个交易日；省略则只返回快照。",
                },
                "end_date": {
                    "type": ["string", "null"],
                    "description": "YYYY-MM-DD，日线窗口截止日；省略用最新快照日期。",
                },
            },
            "required": ["index"],
        },
        returns="Resolved index identity, snapshot fields, daily bars and deterministic window statistics.",
    ),
    AgentToolSchema(
        name="index_constituents",
        time_mode="retrieved_now",
        collection="rows",
        notes=(
            "The provider returns only symbol/thscode/name. It does not provide weights, industry "
            "or market cap; never invent them. member_count is the source total; complete=false "
            "means the requested limit truncated the list."
        ),
        description=(
            "查询一个指数的完整成分股名单，支持沪深300、上证50、中证500、科创50及行业/概念/地域/特色指数。"
            "返回代码、名称、总成分股数和是否截断；来源不提供权重、行业和市值，不得自行补造。"
            "指数不明确时先用 index_search。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "index": {
                    "type": "string",
                    "description": "指数名称或thscode。",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2000,
                    "description": "返回上限，默认1000；超过来源总数时返回全部。",
                },
            },
            "required": ["index"],
        },
        returns="Resolved index identity, constituent rows, total/returned counts, truncation and weight-availability boundary.",
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
]
