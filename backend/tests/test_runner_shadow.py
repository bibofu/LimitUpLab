"""Phase 2 runner protocol tests with scripted models; no live IO."""

from __future__ import annotations

import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage

from app.agents.runner import AgentRunner
from app.agents.runner.guardrails import MAX_MODEL_ROUNDS, REFUSAL
from app.agents.tools import AgentToolRegistry
from app.models import AgentChatRequest
from app.services.sample_data import SAMPLE_EVENTS


def call(name: str, args: dict, call_id: str = "call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


class ScriptedProvider:
    model = "scripted-shadow-fixture"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate_messages(self, messages, tools, **kwargs):
        self.calls.append(messages)
        return self.responses.pop(0)


def registry() -> AgentToolRegistry:
    return AgentToolRegistry(
        events=SAMPLE_EVENTS,
        first_board_repository=SimpleNamespace(database_url=None),
        hithink_collector=SimpleNamespace(),
    )


def request(message: str = "最新交易日涨停多少只？") -> AgentChatRequest:
    return AgentChatRequest(session_id="shadow-test", message=message)


def test_runner_calls_one_tool_and_returns_model_readable_result():
    provider = ScriptedProvider([
        call("market_summary", {}),
        AIMessage(content="最新交易日涨停数量已取得。"),
    ])
    result = AgentRunner(request(), registry(), provider).run()

    assert result.status == "answered"
    assert result.tool_calls == ["market_summary"]
    assert result.tool_count == 1
    assert result.model_rounds == 2

    observation = provider.calls[1][-1]
    assert isinstance(observation, ToolMessage)
    payload = json.loads(observation.content)
    assert payload["status"] == "ok"
    assert payload["field_definitions"]
    assert "summary" in payload


def test_runner_answers_without_a_tool():
    provider = ScriptedProvider([AIMessage(content="你好，请告诉我具体日期和对象。")])
    result = AgentRunner(request("你好"), registry(), provider).run()

    assert result.status == "answered"
    assert result.tool_calls == []
    assert result.tool_count == 0


def test_runner_reports_validation_errors_to_the_model():
    provider = ScriptedProvider([
        call("stock_kline", {}),
        AIMessage(content="未取得该股走势数据。"),
    ])
    result = AgentRunner(request("某只股票走势如何？"), registry(), provider).run()

    assert result.status == "partial"
    assert result.tool_messages[0]["status"] == "error"
    observation = provider.calls[1][-1]
    payload = json.loads(observation.content)
    assert payload["status"] == "error"
    assert "symbol" in payload["error"].lower()


def test_runner_refuses_explicit_trading_advice():
    provider = ScriptedProvider([AIMessage(content="建议买入该股，仓位三成。")])
    result = AgentRunner(request("可以买吗？"), registry(), provider).run()

    assert result.status == "refuse"
    assert result.answer == REFUSAL
    assert result.stop_reason == "compliance_refused"


def test_runner_stops_at_the_model_round_budget():
    provider = ScriptedProvider([
        call("market_summary", {}, f"call_{index}")
        for index in range(MAX_MODEL_ROUNDS + 2)
    ])
    result = AgentRunner(request(), registry(), provider).run()

    assert result.stop_reason == "budget_exhausted"
    assert result.status == "partial"
    assert result.model_rounds >= MAX_MODEL_ROUNDS
    assert result.tool_count <= MAX_MODEL_ROUNDS


def test_runner_includes_session_memory_constraints():
    runner = AgentRunner(
        request("你好"),
        registry(),
        ScriptedProvider([AIMessage(content="好的。")]),
        memory=SimpleNamespace(constraints=["偏好表格", "只看主板"]),
    )
    system = runner.messages[0].content
    assert "偏好表格" in system and "只看主板" in system


def test_runner_bounds_history_text():
    history = [
        {"role": "user", "content": "x" * 4000},
        {"role": "user", "content": "y" * 4000},
    ]
    runner = AgentRunner(
        request("继续"),
        registry(),
        ScriptedProvider([]),
        history=history,
    )
    history_text = "".join(message.content for message in runner.messages[1:-1])
    assert len(history_text) <= 6000


def test_business_definitions_exclude_runtime_control_tools():
    runner = AgentRunner(request(), registry(), ScriptedProvider([]))
    names = {item["function"]["name"] for item in runner.definitions}
    assert "market_summary" in names
    assert "finish" not in names
    assert "compute_result" not in names
    assert "read_evidence" not in names
    assert "update_task" not in names
