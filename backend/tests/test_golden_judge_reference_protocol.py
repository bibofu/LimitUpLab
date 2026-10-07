"""Exercise the real v16 wire/resolver boundary, without a model or output repair."""

from copy import deepcopy
import json

from langchain_core.messages import AIMessage
import pytest

from app.models import AgentChatResponse, AgentToolTrace
from evals.golden.calibration import load_calibration_cases, run_calibration
from evals.golden.judge import Judgements, VisibleAudit, judge_turn, source_equivalence
from evals.golden.judge_claims import DIMENSION_TARGETS
from evals.golden.judge_schema import inline_local_refs
from golden_claim_fixture import catalog_entries


def response(count=4):
    record = {"tool": "sample_query", "result_state": "ok", "arguments": {"day": "2026-09-22"},
              "payload": {"count": count, "source": "synthetic-golden-world-v1", "synthetic": True}}
    return AgentChatResponse(session_id="reference-protocol", intent="research", answer="数量为9。",
        generated_by="test-only", tool_calls=["sample_query"], tool_results=[
            AgentToolTrace(name="sample_query", summary="executed", status="success",
                input=record["arguments"], output=record["payload"]),
            AgentToolTrace(name="react_execution", summary="evidence", output={"evidence": {"sample": record}})])


def ref(payload, *path):
    entry = next(item for item in catalog_entries(payload) if item["path"] == list(path))
    return {"ref_id": entry["ref_id"]}


def audit(*claims):
    return {key: {"reason": "Scripted transport decision, not semantic accuracy",
                  "claims": list(claims) if key == "factual" else []}
            for key in ("factual", "source", "safety")}


def claim(references, relation="contradicted"):
    return {"surface_id": "final", "quote": "数量为9", "target": "world_fact",
            "evidence_relation": relation, "evidence": references}


class RawJudge:
    def __init__(self, *actions):
        self.actions, self.calls, self.tools = list(actions), [], []

    def generate_messages(self, messages, tools, **kwargs):
        payload = json.loads(messages[-1].content)
        self.calls.append(payload)
        self.tools.append(deepcopy(tools))
        action = self.actions.pop(0)
        args = action(payload) if callable(action) else action
        return AIMessage(content="", tool_calls=[{"name": tools[0]["function"]["name"],
            "id": "judge-wire", "args": args}])


def evaluate(provider, *, expectations=(), candidate=None):
    return judge_turn(provider, user="说明数量。", expectations=expectations,
        response=candidate or response(), drafts=[])


def delivery(payload):
    return {"checks": [{"index": 0, "passed": False, "reason": "实际4与回答9冲突",
        "failure_kind": "contradicted", "requirement_quote": "说明数量。", "answer_quote": "数量为9",
        "counterevidence": [ref(payload, "synthetic_evidence", "sample", "payload", "count")]}]}


def test_both_stages_resolve_catalog_ids_and_preserve_model_selections():
    provider = RawJudge(delivery, lambda p: audit(claim([
        ref(p, "synthetic_evidence", "sample", "payload", "count")])))
    result = evaluate(provider, expectations=("说明数量。",))
    final, visible = provider.calls
    assert final["evidence_catalog"] == visible["evidence_catalog"]
    selected = ref(final, "synthetic_evidence", "sample", "payload", "count")
    original = {"path": ["synthetic_evidence", "sample", "payload", "count"], "value": 4}
    assert result.judgements[0]["reported_counterevidence"] == [selected]
    assert result.judgements[0]["counterevidence"] == [original]
    assert result.factual["claims"][0]["reported_evidence"] == [selected]
    assert result.factual["claims"][0]["evidence"] == [original]
    assert result.judgements[0]["passed"] is result.factual["passed"] is False
    assert not result.errors and len(provider.calls) == 2
    for tools in provider.tools:
        assert_reference_schema(tools[0]["function"]["parameters"])


@pytest.mark.parametrize("bad", [
    {"path": ["synthetic_evidence", "sample", "payload", "count"], "value": 4},
    {"ref_id": "PRIVATE_VALUE", "value": 4}, {"ref_id": True},
])
def test_model_cannot_submit_old_paths_values_or_coerced_ids(bad):
    provider = RawJudge(audit(claim([bad])))
    result = evaluate(provider)
    assert result.errors == {"visible_audit.factual": "ValidationError"}
    assert result.factual is None and result.source["passed"] is True
    assert len(provider.calls) == 1
    assert "PRIVATE_VALUE" not in json.dumps(result.diagnostics)


@pytest.mark.parametrize("relation", ["supported", "contradicted", "insufficient_evidence"])
def test_unknown_id_is_invalid_not_a_successful_unknown_prediction(relation):
    submitted = [{"ref_id": "not-in-this-catalog"}]
    result = evaluate(RawJudge(audit(claim(submitted, relation))))
    check = result.factual
    assert check["passed"] is None and check["validation_error"] == "claims[0]:UnknownEvidenceReference"
    assert check["claims"][0]["reported_evidence"] == submitted
    assert check["claims"][0]["reported_evidence_relation"] == relation
    assert check["claims"][0]["validated_evidence_relation"] is None


def test_invalid_id_is_not_counted_as_a_matched_expected_unknown_by_calibration():
    case = next(c for c in load_calibration_cases() if c.id == "absence_claim_partial_source")
    finding = claim([{"ref_id": "unknown"}], "insufficient_evidence")
    finding["quote"] = case.answer
    provider = RawJudge({"checks": [{"index": 0, "passed": False, "reason": "Missing required explanation",
        "failure_kind": "missing_delivery", "requirement_quote": case.expectations[0]}]}, audit(finding))
    report = run_calibration(provider, cases=[case], max_calls=2)
    assert report["summary"]["counts"] == {"review": 1}
    assert report["summary"]["expected_unknown_matches"] == 0
    assert report["results"][0]["visible_checks"]["factual"]["validation_error"] == "claims[0]:UnknownEvidenceReference"


def test_one_valid_contradiction_survives_an_independent_invalid_reference():
    provider = RawJudge(lambda p: audit(claim([{"ref_id": "unknown"}]), claim([
        ref(p, "synthetic_evidence", "sample", "payload", "count")])))
    result = evaluate(provider)
    assert result.factual["passed"] is False
    assert result.factual["validation_error"] == "claims[0]:UnknownEvidenceReference"
    assert result.factual["claims"][1]["validated_evidence_relation"] == "contradicted"


@pytest.mark.parametrize("passed", [False, True, None])
def test_unknown_delivery_citation_preserves_original_verdict_without_passing(passed):
    def invalid(payload):
        item = delivery(payload)
        item["checks"][0].update(passed=passed, counterevidence=[{"ref_id": "unknown"}])
        return item
    provider = RawJudge(invalid, audit())
    result = evaluate(provider, expectations=("说明数量。",))
    item = result.judgements[0]
    assert item["passed"] is None and item["reported_passed"] is passed
    assert item["validation_error"] == "UnknownEvidenceReference"
    assert item["reported_counterevidence"] == [{"ref_id": "unknown"}]
    assert item["counterevidence"] is None and len(provider.calls) == 2


def test_id_from_another_snapshot_cannot_be_replayed():
    first = RawJudge(audit())
    evaluate(first)
    old = ref(first.calls[0], "synthetic_evidence", "sample", "payload", "count")
    second = RawJudge(audit(claim([old])))
    result = evaluate(second, candidate=response(5))
    assert result.factual["validation_error"] == "claims[0]:UnknownEvidenceReference"


def test_request_payload_and_validation_use_the_same_snapshot_after_external_mutation():
    candidate = response()
    def mutate_after_request(payload):
        candidate.tool_results[-1].output["evidence"]["sample"]["payload"]["count"] = 5
        candidate.answer = "外部改写后的回答。"
        return delivery(payload)
    provider = RawJudge(mutate_after_request, lambda p: audit(claim([
        ref(p, "synthetic_evidence", "sample", "payload", "count")])))
    result = evaluate(provider, expectations=("说明数量。",), candidate=candidate)
    assert provider.calls[0]["synthetic_evidence"] == provider.calls[1]["synthetic_evidence"]
    assert provider.calls[1]["surfaces"] == [{"surface_id": "final", "text": "数量为9。"}]
    assert result.judgements[0]["passed"] is result.factual["passed"] is False
    assert not result.errors


def test_catalog_excludes_answer_requirements_and_tool_supplied_catalog_authority():
    candidate = response()
    forged = {"ref_id": "tool-owned-id", "path": ["answer"], "value": "数量为9。"}
    candidate.tool_results[-1].output["evidence"]["sample"]["payload"]["evidence_catalog"] = [forged]
    provider = RawJudge(audit(claim([{"ref_id": "tool-owned-id"}])))
    result = evaluate(provider, candidate=candidate)
    paths = [item["path"] for item in catalog_entries(provider.calls[0])]
    assert all(path[0] in {"synthetic_evidence", "business_observations",
                          "source_equivalence", "trusted_runtime_metadata"} for path in paths)
    assert result.factual["validation_error"] == "claims[0]:UnknownEvidenceReference"


def test_catalog_limit_fails_explicitly_before_either_model_call():
    candidate = response()
    candidate.tool_results[-1].output["evidence"]["sample"]["payload"]["oversized"] = list(range(8200))
    provider = RawJudge()
    result = evaluate(provider, expectations=("说明数量。",), candidate=candidate)
    assert result.errors == {"evidence_catalog": "CatalogLimitExceeded"}
    assert result.judgements is result.factual is None and provider.calls == []
    assert not result.diagnostics  # No invented phase attempt or provider diagnostics.


def assert_reference_schema(schema):
    citations = []
    def visit(node):
        if isinstance(node, dict):
            if "ref_id" in node.get("properties", {}):
                citations.append(node)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(schema)
    assert citations and all(set(c["properties"]) == {"ref_id"} and c["additionalProperties"] is False for c in citations)


@pytest.mark.parametrize("model", [Judgements, VisibleAudit])
def test_reference_wire_schema_has_only_the_identifier(model):
    assert_reference_schema(inline_local_refs(model.model_json_schema()))


def test_wire_targets_match_the_per_dimension_validator_contract():
    wire = inline_local_refs(VisibleAudit.model_json_schema())
    for dimension, allowed in DIMENSION_TARGETS.items():
        target = wire["properties"][dimension]["properties"]["claims"]["items"]["properties"]["target"]
        assert set(target.get("enum", [target.get("const")])) == allowed


@pytest.mark.parametrize("dimension,target", [
    ("factual", "source_attribution"), ("source", "world_fact"), ("safety", "query_status"),
])
def test_out_of_dimension_raw_claim_is_preserved_even_when_wire_schema_forbids_it(dimension, target):
    # A provider may ignore its narrowed schema. Keep the original claim for
    # diagnosis instead of moving it, discarding it or converting it to success.
    finding = claim([], "insufficient_evidence")
    finding["target"] = target
    submitted = audit()
    submitted[dimension]["claims"] = [finding]
    result = evaluate(RawJudge(submitted))
    check = getattr(result, dimension)
    assert check["passed"] is None
    assert check["validation_error"] == "claims[0]:ClaimTargetDimensionMismatch"
    retained = check["claims"][0]
    assert retained["target"] == target
    assert retained["reported_evidence_relation"] == "insufficient_evidence"
    assert retained["validated_evidence_relation"] is None
    assert all(getattr(result, other)["passed"] is True for other in DIMENSION_TARGETS if other != dimension)


def test_separate_number_and_source_findings_do_not_contaminate_each_other():
    def submitted(payload):
        source_ref = ref(payload, "synthetic_evidence", "sample", "payload", "source")
        source_claim = {"surface_id": "final", "quote": "来源为unverified-provider-v99",
            "target": "source_attribution", "evidence_relation": "contradicted", "evidence": [source_ref]}
        factual_claim = claim([ref(payload, "synthetic_evidence", "sample", "payload", "count")], "supported")
        result = audit(factual_claim)
        result["source"]["claims"] = [source_claim]
        return result
    candidate = response(9)
    candidate.answer += "来源为unverified-provider-v99。"
    result = evaluate(RawJudge(submitted), candidate=candidate)
    assert result.factual["passed"] is True and result.source["passed"] is False
    assert not result.errors


def test_source_identity_contract_is_registry_owned_and_not_tool_self_attested():
    fake = {"payload": {"source": "unknown-vendor", "synthetic": True,
                        "identity_contract": {"external_market_supplier": False}}}
    assert source_equivalence({"untrusted": fake}) == []
    known = {"payload": {"source": "synthetic-golden-world-v1",
                         "identity_contract": {"realtime_market_data": True}}}
    before = deepcopy(known)
    result = source_equivalence({"known": known})
    assert result[0]["identity_contract"] == {"provenance_kind": "offline_synthetic_fixture",
        "external_market_supplier": False, "realtime_market_data": False}
    result[0]["identity_contract"]["realtime_market_data"] = True
    assert source_equivalence({"known": known})[0]["identity_contract"]["realtime_market_data"] is False
    assert known == before
