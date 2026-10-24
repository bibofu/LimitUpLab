"""Every Agent tool carries a documented, result-scoped output contract."""

from app.agents.tool_output_contracts import (
    TOOL_OUTPUT_SPECS,
    field_definitions_for,
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


def test_unknown_tool_has_no_field_definitions():
    assert field_definitions_for("unknown_tool") == {}


def test_market_rates_document_the_actual_denominator_and_ratio_unit():
    definitions = field_definitions_for("market_summary")["metadata"]
    assert "收盘涨停数加收盘未回封数" in definitions["touched_limit_count"]
    for field in ("intraday_opened_rate", "unsealed_rate"):
        assert "当日触及涨停总数" in definitions[field]
        assert "0至1比例" in definitions[field]


def test_daily_promotion_contract_uses_ratio_units_for_all_three_cohorts():
    definitions = field_definitions_for("daily_board_promotion")["rows"]
    for field in ("probability", "first_board_probability", "continued_board_probability"):
        assert "0至1比例" in definitions[field]
        assert "乘100" in definitions[field]
