"""The stock-diagnosis playbook follows the standard SKILL.md layout."""

from app.agents.runner.runner import AgentRunner
from app.agents.skills import (
    DEFAULT_SKILL, STOCK_SKILL, available_skills, load_skill, resolve_agent_skill,
)
from app.models import AgentChatRequest
from test_runner_shadow import ScriptedProvider, registry, request


def _agent_request(message: str, *, symbol: str | None = None, page: str | None = None):
    return AgentChatRequest(
        session_id="stock-skill-test",
        message=message,
        symbol=symbol,
        page_context={"page": page} if page else {},
    )


def test_stock_skill_folder_exposes_frontmatter_and_body():
    skill = load_skill(STOCK_SKILL)

    assert skill.name == "stock-diagnosis"
    assert skill.path.name == "SKILL.md"
    assert skill.path.parent.name == skill.name
    assert skill.description
    assert {"这只股", "财报", "市值", "主营业务"} <= set(skill.triggers)
    for tool in (
        "stock_activity", "stock_kline", "stock_financials",
        "stock_profile", "stock_news", "dragon_tiger_list", "limit_up_events",
    ):
        assert tool in skill.body
    assert "data_missing" in skill.body
    assert "unsealed" in skill.body and "intraday_broken" in skill.body
    assert "不输出买入、卖出、仓位、目标价" in skill.body
    assert skill in available_skills()


def test_stock_question_selects_the_stock_skill_body():
    stock_body = load_skill(STOCK_SKILL).body
    review_body = load_skill(DEFAULT_SKILL).body

    assert resolve_agent_skill(request("帮我诊断一下这只股：600519")) == stock_body
    assert resolve_agent_skill(request("贵州茅台最新财报怎么看")) == stock_body
    assert resolve_agent_skill(request("贵州茅台市值多少")) == stock_body
    assert resolve_agent_skill(request("复盘一下这只股")) == stock_body
    assert resolve_agent_skill(request("今天市场怎么样")) == review_body
    assert resolve_agent_skill(request("复盘今天板块轮动")) == review_body
    # Equal-length triggers keep a market review on daily-review.
    assert resolve_agent_skill(request("复盘今天个股表现")) == review_body
    assert resolve_agent_skill(request("查询2026-10-30涨停数量")) is None


def test_stock_page_context_falls_back_only_for_stock_shaped_questions():
    stock_body = load_skill(STOCK_SKILL).body
    review_body = load_skill(DEFAULT_SKILL).body

    assert resolve_agent_skill(_agent_request("最近怎么样", symbol="600519")) == stock_body
    assert resolve_agent_skill(
        _agent_request("最近怎么样", page="stock_detail")
    ) == stock_body
    assert resolve_agent_skill(
        _agent_request("今天市场怎么样", symbol="600519")
    ) == review_body
    assert resolve_agent_skill(_agent_request("涨停数量", symbol="600519")) is None


def test_runner_appends_the_selected_skill_body():
    stock_body = load_skill(STOCK_SKILL).body
    review_body = load_skill(DEFAULT_SKILL).body

    stock = AgentRunner(
        request("帮我看看这只股：600519"), registry(), ScriptedProvider([]),
    )
    plain = AgentRunner(
        request("查询2026-10-30涨停数量"), registry(), ScriptedProvider([]),
    )

    assert stock_body in stock.system_prompt
    assert review_body not in stock.system_prompt
    assert stock_body not in plain.system_prompt


def test_skill_triggers_do_not_collide_across_playbooks():
    owners = {}
    for skill in available_skills():
        for trigger in skill.triggers:
            folded = trigger.casefold()
            assert folded not in owners, f"trigger {trigger!r} in {owners.get(folded)} and {skill.name}"
            owners[folded] = skill.name
