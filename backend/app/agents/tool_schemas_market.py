"""Ordered contracts for market summaries, rankings and remote event sources."""

from app.agents.tool_contracts import AgentToolSchema


MARKET_TOOL_SCHEMAS = [
    AgentToolSchema(
        name="market_summary",
        time_mode="latest_local",
        notes="Only the latest local market date; not historical.",
        description=(
            "读取本地最新涨停数量、首板数量、未回封数量、最高连板和热门行业等客观市场数据；"
            "市场环境综述可按需补充真实跌停池数量。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "include_limit_down": {
                    "type": "boolean",
                    "description": "Fetch the completed limit-down pool for a broad market review.",
                }
            },
            "required": [],
        },
        returns="Objective market facts for the latest local trade date.",
    ),
    AgentToolSchema(
        name="market_index_trend",
        time_mode="historical",
        dates=("end_date",),
        collection="indices",
        description=(
            "查询上证指数、深证成指和创业板指最近一段交易日的客观走势。"
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
            "按需获取A股行业或概念板块行情。可查询指定板块的涨跌幅、排名、成交额、"
            "资金净流入、上涨/下跌家数、领涨股和近期趋势；sector 为空时返回行业强弱榜。"
            "有日期的行业强弱榜按本次取得的行业指数目录核对目标日及前交易日收盘，完整覆盖才返回涨跌前5。"
            "仅询问当日行业表现或强弱时，日期匹配的完整榜单已足够回答；未要求多日趋势、资金或成分股时，不逐一补查入榜行业。"
            "ranking_complete=false时说明本次排名缺口，其他日期或任意行业抽样不能替代该日完整排名。"
            "Top/Bottom摘要不能证明全市场普涨普跌或板块内部上涨广度，不推断未提供的资金流向、行业分类隶属和领涨股。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "sector": {
                    "type": ["string", "null"],
                    "description": (
                        "Industry or concept name such as 半导体 or AI; "
                        "omit for overall industry ranking."
                    ),
                },
                "trade_date": {
                    "type": ["string", "null"],
                    "description": "YYYY-MM-DD; omit/null to use the latest complete local trading day. Only when no local date exists is an undated current snapshot used.",
                },
            },
            "required": [],
        },
        returns=(
            "Sector change, market rank, breadth, turnover, fund flow, leader, "
            "5/20-day returns where available. Dated overall rankings provide top/bottom five, "
            "previous_trade_date and explicit coverage; ranking_universe describes the current catalog, "
            "not reconstructed historical membership. Historical breadth/flow/leader fields may be unavailable."
        ),
    ),
    AgentToolSchema(
        name="sector_stock_ranking",
        time_mode="historical",
        dates=("end_date",),
        collection="items",
        notes="Constituent coverage may be partial; inspect coverage before market-wide claims.",
        description=(
            "解析同花顺行业或概念板块成分股，并按截至最新完整收盘日的K线趋势排序。"
            "适合回答游戏板块哪些股票走势好、半导体近期强势股等问题；结果是历史趋势比较，"
            "不是未来涨跌预测。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "sector": {
                    "type": "string",
                    "description": "Industry or concept name, such as 游戏 or 半导体.",
                },
                "days": {"type": "integer", "minimum": 5, "maximum": 60},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": (
                        "Return 10 stocks unless the user explicitly asks for "
                        "another count."
                    ),
                },
                "end_date": {
                    "type": ["string", "null"],
                    "description": "YYYY-MM-DD; omit for the latest local trade date.",
                },
            },
            "required": ["sector"],
        },
        returns=(
            "Resolved sector name/category, constituent coverage, data cutoff and ranked "
            "stocks with 5/20-day returns, moving-average trend, volume ratio and drawdown."
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
