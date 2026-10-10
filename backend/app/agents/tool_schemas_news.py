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
            "汇总一只 A 股的近期收盘走势、涨停记录、评分补充事实和个股新闻。"
            "用于‘最近有什么动态、发生了什么、近况如何’等综合个股问题。"
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
    AgentToolSchema(
        name="web_search",
        time_mode="retrieved_now",
        collection="items",
        notes="Search retrieval is current; publication date must be checked separately.",
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
