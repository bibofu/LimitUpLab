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
    "stock_profile",
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
    assert "\n14. 公司资料：" in prompt
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


def test_stock_profile_rule_documents_current_market_cap_and_missing_fields():
    prompt = system_prompt({"stock_profile"})

    assert "\n12. 公司资料：" in prompt
    assert "市值是当前快照" in prompt
    assert "不得用注册资金、总资产或成交额替代市值" in prompt
    assert "industry_source" in prompt
    assert "不提供买卖、仓位或目标价建议" in prompt


def test_stock_profile_rule_is_absent_when_tool_is_not_enabled():
    prompt = system_prompt({"stock_kline"})

    assert "stock_profile" not in prompt
    assert "公司资料：" not in prompt


def test_financial_rule_is_included_when_stock_financials_is_enabled():
    prompt = system_prompt({"stock_financials", "market_index_trend"})

    assert "\n12. 使用 stock_financials" in prompt
    assert "\n13. 指数数据：" in prompt
    assert "年初至今累计" in prompt
    assert "单季推算值" in prompt


def test_prompt_distinguishes_null_fields_and_requires_row_consistency():
    prompt = system_prompt(V1_TOOLS)
    assert "字段值为 null 表示字段存在但值缺失" in prompt
    assert "摘要视图未显示某字段不等于原始行没有该字段" in prompt
    assert "不得写成“均为……”。" in prompt


def test_prompt_requires_market_summary_for_opening_and_unsealed_rates():
    prompt = system_prompt(V1_TOOLS)
    assert "开板率、炸板未回封率优先引用 market_summary" in prompt
    assert "event_summary" in prompt
    assert "touched_count 已包含收盘未回封" in prompt


def test_prompt_requires_limit_up_events_for_event_records():
    prompt = system_prompt(V1_TOOLS)
    assert "个股涨停或炸板事件记录必须调用 limit_up_events" in prompt


def test_stock_trend_rule_requires_both_tools():
    both = system_prompt({"stock_kline", "stock_activity"})
    assert "stock_activity 只用于" in both

    only_kline = system_prompt({"stock_kline"})
    assert "stock_activity 只用于" not in only_kline
