"""Progressive tool disclosure: search, admission and bounded active sets."""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from runner_node_harness import NodeRunner
from app.agents.progressive_tools import (
    SEARCH_TOOLS_TOOL,
    SearchToolsRequest,
    resolve_max_active_tools,
    resolve_progressive_tools_mode,
    search_tool_catalog,
)
from app.agents.tool_profiles import resolve_tool_profile
from app.agents.tools import AgentToolRegistry
from app.models import AgentChatRequest
from app.services.sample_data import SAMPLE_EVENTS


CATALOG = {
    "stock_kline": {
        "description": "读取指定股票日K线、均线、成交额和最大回撤。",
        "notes": "成交额缺失时返回 null。",
        "time_mode": "historical_or_live",
    },
    "stock_financials": {
        "description": "查询利润表、营收、净利润、现金流。",
        "notes": "",
        "time_mode": "historical",
    },
    "dragon_tiger_list": {
        "description": "查询龙虎榜、机构和游资买卖额。",
        "notes": "",
        "time_mode": "historical",
    },
    "stock_profile": {
        "description": "查询公司资料、主营业务、市值和上市日期。",
        "notes": "",
        "time_mode": "retrieved_now",
    },
}


class ScriptedProvider:
    model = "progressive-fixture"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.options = []

    def generate_messages(self, messages, tools, **kwargs):
        self.calls.append(messages)
        self.options.append({"tools": tools, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def call(name: str, args: dict, call_id: str = "call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def registry() -> AgentToolRegistry:
    return AgentToolRegistry(
        events=SAMPLE_EVENTS,
        first_board_repository=SimpleNamespace(database_url=None),
        hithink_collector=SimpleNamespace(),
    )


def request(message: str) -> AgentChatRequest:
    return AgentChatRequest(session_id="progressive-test", message=message)


def test_search_catalog_ranks_capability_aliases_and_marks_visible_tools():
    rows = search_tool_catalog(CATALOG, "成交额", limit=3, active_names={"stock_kline"})
    assert rows[0]["name"] == "stock_kline"
    assert rows[0]["already_visible"] is True
    assert len(rows) <= 3

    finance = search_tool_catalog(CATALOG, "营收和净利润", limit=3)
    assert finance[0]["name"] == "stock_financials"

    company = search_tool_catalog(CATALOG, "公司", limit=3)
    assert company[0]["name"] == "stock_profile"


def test_search_catalog_returns_empty_for_unmatched_query():
    assert search_tool_catalog(CATALOG, "完全不存在的词") == []


def test_progressive_mode_and_budget_validation(monkeypatch):
    monkeypatch.delenv("LIMITUPLAB_PROGRESSIVE_TOOLS", raising=False)
    assert resolve_progressive_tools_mode() == "hybrid"
    assert resolve_progressive_tools_mode("off") == "off"
    assert resolve_progressive_tools_mode("true") == "hybrid"
    assert resolve_progressive_tools_mode("full") == "full"
    with pytest.raises(ValueError):
        resolve_progressive_tools_mode("sometimes")
    assert resolve_max_active_tools("") == 20
    assert resolve_max_active_tools("12") == 12
    with pytest.raises(ValueError):
        resolve_max_active_tools("0")


def test_hybrid_keeps_profile_and_adds_searched_tool_for_the_next_round():
    provider = ScriptedProvider([
        call("search_tools", {"query": "营收和净利润"}),
        AIMessage(content="已取得可用工具说明。"),
    ])
    runner = NodeRunner(
        request("最新财经新闻有哪些"), registry(), provider, progressive_mode="hybrid",
    )
    result = runner.run_nodes()

    first_names = {item["function"]["name"] for item in provider.options[0]["tools"]}
    second_names = {item["function"]["name"] for item in provider.options[1]["tools"]}
    assert SEARCH_TOOLS_TOOL in first_names
    assert "finance_news" in first_names
    assert "stock_financials" not in first_names
    assert "stock_financials" in second_names
    assert result.tool_calls == [SEARCH_TOOLS_TOOL]
    observation = json.loads(
        next(item.content for item in provider.calls[1] if isinstance(item, ToolMessage))
    )
    assert any(row["name"] == "stock_financials" for row in observation["rows"])


def test_hybrid_definition_order_is_stable_for_prompt_caching():
    runner = NodeRunner(
        request("最新财经新闻有哪些"), registry(), ScriptedProvider([]),
        progressive_mode="hybrid",
    )
    names = [item["function"]["name"] for item in runner.definitions]
    business = [
        name for name in runner.gateway.structured
        if name in runner.initial_tool_names
    ]
    assert names[0] == SEARCH_TOOLS_TOOL
    assert names[1:-1] == business
    assert names[-1] == "read_tool_result"


def test_off_mode_exposes_no_search_tool_and_rejects_manual_search():
    provider = ScriptedProvider([AIMessage(content="你好。")])
    runner = NodeRunner(request("最新财经新闻有哪些"), registry(), provider, progressive_mode="off")
    runner.run_nodes()
    names = {item["function"]["name"] for item in provider.options[0]["tools"]}
    assert SEARCH_TOOLS_TOOL not in names

    value = runner.execute({
        "name": SEARCH_TOOLS_TOOL, "args": {"query": "财务"}, "id": "manual-search",
    })
    assert value["status"] == "error"
    assert "disabled" in value["error"]


def test_search_treats_a_resolvable_stock_name_as_a_capability_hint():
    tool_registry = registry()

    def resolve(value: str):
        if "宁德时代" in value:
            return ("300750", "宁德时代")
        raise ValueError("not a stock")

    tool_registry.resolve_stock_identity = resolve
    runner = NodeRunner(
        request("最新财经新闻有哪些"), tool_registry, ScriptedProvider([]),
        progressive_mode="hybrid",
    )

    value = runner.execute({
        "name": SEARCH_TOOLS_TOOL, "args": {"query": "宁德时代"}, "id": "stock-search",
    })

    names = [row["name"] for row in value["rows"]]
    assert "stock_profile" in names
    assert "stock_financials" in names


def test_full_mode_starts_with_meta_tools_only():
    provider = ScriptedProvider([AIMessage(content="你好。")])
    runner = NodeRunner(request("最新财经新闻有哪些"), registry(), provider, progressive_mode="full")
    runner.run_nodes()
    names = {item["function"]["name"] for item in provider.options[0]["tools"]}
    assert names == {SEARCH_TOOLS_TOOL, "read_tool_result"}


def test_active_budget_limits_new_tools_and_reports_the_boundary():
    provider = ScriptedProvider([
        call("search_tools", {"query": "营收和净利润"}),
        AIMessage(content="已取得可用工具说明。"),
    ])
    profile = resolve_tool_profile(request("最新财经新闻有哪些"))
    runner = NodeRunner(
        request("最新财经新闻有哪些"), registry(), provider,
        progressive_mode="hybrid", max_active_tools=len(profile.tools),
    )
    runner.run_nodes()
    second_names = {item["function"]["name"] for item in provider.options[1]["tools"]}
    assert "stock_financials" not in second_names
    observation = json.loads(
        next(item.content for item in provider.calls[1] if isinstance(item, ToolMessage))
    )
    assert "活动工具已达上限" in observation["summary"]


def test_search_does_not_consume_a_data_query_budget_slot():
    runner = NodeRunner(
        request("最新财经新闻有哪些"), registry(), object(), progressive_mode="hybrid",
    )
    state = {
        "messages": [],
        "pending": [
            {"name": "limit_up_events", "args": {}, "id": f"data-{index}"}
            for index in range(8)
        ] + [{"name": SEARCH_TOOLS_TOOL, "args": {"query": "财务"}, "id": "search"}],
        "tool_calls": [],
        "tool_statuses": [],
        "tool_messages": [],
        "tool_count": 0,
        "discovered_tools": [],
    }

    update = runner.tools(state)

    assert update["tool_count"] == 8
    assert update["tool_statuses"][-1] == "ok"
    assert update["discovered_tools"] == ["stock_financials"]


def test_search_request_rejects_blank_and_oversized_limits():
    with pytest.raises(ValueError):
        SearchToolsRequest.model_validate({"query": ""})
    with pytest.raises(ValueError):
        SearchToolsRequest.model_validate({"query": "财务", "limit": 9})
