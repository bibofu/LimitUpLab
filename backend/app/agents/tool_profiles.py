"""Deterministic tool exposure profiles for the ReAct runner.

The model sees only the tools relevant to the current page and question.
Profiles are intentionally explicit and stable so prompt caching still works;
an unrecognised request falls back to a broad general profile instead of the
full catalog.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.agents.tools import V1_CLOSED_MARKET_TOOL_NAMES


STOCK_CODE = re.compile(r"(?<!\d)\d{6}(?!\d)")

STOCK_TERMS = (
    "这只股", "该股", "个股", "股票代码", "代码", "股价", "k线", "均线", "走势",
    "财务", "市值", "市盈率", "市净率", "主营业务", "公司资料", "上市日期",
    "换手率", "成交量", "资金流", "公告", "股本",
)
MARKET_TERMS = (
    "涨停", "跌停", "炸板", "连板", "最高标", "最高板", "板高", "首板",
    "晋级", "指数", "板块", "行业", "市场", "情绪", "成交额", "龙虎榜",
    "异动", "热股", "热度", "封板", "开板率",
)
NEWS_TERMS = ("新闻", "快讯", "资讯", "消息", "财联社")
POST_LIMIT_TERMS = ("回撤", "缩量", "高位", "回踩", "接力", "一进二", "筛选", "策略", "盘前")
REVIEW_TERMS = ("复盘", "评分", "top10", "回顾", "表现", "命中率", "评级")

PROFILE_TOOLS: dict[str, frozenset[str]] = {
    "stock": frozenset({
        "trading_calendar", "stock_profile", "stock_kline", "stock_financials",
        "stock_news", "stock_activity", "dragon_tiger_list", "limit_up_events",
        "first_board_ratings",
    }),
    "market": frozenset({
        "trading_calendar", "market_summary", "market_index_trend",
        "limit_up_events", "market_event_pool", "sector_performance",
        "sector_stock_ranking", "hot_stock_ranking", "daily_board_promotion",
        "first_board_ratings", "first_board_filter",
    }),
    "news": frozenset({
        "trading_calendar", "finance_news", "stock_news", "hot_stock_ranking",
        "stock_activity",
    }),
    "post_limit": frozenset({
        "trading_calendar", "post_limit_screen", "post_limit_statistics",
        "post_limit_path", "recent_limit_up_returns", "limit_up_events",
        "first_board_ratings",
    }),
    "review": frozenset({
        "trading_calendar", "review_high_score_picks", "first_board_ratings",
        "market_summary",
    }),
}
PROFILE_TOOLS["general"] = frozenset(
    PROFILE_TOOLS["market"]
    | PROFILE_TOOLS["news"]
    | {"stock_profile", "stock_kline", "stock_financials"}
)
PROFILE_TOOLS["full"] = frozenset(V1_CLOSED_MARKET_TOOL_NAMES)


@dataclass(frozen=True)
class ToolProfile:
    """Resolved profile name and the business tools exposed to the model."""

    name: str
    tools: frozenset[str]


def resolve_tool_profile(request: Any) -> ToolProfile:
    """Select tools from the page context, symbol and question vocabulary."""

    message = str(getattr(request, "message", "") or "").lower()
    symbol = str(getattr(request, "symbol", "") or "").strip()
    page = getattr(request, "page_context", None)
    page = page if isinstance(page, dict) else {}
    page_name = str(page.get("page") or "").lower()
    has_stock_code = bool(STOCK_CODE.search(message))

    matched: set[str] = set()
    if symbol or page_name == "stock_detail" or has_stock_code:
        matched.add("stock")
    if any(term in message for term in STOCK_TERMS):
        matched.add("stock")
    if any(term in message for term in MARKET_TERMS):
        matched.add("market")
    if any(term in message for term in NEWS_TERMS):
        matched.add("news")
    if any(term in message for term in POST_LIMIT_TERMS):
        matched.add("post_limit")
    if any(term in message for term in REVIEW_TERMS):
        matched.add("review")

    if not matched:
        return ToolProfile("general", PROFILE_TOOLS["general"])
    if len(matched) == 1:
        name = next(iter(matched))
        return ToolProfile(name, PROFILE_TOOLS[name])
    tools: set[str] = set()
    for name in matched:
        tools.update(PROFILE_TOOLS[name])
    return ToolProfile("combined:" + "+".join(sorted(matched)), frozenset(tools))
