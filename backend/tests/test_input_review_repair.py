"""Bounded semantic review repair without network calls or inferred query dates."""

from copy import deepcopy
from datetime import date
import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage
from pydantic import ValidationError
import pytest

from app.services import prompt_security


QUESTION = "列出近5个交易日，累计上涨幅度超过20%的股票"
TASK = "列出截至2026-10-16近5个交易日累计上涨幅度超过20%的股票。"


def decision(**updates):
    return {
        "decision": "allow", "signals": [], "reason": "普通历史股票研究请求",
        "request_kind": "research", "context_mode": "standalone",
        "current_task": TASK, "pending_slots": [],
        "date_scope": {
            "mode": "dates", "dates": ["2026-10-16"], "trading_day_comparisons": [],
        },
        "output_contract": {
            "mode": "freeform", "field_requests": [], "table_required": True,
            "row_selection": None,
        },
        **updates,
    }


def incomplete_range(**updates):
    return decision(date_scope={
        "mode": "range", "dates": [], "start_date": None,
        "end_date": "2026-10-16", "trading_day_comparisons": [],
    }, **updates)


class SequenceReviewer:
    def __init__(self, *responses, clock=None, elapsed=0):
        self.responses = list(responses)
        self.calls = []
        self.clock = clock
        self.elapsed = elapsed

    def generate_messages(self, messages, tools, **kwargs):
        self.calls.append((deepcopy(messages), deepcopy(tools), dict(kwargs)))
        if self.clock is not None:
            self.clock["now"] += self.elapsed
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return AIMessage(content="", tool_calls=[{
            "id": f"review-{len(self.calls)}", "name": "submit_input_security_review",
            "args": deepcopy(response),
        }])


def review(provider, **kwargs):
    return prompt_security.review_input(
        provider, message=QUESTION, timeout_seconds=10,
        anchor_date=date(2026, 10, 18), **kwargs,
    )


def test_repair_preserves_original_question_context_and_lookback_task(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(prompt_security, "perf_counter", lambda: clock["now"])
    invalid = incomplete_range()
    provider = SequenceReviewer(invalid, decision(), clock=clock, elapsed=2)
    context = {"preferences": ["简洁中文"], "recent_user_intents": [QUESTION]}
    page = {"trade_date": "2026-10-16", "symbol": None}

    result = review(provider, task_context=context, page_defaults=page)

    assert result.review_attempts == 2
    assert result.current_task == TASK
    assert result.requested_date == date(2026, 10, 16)
    assert result.date_scope.mode == "dates"
    assert result.date_scope.start_date is None
    assert result.date_scope.end_date is None
    assert result.date_scope.trading_day_comparisons == []
    assert len(provider.calls) == 2
    first = json.loads(provider.calls[0][0][-1].content)
    retry = json.loads(provider.calls[1][0][-1].content)
    for field in ("user_message", "trusted_defaults", "task_context", "display_field_catalog"):
        assert retry[field] == first[field]
    assert retry["user_message"] == QUESTION
    assert retry["trusted_defaults"] == {"anchor_date": "2026-10-18", "page": page}
    assert retry["task_context"] == context
    assert retry["previous_review"] == invalid
    assert any(error["loc"][0] == "date_scope" for error in retry["validation_errors"])
    assert 0 < provider.calls[0][2]["timeout_seconds"] <= 10
    assert 0 < provider.calls[1][2]["timeout_seconds"] <= 8


def test_other_business_contract_validation_can_be_repaired():
    invalid = decision(output_contract={
        "mode": "freeform", "field_requests": [], "table_required": True,
        "row_selection": {"requested": "筛选后的股票名单", "limit": 0, "offset": 0},
    })
    provider = SequenceReviewer(invalid, decision())

    result = review(provider)

    assert result.review_attempts == 2
    assert result.output_contract.row_selection is None
    retry = json.loads(provider.calls[1][0][-1].content)
    assert any(error["loc"][0] == "output_contract" for error in retry["validation_errors"])


def test_two_invalid_reviews_raise_without_guessing_a_start_date():
    invalid = incomplete_range()
    provider = SequenceReviewer(invalid, invalid, decision())

    with pytest.raises(ValidationError):
        review(provider)

    assert len(provider.calls) == 2
    retry = json.loads(provider.calls[1][0][-1].content)
    assert retry["previous_review"]["date_scope"]["start_date"] is None
    assert provider.responses == [decision()]


def test_explicit_date_range_is_valid_without_repair():
    task = decision(current_task="列出2026-10-12至2026-10-16累计涨幅超过20%的股票。",
                    date_scope={"mode": "range", "start_date": "2026-10-12",
                                "end_date": "2026-10-16", "trading_day_comparisons": []})
    provider = SequenceReviewer(task)

    result = review(provider)

    assert result.review_attempts == 1
    assert len(provider.calls) == 1
    assert result.date_scope.start_date == date(2026, 10, 12)
    assert result.date_scope.end_date == date(2026, 10, 16)


def test_assessment_defaults_to_one_review_attempt():
    assert prompt_security.PromptInjectionAssessment.model_validate(decision()).review_attempts == 1


@pytest.mark.parametrize("updates", [
    {"decision": "refuse", "signals": ["instruction_override"]},
    {"signals": ["instruction_override"]},
    {"reason": ""},
    {"decision": "invalid_decision"},
    {"signals": ["invalid_signal"]},
])
def test_security_decision_or_validation_errors_are_not_retried(updates):
    provider = SequenceReviewer(incomplete_range(**updates), decision())

    with pytest.raises(ValidationError):
        review(provider)

    assert len(provider.calls) == 1


def test_allowed_review_with_signals_is_not_retried():
    provider = SequenceReviewer(decision(signals=["instruction_override"]), decision())

    with pytest.raises(ValueError):
        review(provider)

    assert len(provider.calls) == 1


def test_valid_refusal_is_preserved_without_retry():
    provider = SequenceReviewer(decision(decision="refuse", signals=["instruction_override"]))

    result = review(provider)

    assert result.detected
    assert result.review_attempts == 1
    assert len(provider.calls) == 1


@pytest.mark.parametrize("error", [RuntimeError("provider unavailable"), TimeoutError("timeout")])
def test_transport_failure_is_not_retried(error):
    provider = SequenceReviewer(error, decision())

    with pytest.raises(type(error)):
        review(provider)

    assert len(provider.calls) == 1


def test_expired_total_budget_does_not_start_a_repair(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(prompt_security, "perf_counter", lambda: clock["now"])
    provider = SequenceReviewer(incomplete_range(), decision(), clock=clock, elapsed=10)

    with pytest.raises((ValidationError, TimeoutError)):
        review(provider)

    assert len(provider.calls) == 1


def test_failed_repair_keeps_attempt_count_in_exception_and_runtime_traces():
    from app.agents.react_runtime import runtime
    from app.agents.tools import TOOL_SCHEMAS
    from app.models import AgentChatRequest

    provider = SequenceReviewer(incomplete_range(), incomplete_range())
    with pytest.raises(ValidationError) as raised:
        review(provider)
    assert raised.value.input_review_attempts == 2

    provider = SequenceReviewer(incomplete_range(), incomplete_range())
    registry = SimpleNamespace(events=[], profile="test", schemas=lambda: TOOL_SCHEMAS,
                               is_enabled=lambda _: True)
    response = runtime.run(
        AgentChatRequest(session_id="input-review-repair-failure", message=QUESTION),
        registry, provider,
    )

    assert response.task_status == "error"
    assert response.stop_reason == "input_policy_error"
    assert response.tool_calls == []
    assert len(provider.calls) == 2
    execution = next(trace.output for trace in response.tool_results if trace.name == "react_execution")
    input_trace = next(trace.output for trace in response.tool_results if trace.name == "react_input_security")
    assert execution["tool_calls"] == 0
    assert execution["input_security_checks"] == 2
    assert input_trace["review_attempts"] == 2


def test_cancelled_after_invalid_review_does_not_start_a_repair():
    from app.agents.react_runtime.lifecycle import CURRENT_CONTROL

    provider = SequenceReviewer(incomplete_range(), decision())
    token = CURRENT_CONTROL.set(SimpleNamespace(cancelled=lambda: len(provider.calls) >= 1))
    try:
        with pytest.raises(InterruptedError) as raised:
            review(provider)
        assert raised.value.input_review_attempts == 1
        assert len(provider.calls) == 1
    finally:
        CURRENT_CONTROL.reset(token)
