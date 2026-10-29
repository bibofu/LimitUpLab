"""Deterministic tool exposure profiles for the ReAct runner.

The model sees only the tools relevant to the current page and question.
Profiles are stable so prompt caching still works. Date and first-board tools
are added only when the question actually needs them, and an unrecognised
request falls back to a broad general profile instead of the full catalog.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.agents.tools import V1_CLOSED_MARKET_TOOL_NAMES


STOCK_CODE = re.compile(r"(?<!\d)\d{6}(?!\d)")
DATE_TEXT = re.compile(r"20\d{2}\s*[-年/]\s*\d{1,2}")

STRONG_STOCK_TERMS = (
    "这只股", "该股", "个股", "股票代码", "代码", "股价", "k线", "均线",
    "财务", "市值", "市盈率", "市净率", "主营业务", "公司资料", "上市日期",
    "换手率", "成交量", "资金流", "公告", "股本",
)
WEAK_STOCK_TERMS = ("走势",)
INDEX_TERMS = (
    "指数", "上证", "深证", "创业板", "沪深300", "中证", "科创50", "大盘", "点位",
)
LIMIT_UP_TERMS = (
    "涨停", "跌停", "炸板", "连板", "最高标", "最高板", "板高", "首板",
    "晋级", "封板", "开板率", "龙虎榜", "异动",
)
SECTOR_TERMS = (
    "板块", "行业", "概念", "题材", "热股", "热度", "涨幅榜", "资金流", "轮动",
)
GENERIC_MARKET_TERMS = ("市场", "情绪", "成交额")
NEWS_TERMS = ("新闻", "快讯", "资讯", "消息", "财联社")
POST_LIMIT_TERMS = ("回撤", "缩量", "高位", "回踩", "接力", "一进二", "筛选", "策略", "盘前")
REVIEW_TERMS = ("复盘", "评分", "top10", "回顾", "表现", "命中率", "评级")
CALENDAR_TERMS = (
    "今天", "交易日", "上一交易日", "最新交易日", "收盘了吗", "几号", "星期", "日期",
)
FIRST_BOARD_TERMS = ("首板", "评分", "评级", "候选", "接力", "一进二")

PROFILE_TOOLS: dict[str, frozenset[str]] = {
    "stock": frozenset({
        "stock_profile", "stock_kline", "stock_financials", "stock_news",
        "dragon_tiger_list", "limit_up_events",
    }),
    "index": frozenset({
        "market_summary", "market_index_trend", "index_search",
        "index_quote", "index_constituents",
    }),
    "limit_up": frozenset({
        "market_summary", "limit_up_events", "market_event_pool",
        "daily_board_promotion", "dragon_tiger_list", "first_board_filter",
    }),
    "sector": frozenset({
        "market_summary", "sector_performance", "sector_stock_ranking",
        "hot_stock_ranking",
    }),
    "market": frozenset({
        "market_summary", "market_index_trend", "limit_up_events",
        "market_event_pool", "sector_performance", "hot_stock_ranking",
        "daily_board_promotion", "dragon_tiger_list", "first_board_filter",
    }),
    "news": frozenset({
        "finance_news", "stock_news", "hot_stock_ranking",
    }),
    "post_limit": frozenset({
        "post_limit_screen", "post_limit_statistics", "post_limit_path",
        "recent_limit_up_returns", "limit_up_events",
    }),
    "review": frozenset({
        "review_high_score_picks", "first_board_ratings", "market_summary",
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
    """Select tools from page context, symbol, question vocabulary and extras."""

    message = str(getattr(request, "message", "") or "").lower()
    symbol = str(getattr(request, "symbol", "") or "").strip()
    page = getattr(request, "page_context", None)
    page = page if isinstance(page, dict) else {}
    page_name = str(page.get("page") or "").lower()
    has_stock_code = bool(STOCK_CODE.search(message))

    has_index = any(term in message for term in INDEX_TERMS)
    has_limit_up = any(term in message for term in LIMIT_UP_TERMS)
    has_sector = any(term in message for term in SECTOR_TERMS)
    has_generic_market = any(term in message for term in GENERIC_MARKET_TERMS)
    has_strong_stock = any(term in message for term in STRONG_STOCK_TERMS)
    has_weak_stock = any(term in message for term in WEAK_STOCK_TERMS)

    matched: set[str] = set()
    if (
        symbol
        or page_name == "stock_detail"
        or has_stock_code
        or has_strong_stock
        or (has_weak_stock and not (has_index or has_limit_up or has_sector))
    ):
        matched.add("stock")
    if has_index:
        matched.add("index")
    if has_limit_up:
        matched.add("limit_up")
    if has_sector:
        matched.add("sector")
    if has_generic_market and not (has_index or has_limit_up or has_sector):
        matched.add("market")
    if any(term in message for term in NEWS_TERMS):
        matched.add("news")
    if any(term in message for term in POST_LIMIT_TERMS):
        matched.add("post_limit")
    if any(term in message for term in REVIEW_TERMS):
        matched.add("review")

    if not matched:
        matched.add("general")

    tools: set[str] = set()
    for name in matched:
        tools.update(PROFILE_TOOLS[name])

    extras = set()
    if any(term in message for term in CALENDAR_TERMS) or DATE_TEXT.search(message):
        extras.add("trading_calendar")
    if any(term in message for term in FIRST_BOARD_TERMS) and "review" not in matched:
        extras.add("first_board_ratings")
    tools.update(extras)

    name = next(iter(matched)) if len(matched) == 1 else (
        "combined:" + "+".join(sorted(matched))
    )
    if extras:
        name += "+" + "+".join(sorted(extras))
    return ToolProfile(name, frozenset(tools))
