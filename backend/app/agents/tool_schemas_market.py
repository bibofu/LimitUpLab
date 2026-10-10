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
        name="sector_stock_ranking",
        time_mode="historical",
        dates=("end_date",),
        collection="stocks",
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
        notes="Captured current ranking, not a historical ranking.",
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
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "source": {
                    "type": "string",
                    "enum": ["auto", "tonghuashun", "eastmoney"],
                    "description": (
                        "auto uses Tonghuashun up to Top30 and Eastmoney for Top31-100."
                    ),
                },
                "enrich_performance": {
                    "type": "boolean",
                    "description": "Attach latest quote change for a broad market-environment review.",
                },
            },
            "required": [],
        },
        returns="Tonghuashun hot-stock rank, heat, rank change and capture time.",
    ),
    AgentToolSchema(
        name="dragon_tiger_list",
        time_mode="historical",
        dates=("trade_date",),
        collection="items",
        description=(
            "查询同花顺龙虎榜，可按交易日、机构/游资榜类型和股票名称或代码过滤，"
            "返回买卖额、净买额、机构净买、游资净买、热度排名和相关题材。默认返回完整榜单，"
            "询问龙虎榜情况时不要自行填写 limit；不同 range_days 必须保留，不按股票代码去重。"
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
        returns="Full Tonghuashun Dragon-Tiger List rows and capital-flow evidence; use finish.table directly without reading every preview page.",
    ),
    AgentToolSchema(
        name="remote_limit_up_pool",
        time_mode="historical_or_live",
        dates=("trade_date",),
        collection="items",
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
]
