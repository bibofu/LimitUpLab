"""Every Agent tool carries a documented, result-scoped output contract."""

from app.agents.tool_output_contracts import (
    TOOL_OUTPUT_SPECS,
    field_definitions_for,
    merge_field_definitions,
)
from app.agents.tools import TOOL_SCHEMAS


def test_every_tool_has_a_documented_output_contract():
    assert set(TOOL_OUTPUT_SPECS) == {contract.name for contract in TOOL_SCHEMAS}
    for contract in TOOL_SCHEMAS:
        spec = TOOL_OUTPUT_SPECS[contract.name]
        if contract.collection:
            assert spec.row_model is not None, contract.name
        else:
            assert spec.payload_model is not None, contract.name
        definitions = field_definitions_for(contract.name)
        assert definitions, contract.name
        for section in definitions.values():
            assert section
            assert all(text.strip() for text in section.values())


def test_field_definitions_are_limited_to_present_fields():
    definitions = field_definitions_for(
        "limit_up_events",
        rows=[{"symbol": "600519"}],
        metadata={"matched_count": 1},
    )
    assert set(definitions["rows"]) == {"symbol"}
    assert set(definitions["metadata"]) == {"matched_count"}


def test_unknown_or_empty_parent_definitions_are_safe():
    assert field_definitions_for("unknown_tool") == {}
    assert merge_field_definitions([{}, {}], rows=[{"symbol": "000011"}]) == {}
