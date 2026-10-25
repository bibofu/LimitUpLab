"""Public adapter tests using real owned PostgreSQL graph execution."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from app.agents.chat import answer_first_board_chat
from app.agents.runner.guardrails import REFUSAL
from app.agents.tools import AgentToolRegistry
from app.models import AgentChatRequest
from app.services.sample_data import SAMPLE_EVENTS
from app.agents.runtime_errors import RecoverableRunError
from runner_node_harness import durable_request


def call(name: str, args: dict, call_id: str = "call") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def input_review(*, decision: str = "allow", signals=(), trading_action_quote=None) -> AIMessage:
    return call("submit_input_security_review", {
        "decision": decision,
        "signals": list(signals),
        "reason": "scripted security review",
        "trading_action_quote": trading_action_quote,
    }, "review")


def compliance(*, decision: str = "allow", violations=()) -> AIMessage:
    return call("submit_compliance_review", {
        "decision": decision,
        "violations": list(violations),
        "reason": "scripted compliance review",
    }, "compliance")


class ScriptedProvider:
    model = "scripted-production-fixture"

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


def ask(provider, monkeypatch, message: str = "最新交易日涨停多少只？"):
    with durable_request(AgentChatRequest(session_id="production-test", message=message)) as request:
        return answer_first_board_chat(
            request, SAMPLE_EVENTS, repository=SimpleNamespace(database_url=None),
            llm_provider=provider, tool_registry=registry(),
        )


def test_public_entry_runs_durable_graph(monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_AGENT_COMPLIANCE", "deterministic")
    provider = ScriptedProvider([
        input_review(),
        call("market_summary", {}),
        AIMessage(content="最新交易日涨停数量已取得。"),
    ])
    response = ask(provider, monkeypatch)

    assert response.generated_by == "agent-runner-v1"
    assert response.task_status == "complete"
    assert response.tool_calls == ["market_summary"]
    assert response.tool_results[0].name == "market_summary"
    assert response.tool_results[0].result.status == "ok"


def test_public_entry_fails_closed_on_input_policy(monkeypatch):
    provider = ScriptedProvider([
        input_review(decision="refuse", signals=("instruction_override",)),
    ])
    response = ask(provider, monkeypatch)

    assert response.generated_by == "agent-runner-v1"
    assert response.task_status == "refuse"
    assert response.stop_reason == "input_policy"
    assert response.tool_calls == []
    assert provider.responses == []


def test_public_entry_does_not_call_the_legacy_input_compiler(monkeypatch):
    import importlib.util

    monkeypatch.setenv("LIMITUPLAB_AGENT_COMPLIANCE", "deterministic")
    assert importlib.util.find_spec("app.services.prompt_security") is None
    provider = ScriptedProvider([
        input_review(),
        AIMessage(content="最新交易日涨停数量已取得。"),
    ])
    response = ask(provider, monkeypatch)

    assert response.generated_by == "agent-runner-v1"
    assert response.task_status == "complete"


def test_public_entry_fails_closed_on_malformed_security_review(monkeypatch):
    provider = ScriptedProvider([
        input_review(decision="allow", signals=("instruction_override",)),
        input_review(decision="allow", signals=("instruction_override",)),
    ])
    with pytest.raises(RecoverableRunError):
        ask(provider, monkeypatch)
    assert provider.responses == []


def test_public_entry_reviews_signaled_advice_and_refuses_actual_trade_instruction(monkeypatch):
    monkeypatch.delenv("LIMITUPLAB_AGENT_COMPLIANCE", raising=False)
    provider = ScriptedProvider([
        input_review(),
        AIMessage(content="建议买入该股，仓位三成。"),
        compliance(decision="reject", violations=("trade_instruction",)),
    ])
    response = ask(provider, monkeypatch, "可以买吗？")

    assert response.generated_by == "agent-runner-v1"
    assert response.task_status == "refuse"
    assert response.stop_reason == "compliance_refused"
    assert response.answer == REFUSAL
    assert provider.responses == []


def test_full_review_mode_reuses_runner_approval_without_a_second_review(monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_AGENT_COMPLIANCE", "llm")
    answer = "以上为历史行情与评分事实的客观梳理，不构成任何买卖或仓位建议。"
    provider = ScriptedProvider([input_review(), AIMessage(content=answer), compliance()])
    response = ask(provider, monkeypatch, "比较历史行情与评分依据。")
    assert response.task_status == "complete" and response.answer == answer
    assert len(provider.calls) == 3


def test_full_review_mode_does_not_retry_failed_triggered_review(monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_AGENT_COMPLIANCE", "llm")
    provider = ScriptedProvider([input_review(), AIMessage(content="不构成任何仓位建议。"), AIMessage(content="malformed")])
    response = ask(provider, monkeypatch)
    assert response.task_status == "error" and response.stop_reason == "compliance_error"
    assert len(provider.calls) == 3


def test_public_entry_applies_semantic_compliance_review(monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_AGENT_COMPLIANCE", "llm")
    provider = ScriptedProvider([
        input_review(),
        AIMessage(content="从历史走势看，该股近期表现较强。"),
        compliance(decision="reject", violations=("deterministic_prediction",)),
    ])
    response = ask(provider, monkeypatch, "这只股票后续会涨吗？")

    assert response.generated_by == "agent-runner-v1"
    assert response.task_status == "refuse"
    assert response.stop_reason == "compliance_refused"
    assert response.answer == REFUSAL
    assert len(provider.calls) == 3, "Unflagged answers use the same single Runner review"


def test_public_entry_refuses_trading_advice_before_business_queries(monkeypatch):
    provider = ScriptedProvider([input_review(
        decision="refuse", signals=("trading_advice",),
        trading_action_quote="选一只让我下个交易日买入，直接给出买入价、仓位和目标价",
    )])
    response = ask(provider, monkeypatch,
                   "请从2026年10月23日的首板名单里选一只让我下个交易日买入，直接给出买入价、仓位和目标价。")
    assert response.task_status == "refuse"
    assert response.stop_reason == "input_trading_advice"
    assert response.answer == REFUSAL
    assert response.tool_calls == [] and response.tool_results == []
    assert len(provider.calls) == 1


@pytest.mark.parametrize("message", [
    "不要给买入或卖出建议，只比较两只股票的历史收益与回撤。",
    "解释目标价这个概念，不要给出任何股票的价格预测。",
])
def test_public_entry_allows_research_with_trading_terms_when_semantic_review_allows(monkeypatch, message):
    provider = ScriptedProvider([input_review(), AIMessage(content="可以做客观研究与概念解释。")])
    response = ask(provider, monkeypatch, message)
    assert response.task_status == "complete"
    assert response.answer == "可以做客观研究与概念解释。"
    assert len(provider.calls) == 2


@pytest.mark.parametrize("message", [
    "按历史评分选取前5只股票，比较当时的20日走势和当前资讯，解释评分依据与风险。",
    "筛选上周高分首板，按分数降序列出名单与数据缺口，只做历史研究。",
])
def test_ranked_historical_research_can_reach_business_tools(monkeypatch, message):
    provider = ScriptedProvider([
        input_review(), call("market_summary", {}), AIMessage(content="已取得历史研究事实。"),
    ])
    response = ask(provider, monkeypatch, message)
    assert response.task_status == "complete"
    assert response.tool_calls == ["market_summary"]
    assert len(provider.calls) == 3


@pytest.mark.parametrize("quote", [None, "我要选择一只实际买入"])
def test_inferred_trading_intent_requires_review_repair_before_research_is_blocked(monkeypatch, quote):
    provider = ScriptedProvider([
        input_review(decision="refuse", signals=("trading_advice",), trading_action_quote=quote),
        input_review(), call("market_summary", {}), AIMessage(content="可做客观排名与风险研究。"),
    ])
    response = ask(provider, monkeypatch, "按历史评分排序，结合行情和新闻解释高分样本的风险。")
    assert response.task_status == "complete"
    assert response.tool_calls == ["market_summary"]
    assert "validation_errors" in provider.calls[1][-1].content
    assert len(provider.calls) == 4


def test_unrepairable_trading_assessment_fails_closed_without_business_queries(monkeypatch):
    provider = ScriptedProvider([
        input_review(decision="refuse", signals=("trading_advice",)),
        input_review(decision="refuse", signals=("trading_advice",)),
    ])
    with pytest.raises(RecoverableRunError):
        ask(provider, monkeypatch, "比较历史评分与风险。")
    assert len(provider.calls) == 2
