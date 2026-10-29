"""Ordered contracts for news, stock activity and public web search."""

from app.agents.tool_contracts import AgentToolSchema


NEWS_TOOL_SCHEMAS = [
    AgentToolSchema(
        name="finance_news",
        time_mode="current",
        collection="items",
        notes="Lookback hours from retrieval time, not historical as-of retrieval.",
        description=(
            "聚合东方财富和同花顺的最新财经快讯，返回北京时间、正文摘要、类别和来源。"
            "适合回答泛化的今日/最新财经新闻或市场快讯；具体公司公告使用 stock_news。"
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
        name="stock_news",
        time_mode="current",
        collection="items",
        notes="Lookback calendar days from retrieval time; not historical news as-of.",
        description=(
            "查询一只已明确 A 股的近期个股新闻、公告类报道和监管动态，返回发布时间、来源、摘要和原文链接。"
            "用于指定公司或股票的消息问题，不用于综合财经新闻或行业新闻。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Six-digit A-share symbol or exact stock name.",
                },
                "days": {"type": "integer", "minimum": 1, "maximum": 30},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["symbol"],
        },
        returns="Resolved stock identity and recent deduplicated stock-news items with explicit source and cache status.",
    ),
    AgentToolSchema(
        name="stock_activity",
        time_mode="latest_local_and_current",
        notes="Combines latest available local K-lines with current news. Not historical as-of evidence.",
        description=(
            "汇总一只 A 股的近期动态、涨停记录、评分补充事实和个股新闻。"
            "用于‘最近有什么动态、发生了什么、近况如何’等综合个股问题；"
            "单纯走势、K线、均线、量能、回撤问题请使用 stock_kline。"
            "返回的K线是摘要，逐日K线请使用 stock_kline。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Six-digit A-share symbol or exact stock name.",
                },
                "days": {"type": "integer", "minimum": 1, "maximum": 30},
                "news_limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["symbol"],
        },
        returns="After-close K-line summary, recent limit-up events, rating context and stock-specific news for one resolved stock.",
    ),
]
