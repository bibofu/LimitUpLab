"""Tool-surface integration for stock_profile."""

from unittest.mock import patch

from app.agents.runner.gateway import BusinessToolGateway
from app.agents.tool_output_contracts import field_definitions_for
from app.agents.tools import AgentToolRegistry
from app.services.stock_profile import build_stock_profile
from test_stock_profile import NOW, cninfo_profile, loader, raiser


def test_registry_wraps_profile_payload_with_partial_outcome_and_source_errors():
    from unittest.mock import patch

    from app.agents.tools import AgentToolRegistry

    payload = build_stock_profile(
        "600519",
        profile_loader=loader(cninfo_profile()),
        market_cap_loader=raiser(TimeoutError("eastmoney timed out")),
        now=NOW,
    )
    registry = AgentToolRegistry(events=[], first_board_repository=object())
    with patch.object(
        registry, "resolve_stock_identity", return_value=("600519", "贵州茅台"),
    ), patch("app.agents.tools.build_stock_profile", return_value=payload):
        result = registry.stock_profile("600519")

    assert result.name == "stock_profile"
    assert result.input == {"symbol": "600519", "include_market_cap": False}
    assert result.result_status == "partial"
    assert result.status == "success"
    assert result.output["total_market_cap"] is None
    assert result.source_errors
    trace = result.trace()
    assert trace.result is not None
    assert trace.result.status == "partial"
    assert trace.result.source_errors == list(result.source_errors)


def test_output_contract_documents_market_cap_and_registered_capital_units():
    from app.agents.tool_output_contracts import field_definitions_for

    definitions = field_definitions_for("stock_profile")["metadata"]
    assert "元" in definitions["total_market_cap"]
    assert "不是0" in definitions["total_market_cap"]
    assert "万元" in definitions["registered_capital_unit"]
    assert "不是总市值" in definitions["registered_capital"]
    assert "市值快照" in definitions["market_cap_as_of"]
    assert "data_missing" in definitions


def test_gateway_validates_and_executes_stock_profile_with_partial_state():
    from unittest.mock import patch

    from app.agents.runner.gateway import BusinessToolGateway
    from app.agents.tools import AgentToolRegistry

    payload = build_stock_profile(
        "600519",
        profile_loader=loader(cninfo_profile()),
        market_cap_loader=raiser(TimeoutError("eastmoney timed out")),
        now=NOW,
    )
    registry = AgentToolRegistry(events=[], first_board_repository=object())
    with patch.object(
        registry, "resolve_stock_identity", return_value=("600519", "贵州茅台"),
    ), patch("app.agents.tools.build_stock_profile", return_value=payload):
        gateway = BusinessToolGateway(registry)
        args = gateway.validate({"name": "stock_profile", "args": {"symbol": "600519"}})
        result, output, state = gateway.execute("stock_profile", args)

    assert args == {"symbol": "600519"}
    assert state == "partial"
    assert output["profile_status"] == "ok"
    assert output["market_cap_status"] == "unavailable"
    assert result.trace().result.status == "partial"
