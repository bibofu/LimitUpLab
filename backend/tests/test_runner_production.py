"""Production adapter tests for the Phase 3 lightweight runner."""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage

from app.agents.chat import answer_first_board_chat
from app.agents.runner.guardrails import REFUSAL
from app.agents.tools import AgentToolRegistry
from app.models import AgentChatRequest
from app.services.sample_data import SAMPLE_EVENTS


def call(name: str, args: dict, call_id: str = "call") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def input_review(*, decision: str = "allow", signals=()) -> AIMessage:
    return call("submit_input_security_review", {
        "decision": decision,
        "signals": list(signals),
        "reason": "scripted security review",
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
    return answer_first_board_chat(
        AgentChatRequest(session_id="production-test", message=message),
        SAMPLE_EVENTS,
        repository=SimpleNamespace(database_url=None),
        llm_provider=provider,
        tool_registry=registry(),
    )


def test_public_entry_runs_lightweight_runner(monkeypatch):
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
    monkeypatch.setenv("LIMITUPLAB_AGENT_COMPLIANCE", "deterministic")

    def forbidden(*args, **kwargs):
        raise AssertionError("legacy input compiler must not run on the new path")

    monkeypatch.setattr("app.services.prompt_security.review_input", forbidden)
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
    response = ask(provider, monkeypatch)

    assert response.generated_by == "agent-runner-v1"
    assert response.task_status == "error"
    assert response.stop_reason == "input_policy_error"
    assert response.tool_calls == []


def test_public_entry_deterministic_compliance_refuses_advice_without_llm(monkeypatch):
    monkeypatch.delenv("LIMITUPLAB_AGENT_COMPLIANCE", raising=False)
    provider = ScriptedProvider([
        input_review(),
        AIMessage(content="建议买入该股，仓位三成。"),
    ])
    response = ask(provider, monkeypatch, "可以买吗？")

    assert response.generated_by == "agent-runner-v1"
    assert response.task_status == "refuse"
    assert response.stop_reason == "compliance_refused"
    assert response.answer == REFUSAL
    assert provider.responses == []


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
