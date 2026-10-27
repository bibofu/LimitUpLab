"""Small closed vocabulary shared by memory extraction, retrieval and policy."""

from __future__ import annotations

import re

TOOL_SCOPE_TERMS = {
    "财报": "stock_financials",
    "财务": "stock_financials",
    "k线": "stock_kline",
    "龙虎榜": "dragon_tiger_list",
    "个股新闻": "stock_news",
    "新闻": "stock_news",
    "公司资料": "stock_profile",
    "热股榜": "hot_stock_ranking",
    "涨停池": "limit_up_pool",
    "炸板池": "failed_limit_up_pool",
    "跌停池": "limit_down_pool",
}
TOPIC_SCOPE_TERMS = ("首板", "连板", "涨停", "炸板", "行业", "市场情绪")
A_SHARE_PREFIXES = frozenset({
    "000", "001", "002", "003", "300", "301",
    "600", "601", "603", "605", "688", "689",
    "430", "830", "831", "832", "833", "834", "835", "836", "837",
    "838", "839", "870", "871", "872", "873",
})
SYMBOL_PATTERN = re.compile(r"(?<!\d)(\d{6})(?!\d)")


def tools_in_text(value: str | None) -> set[str]:
    text = (value or "").casefold()
    return {
        tool for term, tool in TOOL_SCOPE_TERMS.items()
        if term in text
    }


def tool_from_text(value: str | None) -> str | None:
    tools = tools_in_text(value)
    return next(iter(tools)) if len(tools) == 1 else None


def topics_in_text(value: str | None) -> list[str]:
    text = value or ""
    return [topic for topic in TOPIC_SCOPE_TERMS if topic in text]


def symbols_in_text(value: str | None) -> set[str]:
    return {
        symbol for symbol in SYMBOL_PATTERN.findall(value or "")
        if symbol[:3] in A_SHARE_PREFIXES
    }


def is_a_share_symbol(value: str | None) -> bool:
    text = str(value or "").strip()
    return bool(text) and text[:3] in A_SHARE_PREFIXES and bool(SYMBOL_PATTERN.fullmatch(text))
