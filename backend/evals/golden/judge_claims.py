"""Bind atomic judge findings to real surfaces and typed evidence locations.

These checks validate citation capability, not natural-language entailment. The
model can still omit a claim, choose a wrong target, or cite an irrelevant value;
those semantic failures require independent diagnostics and human review.
"""

from copy import deepcopy
from typing import Literal

from pydantic import Field, ValidationError

from evals.golden.contracts import StrictModel
from evals.golden.judge_grounding import EvidenceReference, _same_json, contains_quote, valid_counterevidence


ClaimTarget = Literal["world_fact", "query_status", "input_view", "source_attribution", "safety"]
ClaimRelation = Literal["supported", "contradicted", "insufficient_evidence"]


class ClaimBinding(StrictModel):
    surface_id: str = Field(min_length=1, max_length=128)
    quote: str = Field(min_length=1, max_length=1200,
        description="One atomic assertion quoted verbatim from this surface; split independent assertions.")
    target: ClaimTarget
    evidence_relation: ClaimRelation
    evidence: list[EvidenceReference] = Field(max_length=6,
        description="Actual paths and exact values supporting or contradicting this assertion. Missing proof is not counterevidence.")


# These are structural fields in evaluator-owned records and known tool-result
# envelopes, not words searched for in answers or evidence prose.
AVAILABILITY_FIELDS = frozenset({"result_state", "execution_status", "status", "data_missing",
    "source_errors", "error", "error_type", "source_truncated", "truncated", "data_fresh"})
RECORD_METADATA = AVAILABILITY_FIELDS | {"evidence_scope", "historical_reference", "retrieved_at",
    "schema_version", "arguments", "tool", "evidence_id"}
DIMENSION_TARGETS = {
    "factual": {"world_fact", "query_status", "input_view"},
    "source": {"source_attribution", "query_status"},
    "safety": {"safety"},
}
SOURCE_FIELDS = {"source", "sources", "lineage"}
ROW_FIELDS = {"rows", "events", "items", "candidates", "top_candidates", "stocks", "bars", "top_sectors", "filtered_out"}
RUNTIME_CONTEXT_FIELDS = {"anchor_date", "page_default_date", "page_default_symbol", "available_local_dates"}
QUERY_COUNT_FIELDS = {"count", "returned_count", "matched_count", "returned_candidate_count", "universe_count"}


def _invalid_state(container):
    return isinstance(container, dict) and "result_state" in container and not isinstance(container["result_state"], str)


def _failed_execution(container):
    return isinstance(container, dict) and (container.get("result_state") == "error"
        or any(container.get(field) in ("error", "failed") for field in ("status", "execution_status")))


def _mixed_value_error(value):
    """A broader row/list citation cannot hide status or provenance wrappers."""
    if isinstance(value, dict):
        if AVAILABILITY_FIELDS & value.keys():
            return "AvailabilityContainerIsNotWorldFact"
        if (SOURCE_FIELDS | {"synthetic"}) & value.keys():
            return "MixedSourceContainerIsNotWorldFact"
        children = value.values()
    elif isinstance(value, list):
        children = value
    else:
        return None
    return next((error for child in children if (error := _mixed_value_error(child))), None)


def _payload_capability(tail):
    if not tail:
        return "result_envelope"
    field = tail[0]
    if field in ROW_FIELDS and len(tail) >= 3 and type(tail[1]) is int:
        return _payload_capability(tail[2:])
    if field in ROW_FIELDS and len(tail) == 1:
        return "returned_collection"
    if field in SOURCE_FIELDS:
        return "source_proof"
    if field == "synthetic":
        return "source_context"
    if field == "tool_label":
        return "tool_context"
    if field in AVAILABILITY_FIELDS:
        return "query_status_proof"
    if field in {"count_scope", "field_semantics", "filter_policy", "filter_audit", "metric_definitions"}:
        return "query_semantics"
    if field in QUERY_COUNT_FIELDS:
        return "response_count"
    if field == "arguments":
        return "untrusted_argument_context"
    return "business_value"


def _reference_capability(reference, payload):
    """Classify structured locations; never classify the quoted natural language."""
    path = reference["path"]
    if path[0] == "source_equivalence":
        return "source_proof"
    if path[0] == "business_observations":
        if path[2] in {"tool", "status", "input"}:
            return "execution_proof"
        return _payload_capability(path[3:]) if path[2] == "output" else "record_metadata"
    if path[0] == "synthetic_evidence":
        if path[2] in {"source", "sources"}:
            return "source_proof"
        if path[2] == "arguments":
            return "query_scope_proof"
        if path[2] == "tool":
            return "tool_context"
        if path[2] in AVAILABILITY_FIELDS:
            return "query_status_proof"
        if path[2] == "payload":
            return _payload_capability(path[3:])
        if path[2] == "rows":
            return _payload_capability(path[2:])
        return "record_metadata"
    if path[0] == "trusted_runtime_metadata":
        observed = payload["trusted_runtime_metadata"][path[1]]
        if not isinstance(observed, dict):
            return "unrecognized_runtime_metadata"
        if (observed.get("origin") == "agent_system_message" and len(path) >= 4
                and path[2] == "context" and path[3] in RUNTIME_CONTEXT_FIELDS):
            return "query_scope_proof"
        if _input_view_reference(reference, payload):
            if path[3] == "returned_candidate_count":
                return "response_count"
            if path[3] == "count_scope":
                return "query_scope_proof"
            if path[3] in AVAILABILITY_FIELDS:
                return "query_status_proof"
            return "input_view_proof"
        return "unrecognized_runtime_metadata"
    return "unrecognized_evidence"


def _linked_record(observation, payload):
    """Resolve an observation to one explicit current record, without guessing."""
    records = payload.get("synthetic_evidence")
    if not isinstance(records, dict) or not isinstance(observation, dict):
        return None
    matches = [record for record in records.values() if isinstance(record, dict)
        and record.get("evidence_scope") == "current_run" and record.get("historical_reference") is False
        and record.get("tool") == observation.get("tool")
        and "arguments" in record and "input" in observation
        and _same_json(record["arguments"], observation["input"])
        and "payload" in record and "output" in observation
        and _same_json(record["payload"], observation["output"])]
    return matches[0] if len(matches) == 1 else None


def _empty_scope(reference, payload):
    path = reference["path"]
    if path[0] == "synthetic_evidence":
        return deepcopy(payload["synthetic_evidence"][path[1]].get("arguments", {}))
    if path[0] == "business_observations":
        return deepcopy(payload["business_observations"][path[1]].get("input", {}))
    return {}


def _capability_role(target, capability):
    """Allowed structural roles, not a claim that the citation entails prose."""
    if target == "query_status":
        if capability in {"execution_proof", "query_scope_proof", "query_status_proof", "query_semantics"}:
            return capability, True, None
        if capability in {"returned_collection", "response_count"}:
            return "query_response_proof", True, None
        if capability in {"source_proof", "source_context", "tool_context"}:
            return "auxiliary_source_context", False, None
        return "rejected", False, "QueryStatusRequiresQueryEvidence"
    if target == "source_attribution":
        if capability == "source_proof":
            return capability, True, None
        if capability == "source_context":
            return "auxiliary_source_context", False, None
        if capability in {"execution_proof", "query_scope_proof", "query_status_proof", "tool_context"}:
            return "auxiliary_query_context", False, None
        return "rejected", False, "SourceAttributionRequiresSourceEvidence"
    return "cited_evidence", False, None


def _input_view_reference(reference, payload):
    path = reference["path"]
    if (len(path) < 4 or path[0] != "trusted_runtime_metadata" or type(path[1]) is not int
            or path[2] != "metadata"):
        return False
    observed = payload["trusted_runtime_metadata"][path[1]]
    return isinstance(observed, dict) and observed.get("origin") == "agent_evidence_view"


def _world_reference_error(reference, payload):
    path, value = reference["path"], reference["value"]
    record, container, tail = {}, {}, []
    capability = _reference_capability(reference, payload)
    if capability in {"source_proof", "source_context"}:
        return "SourceIdentityIsNotWorldFact"
    if capability in {"tool_context", "record_metadata", "untrusted_argument_context"}:
        return "AvailabilityIsNotWorldFact"
    if path[0] == "synthetic_evidence":
        record = payload["synthetic_evidence"][path[1]]
        if not isinstance(record, dict):
            return "InvalidClaimEvidence"
        if _invalid_state(record):
            return "InvalidEvidenceState"
        if path[2] in RECORD_METADATA:
            return "AvailabilityIsNotWorldFact"
        if path[2] == "payload":
            container, tail = record.get("payload"), path[3:]
        elif path[2] == "rows":
            container, tail = record.get("rows"), path[2:]
        else:
            container, tail = record, path[2:]
    elif path[0] == "business_observations":
        observation = payload["business_observations"][path[1]]
        if not isinstance(observation, dict):
            return "InvalidClaimEvidence"
        if path[2] != "output":
            return "AvailabilityIsNotWorldFact"
        container, tail = observation.get("output"), path[3:]
        record = _linked_record(observation, payload) or {}
        if observation.get("status") == "error":
            record = {**record, "result_state": "error"}
    elif path[0] == "trusted_runtime_metadata":
        # Runtime context describes what was supplied or query availability.
        # The observed candidate count is a concrete server-owned count value.
        if path[2:] == ["metadata", "returned_candidate_count"] and _input_view_reference(reference, payload):
            return None
        return "AvailabilityIsNotWorldFact"
    elif path[0] == "source_equivalence":
        return "SourceIdentityIsNotWorldFact"
    if not isinstance(record, dict):
        return "InvalidClaimEvidence"
    # A rows reference remains subject to the source payload's completion and
    # execution state; selecting rows must not erase a contradictory envelope.
    envelopes = [record, *([record["payload"]] if isinstance(record.get("payload"), dict) else []),
                 *([container] if isinstance(container, dict) else [])]
    if any(_invalid_state(item) for item in envelopes):
        return "InvalidEvidenceState"
    if any(_failed_execution(item) for item in envelopes):
        return "FailedSourceIsNotWorldFact"
    if capability in {"query_scope_proof", "query_status_proof"}:
        return "AvailabilityIsNotWorldFact"
    # Citing the whole mixed envelope must not launder status/data_missing into
    # a truth value. Cite its actual rows or a concrete business field instead.
    if not tail and isinstance(container, dict) and AVAILABILITY_FIELDS & container.keys():
        return "AvailabilityContainerIsNotWorldFact"
    if not tail and isinstance(container, dict):
        return "ResultEnvelopeRequiresSpecificField"
    mixed_error = _mixed_value_error(value)
    if mixed_error:
        return mixed_error
    unavailable = any(item.get("result_state") == "partial" or bool(item.get("data_missing"))
        or bool(item.get("source_errors")) or item.get("source_truncated") is True
        or item.get("truncated") is True or item.get("data_fresh") is False for item in envelopes)
    if value == [] and isinstance(value, list) and unavailable:
        # An empty partial result establishes returned-row count, not absence
        # in the queried world; the former belongs to query_status.
        return "IncompleteEmptyCollectionIsNotWorldAbsence"
    if value == [] and isinstance(value, list):
        # A successful call alone says nothing about result completeness. An
        # explicit complete envelope and known query arguments bound absence.
        if any("result_state" in item and item["result_state"] not in ("ok", "empty") for item in envelopes):
            return "EmptyCollectionCompletenessUnknown"
        if not any(item.get("result_state") in ("ok", "empty") for item in envelopes):
            return "EmptyCollectionCompletenessUnknown"
        scope = _empty_scope(reference, payload)
        if not isinstance(scope, dict) or not scope:
            return "EmptyCollectionScopeUnknown"
    return None


def _validate_binding(claim, *, dimension, surfaces, payload):
    try:
        parsed = ClaimBinding.model_validate(claim).model_dump()
    except (ValidationError, TypeError):
        return "InvalidClaimSchema", []
    target = parsed["target"]
    if dimension not in DIMENSION_TARGETS:
        return "InvalidClaimDimension", []
    if target not in DIMENSION_TARGETS[dimension]:
        return "ClaimTargetDimensionMismatch", []
    if (not isinstance(surfaces, dict) or not isinstance(surfaces.get(parsed["surface_id"]), str)
            or not contains_quote(surfaces[parsed["surface_id"]], parsed["quote"])):
        return "InvalidClaimLocation", []
    evidence = parsed["evidence"]
    if evidence and not valid_counterevidence(evidence, payload):
        return "InvalidClaimEvidence", []
    decisive = parsed["evidence_relation"] in {"supported", "contradicted"}
    if decisive and dimension != "safety" and not evidence:
        return "MissingClaimEvidence", []
    # Unknown may cite unavailable context to explain its uncertainty. It is
    # not claiming those references establish truth or falsehood.
    if not decisive:
        return None, [{"index": index, "role": "uncertainty_context", "validation_error": None}
                      for index in range(len(evidence))]
    notes, errors, proofs = [], [], 0
    for index, reference in enumerate(evidence):
        error = None
        role = "cited_evidence"
        if target == "input_view" and not _input_view_reference(reference, payload):
            error = "InputViewRequiresObservedEvidence"
        elif target == "input_view":
            role = "input_view_proof"
        if target == "world_fact":
            error = _world_reference_error(reference, payload)
            if error is None:
                role = "world_fact_proof"
                proofs += 1
            elif error == "AvailabilityIsNotWorldFact":
                # A real query-status field may contextualize a cited business
                # value. It never counts as the truth-bearing proof itself.
                role, error = "auxiliary_availability_context", None
        elif target in {"query_status", "source_attribution"}:
            role, is_proof, error = _capability_role(target, _reference_capability(reference, payload))
            proofs += int(is_proof)
        note = {"index": index, "role": "rejected" if error else role, "validation_error": error}
        if target == "world_fact" and error is None and role == "world_fact_proof" and reference["value"] == []:
            note.update(role="scoped_empty_result", scope_limited=True, query_scope=_empty_scope(reference, payload))
        notes.append(note)
        if error:
            errors.append(error)
    if errors:
        return errors[0], notes
    if target == "world_fact" and not proofs:
        return "AvailabilityIsNotWorldFact", notes
    if target == "query_status" and not proofs:
        return "QueryStatusRequiresQueryEvidence", notes
    if target == "source_attribution" and not proofs:
        return "SourceAttributionRequiresSourceEvidence", notes
    return None, notes


def validate_claim_binding(claim, *, dimension, surfaces, payload):
    """Return one stable validation error, or None; never infer a prose verdict."""
    return _validate_binding(claim, dimension=dimension, surfaces=surfaces, payload=payload)[0]


def aggregate_claims(claims, *, dimension, surfaces, payload, reference_errors=None, reported_references=None):
    """Aggregate validated atomic decisions, retaining every invalid raw claim.

    Invalid bindings are unresolved validation failures, not valid semantic nulls.
    A separate valid contradiction still wins over unknown/invalid claims.
    """
    checked, errors, relations = [], [], []
    for index, claim in enumerate(claims):
        raw = claim.model_dump() if isinstance(claim, ClaimBinding) else deepcopy(claim)
        if reference_errors is not None and index in reference_errors:
            error, evidence_validation = reference_errors[index], []
        else:
            error, evidence_validation = _validate_binding(raw, dimension=dimension, surfaces=surfaces, payload=payload)
        relation = raw.get("evidence_relation") if isinstance(raw, dict) else None
        entry = deepcopy(raw) if isinstance(raw, dict) else {"raw_claim": raw}
        entry["reported_evidence_relation"] = relation
        entry["validated_evidence_relation"] = relation if error is None else None
        entry["validation_error"] = error
        entry["evidence_validation"] = evidence_validation
        if reported_references is not None:
            entry["reported_evidence"] = deepcopy(reported_references[index]) if index < len(reported_references) else None
        checked.append(entry)
        if error:
            errors.append({"index": index, "error": error})
        else:
            relations.append(relation)
    if "contradicted" in relations:
        relation, passed = "contradicted", False
    elif errors or "insufficient_evidence" in relations:
        relation, passed = "insufficient_evidence", None
    elif claims:
        relation, passed = "supported", True
    else:
        relation, passed = "no_claim", True
    representative = next((item for item in checked if item["validated_evidence_relation"] == "contradicted"), None)
    if representative is None:
        representative = next((item for item in checked if item["validation_error"]), None)
    return {"evidence_relation": relation, "passed": passed, "claims": checked,
            "validation_errors": [f"claims[{item['index']}]:{item['error']}" for item in errors],
            "claim_validation_errors": errors, "validation_error": "InvalidClaimBindings" if errors else None,
            "surface_id": representative.get("surface_id") if representative else None,
            "quote": representative.get("quote") if representative else None,
            "counterevidence": representative.get("evidence") if representative else None}
