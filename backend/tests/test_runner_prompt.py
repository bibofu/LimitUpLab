"""Prompt rules must be derived from the enabled tool set, never hardcoded."""

from __future__ import annotations

from datetime import date
from app.agents.runner.prompt import build_system_prompt


V1_TOOLS = {
    "trading_calendar",
    "market_summary",
    "market_index_trend",
    "stock_kline",
}

EXTENDED_TOOLS = V1_TOOLS | {
    "stock_financials",
    "index_search",
    "index_quote",
    "index_constituents",
}


def system_prompt(enabled_tools: set[str]) -> str:
    return build_system_prompt(
        date(2026, 10, 25),
        enabled_tools=enabled_tools,
    )


def test_trend_only_profile_never_promises_unavailable_index_tools():
    prompt = system_prompt(V1_TOOLS)

    assert "market_index_trend" in prompt
    assert "不提供开盘、最高、最低、成交量或成交额" in prompt
    assert "index_quote" not in prompt
    assert "index_search" not in prompt
    assert "index_constituents" not in prompt
    assert "stock_financials" not in prompt
    assert "\n12. 指数数据：" in prompt
    assert "\n13. " not in prompt


def test_extended_profile_documents_every_enabled_capability():
    prompt = system_prompt(EXTENDED_TOOLS)

    for name in ("stock_financials", "index_search", "index_quote", "index_constituents"):
        assert name in prompt
    assert "\n12. 使用 stock_financials" in prompt
    assert "\n13. 指数数据：" in prompt
    assert "snapshot_captured_at" in prompt
    assert "当前没有提供开盘、最高、最低、成交量或成交额的指数工具" not in prompt


def test_search_only_profile_keeps_the_quote_limitation_explicit():
    prompt = system_prompt({"index_search", "market_index_trend"})

    assert "index_search" in prompt
    assert "index_quote" not in prompt
    assert "当前没有提供开盘、最高、最低、成交量或成交额的指数工具" in prompt


def test_no_index_tools_means_no_index_rule():
    prompt = system_prompt({"market_summary"})

    assert "指数数据：" not in prompt
    assert "\n12. " not in prompt
