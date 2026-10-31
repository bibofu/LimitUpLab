"""The daily-review playbook follows the standard SKILL.md layout."""

from app.agents.runner.runner import AgentRunner
from app.agents.skills import (
    DEFAULT_SKILL, available_skills, load_skill, matches_skill, resolve_agent_skill,
)
from test_runner_shadow import ScriptedProvider, registry, request


def test_skill_folder_exposes_frontmatter_and_body():
    skill = load_skill(DEFAULT_SKILL)

    assert skill.name == "daily-review"
    assert skill.path.name == "SKILL.md"
    assert skill.path.parent.name == skill.name
    assert skill.description
    assert "复盘" in skill.triggers and "今天市场" in skill.triggers
    assert "market_event_pool" in skill.body
    assert "market_summary" not in skill.body
    assert "不含成交量" in skill.body
    assert "不输出买入、卖出、仓位、目标价" in skill.body
    assert skill in available_skills()


def test_review_question_selects_the_skill_body():
    body = load_skill(DEFAULT_SKILL).body
    assert resolve_agent_skill(request("复盘今天")) == body
    assert resolve_agent_skill(request("今天市场怎么样")) == body
    assert resolve_agent_skill(request("复盘今天板块轮动")) == body
    assert resolve_agent_skill(request("查询2026-10-30涨停数量")) is None
    assert matches_skill(DEFAULT_SKILL, "复盘今天")
    assert not matches_skill(DEFAULT_SKILL, "查询涨停数量")


def test_runner_appends_the_skill_body_only_for_review_requests():
    body = load_skill(DEFAULT_SKILL).body
    review = AgentRunner(request("复盘今天"), registry(), ScriptedProvider([]))
    plain = AgentRunner(request("查询2026-10-30涨停数量"), registry(), ScriptedProvider([]))

    assert body in review.system_prompt
    assert body not in plain.system_prompt
