"""Protocol diagnostics preserve actionable shape without persisting raw judge failures."""

import json

from langchain_core.messages import AIMessage
import pytest

from app.models import AgentChatResponse
from app.services.llm_provider import NativeFunctionCallingError
from app.services.protocol_diagnostics import safe_protocol_metadata
from evals.golden.judge import judge_turn
from evals.golden.protocol_metrics import protocol_metrics


class Judge:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def generate_messages(self, messages, tools, **kwargs):
        self.calls += 1
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return AIMessage(content="PRIVATE_RESPONSE", response_metadata={"finish_reason": "stop"},
                         tool_calls=[{"name": tools[0]["function"]["name"], "id": "PRIVATE_ID", "args": result}])


def evaluate(*responses):
    provider = Judge(*responses)
    review = judge_turn(provider, user="PRIVATE_USER", expectations=["交付。"], drafts=[],
        response=AgentChatResponse(session_id="diagnostics", intent="research", answer="PRIVATE_ANSWER",
                                   generated_by="scripted"))
    assert provider.calls == 2 and not provider.responses
    return review


def audit():
    return {key: {"evidence_relation": "no_claim", "reason": "No claim"} for key in ("safety", "source", "factual")}


def test_schema_errors_have_safe_paths_and_codes_but_never_inputs_messages_or_extra_keys():
    review = evaluate({"checks": [{"index": 0, "passed": "PRIVATE_BOOL", "reason": "PRIVATE_REASON",
                                   "PRIVATE_EXTRA_KEY": "PRIVATE_VALUE"}]}, audit())
    assert review.errors == {"final_delivery": "ValidationError"}
    detail = review.diagnostics["final_delivery"]
    assert detail["finish_reason"] == "stop" and detail["schema_error_count"] == 2
    assert {tuple(item["path"]) for item in detail["schema_errors"]} == {
        ("checks", 0, "passed"), ("checks", 0, "<unknown>")}
    assert {item["type"] for item in detail["schema_errors"]} == {"bool_type", "extra_forbidden"}
    assert "PRIVATE" not in json.dumps(review.diagnostics)
    assert review.safety["passed"] is True and review.judgements is None


def test_provider_metadata_and_partial_dimension_failure_are_retained_independently():
    error = NativeFunctionCallingError("PRIVATE_EXCEPTION", diagnostics={
        "finish_reason": "length", "failure_category": "tool_arguments", "tool_call_count": 0,
        "invalid_tool_call_count": 1, "response_kind": "ai_message", "private": "PRIVATE_VALUE"})
    visible = audit()
    visible["source"]["evidence_relation"] = "PRIVATE_RELATION"
    review = evaluate(error, visible)
    assert review.errors == {"final_delivery": "NativeFunctionCallingError", "visible_audit.source": "ValidationError"}
    assert review.diagnostics["final_delivery"]["finish_reason"] == "length"
    assert review.diagnostics["visible_audit.source"]["schema_errors"] == [
        {"path": ["evidence_relation"], "type": "literal_error"}]
    assert review.source is None and review.factual["passed"] is True
    assert "PRIVATE" not in json.dumps(review.diagnostics)
    metrics = protocol_metrics([{"errors": review.errors, "diagnostics": review.diagnostics}],
                               errors_key="errors", diagnostics_key="diagnostics")
    assert metrics["judge_phase_protocol_error_count"] == metrics["judge_dimension_error_count"] == 1
    assert metrics["judge_phase_call_count"] == 2 and metrics["judge_phase_protocol_error_rate"] == 0.5
    assert metrics["judge_protocol_affected_phase_count"] == 2
    assert metrics["judge_protocol_affected_phase_rate"] == 1


def test_three_dimension_schema_failures_affect_one_phase_call():
    review = evaluate({"checks": [{"index": 0, "passed": True, "reason": "Delivered"}]},
                      {key: {"evidence_relation": "invalid", "reason": "Invalid enum"}
                       for key in ("safety", "source", "factual")})
    metrics = protocol_metrics([{"errors": review.errors, "diagnostics": review.diagnostics}],
                               errors_key="errors", diagnostics_key="diagnostics")
    assert metrics["judge_phase_protocol_error_count"] == 0
    assert metrics["judge_dimension_error_count"] == 3
    assert metrics["judge_phase_call_count"] == 2
    assert metrics["judge_protocol_affected_phases"] == {"visible_audit": 1}
    assert metrics["judge_protocol_affected_phase_count"] == 1
    assert metrics["judge_protocol_affected_phase_rate"] == 0.5


@pytest.mark.parametrize("value", [
    {"finish_reason": "PRIVATE_SECRET", "response_kind": "PRIVATE_TYPE", "tool_call_count": True},
    {"arguments": [{"shape": {"private": "PRIVATE_SECRET"}}, {"shape": "object", "json_failure": ["PRIVATE"]}]},
])
def test_untrusted_diagnostic_metadata_cannot_smuggle_arbitrary_strings(value):
    serialized = json.dumps(safe_protocol_metadata(value))
    assert "PRIVATE" not in serialized


def test_protocol_metrics_exclude_budget_denials_and_do_not_guess_legacy_denominator():
    records = [{"errors": {"final_delivery": "BudgetExceeded", "visible_audit": "BudgetExceeded"},
                "diagnostics": {"final_delivery": {"failure_category": "request"}}},
               {"errors": {"visible_audit.factual": "InvalidCounterevidence",
                           "visible_audit.source": "InvalidFindingLocation"},
                "diagnostics": {"visible_audit": {"response_kind": "ai_message"}}}]
    metrics = protocol_metrics(records, errors_key="errors", diagnostics_key="diagnostics")
    assert metrics["judge_phase_call_count"] == 1 and metrics["judge_phase_protocol_error_rate"] == 0
    assert metrics["judge_dimension_error_count"] == 2
    assert metrics["judge_protocol_affected_phase_count"] == 0
    assert metrics["judge_protocol_affected_phase_rate"] == 0
    legacy = protocol_metrics([{"errors": {"final_delivery": "NativeFunctionCallingError"}}],
                              errors_key="errors", diagnostics_key="diagnostics")
    assert legacy["judge_phase_protocol_error_count"] == 1
    assert legacy["judge_phase_call_count"] is None and legacy["judge_phase_protocol_error_rate"] is None
    assert legacy["judge_protocol_affected_phase_count"] == 1
    assert legacy["judge_protocol_affected_phase_rate"] is None
