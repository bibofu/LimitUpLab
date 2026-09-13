from datetime import date
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.agents.react_runtime.catalog import CATALOG, _assert_direct_implementation
from app.agents.react_runtime.context import prepare_history
from app.agents.react_runtime.contracts import Compute
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.tools import ToolGateway
from app.agents.tools import TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest, ChatSessionMessage
from app.post_limit_query_contract import PostLimitQueryContract


def gateway(**methods):
    registry = SimpleNamespace(events=[], schemas=lambda: TOOL_SCHEMAS, is_enabled=lambda name: True, **methods)
    return ToolGateway(registry, EvidenceStore())


def test_every_existing_tool_has_reviewed_contract():
    assert {s.name for s in TOOL_SCHEMAS} == set(CATALOG)
    assert all(CATALOG[schema.name] is schema for schema in TOOL_SCHEMAS)
    assert set(gateway().schemas) == set(CATALOG)


def test_canonical_contract_builds_typed_langchain_tools():
    target = gateway()

    assert all(isinstance(tool, StructuredTool) for tool in target.structured.values())
    assert all(
        isinstance(tool.args_schema, type)
        and issubclass(tool.args_schema, BaseModel)
        for tool in target.structured.values()
    )
    assert target.structured["market_summary"].metadata == {
        "time_mode": "latest_local",
        "dates": (),
        "collection": None,
        "adapter": "direct",
    }
    assert target.schemas["market_summary"]["args_schema"]["properties"][
        "include_limit_down"
    ]["default"] is False
    assert target.schemas["hot_stock_ranking"]["args_schema"]["properties"][
        "limit"
    ]["default"] == 20


def test_direct_implementation_drift_fails_before_runtime_repair():
    original = CATALOG["market_summary"]
    drifted = replace(
        original,
        args_schema={
            **original.args_schema,
            "properties": {
                **original.args_schema["properties"],
                "invented_argument": {"type": "string"},
            },
        },
    )

    with pytest.raises(RuntimeError, match="contract/signature drift"):
        _assert_direct_implementation(drifted)


def test_known_schema_signature_drift_is_removed_from_public_contract():
    target = gateway()
    limit_up = target.schemas["limit_up_events"]["args_schema"]
    stock_kline = target.schemas["stock_kline"]["args_schema"]
    post_path = target.schemas["post_limit_path"]["args_schema"]

    assert "result_mode" not in limit_up["properties"]
    assert stock_kline["properties"]["symbol"]["type"] == "string"
    assert post_path["required"] == ["symbol"]
    with pytest.raises(ValueError, match="Invalid tool arguments"):
        target.validate({
            "name": "limit_up_events",
            "args": {"result_mode": "count"},
        })


@pytest.mark.parametrize("name", ["prediction_quality_audit", "rating_backtest", "rating_evaluation", "review_high_score_picks"])
def test_required_dates_enforced_before_execution(name):
    with pytest.raises(ValueError, match="Missing required"):
        gateway().validate({"name": name, "args": {}})


def test_asof_guard_does_not_replace_history_with_current():
    with pytest.raises(ValueError, match="historical scope"):
        gateway().validate({"name": "hot_stock_ranking", "args": {"requested_as_of": "1990-01-01"}})


def test_first_board_filter_is_executable_not_virtual_capability():
    def ratings(trade_date=None):
        assert trade_date == date(2026, 5, 15)
        return ToolResult(name="first_board_ratings", input={}, summary="fixture", output={
            "trade_date": "2026-05-15", "candidates": [{"facts": {"symbol": "000001", "industry": "银行"}, "score": 80}],
        })
    result, payload, state = gateway(first_board_ratings=ratings).execute(
        "first_board_filter", {"query": "银行", "trade_date": "2026-05-15"},
    )
    assert state == "ok" and payload["items"][0]["symbol"] == "000001"


def test_post_limit_adapter_is_declared_and_invoked_through_structured_tool():
    def screen(contract):
        assert isinstance(contract, PostLimitQueryContract)
        assert contract.shape == "high_drawdown"
        assert contract.data_as_of == date(2026, 9, 11)
        return ToolResult(
            name="post_limit_screen",
            input=contract.to_tool_arguments(),
            output={"candidates": [], "data_as_of": "2026-09-11"},
            summary="fixture",
            result_status="empty",
        )

    result, payload, state = gateway(post_limit_screen=screen).execute(
        "post_limit_screen",
        {"shape": "high_drawdown", "data_as_of": "2026-09-11"},
    )

    assert result.name == "post_limit_screen"
    assert payload["data_as_of"] == "2026-09-11"
    assert state == "empty"


def test_partial_source_never_becomes_complete_set_difference():
    store = EvidenceStore()
    a = store.add(tool="limit_up_events", payload={"items": [{"symbol": "000001"}], "matched_count": 20}, state="ok", arguments={})
    b = store.add(tool="limit_up_events", payload={"items": []}, state="empty", arguments={})
    with pytest.raises(ValueError, match="complete source sets"):
        store.compute(Compute(evidence_id=a, other_id=b, operation="difference"))
    derived = store.compute(Compute(evidence_id=a, operation="aggregate"))
    assert store.get(derived)["result_state"] == "partial"


def test_historical_reference_only_loaded_from_requested_session():
    store = EvidenceStore()
    key = store.add(tool="fixture", payload={"items": [{"symbol": "000001"}]}, state="ok", arguments={})
    metadata = {"tool_results": [{"name": "react_execution", "output": {"evidence": store.records}}]}
    old = ChatSessionMessage(message_id="m", session_id="other", role="assistant", content="上一组", metadata=metadata, created_at="2026-09-12T00:00:00Z")
    target = EvidenceStore()
    messages, refs = prepare_history(AgentChatRequest(session_id="mine", message="这组"), [old], target)
    assert not messages and not refs and not target.records
    old.session_id = "mine"
    messages, refs = prepare_history(AgentChatRequest(session_id="mine", message="这组"), [old], target)
    assert refs[0]["evidence_id"] == key and target.get(key)["historical_reference"]
    assert refs[0]["evidence_scope"] == "conversation_history"


def test_history_keeps_the_complete_upstream_context_window():
    history = [
        ChatSessionMessage(
            message_id=f"m-{index}",
            session_id="mine",
            role="user" if index % 2 == 0 else "assistant",
            content=f"context-{index}",
            created_at=f"2026-09-12T00:{index:02d}:00Z",
        )
        for index in range(16)
    ]

    messages, refs = prepare_history(
        AgentChatRequest(session_id="mine", message="继续上面的研究"),
        history,
        EvidenceStore(),
    )

    assert [message.content for message in messages] == [
        f"context-{index}" for index in range(16)
    ]
    assert refs == []


def test_empty_symbols_rejected_instead_of_unbounded_query():
    target = gateway(resolve_stock_identity=lambda value: (value, value))
    with pytest.raises(ValueError, match="Empty symbol set"):
        target.execute("first_board_ratings", {"symbols": []})
