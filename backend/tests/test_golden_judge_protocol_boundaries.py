"""Strict Judge wire failures and lossless grouping of identical visible text."""

from copy import deepcopy
import json

import httpx
import pytest

from app.models import AgentChatResponse
from app.services.llm_provider import capture_llm_usage
from evals.golden.judge import judge_turn
from evals.golden.runner import Budget, BudgetedProvider
from test_langchain_provider import completion, provider_for


TOOL = "submit_golden_visible_audit"


def audit():
    return {key: {"reason": "No substantive assertion in this dimension", "claims": []}
            for key in ("factual", "source", "safety")}


def call(arguments, index=0):
    return {"id": f"call-{index}", "type": "function", "function": {
        "name": TOOL, "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments)}}


def evaluate_wire(calls, *, finish_reason="tool_calls", answer="研究暂停。", drafts=(), max_calls=5,
                  expected_denials=0):
    requests = []
    packet = completion({"role": "assistant", "content": None, "tool_calls": calls})
    packet["choices"][0]["finish_reason"] = finish_reason

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=packet)

    budget = Budget(max_calls)
    with provider_for(handle, retries=2) as provider, capture_llm_usage() as usage:
        review = judge_turn(BudgetedProvider(provider, budget), user="研究请求", expectations=(),
            response=AgentChatResponse(session_id="protocol", intent="research", answer=answer,
                                       generated_by="test-only"), drafts=drafts)
        assert provider.chat_model.root_client.max_retries == 2
    assert len(requests) == budget.used == usage.call_count == usage.measured_call_count == 1
    assert budget.denied == expected_denials and usage.token_usage_complete
    assert usage.total_tokens == 16
    assert requests[0]["tool_choice"] == {"type": "function", "function": {"name": TOOL}}
    assert requests[0]["max_tokens"] == 4800
    return review, requests[0], usage


@pytest.mark.parametrize("calls,finish_reason,invalid_count", [
    # Capture r03: a flattened JSON object, then four identical malformed copies.
    ([call({"factual": "PRIVATE_REASON", "claims": [], "source": "PRIVATE_SOURCE"}),
      *(call('{"factual": PRIVATE_REASON, "claims": [], "source": "PRIVATE_SOURCE"}', i)
        for i in range(1, 5))], "length", 4),
    # Capture s14: a flattened object, then a second malformed placeholder call.
    ([call({"factual": "PRIVATE_REASON", "claims": []}),
      call('{"factual": "占位", "source": 占位, "safety": 占位}', 1)], "tool_calls", 1),
    # Neither a fully valid first call nor two individually valid calls permit salvage.
    ([call(audit()), call('{"factual": PRIVATE_REASON}', 1)], "tool_calls", 1),
    ([call(audit()), call(audit(), 1)], "tool_calls", 0),
    # A parsable object does not make a token-limited response complete.
    ([call(audit())], "length", 0),
])
def test_native_judge_rejects_incomplete_or_non_unique_calls_without_retry(calls, finish_reason, invalid_count):
    review, _, usage = evaluate_wire(calls, finish_reason=finish_reason)
    assert review.factual is review.source is review.safety is None
    assert set(review.errors) == {"visible_audit"}
    detail = review.diagnostics["visible_audit"]
    assert detail["finish_reason"] == finish_reason
    assert detail["tool_call_count"] == len(calls) - invalid_count
    assert detail["invalid_tool_call_count"] == invalid_count
    assert usage.failed_call_count == int(invalid_count > 0)
    assert "PRIVATE" not in json.dumps(review.diagnostics)


@pytest.mark.parametrize("flattened", [
    {"factual": "PRIVATE_REASON", "claims": [], "source": "PRIVATE_SOURCE"},
    {"factual": "PRIVATE_REASON", "claims": []},
])
def test_single_flattened_json_is_not_a_complete_three_dimension_audit(flattened):
    review, _, usage = evaluate_wire([call(flattened)])
    assert review.factual is review.source is review.safety is None
    assert set(review.errors) == {"visible_audit"}
    assert usage.failed_call_count == 0  # Native JSON passed; Judge schema rejected it.
    assert "PRIVATE" not in json.dumps(review.diagnostics)


def test_one_complete_nested_audit_is_accepted_on_real_wire():
    review, request, usage = evaluate_wire([call(audit())])
    assert not review.errors and usage.failed_call_count == 0
    assert all(getattr(review, dimension)["passed"] is True for dimension in audit())
    schema = request["tools"][0]["function"]["parameters"]
    assert set(schema["required"]) == {"factual", "source", "safety"}
    assert schema["additionalProperties"] is False
    for field in schema["required"]:
        child = schema["properties"][field]
        assert child["type"] == "object" and child["additionalProperties"] is False
        assert set(child["required"]) == {"reason", "claims"}


def test_factual_only_call_preserves_missing_dimensions_as_review():
    # Live m20 returned one legal call but omitted source and safety entirely.
    from evals.golden.protocol_metrics import protocol_metrics

    review, _, usage = evaluate_wire([call({"factual": audit()["factual"]})], max_calls=1, expected_denials=1)
    assert review.factual["passed"] is True
    assert review.source is review.safety is None
    assert review.errors == {"visible_audit.source": "ValidationError",
                             "visible_audit.safety": "ValidationError",
                             "visible_audit.repair": "BudgetExceeded"}
    assert usage.call_count == 1 and usage.failed_call_count == 0
    for dimension in ("source", "safety"):
        errors = review.diagnostics[f"visible_audit.{dimension}"]["schema_errors"]
        assert errors == [{"path": [], "type": "model_type", "input_shape": {"shape": "null"}}]
    metrics = protocol_metrics([{"errors": review.errors, "diagnostics": review.diagnostics}],
                               errors_key="errors", diagnostics_key="diagnostics")
    assert metrics["judge_phase_protocol_error_count"] == 0
    assert metrics["judge_protocol_affected_phase_count"] == 1
    assert metrics["judge_dimension_error_count"] == 2


def test_surface_grouping_is_exact_ordered_and_preserves_every_original_location():
    from evals.golden.judge_surfaces import prepare_visible_surfaces

    drafts = ["最终正文", "错误草稿", "错误草稿", "最终正文 ", "最终正文\n", "最终正文"]
    before = deepcopy(drafts)
    surfaces, texts, aliases = prepare_visible_surfaces("最终正文", drafts)
    assert drafts == before
    assert surfaces == [
        {"surface_id": "final", "text": "最终正文",
         "equivalent_surface_ids": ["final", "draft_0", "draft_5"]},
        {"surface_id": "draft_1", "text": "错误草稿", "equivalent_surface_ids": ["draft_1", "draft_2"]},
        {"surface_id": "draft_3", "text": "最终正文 "},
        {"surface_id": "draft_4", "text": "最终正文\n"},
    ]
    assert texts == {"final": "最终正文", **{f"draft_{i}": text for i, text in enumerate(drafts)}}
    assert aliases == {"final": ["final", "draft_0", "draft_5"], "draft_1": ["draft_1", "draft_2"]}
    surfaces[0]["equivalent_surface_ids"].append("invented")
    assert "invented" not in aliases["final"] and "invented" not in texts


def test_empty_text_and_unicode_variants_are_neither_dropped_nor_normalized():
    from evals.golden.judge_surfaces import prepare_visible_surfaces

    surfaces, texts, aliases = prepare_visible_surfaces("", ["", " ", "é", "e\u0301"])
    assert [item["text"] for item in surfaces] == ["", " ", "é", "e\u0301"]
    assert aliases == {"final": ["final", "draft_0"]} and len(texts) == 5


def test_judge_groups_only_identical_text_and_still_validates_an_aliased_wrong_draft():
    # A scripted safety contradiction checks retention/binding, not model accuracy.
    decision = audit()
    decision["safety"]["claims"] = [{"surface_id": "draft_2", "quote": "保证明天上涨",
        "target": "safety", "evidence_relation": "contradicted", "evidence": []}]
    drafts = ["研究暂停。", "保证明天上涨", "保证明天上涨", "研究暂停。 "]
    review, request, _ = evaluate_wire([call(decision)], drafts=drafts)
    payload = json.loads(request["messages"][-1]["content"])
    assert payload["surfaces"] == [
        {"surface_id": "final", "text": "研究暂停。", "equivalent_surface_ids": ["final", "draft_0"]},
        {"surface_id": "draft_1", "text": "保证明天上涨", "equivalent_surface_ids": ["draft_1", "draft_2"]},
        {"surface_id": "draft_3", "text": "研究暂停。 "},
    ]
    assert review.errors == {} and review.safety["passed"] is False
    assert review.diagnostics["visible_audit"]["surface_aliases"] == {
        "final": ["final", "draft_0"], "draft_1": ["draft_1", "draft_2"]}
    assert review.safety["claims"][0]["surface_id"] == "draft_2"
    assert review.safety["claims"][0]["validated_evidence_relation"] == "contradicted"


def test_unknown_surface_alias_cannot_be_invented_by_model():
    decision = audit()
    decision["safety"]["claims"] = [{"surface_id": "draft_999", "quote": "研究暂停。",
        "target": "safety", "evidence_relation": "contradicted", "evidence": []}]
    review, _, _ = evaluate_wire([call(decision)], drafts=["研究暂停。"])
    assert review.safety["passed"] is None
    assert review.errors == {"visible_audit.safety": "claims[0]:InvalidClaimLocation"}
