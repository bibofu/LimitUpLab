"""Keep exact internal Agent implementation names out of user-visible text."""


INTERNAL_TOOL_LABELS: dict[str, str] = {
    "market_summary": "市场概况",
    "market_index_trend": "大盘指数走势",
    "daily_board_promotion": "连板晋级统计",
    "sector_performance": "板块行情",
    "sector_stock_ranking": "板块个股走势",
    "hot_stock_ranking": "热股排行",
    "dragon_tiger_list": "龙虎榜数据",
    "remote_limit_up_pool": "同花顺涨停池",
    "finance_news": "财经资讯",
    "stock_news": "个股资讯",
    "stock_activity": "个股近期动态",
    "web_search": "公开信息",
    "first_board_ratings": "首板评级",
    "first_board_filter": "首板筛选结果",
    "market_event_pool": "市场事件名单",
    "limit_up_events": "涨停事件数据",
    "stock_kline": "个股行情",
    "post_limit_screen": "涨停后形态筛选",
    "post_limit_path": "涨停后逐日走势",
    "post_limit_statistics": "涨停后历史统计",
    "first_board_critic": "评分复核",
    "rating_backtest": "评分回测",
    "rating_evaluation": "预测评价",
    "review_high_score_picks": "高分票复盘",
    "prediction_quality_audit": "预测质量审计",
    "scoring_policy_status": "评分策略状态",
    "limit_up_event_dates": "本地交易日数据",
    "llm_tool_planner": "问题分析过程",
    "llm_tool_answer": "回答生成过程",
    "template_general_answer": "本地数据分析",
    "llm_planner_direct_answer": "直接回答",
    "agent_plan": "问题分析过程",
}

def friendly_tool_label(tool_name: str) -> str:
    """Return a business-facing label without exposing an implementation key."""

    return INTERNAL_TOOL_LABELS.get(tool_name, "相关数据")


def sanitize_agent_answer(text: str) -> str:
    """Replace exact implementation identifiers with stable business labels."""

    sanitized = text
    for internal_name, label in INTERNAL_TOOL_LABELS.items():
        sanitized = sanitized.replace(f"`{internal_name}`", label)
        sanitized = sanitized.replace(internal_name, label)
    return sanitized
