"""The daily-review playbook is selected by intent and appended once."""

from app.agents.runner.runner import AgentRunner
from app.agents.skills import DAILY_REVIEW_SKILL, resolve_agent_skill
from test_runner_shadow import ScriptedProvider, registry, request


def test_review_question_selects_the_stable_playbook():
    assert resolve_agent_skill(request("复盘今天")) == DAILY_REVIEW_SKILL
    assert resolve_agent_skill(request("今天市场怎么样")) == DAILY_REVIEW_SKILL
    assert resolve_agent_skill(request("复盘今天板块轮动")) == DAILY_REVIEW_SKILL
    assert resolve_agent_skill(request("查询2026-10-30涨停数量")) is None


def test_playbook_uses_the_aggregate_and_never_mentions_market_summary():
    assert 'market_event_pool(event_type="limit_up", result_mode="summary"' in DAILY_REVIEW_SKILL
    assert "禁止用事件行相加重算" in DAILY_REVIEW_SKILL
    assert "market_summary" not in DAILY_REVIEW_SKILL
    assert "不含成交量" in DAILY_REVIEW_SKILL
    assert "不输出买入、卖出、仓位、目标价" in DAILY_REVIEW_SKILL


def test_runner_appends_the_playbook_only_for_review_requests():
    review = AgentRunner(request("复盘今天"), registry(), ScriptedProvider([]))
    plain = AgentRunner(request("查询2026-10-30涨停数量"), registry(), ScriptedProvider([]))

    assert DAILY_REVIEW_SKILL in review.system_prompt
    assert DAILY_REVIEW_SKILL not in plain.system_prompt
