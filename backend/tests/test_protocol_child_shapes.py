"""Malformed judge children expose schema-owned shapes without model content."""

import json

from pydantic import ValidationError
import pytest

from app.services.protocol_diagnostics import schema_diagnostics, schema_owned_argument_shapes
from evals.golden.judge import AuditJudgement as Finding, VisibleAudit as Audit


def test_three_invalid_children_reveal_distinct_types_without_values():
    raw = {"factual": None, "source": "PRIVATE_BODY", "safety": ["PRIVATE_ELEMENT"]}
    with pytest.raises(ValidationError) as caught:
        Audit.model_validate(raw)
    diagnostic = schema_diagnostics(caught.value, Audit.model_json_schema())
    assert diagnostic["schema_error_count"] == 3
    assert diagnostic["schema_errors"] == [
        {"path": ["factual"], "type": "model_type", "input_shape": {"shape": "null"}},
        {"path": ["source"], "type": "model_type", "input_shape": {"shape": "string", "size": 12}},
        {"path": ["safety"], "type": "model_type", "input_shape": {"shape": "array", "size": 1}},
    ]
    assert "PRIVATE" not in json.dumps(diagnostic)


@pytest.mark.parametrize(("raw", "shape"), [
    (None, {"shape": "null"}), ("PRIVATE_BODY", {"shape": "string", "size": 12}),
    (["PRIVATE_ELEMENT"], {"shape": "array", "size": 1}),
    (True, {"shape": "boolean"}), (12.3, {"shape": "number"}),
])
def test_root_model_type_records_actual_shape_for_per_dimension_validation(raw, shape):
    with pytest.raises(ValidationError) as caught:
        Finding.model_validate(raw)
    diagnostic = schema_diagnostics(caught.value, Finding.model_json_schema())
    assert diagnostic["schema_errors"] == [{"path": [], "type": "model_type", "input_shape": shape}]
    assert "PRIVATE" not in json.dumps(diagnostic)


def test_declared_field_shapes_distinguish_missing_null_and_object_without_private_keys():
    raw = {"factual": None, "source": {"PRIVATE_KEY": "PRIVATE_VALUE"}, "PRIVATE_EXTRA": "PRIVATE_VALUE"}
    fields = schema_owned_argument_shapes(raw, Audit.model_json_schema())
    assert fields == [
        {"path": ["factual"], "present": True, "shape": "null"},
        {"path": ["source"], "present": True, "shape": "object", "size": 1},
        {"path": ["safety"], "present": False},
    ]
    assert "PRIVATE" not in json.dumps(fields)


def test_missing_error_parent_values_and_unknown_extra_keys_are_not_treated_as_child_shapes():
    with pytest.raises(ValidationError) as caught:
        Finding.model_validate({"PRIVATE_KEY": {"PRIVATE_NESTED": "PRIVATE_VALUE"}})
    diagnostic = schema_diagnostics(caught.value, Finding.model_json_schema())
    assert diagnostic["schema_errors"] == [
        {"path": ["reason"], "type": "missing"},
        {"path": ["claims"], "type": "missing"},
        {"path": ["<unknown>"], "type": "extra_forbidden"},
    ]
    assert "PRIVATE" not in json.dumps(diagnostic)


def test_invalid_claim_relation_retains_nested_schema_path_without_quote_or_evidence_value():
    raw = {"reason": "PRIVATE_REASON", "claims": [{"surface_id": "final", "quote": "PRIVATE_QUOTE",
        "target": "world_fact", "evidence_relation": "PRIVATE_RELATION", "evidence": []}]}
    with pytest.raises(ValidationError) as caught:
        Finding.model_validate(raw)
    diagnostic = schema_diagnostics(caught.value, Finding.model_json_schema())
    assert diagnostic["schema_errors"] == [{"path": ["claims", 0, "evidence_relation"],
        "type": "literal_error", "input_shape": {"shape": "string", "size": len("PRIVATE_RELATION")}}]
    assert "PRIVATE" not in json.dumps(diagnostic)


def test_shape_diagnostics_are_bounded_and_leave_input_unchanged():
    schema = {"type": "object", "properties": {f"owned_{index}": {} for index in range(30)}}
    raw = {key: ["PRIVATE_VALUE"] for key in schema["properties"]}
    before = json.dumps(raw)
    fields = schema_owned_argument_shapes(raw, schema)
    assert len(fields) == 16 and all(item["shape"] == "array" and item["size"] == 1 for item in fields)
    assert json.dumps(raw) == before and "PRIVATE" not in json.dumps(fields)
