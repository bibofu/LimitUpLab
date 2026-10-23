"""Every Agent tool carries a documented, result-scoped output contract."""

from app.agents.react_runtime.contracts import Compute
from app.agents.react_runtime.evidence import EvidenceStore
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


def test_evidence_view_carries_field_definitions():
    store = EvidenceStore()
    key = store.add(
        tool="limit_up_events",
        state="ok",
        arguments={},
        payload={
            "matched_count": 1,
            "events": [{"symbol": "000011", "name": "测试", "board_height": 1}],
        },
    )
    view = store.view(key)
    assert view["field_definitions"]["rows"]["board_height"].startswith("连板高度")
    assert "matched_count" in view["field_definitions"]["metadata"]


def test_compute_result_inherits_parent_field_definitions():
    store = EvidenceStore()
    key = store.add(
        tool="limit_up_events",
        state="ok",
        arguments={},
        payload={
            "matched_count": 1,
            "events": [{"symbol": "000011", "board_height": 1}],
        },
    )
    computed = store.compute(Compute(evidence_id=key, limit=1))
    view = store.view(computed)
    assert "board_height" in view["field_definitions"]["rows"]


def test_unknown_or_empty_parent_definitions_are_safe():
    assert field_definitions_for("unknown_tool") == {}
    assert merge_field_definitions([{}, {}], rows=[{"symbol": "000011"}]) == {}
