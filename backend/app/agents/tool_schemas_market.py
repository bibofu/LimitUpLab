"""Ordered contracts for market summaries, rankings and remote event sources."""

from app.agents.tool_contracts import AgentToolSchema


MARKET_TOOL_SCHEMAS = [
    AgentToolSchema(
        name="trading_calendar",
        time_mode="current",
        notes=(
            "Current calendar status only; does not serve arbitrary historical ranges. "
            "local_data_through is data coverage, not a trading-calendar conclusion."
        ),
        description=(
            "以A股交易日历为准，返回当前时点最新已收盘交易日、上一交易日、"
            "下一交易日和今日交易状态。适合回答‘今天收盘了吗’、‘最新交易日是哪天’、"
            "‘今天是交易日吗’、‘上一交易日是哪天’。15:00为收盘边界；"
            "calendar_verified=false时不得把latest_closed_trade_date表述为已确认结论。"
            "local_data_through只表示本地涨停事件数据覆盖，不等于交易日历。"
        ),
        args_schema={"type": "object", "properties": {}, "required": []},
        returns=(
            "Latest closed trading day, previous/next session, today's session status, "
            "calendar source and verification, local data coverage and explicit missing-data warnings."
        ),
    ),
    AgentToolSchema(
        name="market_index_trend",
        time_mode="historical",
        dates=("end_date",),
        collection="indices",
        notes=(
            "Only closes and daily change_pct; no open/high/low/volume/turnover. "
            "Use index_quote for OHLC/volume, for arbitrary indices, or when the user asks for open prices."
        ),
        description=(
            "查询上证指数、深证成指和创业板指最近一段交易日的客观走势。"
            "只返回收盘点位和涨跌幅，不返回开盘、最高、最低、成交量或成交额；"
            "需要这些字段或查询其他指数时使用 index_quote。"
            "返回区间涨跌、每日收盘点位、上涨/下跌天数和最大回撤；"
            "适合回答大盘、指数、沪指近一周或近期走势问题。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 2, "maximum": 20},
                "end_date": {
                    "type": ["string", "null"],
                    "description": "YYYY-MM-DD; omit for the latest local trade date.",
                },
            },
            "required": [],
        },
        returns=(
            "Major-index closes, period returns, up/down day counts and maximum "
            "drawdowns for a 2-20 trading-day window."
        ),
    ),
    AgentToolSchema(
        name="daily_board_promotion",
        time_mode="historical",
        dates=("end_date",),
        collection="items",
        description=(
            "统计最近若干交易日的涨停晋级率。以前一交易日收盘封住的股票为分母，"
            "按下一交易日是否收盘晋级一板计算总晋级率、首板到二板和连板梯队晋级率，"
            "并返回每个交易日晋级成功的具体股票。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 60},
                "end_date": {
                    "type": ["string", "null"],
                    "description": "YYYY-MM-DD; omit for the latest local trade date.",
                },
            },
            "required": [],
        },
        returns=(
            "Daily promotion observation date, previous trade date, total rate, "
            "first-to-second rate, continued-board rate, board-height buckets and "
            "the promoted stock list."
        ),
    ),
    AgentToolSchema(
        name="sector_performance",
        time_mode="historical_or_live",
        dates=("trade_date",),
        collection="top_sectors",
        description=(
            "查询A股行业或概念板块行情，数据来自收盘后同步到本地数据库的板块日线。"
            "sector 为空时返回指定交易日/最近交易日的行业强弱榜（默认90个同花顺行业，前5/后5）；"
            "category=cn_concept 时返回概念榜（只有涨跌幅和成交额）。"
            "sector 有值时返回该板块的涨跌幅、排名、成交额、资金净流入、上涨/下跌家数、领涨股、5/20日收益和近20日走势；"
            "汽车、军工等宽泛名称按子板块分组返回，并给出等权平均（aggregation=equal_weight），不伪造官方板块指数。"
            "ranking_complete=false 或 data_fresh=false 时必须说明覆盖/日期缺口；历史日期的广度和资金字段可能为 null，不得填零。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "sector": {
                    "type": ["string", "null"],
                    "description": (
                        "Industry or concept name such as 半导体, 汽车 or AI; "
                        "omit for the whole-market ranking."
                    ),
                },
                "trade_date": {
                    "type": ["string", "null"],
                    "description": "YYYY-MM-DD; omit for the latest local trade date.",
                },
                "category": {
                    "type": ["string", "null"],
                    "enum": ["industry", "cn_concept", None],
                    "description": "industry (default) or cn_concept for the concept ranking.",
                },
                "days": {
                    "type": "integer",
                    "minimum": 5,
                    "maximum": 60,
                    "description": "History window for a named sector; default 20.",
                },
            },
            "required": [],
        },
        returns=(
            "Whole-market ranking with top/bottom sectors, coverage and data_as_of; or one "
            "sector's change, rank, breadth, turnover, fund flow, leader, 5/20-day returns and "
            "history. Industry groups return member facts plus an equal-weight aggregate."
        ),
    ),
    AgentToolSchema(
        name="sector_stocks",
        time_mode="retrieved_now",
        collection="items",
        notes=(
            "Current constituent membership from a cached Tonghuashun directory, not historical "
            "membership. The list is not a price or trend ranking."
        ),
        description=(
            "按板块名列出当前成分股代码和名称。行业和概念都支持；"
            "汽车、军工等宽泛名称会把同层级子板块合并为一个分组并按代码去重。"
            "默认只返回20只；只有用户原话明确说“全部/所有/完整名单”时才设置 limit=500，其他情况不要设置大 limit。"
            "truncated=true 时必须说明还有成员未返回。"
            "用于回答“汽车板块有哪些股票”“半导体板块包含哪些公司”；"
            "不提供涨跌排序，需要板块走势或排行时用 sector_performance。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "sector": {
                    "type": "string",
                    "description": "Industry or concept name, such as 汽车 or 半导体.",
                },
                "category": {
                    "type": ["string", "null"],
                    "enum": ["industry", "cn_concept", None],
                    "description": "Optional category when the same name exists in both taxonomies.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "description": "Maximum rows to return; default 20, max 500. truncated=true when reduced.",
                },
                "sort_by": {
                    "type": ["string", "null"],
                    "enum": ["symbol", "name", None],
                    "description": "Row order; default symbol.",
                },
            },
            "required": ["sector"],
        },
        returns=(
            "Resolved sector/group, member_count, row_count, truncated, items with symbol/name, "
            "matched_sectors, data_as_of and sources."
        ),
    ),
    AgentToolSchema(
        name="hot_stock_ranking",
        time_mode="current",
        collection="items",
        notes=(
            "Captured current ranking, not a historical ranking. Provider rank_change has an "
            "unconfirmed comparison time; neither rank_change nor rank_trend can substitute "
            "for previous_trade_day_rank_change."
        ),
        description=(
            "优先查询同花顺当前热股榜，日榜不可用时回退东方财富当前榜并标明来源。"
            "适合回答市场关注度、热门股票、"
            "某只股票当前人气排名等问题；榜单热度不代表投资价值。"
            "rank_change和rank_trend沿用来源变化口径，不能视为相较上一交易日变化；"
            "未查询历史基准时previous_trade_day_rank_change为null。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["day", "hour"],
                    "description": "Ranking period; defaults to day.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "source": {
                    "type": "string",
                    "enum": ["auto", "tonghuashun", "eastmoney"],
                    "description": (
                        "auto prefers Tonghuashun for every requested size; "
                        "if its day ranking is unavailable, falls back to the "
                        "Eastmoney current ranking without mixing provider ranks. "
                        "A short Tonghuashun ranking is returned as incomplete."
                    ),
                },
                "enrich_performance": {
                    "type": "boolean",
                    "description": "Attach latest quote change for a broad market-environment review.",
                },
            },
            "required": [],
        },
        returns=(
            "Provider-labeled current popularity rank, available heat/source-defined rank_change "
            "and capture time; null previous_trade_day_rank/previous_trade_day_rank_change "
            "with explicit metric definitions when the historical baseline was not obtained."
        ),
    ),
    AgentToolSchema(
        name="dragon_tiger_list",
        time_mode="historical",
        dates=("trade_date",),
        collection="items",
        description=(
            "优先查询同花顺龙虎榜；明确交易日的全部榜不可用时回退东方财富。"
            "可按交易日、机构/游资榜类型和股票名称或代码过滤，"
            "返回买卖额、净买额、机构净买、游资净买、热度排名和相关题材。默认返回完整榜单；"
            "查询全榜或汇总统计时 limit 留空，不同 range_days 保留，不按股票代码去重。"
            "overview已确定性计算本次范围的记录数、去重股票数，以及各range_days的已知净买额合计与净买/净卖和机构/游资前5记录。"
            "普通概览直接引用口径匹配的overview，无需重复计算或翻页；"
            "用户另有筛选、统计或表格要求时仍基于完整items计算与交付，不用概览替代明细。"
            "回答范围以用户要求为准：指标或概览不自动附名单；请求名单且未指定列时，"
            "默认展示代码、名称、区间天数、涨跌幅、买入额、卖出额、净买额、机构净买额、游资净买额，金额单位为元。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "trade_date": {
                    "type": ["string", "null"],
                    "description": (
                        "YYYY-MM-DD; omit when the user gives no date. The backend "
                        "pins the request to its latest complete local trading day."
                    ),
                },
                "board_type": {
                    "type": "string",
                    "enum": ["all", "org", "hot_money"],
                },
                "query": {
                    "type": ["string", "null"],
                    "description": "Optional exact/partial stock name or six-digit symbol.",
                },
                "limit": {"type": ["integer", "null"], "minimum": 1, "maximum": 1000,
                          "description": "Omit/null for the complete list; set only when the user requests a bounded list."},
            },
            "required": [],
        },
        returns="Returned provider-labeled records plus a deterministic, date/filter/window-scoped overview; completeness is explicit in source_truncated/complete_matching_query. Reuse matching overview metrics; compute only additional requested statistics. For requested lists use the returned items directly in the answer.",
    ),
]
