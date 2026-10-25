"""Isolated business-node tests; durable graph coverage lives in test_durable_*."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from runner_node_harness import NodeRunner
from app.agents.runner.guardrails import MAX_MODEL_ROUNDS, REFUSAL
from app.agents.runtime_errors import RecoverableRunError
from app.agents.tool_context import expand_tool_observation
from app.agents.tools import EXTENDED_AGENT_PROFILE, AgentToolRegistry
from app.models import AgentChatRequest
from app.services.llm_provider import LLMResult, capture_llm_usage, current_llm_usage
from app.services.sample_data import SAMPLE_EVENTS


def call(name: str, args: dict, call_id: str = "call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def compliance(decision="allow"):
    return call("submit_compliance_review", {
        "decision": decision,
        "violations": [] if decision == "allow" else ["trade_instruction"],
        "reason": "historical facts or explicit trading instruction",
    })


class ScriptedProvider:
    model = "scripted-shadow-fixture"

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


class MeteredProvider(ScriptedProvider):
    """Scripted provider that participates in request-local usage accounting."""

    def generate_messages(self, messages, tools, **kwargs):
        tracker = current_llm_usage()
        if tracker is not None:
            tracker.begin_call(self.model)
        try:
            response = super().generate_messages(messages, tools, **kwargs)
        except BaseException:
            if tracker is not None:
                tracker.fail_call()
            raise
        if tracker is not None:
            tracker.complete_call(LLMResult(
                content="", model=self.model, provider="scripted",
                prompt_tokens=1000, completion_tokens=20, total_tokens=1020,
                cached_tokens=600, duration_ms=5,
            ))
        return response


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
    result = NodeRunner(request(), registry(), provider).run_nodes()

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


def test_runner_preserves_repeated_field_definitions_for_replayable_tool_context():
    provider = ScriptedProvider([
        call("limit_up_events", {"recent_trade_days": 1}, "call_1"),
        call("limit_up_events", {"recent_trade_days": 2}, "call_2"),
        AIMessage(content="两次查询均已完成。"),
    ])
    result = NodeRunner(request(), registry(), provider).run_nodes()

    assert result.status == "answered"
    # Public traces keep complete evidence for both calls.
    assert result.tool_messages[0]["field_definitions"]
    assert result.tool_messages[1]["field_definitions"]

    observations = [item for item in provider.calls[-1] if isinstance(item, ToolMessage)]
    assert len(observations) == 2
    first = json.loads(observations[0].content)
    second = json.loads(observations[1].content)
    assert first["field_definitions"]
    assert second["field_definitions"] == result.tool_messages[1]["field_definitions"]
    assert "field_definitions_ref" not in second
    assert expand_tool_observation(second)["rows"] == result.tool_messages[1]["rows"]


def test_runner_records_round_context_and_definition_cost():
    provider = MeteredProvider([
        call("market_summary", {}),
        AIMessage(content="最新交易日涨停数量已取得。"),
    ])
    with capture_llm_usage() as tracker:
        result = NodeRunner(request(), registry(), provider).run_nodes()

    assert result.status == "answered"
    assert len(tracker.calls) == 2
    first, second = tracker.calls
    assert first.context["stage"] == "agent"
    assert first.context["round"] == 1
    assert first.context["tool_count"] > 0
    assert first.context["tool_definition_chars"] > 0
    assert first.context["message_chars"] > 0
    assert first.context["tool_results"] == 0
    assert first.cached_tokens == 600
    assert second.context["round"] == 2
    assert second.context["tool_results"] == 1
    assert second.context["message_chars"] > first.context["message_chars"]
    assert second.cached_tokens == 600


def test_runner_prompt_matches_the_enabled_tool_set():
    provider = ScriptedProvider([AIMessage(content="你好。")])
    NodeRunner(request("你好"), registry(), provider).run_nodes()
    system = provider.calls[0][0].content

    assert "index_quote" in system
    assert "stock_financials" not in system

    extended = ScriptedProvider([AIMessage(content="你好。")])
    extended_registry = AgentToolRegistry(
        events=SAMPLE_EVENTS,
        first_board_repository=SimpleNamespace(database_url=None),
        hithink_collector=SimpleNamespace(),
        profile=EXTENDED_AGENT_PROFILE,
    )
    NodeRunner(request("你好"), extended_registry, extended).run_nodes()

    assert "stock_financials" in extended.calls[0][0].content


def test_runner_answers_without_a_tool():
    provider = ScriptedProvider([AIMessage(content="你好，请告诉我具体日期和对象。")])
    result = NodeRunner(request("你好"), registry(), provider).run_nodes()

    assert result.status == "answered"
    assert result.tool_calls == []
    assert result.tool_count == 0
    assert len(provider.calls) == 1


def test_runner_reports_validation_errors_to_the_model():
    provider = ScriptedProvider([
        call("stock_kline", {}),
        AIMessage(content="未取得该股走势数据。"),
    ])
    result = NodeRunner(request("某只股票走势如何？"), registry(), provider).run_nodes()

    assert result.status == "partial"
    assert result.tool_messages[0]["status"] == "error"
    observation = provider.calls[1][-1]
    payload = json.loads(observation.content)
    assert payload["status"] == "error"
    assert "symbol" in payload["error"].lower()


@pytest.mark.parametrize("answer", [
    "建议买入该股，仓位三成。",
    "建议买入该股，仓位三成。以上不构成任何买卖或仓位建议。",
])
def test_runner_refuses_explicit_trading_advice(answer):
    provider = ScriptedProvider([AIMessage(content=answer), compliance("reject")])
    result = NodeRunner(request("可以买吗？"), registry(), provider).run_nodes()

    assert result.status == "refuse"
    assert result.answer == REFUSAL
    assert result.stop_reason == "compliance_refused"
    assert result.compliance_reviewed is True


@pytest.mark.parametrize("answer", [
    "以上为历史行情与评分事实的客观梳理，不构成任何买卖或仓位建议。",
    "公开历史公告记载：股东于2026年10月12日减仓，公告来源为公司披露；这只是历史事实。",
])
def test_runner_keeps_approved_negative_or_historical_statements_intact(answer):
    provider = ScriptedProvider([AIMessage(content=answer), compliance()])
    result = NodeRunner(request("解释历史事实与风险"), registry(), provider).run_nodes()
    assert result.status == "answered" and result.answer == answer
    assert result.compliance_reviewed is True and result.model_rounds == 2
    assert provider.options[1]["tools"][0]["function"]["name"] == "submit_compliance_review"
    assert provider.options[1]["timeout_seconds"] <= 10
    assert result.tool_count == 0


@pytest.mark.parametrize("review", [AIMessage(content="review failed"), RuntimeError("timeout")])
def test_runner_withholds_candidate_when_triggered_review_fails(review):
    provider = ScriptedProvider([AIMessage(content="不构成任何仓位建议。"), review])
    result = NodeRunner(request(), registry(), provider).run_nodes()
    assert result.status == "error" and result.stop_reason == "compliance_error"
    assert result.answer == "回答合规检查未完成，本次结果未发布。"
    assert result.compliance_reviewed is False


@pytest.mark.parametrize("review_all", [False, True])
def test_runner_does_not_spend_an_extra_review_after_the_model_round_budget(review_all):
    provider = ScriptedProvider([
        *(call("market_summary", {}, f"budget_{index}") for index in range(MAX_MODEL_ROUNDS - 1)),
        AIMessage(content="历史研究事实。" if review_all else "不构成任何仓位建议。"),
    ])
    result = NodeRunner(request(), registry(), provider, review_all_answers=review_all).run_nodes()
    assert result.status == "error" and result.stop_reason == "compliance_error"
    assert result.error == "ReviewBudgetExhausted"
    assert result.model_rounds == MAX_MODEL_ROUNDS and len(provider.calls) == MAX_MODEL_ROUNDS


@pytest.mark.parametrize("review_all", [False, True])
def test_runner_does_not_publish_an_allowed_review_after_deadline(monkeypatch, review_all):
    clock = [0.0]
    monkeypatch.setattr("app.agents.runner.runner.perf_counter", lambda: clock[0])

    class SlowReviewProvider(ScriptedProvider):
        def generate_messages(self, messages, tools, **kwargs):
            response = super().generate_messages(messages, tools, **kwargs)
            if len(self.calls) == 2:
                clock[0] = 6
            return response

    provider = SlowReviewProvider([
        AIMessage(content="历史研究事实。" if review_all else "不构成任何仓位建议。"), compliance(),
    ])
    result = NodeRunner(request(), registry(), provider, deadline_seconds=5,
                         review_all_answers=review_all).run_nodes()
    assert provider.options[1]["timeout_seconds"] == 5
    assert result.status == "error" and result.error == "ReviewDeadlineExceeded"


def test_full_review_mode_checks_unflagged_answer_once_inside_runner():
    provider = ScriptedProvider([AIMessage(content="完整历史研究事实。"), compliance()])
    result = NodeRunner(request(), registry(), provider, review_all_answers=True).run_nodes()
    assert result.status == "answered" and result.answer == "完整历史研究事实。"
    assert result.compliance_reviewed is True and result.model_rounds == 2
    assert len(provider.calls) == 2


@pytest.mark.parametrize("cancel_on_call", [1, 2])
def test_full_review_mode_never_publishes_a_cancelled_candidate(monkeypatch, cancel_on_call):
    cancelled = [False]
    monkeypatch.setattr("app.agents.runner.runner._cancelled", lambda: cancelled[0])

    class CancellingReviewProvider(ScriptedProvider):
        def generate_messages(self, messages, tools, **kwargs):
            response = super().generate_messages(messages, tools, **kwargs)
            if len(self.calls) == cancel_on_call:
                cancelled[0] = True
            return response

    provider = CancellingReviewProvider([AIMessage(content="尚未获准的研究回答。"), compliance()])
    result = NodeRunner(request(), registry(), provider, review_all_answers=True).run_nodes()
    assert result.status == "cancelled" and result.stop_reason == "cancelled"
    assert "尚未获准的研究回答" not in result.answer
    assert len(provider.calls) == cancel_on_call


def test_runner_stops_at_the_model_round_budget():
    provider = ScriptedProvider([
        call("market_summary", {}, f"call_{index}")
        for index in range(MAX_MODEL_ROUNDS + 2)
    ])
    result = NodeRunner(request(), registry(), provider).run_nodes()

    assert result.stop_reason == "budget_exhausted"
    assert result.status == "partial"
    assert result.model_rounds >= MAX_MODEL_ROUNDS
    assert result.tool_count <= MAX_MODEL_ROUNDS


def test_runner_keeps_history_out_of_trusted_system_instructions():
    history = [
        {"role": "user", "content": "x" * 4000},
        {"role": "user", "content": "y" * 4000},
    ]
    runner = NodeRunner(
        request("继续"),
        registry(),
        ScriptedProvider([]),
        history=history,
    )
    assert runner.history == history
    assert history[0]["content"] not in runner.system_prompt
    assert history[1]["content"] not in runner.system_prompt


def test_business_definitions_exclude_runtime_control_tools():
    runner = NodeRunner(request(), registry(), ScriptedProvider([]))
    names = {item["function"]["name"] for item in runner.definitions}
    assert "market_summary" in names
    assert "finish" not in names
    assert "compute_result" not in names
    assert "read_evidence" not in names
    assert "update_task" not in names


def interrupted_answer():
    return AIMessage(content="其余19个交易日的 amount", response_metadata={"finish_reason": "length"})


def test_runner_rewrites_truncated_answer_once_using_existing_evidence():
    provider = ScriptedProvider([
        call("market_summary", {}), interrupted_answer(),
        AIMessage(content="完整的紧凑研究回答。", response_metadata={"finish_reason": "stop"}),
    ])
    result = NodeRunner(request(), registry(), provider).run_nodes()

    assert result.status == "answered"
    assert result.stop_reason == "answered_after_length_recovery"
    assert result.answer == "完整的紧凑研究回答。"
    assert result.model_rounds == 3 and result.tool_count == 1
    repair_context = provider.calls[2]
    assert any(isinstance(message, ToolMessage) for message in repair_context)
    assert all(message.content != interrupted_answer().content for message in repair_context)
    assert provider.options[2]["tools"] == [] and provider.options[2]["tool_choice"] == "none"


@pytest.mark.parametrize("repair_response", [
    interrupted_answer(), AIMessage(content=""),
    call("market_summary", {}, "unexpected_repair_tool"),
])
def test_runner_never_publishes_interrupted_answer_as_complete(repair_response):
    provider = ScriptedProvider([call("market_summary", {}), interrupted_answer(), repair_response])
    result = NodeRunner(request(), registry(), provider).run_nodes()

    assert result.status == "partial"
    assert result.stop_reason == "output_incomplete"
    assert "未能完成全部问题" in result.answer
    assert result.tool_messages[0]["summary"] in result.answer
    assert interrupted_answer().content not in result.answer
    assert result.tool_count == 1 and len(provider.calls) == 3


def test_runner_keeps_failed_repair_resumable_instead_of_publishing_partial_draft():
    provider = ScriptedProvider([call("market_summary", {}), interrupted_answer(), RuntimeError("upstream unavailable")])
    with pytest.raises(RecoverableRunError):
        NodeRunner(request(), registry(), provider).run_nodes()
    assert len(provider.calls) == 3


def test_runner_does_not_invent_evidence_when_both_text_attempts_are_truncated():
    provider = ScriptedProvider([interrupted_answer(), interrupted_answer()])
    result = NodeRunner(request(), registry(), provider).run_nodes()
    assert result.status == "error" and result.stop_reason == "output_incomplete"
    assert "尚无可核验的工具结果" in result.answer
    assert result.tool_calls == [] and result.model_rounds == 2


def test_runner_length_recovery_cannot_exceed_existing_round_budget():
    provider = ScriptedProvider([
        *(call("market_summary", {}, f"call_{index}") for index in range(MAX_MODEL_ROUNDS - 1)),
        interrupted_answer(),
    ])
    result = NodeRunner(request(), registry(), provider).run_nodes()
    assert result.status == "partial" and result.stop_reason == "output_incomplete"
    assert result.model_rounds == MAX_MODEL_ROUNDS
    assert len(provider.calls) == MAX_MODEL_ROUNDS


def test_runner_does_not_execute_tool_calls_from_length_interrupted_turn():
    truncated_call = call("market_summary", {})
    truncated_call.response_metadata["finish_reason"] = "length"
    provider = ScriptedProvider([truncated_call, interrupted_answer()])
    result = NodeRunner(request(), registry(), provider).run_nodes()
    assert result.status == "error" and result.tool_count == 0
    assert result.tool_calls == [] and result.stop_reason == "output_incomplete"
