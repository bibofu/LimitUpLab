"""The system prompt stays small, tool-agnostic and bounded."""

from datetime import date

from app.agents.runner.prompt import build_system_prompt


def system_prompt() -> str:
    return build_system_prompt(date(2026, 10, 29))


def test_prompt_stays_within_its_budget():
    assert len(system_prompt()) <= 1000


def test_core_grounding_rules_remain():
    prompt = system_prompt()
    for phrase in (
        "本轮工具结果",
        "field_definitions",
        "data_missing",
        "calendar_coverage_status=unverified",
        "limit_up_events",
        "不输出买入、卖出",
        "row_count",
        "均为",
    ):
        assert phrase in prompt


def test_tool_specific_details_are_not_duplicated_in_the_prompt():
    prompt = system_prompt()
    for phrase in (
        "include_market_cap",
        "snapshot_captured_at",
        "calendar_verified",
        "quarterly",
        "stock_activity 只用于",
        "指数数据：",
        "公司资料：",
        "使用 stock_financials",
    ):
        assert phrase not in prompt


def test_prompt_has_no_capability_rule_numbering():
    prompt = system_prompt()
    assert "\n10. " in prompt
    assert "\n12. " not in prompt
    assert "\n13. " not in prompt
