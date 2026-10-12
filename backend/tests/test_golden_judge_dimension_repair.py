"""One budgeted schema-only repair through real HTTP serialization; no live model."""

from copy import deepcopy
import json
import hashlib

import httpx
import pytest

from app.models import AgentChatResponse
from app.services.llm_provider import capture_llm_usage
from evals.golden.judge import REPAIR_PROTOCOL_VERSION, judge_turn
from evals.golden.protocol_metrics import protocol_metrics
from evals.golden.runner import Budget, BudgetedProvider
from test_langchain_provider import completion, provider_for


def audit(*dimensions):
    return {dimension: {"reason": "No assertion", "claims": []}
            for dimension in dimensions or ("factual", "source", "safety")}


def claim(target, relation, quote="研究暂停。", evidence=()):
    return {"surface_id": "final", "quote": quote, "target": target,
            "evidence_relation": relation, "evidence": list(evidence)}


def evaluate(packets, *, budget=3, answer="研究暂停。", expectations=(), on_reserve=None):
    requests, pending = [], deepcopy(packets)
    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        assert pending, "Unexpected hidden retry"
        spec = pending.pop(0)
        if isinstance(spec, httpx.Response):
            return spec
        arguments, finish, count = spec if isinstance(spec, tuple) else (spec, "tool_calls", 1)
        calls = [{"id": f"call-{index}", "type": "function", "function": {
            "name": body["tools"][0]["function"]["name"],
            "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments)}}
            for index in range(count)]
        packet = completion({"role": "assistant", "content": None, "tool_calls": calls})
        packet["choices"][0]["finish_reason"] = finish
        return httpx.Response(200, json=packet)
    counter = Budget(budget, on_take=on_reserve)
    with provider_for(handle, retries=0) as provider, capture_llm_usage() as usage:
        review = judge_turn(BudgetedProvider(provider, counter), user="研究请求", expectations=expectations,
            response=AgentChatResponse(session_id="dimension-repair", intent="research", answer=answer,
                                       generated_by="test-only"), drafts=[answer])
    return review, requests, counter, usage, pending


def metrics(review):
    return protocol_metrics([{"errors": review.errors, "diagnostics": review.diagnostics}],
                            errors_key="errors", diagnostics_key="diagnostics")


def test_real_wire_repairs_only_missing_m20_dimensions_once_and_counts_original_failure():
    initial = audit("factual")
    review, requests, counter, usage, pending = evaluate([initial, audit("source", "safety")])
    assert len(requests) == counter.used == usage.call_count == usage.measured_call_count == 2
    assert not pending and not review.errors and usage.failed_call_count == 0
    assert usage.total_tokens == 32
    assert all(getattr(review, dimension)["passed"] is True for dimension in audit())
    first, repair = [json.loads(item["messages"][-1]["content"]) for item in requests]
    assert repair["requested_dimensions"] == ["source", "safety"]
    assert repair["audit_attempt"] == 2 and first["phase"] == repair["phase"] == "visible_audit"
    for key in ("surfaces", "evidence_catalog", "synthetic_evidence", "business_observations"):
        assert repair[key] == first[key]
    schema = requests[1]["tools"][0]["function"]["parameters"]
    assert set(schema["properties"]) == set(schema["required"]) == {"source", "safety"}
    assert schema["additionalProperties"] is False
    attempts = review.diagnostics["visible_audit"]["attempts"]
    assert len(attempts) == 2 and attempts[0]["errors"] == {
        "visible_audit.source": "ValidationError", "visible_audit.safety": "ValidationError"}
    assert attempts[1]["errors"] == {}
    assert review.diagnostics["visible_audit"]["repair_protocol_version"] == REPAIR_PROTOCOL_VERSION
    assert review.diagnostics["visible_audit"]["repair_prompt_sha256"] == hashlib.sha256(
        requests[1]["messages"][0]["content"].encode("utf-8")).hexdigest()
    result = metrics(review)
    assert result["judge_phase_call_count"] == 2 and result["judge_dimension_error_count"] == 2
    assert result["judge_protocol_affected_phase_count"] == 1
    assert result["judge_protocol_affected_phase_rate"] == .5
    assert result["judge_schema_repair_call_count"] == 1
    assert result["judge_schema_recovered_dimension_count"] == 2


@pytest.mark.parametrize("valid_kind", ["negative", "unknown", "invalid_reference"])
def test_existing_verdicts_and_invalid_references_are_locked_during_other_dimension_repair(valid_kind):
    initial = audit("factual", "safety")
    if valid_kind == "negative":
        initial["safety"]["claims"] = [claim("safety", "contradicted")]
        expected, dimension = False, "safety"
    else:
        initial["factual"]["claims"] = [claim("world_fact",
            "insufficient_evidence" if valid_kind == "unknown" else "supported",
            evidence=() if valid_kind == "unknown" else [{"ref_id": "unknown-reference"}])]
        expected, dimension = None, "factual"
    review, requests, _, _, _ = evaluate([initial, audit("source")])
    assert len(requests) == 2 and getattr(review, dimension)["passed"] is expected
    assert json.loads(requests[1]["messages"][-1]["content"])["requested_dimensions"] == ["source"]
    if valid_kind == "invalid_reference":
        assert review.errors == {"visible_audit.factual": "claims[0]:UnknownEvidenceReference"}
    else:
        assert not review.errors


@pytest.mark.parametrize("malformed", [None, "PRIVATE_REASON", {"reason": "Missing claims"},
                                      {"reason": "Wrong claims shape", "claims": "PRIVATE_CLAIMS"}])
def test_unparseable_dimension_envelopes_can_be_repaired_without_leaking_bad_values(malformed):
    initial = audit()
    initial["source"] = malformed
    review, requests, _, _, _ = evaluate([initial, audit("source")])
    assert not review.errors and len(requests) == 2
    assert "PRIVATE" not in json.dumps(review.diagnostics)
    assert "PRIVATE" not in requests[1]["messages"][-1]["content"]


@pytest.mark.parametrize("bad", [None, {}, "broken-json", (audit("source", "safety"), "length", 1),
    (audit("source", "safety"), "tool_calls", 2), audit(),
    {**audit("source", "safety"), "PRIVATE_EXTRA": "PRIVATE_VALUE"}])
def test_repair_failures_never_loop_or_override_the_original_valid_dimension(bad):
    review, requests, counter, usage, pending = evaluate([audit("factual"), bad])
    assert len(requests) == counter.used == usage.call_count == 2 and not pending
    assert review.factual["passed"] is True and review.source is review.safety is None
    assert review.errors and len(review.diagnostics["visible_audit"]["attempts"]) == 2
    assert "PRIVATE" not in json.dumps(review.diagnostics)
    result = metrics(review)
    assert result["judge_phase_call_count"] == result["judge_protocol_affected_phase_count"] == 2
    assert result["judge_dimension_error_count"] >= 2
    assert result["judge_schema_recovered_dimension_count"] == 0


def test_partial_repair_preserves_its_valid_dimension_and_keeps_the_rest_unresolved():
    review, requests, _, _, _ = evaluate([audit("factual"), audit("source")])
    assert len(requests) == 2 and review.factual["passed"] is review.source["passed"] is True
    assert review.safety is None and review.errors == {"visible_audit.safety": "ValidationError"}
    assert metrics(review)["judge_dimension_error_count"] == 3
    assert metrics(review)["judge_schema_recovered_dimension_count"] == 1


def test_budget_denial_is_not_a_request_and_keeps_all_original_missing_dimensions():
    review, requests, counter, usage, _ = evaluate([audit("factual")], budget=1)
    assert len(requests) == counter.used == usage.call_count == 1 and counter.denied == 1
    assert review.factual["passed"] is True and review.source is review.safety is None
    assert review.errors["visible_audit.repair"] == "BudgetExceeded"
    assert metrics(review)["judge_phase_call_count"] == 1
    assert metrics(review)["judge_schema_repair_call_count"] == 0
    assert review.diagnostics["visible_audit"]["attempts"][1]["request_issued"] is False


def test_repair_budget_persistence_failure_prevents_the_second_http_request():
    def reserve(count):
        if count == 2:
            raise OSError("PRIVATE persistence failure")
    review, requests, counter, usage, _ = evaluate([audit("factual")], on_reserve=reserve)
    assert counter.used == 2 and len(requests) == usage.call_count == 1
    assert review.errors["visible_audit.repair"] == "BudgetPersistenceError"
    assert metrics(review)["judge_phase_call_count"] == 1
    assert "PRIVATE" not in json.dumps(review.diagnostics)


def test_nested_claim_schema_errors_and_semantic_reference_failures_do_not_trigger_repair():
    initial = audit()
    initial["source"]["claims"] = [claim("source_attribution", "supported")]
    initial["factual"]["claims"] = [claim("world_fact", "PRIVATE_INVALID_RELATION")]
    review, requests, counter, _, _ = evaluate([initial])
    assert len(requests) == counter.used == 1
    assert review.errors == {"visible_audit.factual": "ValidationError",
                             "visible_audit.source": "claims[0]:MissingClaimEvidence"}
    assert metrics(review)["judge_schema_repair_call_count"] == 0


def test_repair_schema_success_with_bad_reference_is_not_reaudited_for_a_better_label():
    repair = audit("source", "safety")
    repair["source"]["claims"] = [claim("source_attribution", "supported")]
    review, requests, _, _, _ = evaluate([audit("factual"), repair])
    assert len(requests) == 2 and review.safety["passed"] is True and review.source["passed"] is None
    assert review.errors == {"visible_audit.source": "claims[0]:MissingClaimEvidence"}


def test_final_delivery_negative_is_not_repaired_or_overwritten_by_visible_repair():
    delivery = {"checks": [{"index": 0, "passed": False, "reason": "Missing required list",
        "failure_kind": "missing_delivery", "requirement_quote": "交付名单。"}]}
    review, requests, counter, usage, _ = evaluate([delivery, audit("factual"), audit("source", "safety")],
                                                 expectations=("交付名单。",))
    assert len(requests) == counter.used == usage.call_count == 3
    assert review.judgements[0]["passed"] is False and not review.errors
    assert metrics(review)["judge_phase_call_count"] == 3
