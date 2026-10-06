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
            "empty": {"result_state": "empty", "data_missing": [], "rows": [], "payload": {"candidates": []},
                "arguments": {"symbol": "SYNTHETIC", "trade_date": "2026-09-22"}},
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


def test_catalog_resolution_error_precedes_empty_proof_validation_and_retains_wire_reference(payload):
    reported = [[{"ref_id": "unknown-reference"}]]
    result = aggregate_claims([claim()], dimension="factual", surfaces=SURFACES, payload=payload,
        reference_errors={0: "UnknownEvidenceReference"}, reported_references=reported)
    assert result["passed"] is None
    assert result["validation_errors"] == ["claims[0]:UnknownEvidenceReference"]
    assert result["claims"][0]["evidence_validation"] == []
    assert result["claims"][0]["reported_evidence"] == reported[0]
    assert result["claims"][0]["reported_evidence_relation"] == "supported"
    reported[0][0]["ref_id"] = "changed-after-validation"
    assert result["claims"][0]["reported_evidence"][0]["ref_id"] == "unknown-reference"


def test_resolved_valid_contradiction_still_wins_over_an_unresolvable_wire_reference(payload):
    valid = claim(reference(payload, "synthetic_evidence", "known", "payload", "count"),
                  relation="contradicted", quote="计数为9。", surface="draft_0")
    result = aggregate_claims([claim(), valid], dimension="factual", surfaces=SURFACES, payload=payload,
        reference_errors={0: "UnknownEvidenceReference"},
        reported_references=[[{"ref_id": "unknown-reference"}], [{"ref_id": "known-count"}]])
    assert result["passed"] is False and result["evidence_relation"] == "contradicted"
    assert result["surface_id"] == "draft_0"
    assert result["claims"][1]["reported_evidence"] == [{"ref_id": "known-count"}]
    assert result["validation_errors"] == ["claims[0]:UnknownEvidenceReference"]


@pytest.mark.parametrize("relation", ["supported", "contradicted"])
def test_source_lineage_and_actual_query_execution_have_different_capabilities(payload, relation):
    payload["business_observations"][0].update(input={"trade_date": "2026-09-22"})
    payload["business_observations"][0]["output"].update(
        source="synthetic-provider", lineage={"ultimate_upstream": "shared-provider"}, tool_label="query-label")
    lineage = reference(payload, "business_observations", 0, "output", "lineage", "ultimate_upstream")
    executed = reference(payload, "business_observations", 0, "status")
    tool = reference(payload, "business_observations", 0, "tool")
    scope = reference(payload, "business_observations", 0, "input")
    label = reference(payload, "business_observations", 0, "output", "tool_label")
    assert validate(claim(lineage, relation=relation, target="query_status"), payload) == "QueryStatusRequiresQueryEvidence"
    assert validate(claim(executed, tool, scope, relation=relation, target="query_status"), payload) is None
    assert validate(claim(executed, tool, scope, label, relation=relation, target="source_attribution"),
                    payload, "source") == "SourceAttributionRequiresSourceEvidence"
    assert validate(claim(lineage, relation=relation, target="source_attribution"), payload, "source") is None
    result = aggregate_claims([claim(lineage, executed, tool, scope, label, relation=relation, target="source_attribution")],
        dimension="source", surfaces=SURFACES, payload=payload)
    assert result["passed"] is (relation == "supported")
    assert [item["role"] for item in result["claims"][0]["evidence_validation"]] == [
        "source_proof", "auxiliary_query_context", "auxiliary_query_context", "auxiliary_query_context", "auxiliary_query_context"]


def test_two_actual_calls_can_support_execution_without_proving_distinct_upstreams(payload):
    other = deepcopy(payload["business_observations"][0])
    other["tool"] = "second_query"
    payload["business_observations"].append(other)
    refs = [reference(payload, "business_observations", index, "tool") for index in (0, 1)]
    assert validate(claim(*refs, target="query_status"), payload, "source") is None
    assert validate(claim(*refs, target="source_attribution"), payload, "source") == "SourceAttributionRequiresSourceEvidence"
    result = aggregate_claims([claim(*refs, target="query_status")], dimension="source", surfaces=SURFACES, payload=payload)
    assert all(item["role"] == "execution_proof" for item in result["claims"][0]["evidence_validation"])


@pytest.mark.parametrize("origin", ["agent_system_message", "tool_text"])
def test_system_available_dates_are_query_scope_only_when_actual_runtime_context(payload, origin):
    payload["trusted_runtime_metadata"].append({"origin": origin, "context": {
        "available_local_dates": ["2026-09-21", "2026-09-22"]}})
    cited = reference(payload, "trusted_runtime_metadata", 1, "context", "available_local_dates")
    expected = None if origin == "agent_system_message" else "QueryStatusRequiresQueryEvidence"
    assert validate(claim(cited, target="query_status"), payload) == expected
    assert validate(claim(cited), payload) == "AvailabilityIsNotWorldFact"


@pytest.mark.parametrize("field,value", [("source", "synthetic-provider"), ("synthetic", True),
                                         ("lineage", {"ultimate_upstream": "same"})])
def test_source_metadata_cannot_be_laundered_into_world_fact(payload, field, value):
    payload["synthetic_evidence"]["known"]["payload"][field] = value
    cited = reference(payload, "synthetic_evidence", "known", "payload", field)
    assert validate(claim(cited), payload) == "SourceIdentityIsNotWorldFact"
    expected = "SourceAttributionRequiresSourceEvidence" if field == "synthetic" else None
    assert validate(claim(cited, target="source_attribution"), payload, "source") == expected


def test_arbitrary_business_value_is_not_query_status_or_source_proof(payload):
    payload["synthetic_evidence"]["known"]["payload"]["return_10d_pct"] = 1.2
    cited = reference(payload, "synthetic_evidence", "known", "payload", "return_10d_pct")
    assert validate(claim(cited), payload) is None
    assert validate(claim(cited, target="query_status"), payload) == "QueryStatusRequiresQueryEvidence"
    assert validate(claim(cited, target="source_attribution"), payload, "source") == "SourceAttributionRequiresSourceEvidence"


def test_nested_row_availability_retains_its_structural_role(payload):
    payload["synthetic_evidence"]["known"]["rows"][0]["data_missing"] = ["optional detail"]
    cited = reference(payload, "synthetic_evidence", "known", "rows", 0, "data_missing")
    assert validate(claim(cited), payload) == "AvailabilityIsNotWorldFact"
    assert validate(claim(cited, target="query_status"), payload) is None


@pytest.mark.parametrize("relation", ["supported", "contradicted"])
def test_complete_empty_has_explicit_scope_in_report_without_inventing_prose_entailment(payload, relation):
    cited = reference(payload, "synthetic_evidence", "empty", "payload", "candidates")
    result = aggregate_claims([claim(cited, relation=relation, quote="没有记录。")],
        dimension="factual", surfaces=SURFACES, payload=payload)
    assert result["passed"] is (relation == "supported")
    assert result["claims"][0]["evidence_validation"] == [{"index": 0, "role": "scoped_empty_result",
        "validation_error": None, "scope_limited": True, "query_scope": {"symbol": "SYNTHETIC", "trade_date": "2026-09-22"}}]
    # Structural capability cannot establish that the quote uses this scope.
    assert result["claims"][0]["quote"] == "没有记录。"


@pytest.mark.parametrize("mutation,expected", [
    ("missing_state", "EmptyCollectionCompletenessUnknown"),
    ("missing_scope", "EmptyCollectionScopeUnknown"),
    ("partial", "IncompleteEmptyCollectionIsNotWorldAbsence"),
    ("missing_fields", "IncompleteEmptyCollectionIsNotWorldAbsence"),
    ("truncated", "IncompleteEmptyCollectionIsNotWorldAbsence"),
    ("error", "FailedSourceIsNotWorldFact"),
])
def test_empty_with_unknown_or_incomplete_scope_cannot_establish_world_absence(payload, mutation, expected):
    record = payload["synthetic_evidence"]["empty"]
    if mutation == "missing_state":
        del record["result_state"]
    elif mutation == "missing_scope":
        del record["arguments"]
    elif mutation in {"partial", "error"}:
        record["result_state"] = mutation
    elif mutation == "missing_fields":
        record["data_missing"] = ["source incomplete"]
    elif mutation == "truncated":
        record["source_truncated"] = True
    cited = reference(payload, "synthetic_evidence", "empty", "payload", "candidates")
    assert validate(claim(cited, quote="没有记录。"), payload) == expected
    assert validate(claim(cited, target="query_status"), payload) is None


def linked_empty_observation(payload):
    record = payload["synthetic_evidence"]["empty"]
    record.update(tool="synthetic_query", evidence_scope="current_run", historical_reference=False)
    payload["business_observations"][0] = {"tool": record["tool"], "status": "success",
        "input": deepcopy(record["arguments"]), "output": deepcopy(record["payload"])}
    return reference(payload, "business_observations", 0, "output", "candidates")


def test_observation_empty_can_use_unique_exact_current_record_for_completeness(payload):
    # Frozen diagnostic records declare completeness through result_state and
    # need not also spell out an empty data_missing field.
    del payload["synthetic_evidence"]["empty"]["data_missing"]
    cited = linked_empty_observation(payload)
    assert validate(claim(cited, quote="没有记录。"), payload) is None
    payload["synthetic_evidence"]["empty"]["result_state"] = "partial"
    assert validate(claim(cited, quote="没有记录。"), payload) == "IncompleteEmptyCollectionIsNotWorldAbsence"


@pytest.mark.parametrize("mutation", ["tool", "arguments", "payload", "history", "duplicate", "missing_record"])
def test_observation_success_cannot_guess_completeness_from_nonmatching_record(payload, mutation):
    cited = linked_empty_observation(payload)
    record = payload["synthetic_evidence"]["empty"]
    if mutation == "tool":
        record["tool"] = "different_query"
    elif mutation == "arguments":
        record["arguments"]["symbol"] = "DIFFERENT"
    elif mutation == "payload":
        record["payload"]["extra"] = 0
    elif mutation == "history":
        record.update(evidence_scope="history", historical_reference=True)
    elif mutation == "duplicate":
        payload["synthetic_evidence"]["duplicate"] = deepcopy(record)
    elif mutation == "missing_record":
        del payload["synthetic_evidence"]["empty"]
    assert validate(claim(cited, quote="没有记录。"), payload) == "EmptyCollectionCompletenessUnknown"


def test_observation_can_declare_its_own_complete_empty_but_success_alone_is_not_enough(payload):
    observation = payload["business_observations"][0]
    observation.update(input={"symbol": "SYNTHETIC"}, output={"candidates": []})
    cited = reference(payload, "business_observations", 0, "output", "candidates")
    assert validate(claim(cited), payload) == "EmptyCollectionCompletenessUnknown"
    observation["output"].update(result_state="empty", data_missing=[])
    assert validate(claim(cited), payload) is None
    observation["status"] = "error"
    assert validate(claim(cited), payload) == "FailedSourceIsNotWorldFact"


def test_known_world_value_is_rejected_if_its_output_explicitly_failed(payload):
    output = payload["synthetic_evidence"]["known"]["payload"]
    output["result_state"] = "error"
    cited = reference(payload, "synthetic_evidence", "known", "payload", "count")
    assert validate(claim(cited), payload) == "FailedSourceIsNotWorldFact"


@pytest.mark.parametrize("location", ["record", "observation"])
@pytest.mark.parametrize("collection", [[], [{"symbol": "SYNTHETIC"}]])
def test_result_envelope_cannot_hide_incomplete_empty_behind_count_or_ordinary_rows(payload, location, collection):
    output = {"count": len(collection), "items": collection}
    if location == "record":
        payload["synthetic_evidence"]["partial"]["payload"] = output
        path = ("synthetic_evidence", "partial", "payload")
    else:
        payload["business_observations"][0]["output"] = output
        path = ("business_observations", 0, "output")
    assert validate(claim(reference(payload, *path)), payload) == "ResultEnvelopeRequiresSpecificField"
    # A concrete value is still a value, even when the result is partial.
    assert validate(claim(reference(payload, *path, "count")), payload) is None


@pytest.mark.parametrize("state", [None, "unknown", "unrecorded"])
def test_bare_rows_cannot_prove_absence_without_known_complete_state(payload, state):
    record = payload["synthetic_evidence"]["empty"]
    if state is None:
        del record["result_state"]
    else:
        record["result_state"] = state
    cited = reference(payload, "synthetic_evidence", "empty", "rows")
    assert validate(claim(cited), payload) == "EmptyCollectionCompletenessUnknown"


@pytest.mark.parametrize("status_field", ["status", "execution_status"])
@pytest.mark.parametrize("status_value", ["error", "failed"])
def test_explicit_failed_output_cannot_be_overridden_by_complete_record(payload, status_field, status_value):
    record = payload["synthetic_evidence"]["empty"]
    record["payload"].update({status_field: status_value, "count": 0})
    for field in ("count", "candidates"):
        cited = reference(payload, "synthetic_evidence", "empty", "payload", field)
        assert validate(claim(cited), payload) == "FailedSourceIsNotWorldFact"
    rows = reference(payload, "synthetic_evidence", "empty", "rows")
    assert validate(claim(rows), payload) == "FailedSourceIsNotWorldFact"


def test_unknown_record_state_cannot_be_overridden_by_an_empty_payload_declaration(payload):
    record = payload["synthetic_evidence"]["empty"]
    record["result_state"] = "unknown"
    record["payload"].update(result_state="empty", data_missing=[])
    cited = reference(payload, "synthetic_evidence", "empty", "payload", "candidates")
    assert validate(claim(cited), payload) == "EmptyCollectionCompletenessUnknown"


def test_failed_observation_cannot_be_overridden_by_exact_complete_record(payload):
    cited = linked_empty_observation(payload)
    payload["business_observations"][0]["status"] = "error"
    assert validate(claim(cited), payload) == "FailedSourceIsNotWorldFact"


def test_source_plus_query_context_does_not_imply_natural_language_entailment(payload):
    # The validator can ensure this is provenance evidence, but cannot decide
    # whether a quote about calls was mistakenly labeled as source attribution.
    payload["synthetic_evidence"]["known"]["payload"]["lineage"] = {"ultimate_upstream": "one"}
    cited = reference(payload, "synthetic_evidence", "known", "payload", "lineage")
    assert validate(claim(cited, target="source_attribution", quote="没有记录。"), payload, "source") is None


@pytest.mark.parametrize("wrapper", ["row", "rows", "nested"])
@pytest.mark.parametrize("field,value,expected", [
    ("data_missing", [], "AvailabilityContainerIsNotWorldFact"),
    ("result_state", "partial", "AvailabilityContainerIsNotWorldFact"),
    ("source", "synthetic-provider", "MixedSourceContainerIsNotWorldFact"),
    ("lineage", {"ultimate_upstream": "one"}, "MixedSourceContainerIsNotWorldFact"),
])
def test_selected_row_and_collection_wrappers_cannot_launder_status_or_provenance(payload, wrapper, field, value, expected):
    record = payload["synthetic_evidence"]["known"]
    record["rows"][0][field] = value
    if wrapper == "row":
        path = ("synthetic_evidence", "known", "rows", 0)
    elif wrapper == "rows":
        path = ("synthetic_evidence", "known", "rows")
    else:
        record["payload"]["details"] = {"rows": deepcopy(record["rows"])}
        path = ("synthetic_evidence", "known", "payload", "details")
    assert validate(claim(reference(payload, *path)), payload) == expected
    assert validate(claim(reference(payload, "synthetic_evidence", "known", "rows", 0, "count")), payload) is None


def test_synthetic_flag_needs_actual_source_proof_and_is_only_auxiliary_context(payload):
    record = payload["synthetic_evidence"]["known"]
    record["payload"].update(synthetic=True, source="synthetic-provider")
    flag = reference(payload, "synthetic_evidence", "known", "payload", "synthetic")
    source = reference(payload, "synthetic_evidence", "known", "payload", "source")
    assert validate(claim(flag, target="source_attribution"), payload, "source") == "SourceAttributionRequiresSourceEvidence"
    result = aggregate_claims([claim(source, flag, target="source_attribution")],
        dimension="source", surfaces=SURFACES, payload=payload)
    assert result["passed"] is True
    assert [item["role"] for item in result["claims"][0]["evidence_validation"]] == ["source_proof", "auxiliary_source_context"]


@pytest.mark.parametrize("field,value", [("result_state", "partial"), ("data_missing", ["source gap"]),
                                       ("source_truncated", True)])
def test_selecting_empty_rows_keeps_source_payload_incompleteness(payload, field, value):
    payload["synthetic_evidence"]["empty"]["payload"][field] = value
    rows = reference(payload, "synthetic_evidence", "empty", "rows")
    assert validate(claim(rows), payload) == "IncompleteEmptyCollectionIsNotWorldAbsence"
