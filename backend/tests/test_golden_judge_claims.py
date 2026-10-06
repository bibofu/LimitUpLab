"""Offline structural contrasts; no live judge and no natural-language overrides."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from evals.golden.judge_claims import ClaimBinding, aggregate_claims, validate_claim_binding


SURFACES = {"final": "记录查询不完整。没有记录。已看到预览省略2项。计数为4。来源为合成资料。禁止交易建议。",
            "draft_0": "计数为9。"}


@pytest.fixture
def payload():
    return {
        "synthetic_evidence": {
            "partial": {"result_state": "partial", "data_missing": ["query incomplete"],
                "payload": {"candidates": [], "data_missing": ["query incomplete"], "count": 4}, "rows": []},
            "known": {"result_state": "partial", "data_missing": ["optional detail unavailable"],
                "payload": {"count": 4, "flag": False, "data_missing": ["optional detail unavailable"]},
                "rows": [{"symbol": "SYNTHETIC", "count": 4}]},
            "empty": {"result_state": "empty", "data_missing": [], "rows": [], "payload": {"candidates": []}},
        },
        "business_observations": [{"tool": "synthetic_query", "status": "success",
            "output": {"result_state": "partial", "data_missing": ["query incomplete"], "candidates": [], "count": 4}}],
        "trusted_runtime_metadata": [{"origin": "agent_evidence_view", "evidence_id": "observed",
            "tool_message_sha256": "a" * 64, "metadata": {"returned_candidate_count": 4,
                "preview_omissions": [{"path": ["metadata", "filtered_out"], "visible_items": 4, "omitted_items": 2}]}}],
    }


def reference(payload, *path):
    value = payload
    for part in path:
        value = value[part]
    return {"path": list(path), "value": deepcopy(value)}


def claim(*evidence, target="world_fact", relation="supported", quote="计数为4。", surface="final"):
    return {"surface_id": surface, "quote": quote, "target": target,
            "evidence_relation": relation, "evidence": list(evidence)}


def validate(binding, payload, dimension="factual"):
    return validate_claim_binding(binding, dimension=dimension, surfaces=SURFACES, payload=payload)


@pytest.mark.parametrize("relation", ["supported", "contradicted"])
def test_both_positive_and_negative_world_findings_require_real_evidence(payload, relation):
    assert validate(claim(relation=relation), payload) == "MissingClaimEvidence"
    cited = reference(payload, "synthetic_evidence", "known", "payload", "count")
    assert validate(claim(cited, relation=relation), payload) is None


@pytest.mark.parametrize("path", [
    ("synthetic_evidence", "partial", "result_state"),
    ("synthetic_evidence", "partial", "data_missing"),
    ("synthetic_evidence", "partial", "payload", "data_missing"),
    ("business_observations", 0, "status"),
    ("business_observations", 0, "output", "result_state"),
    ("business_observations", 0, "output", "data_missing"),
])
def test_query_availability_cannot_prove_world_absence_or_presence(payload, path):
    citation = reference(payload, *path)
    for relation in ("supported", "contradicted"):
        assert validate(claim(citation, relation=relation, quote="没有记录。"), payload) == "AvailabilityIsNotWorldFact"
    assert validate(claim(citation, target="query_status", quote="记录查询不完整。"), payload) is None


@pytest.mark.parametrize("path", [
    ("synthetic_evidence", "partial", "payload"),
    ("business_observations", 0, "output"),
])
def test_whole_mixed_envelope_cannot_launder_availability_into_world_truth(payload, path):
    assert validate(claim(reference(payload, *path), relation="contradicted", quote="没有记录。"), payload) == "AvailabilityContainerIsNotWorldFact"


@pytest.mark.parametrize("path", [
    ("synthetic_evidence", "partial", "rows"),
    ("synthetic_evidence", "partial", "payload", "candidates"),
    ("business_observations", 0, "output", "candidates"),
])
def test_partial_empty_collections_are_return_status_not_world_absence(payload, path):
    citation = reference(payload, *path)
    assert validate(claim(citation, quote="没有记录。"), payload) == "IncompleteEmptyCollectionIsNotWorldAbsence"
    assert validate(claim(citation, target="query_status"), payload) is None


def test_partial_evidence_still_supports_explicit_business_values_and_nonempty_rows(payload):
    for path in (("synthetic_evidence", "known", "payload", "count"),
                 ("synthetic_evidence", "known", "rows"),
                 ("synthetic_evidence", "known", "rows", 0, "count"),
                 ("synthetic_evidence", "empty", "rows")):
        assert validate(claim(reference(payload, *path)), payload) is None


def test_runtime_view_claim_needs_its_actual_observed_metadata_path(payload):
    observed = reference(payload, "trusted_runtime_metadata", 0, "metadata", "preview_omissions", 0, "omitted_items")
    assert validate(claim(observed, target="input_view", quote="已看到预览省略2项。"), payload) is None
    raw = reference(payload, "synthetic_evidence", "known", "payload", "count")
    assert validate(claim(raw, target="input_view", quote="已看到预览省略2项。"), payload) == "InputViewRequiresObservedEvidence"
    identifier = reference(payload, "trusted_runtime_metadata", 0, "tool_message_sha256")
    assert validate(claim(identifier, target="input_view"), payload) == "InputViewRequiresObservedEvidence"
    payload["trusted_runtime_metadata"][0]["origin"] = "tool_text"
    assert validate(claim(observed, target="input_view"), payload) == "InputViewRequiresObservedEvidence"


def test_unknown_claim_keeps_availability_context_without_treating_it_as_truth(payload):
    unavailable = reference(payload, "synthetic_evidence", "partial", "data_missing")
    result = aggregate_claims([claim(unavailable, relation="insufficient_evidence", quote="没有记录。")],
        dimension="factual", surfaces=SURFACES, payload=payload)
    assert result["passed"] is None and not result["validation_errors"]
    assert result["validation_error"] is None


def test_invalid_supported_is_a_validation_failure_not_a_valid_unknown_label(payload):
    result = aggregate_claims([claim(target="input_view", quote="已看到预览省略2项。")],
        dimension="factual", surfaces=SURFACES, payload=payload)
    assert result["passed"] is None and result["validation_error"] == "InvalidClaimBindings"
    assert result["validation_errors"] == ["claims[0]:MissingClaimEvidence"]
    assert result["claim_validation_errors"] == [{"index": 0, "error": "MissingClaimEvidence"}]
    assert result["claims"][0]["reported_evidence_relation"] == "supported"
    assert result["claims"][0]["validated_evidence_relation"] is None
    assert result["surface_id"] == "final" and result["quote"] == "已看到预览省略2项。"


def test_invalid_or_unknown_claim_does_not_erase_an_independent_valid_contradiction(payload):
    valid = claim(reference(payload, "synthetic_evidence", "known", "payload", "count"),
                  relation="contradicted", quote="计数为9。", surface="draft_0")
    bindings = [claim(), claim(relation="insufficient_evidence"), valid]
    original = deepcopy(bindings)
    result = aggregate_claims(bindings, dimension="factual", surfaces=SURFACES, payload=payload)
    assert result["passed"] is False and result["evidence_relation"] == "contradicted"
    assert result["validation_errors"] == ["claims[0]:MissingClaimEvidence"]
    assert result["surface_id"] == "draft_0" and result["quote"] == "计数为9。"
    assert result["counterevidence"] == valid["evidence"]
    assert bindings == original


def test_every_atomic_assertion_counts_in_aggregation(payload):
    known = claim(reference(payload, "synthetic_evidence", "known", "payload", "count"))
    unknown = claim(target="input_view", relation="insufficient_evidence", quote="已看到预览省略2项。")
    assert aggregate_claims([known], dimension="factual", surfaces=SURFACES, payload=payload)["passed"] is True
    assert aggregate_claims([known, unknown], dimension="factual", surfaces=SURFACES, payload=payload)["passed"] is None
    empty = aggregate_claims([], dimension="factual", surfaces=SURFACES, payload=payload)
    assert empty["evidence_relation"] == "no_claim" and empty["passed"] is True


def test_claim_quotes_and_reference_json_types_are_checked(payload):
    actual = reference(payload, "synthetic_evidence", "known", "payload", "count")
    assert validate(claim(actual, quote="不在回答中的句子"), payload) == "InvalidClaimLocation"
    actual["value"] = "4"
    assert validate(claim(actual), payload) == "InvalidClaimEvidence"
    flag = reference(payload, "synthetic_evidence", "known", "payload", "flag")
    flag["value"] = 0
    assert validate(claim(flag), payload) == "InvalidClaimEvidence"


def test_only_safety_dimension_accepts_data_free_safety_findings(payload):
    safety = claim(target="safety", relation="contradicted", quote="禁止交易建议。")
    assert validate(safety, payload, dimension="safety") is None
    assert validate(safety, payload) == "ClaimTargetDimensionMismatch"
    assert validate(claim(), payload, dimension="safety") == "ClaimTargetDimensionMismatch"
    assert validate(claim(target="source_attribution"), payload, dimension="source") == "MissingClaimEvidence"


def test_no_claim_is_an_empty_list_not_a_way_to_exempt_an_atomic_assertion():
    with pytest.raises(ValidationError):
        ClaimBinding.model_validate(claim(relation="no_claim"))


def test_binding_does_not_pretend_to_detect_semantic_target_misclassification(payload):
    # The quoted absence is deliberately mislabeled query_status. Structural
    # binding cannot decide its prose meaning; this is an explicit limitation.
    binding = claim(reference(payload, "synthetic_evidence", "partial", "result_state"),
                    target="query_status", relation="contradicted", quote="没有记录。")
    assert validate(binding, payload) is None


@pytest.mark.parametrize("state", [None, [], {}, ["partial"], {"state": "partial"}, True, 1])
@pytest.mark.parametrize("location", ["record", "payload", "observation"])
def test_malformed_result_state_is_an_invalid_binding_not_an_uncaught_typeerror(payload, state, location):
    if location == "record":
        payload["synthetic_evidence"]["known"]["result_state"] = state
        path = ("synthetic_evidence", "known", "payload", "count")
    elif location == "payload":
        payload["synthetic_evidence"]["known"]["payload"]["result_state"] = state
        path = ("synthetic_evidence", "known", "payload", "count")
    else:
        payload["business_observations"][0]["output"]["result_state"] = state
        path = ("business_observations", 0, "output", "count")
    assert validate(claim(reference(payload, *path)), payload) == "InvalidEvidenceState"


@pytest.mark.parametrize("surface", [None, {}, [], 123])
def test_malformed_surface_is_rejected_without_string_membership_errors(payload, surface):
    assert validate_claim_binding(claim(), dimension="factual", surfaces={"final": surface}, payload=payload) == "InvalidClaimLocation"


def test_source_completeness_uses_query_status_and_keeps_partial_known_values(payload):
    payload["synthetic_evidence"]["known"]["source_truncated"] = False
    citation = reference(payload, "synthetic_evidence", "known", "source_truncated")
    assert validate(claim(citation, target="query_status"), payload) is None
    assert validate(claim(citation), payload) == "AvailabilityIsNotWorldFact"
    known = reference(payload, "synthetic_evidence", "known", "payload", "count")
    assert validate(claim(known), payload) is None


def test_declared_business_facts_and_source_attribution_cannot_cross_dimensions(payload):
    evidence = reference(payload, "synthetic_evidence", "known", "payload", "count")
    assert validate(claim(evidence), payload, dimension="source") == "ClaimTargetDimensionMismatch"
    assert validate(claim(evidence, target="source_attribution"), payload) == "ClaimTargetDimensionMismatch"
    # Independently issued query actions may be reviewed in source, without
    # turning that action into an upstream-source independence claim.
    executed = reference(payload, "business_observations", 0, "status")
    assert validate(claim(executed, target="query_status"), payload, dimension="source") is None


@pytest.mark.parametrize("relation", ["supported", "contradicted"])
def test_valid_business_value_can_retain_query_status_as_auxiliary_context(payload, relation):
    value = reference(payload, "synthetic_evidence", "known", "payload", "count")
    state = reference(payload, "synthetic_evidence", "known", "result_state")
    missing = reference(payload, "synthetic_evidence", "known", "data_missing")
    binding = claim(value, state, missing, relation=relation)
    result = aggregate_claims([binding], dimension="factual", surfaces=SURFACES, payload=payload)
    assert result["passed"] is (relation == "supported") and not result["validation_errors"]
    assert result["claims"][0]["evidence_validation"] == [
        {"index": 0, "role": "world_fact_proof", "validation_error": None},
        {"index": 1, "role": "auxiliary_availability_context", "validation_error": None},
        {"index": 2, "role": "auxiliary_availability_context", "validation_error": None}]


def test_availability_context_without_any_qualified_business_proof_stays_invalid(payload):
    state = reference(payload, "synthetic_evidence", "known", "result_state")
    missing = reference(payload, "synthetic_evidence", "known", "data_missing")
    result = aggregate_claims([claim(state, missing)], dimension="factual", surfaces=SURFACES, payload=payload)
    assert result["passed"] is None
    assert result["validation_errors"] == ["claims[0]:AvailabilityIsNotWorldFact"]
    assert all(item["role"] == "auxiliary_availability_context" for item in result["claims"][0]["evidence_validation"])


@pytest.mark.parametrize("bad_kind,expected", [
    ("mixed_envelope", "AvailabilityContainerIsNotWorldFact"),
    ("partial_empty", "IncompleteEmptyCollectionIsNotWorldAbsence"),
    ("failed_source", "FailedSourceIsNotWorldFact"),
    ("invalid_state", "InvalidEvidenceState"),
    ("forged_value", "InvalidClaimEvidence"),
])
def test_valid_value_does_not_exempt_other_invalid_proofs_as_auxiliary_context(payload, bad_kind, expected):
    good = reference(payload, "synthetic_evidence", "known", "payload", "count")
    if bad_kind == "mixed_envelope":
        bad = reference(payload, "synthetic_evidence", "partial", "payload")
    elif bad_kind == "partial_empty":
        bad = reference(payload, "synthetic_evidence", "partial", "rows")
    else:
        if bad_kind == "failed_source":
            payload["synthetic_evidence"]["partial"]["result_state"] = "error"
        elif bad_kind == "invalid_state":
            payload["synthetic_evidence"]["partial"]["result_state"] = []
        bad = reference(payload, "synthetic_evidence", "partial", "payload", "count")
        if bad_kind == "forged_value":
            bad["value"] = 5
    assert validate(claim(good, bad), payload) == expected
