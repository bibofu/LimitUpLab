"""Self-contained schemas preserve nested required fields and strict objects."""

from copy import deepcopy
import json

import pytest

from evals.golden.judge import Judgements, VisibleAudit
from evals.golden.judge_schema import inline_local_refs


@pytest.mark.parametrize("model", [Judgements, VisibleAudit])
def test_wire_schema_inlines_all_nested_objects_without_mutating_validation_schema(model):
    schema = model.model_json_schema()
    original = deepcopy(schema)
    wire = inline_local_refs(schema)
    assert schema == original
    assert '"$ref"' not in json.dumps(wire) and '"$defs"' not in json.dumps(wire)
    assert wire["additionalProperties"] is False
    assert wire["required"] == schema["required"]
    if model is VisibleAudit:
        for field in ("factual", "source", "safety"):
            assert wire["properties"][field]["type"] == "object"
            assert wire["properties"][field]["additionalProperties"] is False
    else:
        assert wire["properties"]["checks"]["items"]["type"] == "object"
        assert "index" in wire["properties"]["checks"]["items"]["required"]


def test_reference_sibling_description_and_falsy_schema_values_are_preserved():
    schema = {"$defs": {"A": {"type": "object", "additionalProperties": False}},
              "properties": {"value": {"$ref": "#/$defs/A", "description": "local meaning"}}}
    assert inline_local_refs(schema)["properties"]["value"] == {
        "type": "object", "additionalProperties": False, "description": "local meaning"}


@pytest.mark.parametrize("schema", [
    {"$ref": "https://example.invalid/schema"},
    {"$ref": "#/$defs/absent"},
    {"$defs": {"A": {"$ref": "#/$defs/A"}}, "$ref": "#/$defs/A"},
    {"$defs": {"A": {"type": "string", "minLength": 5}},
     "properties": {"value": {"$ref": "#/$defs/A", "minLength": 1}}},
])
def test_unknown_or_recursive_references_fail_before_any_model_request(schema):
    with pytest.raises(ValueError):
        inline_local_refs(schema)
