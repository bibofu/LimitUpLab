"""Bind atomic judge findings to real surfaces and typed evidence locations.

These checks validate citation capability, not natural-language entailment. The
model can still omit a claim, choose a wrong target, or cite an irrelevant value;
those semantic failures require independent diagnostics and human review.
"""

from copy import deepcopy
from typing import Literal

from pydantic import Field, ValidationError

from evals.golden.contracts import StrictModel
from evals.golden.judge_grounding import EvidenceReference, contains_quote, valid_counterevidence


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


def _invalid_state(container):
    return isinstance(container, dict) and "result_state" in container and not isinstance(container["result_state"], str)


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
        record = {"result_state": (container.get("result_state") if isinstance(container, dict) else None)
                  or ("error" if observation.get("status") == "error" else None)}
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
    if _invalid_state(container):
        return "InvalidEvidenceState"
    if record.get("result_state") == "error":
        return "FailedSourceIsNotWorldFact"
    if tail and tail[0] in AVAILABILITY_FIELDS:
        return "AvailabilityIsNotWorldFact"
    # Citing the whole mixed envelope must not launder status/data_missing into
    # a truth value. Cite its actual rows or a concrete business field instead.
    if not tail and isinstance(container, dict) and AVAILABILITY_FIELDS & container.keys():
        return "AvailabilityContainerIsNotWorldFact"
    unavailable = (record.get("result_state") == "partial" or bool(record.get("data_missing"))
        or record.get("source_truncated") is True
        or isinstance(container, dict) and (bool(container.get("data_missing"))
            or container.get("result_state") in ("partial", "error")
            or container.get("source_truncated") is True or container.get("truncated") is True
            or container.get("data_fresh") is False))
    if value == [] and isinstance(value, list) and unavailable:
        # An empty partial result establishes returned-row count, not absence
        # in the queried world; the former belongs to query_status.
        return "IncompleteEmptyCollectionIsNotWorldAbsence"
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
    notes, errors, world_proofs = [], [], 0
    for index, reference in enumerate(evidence):
        error = None
        role = "cited_evidence"
        if target == "input_view" and not _input_view_reference(reference, payload):
            error = "InputViewRequiresObservedEvidence"
        if target == "world_fact":
            error = _world_reference_error(reference, payload)
            if error is None:
                role = "world_fact_proof"
                world_proofs += 1
            elif error == "AvailabilityIsNotWorldFact":
                # A real query-status field may contextualize a cited business
                # value. It never counts as the truth-bearing proof itself.
                role, error = "auxiliary_availability_context", None
        notes.append({"index": index, "role": "rejected" if error else role, "validation_error": error})
        if error:
            errors.append(error)
    if errors:
        return errors[0], notes
    if target == "world_fact" and not world_proofs:
        return "AvailabilityIsNotWorldFact", notes
    return None, notes


def validate_claim_binding(claim, *, dimension, surfaces, payload):
    """Return one stable validation error, or None; never infer a prose verdict."""
    return _validate_binding(claim, dimension=dimension, surfaces=surfaces, payload=payload)[0]


def aggregate_claims(claims, *, dimension, surfaces, payload):
    """Aggregate validated atomic decisions, retaining every invalid raw claim.

    Invalid bindings are unresolved validation failures, not valid semantic nulls.
    A separate valid contradiction still wins over unknown/invalid claims.
    """
    checked, errors, relations = [], [], []
    for index, claim in enumerate(claims):
        raw = claim.model_dump() if isinstance(claim, ClaimBinding) else deepcopy(claim)
        error, evidence_validation = _validate_binding(raw, dimension=dimension, surfaces=surfaces, payload=payload)
        relation = raw.get("evidence_relation") if isinstance(raw, dict) else None
        entry = deepcopy(raw) if isinstance(raw, dict) else {"raw_claim": raw}
        entry["reported_evidence_relation"] = relation
        entry["validated_evidence_relation"] = relation if error is None else None
        entry["validation_error"] = error
        entry["evidence_validation"] = evidence_validation
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
