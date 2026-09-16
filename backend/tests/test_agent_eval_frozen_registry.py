"""Replay through the real Gateway and parallel Runtime node, without an LLM."""

from datetime import date
import socket
import sqlite3

import pytest

from app.agent_eval.frozen_registry import FrozenAgentToolRegistry, FrozenFixtureError
from app.agent_eval.models import WorldSpec
from app.agents.query_contract import current_query_reference_date, query_reference_date_override
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.runtime import Run
from app.agents.react_runtime.tools import ToolGateway
from app.models import AgentChatRequest


def recording(tool="market_summary", arguments=None, state="ok", payload=None, key="r"):
    return dict(id=key, tool=tool, arguments=arguments or {}, origin="synthetic",
                provenance="protocol test only, not market data",
                observation=dict(state=state, payload=payload if payload is not None else
                                 {"trade_date": "2026-09-11", "count": 2}, summary="fixture"))


def world(recordings=None, profile="v1_close_review", **overrides):
    data = dict(
        world_id="replay-contract", world_version=1, profile=profile,
        anchor_datetime="2026-09-13T18:00:00+08:00", latest_local_trade_date="2026-09-11",
        tool_contract_version="agent-tools-v2", evidence_version="react-evidence-v4",
        trading_calendar=dict(id="test", version=1, start_date="2026-09-10",
                              end_date="2026-09-13", trading_dates=["2026-09-10", "2026-09-11"]),
        recordings=recordings or [recording()],
    )
    return WorldSpec.model_validate({**data, **overrides})


@pytest.fixture(autouse=True)
def forbid_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("frozen replay attempted real network or database access")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)


def invoke(registry, tool="market_summary", arguments=None):
    gateway = ToolGateway(registry, EvidenceStore())
    with registry.anchored():
        args = gateway.validate({"name": tool, "args": arguments or {}})
        return gateway.execute(tool, args)


@pytest.mark.parametrize("profile,count", [("v1_close_review", 24), ("extended", 26)])
def test_profile_uses_production_catalog(profile, count):
    registry = FrozenAgentToolRegistry(world(profile=profile))
    definitions = ToolGateway(registry, EvidenceStore()).definitions()
    assert len(registry.schemas()) == count
    names = {item["function"]["name"] for item in definitions}
    for name in ("web_search", "remote_limit_up_pool"):
        assert (name in names) == (profile == "extended")
        if profile == "v1_close_review":
            with registry.anchored(), pytest.raises(ValueError, match="profile"):
                registry.execute_frozen_calls([{"name": name, "arguments": {}}],
                    request=AgentChatRequest(session_id="test", message=""))


@pytest.mark.parametrize("arguments", [{}, {"include_limit_down": False}])
def test_omitted_and_explicit_default_match_effective_production_default(arguments):
    registry = FrozenAgentToolRegistry(world())
    _, payload, state = invoke(registry, arguments=arguments)
    assert payload["count"] == 2 and state == "ok"
    assert registry.attempts[0]["recording_id"] == "r"


def test_limit_up_aliases_match_the_same_production_status_and_sort_defaults():
    entry = recording("limit_up_events", {
        "trade_date": "2026-09-11", "market": "chinext", "limit": 100,
    }, payload={"trade_date": "2026-09-11", "events": [{"symbol": "300563"}]})
    registry = FrozenAgentToolRegistry(world([entry]))

    _, payload, state = invoke(registry, "limit_up_events", {
        "trade_date": "2026-09-11", "market": "chinext", "closed_only": True,
        "sort_by": "board_height", "sort_order": "desc", "limit": 100,
    })

    assert state == "ok" and payload["events"][0]["symbol"] == "300563"
    assert registry.attempts[0]["recording_id"] == "r"


def test_only_schema_nullable_parameters_accept_explicit_null():
    registry = FrozenAgentToolRegistry(world([recording("stock_kline", {"symbol": "000001"})]))
    assert invoke(registry, "stock_kline", {"symbol": "000001", "end_date": None})[2] == "ok"
    with pytest.raises(ValueError, match="Invalid tool arguments"):
        invoke(FrozenAgentToolRegistry(world()), arguments={"include_limit_down": None})


@pytest.mark.parametrize("arguments", [
    {"symbol": "000002", "days": 20, "end_date": "2026-09-11"},
    {"symbol": "000001", "days": 10, "end_date": "2026-09-11"},
    {"symbol": "000001", "days": 20, "end_date": "2026-09-10"},
])
def test_changed_symbol_window_or_date_never_matches(arguments):
    registry = FrozenAgentToolRegistry(world([recording("stock_kline", {
        "symbol": "000001", "days": 20, "end_date": "2026-09-11"})]))
    with pytest.raises(FrozenFixtureError, match="no recording"):
        invoke(registry, "stock_kline", arguments)
    assert registry.attempts[-1]["outcome"] == "rejected"


def test_invalid_schema_and_unrecorded_tool_do_not_fall_back():
    registry = FrozenAgentToolRegistry(world())
    with pytest.raises(ValueError, match="Invalid tool arguments"):
        invoke(registry, arguments={"include_limit_down": "false"})
    with pytest.raises(FrozenFixtureError):
        invoke(registry, "stock_news", {"symbol": "000001"})
    with pytest.raises(FrozenFixtureError):
        invoke(registry, arguments={"include_limit_down": True})


def test_weekend_anchor_and_latest_local_are_independent_and_restore():
    registry = FrozenAgentToolRegistry(world())
    with query_reference_date_override(date(2026, 5, 15)):
        _, payload, _ = invoke(registry, arguments={"requested_as_of": "2026-09-11"})
        assert payload["trade_date"] == "2026-09-11"
        assert current_query_reference_date() == date(2026, 5, 15)
        with pytest.raises(ValueError, match="historical scope"):
            invoke(registry, arguments={"requested_as_of": "2026-09-13"})
        with pytest.raises(FrozenFixtureError, match="anchored"):
            registry.execute_frozen_calls([{"name": "market_summary", "arguments": {}}],
                request=AgentChatRequest(session_id="test", message=""))


@pytest.mark.parametrize("state", ["ok", "empty", "partial", "error"])
def test_canonical_outcome_survives_gateway_without_empty_reclassification(state):
    entry = recording("limit_up_events", state=state, payload={"events": []})
    if state in {"partial", "error"}:
        entry["observation"]["source_errors"] = ["test-source unavailable"]
    registry = FrozenAgentToolRegistry(world([entry]))
    result, payload, actual = invoke(registry, "limit_up_events")
    assert actual == state
    assert result.trace().result.status == state
    if state in {"partial", "error"}:
        assert payload == {"events": []}  # Preserve payload and outcome as separate layers.
        assert result.trace().result.source_errors == ["test-source unavailable"]


def test_canonical_ratings_not_flattened_twice_and_payloads_not_shared():
    payload = {"top_candidates": [{"symbol": "000001", "score": 80}]}
    spec = world([recording("first_board_ratings", payload=payload)])
    registry = FrozenAgentToolRegistry(spec)
    spec.recordings[0].observation.payload["top_candidates"].clear()
    _, first, _ = invoke(registry, "first_board_ratings")
    assert first == payload
    first["top_candidates"].clear()
    assert invoke(registry, "first_board_ratings")[1] == payload
    attempts = registry.attempts
    attempts.clear()
    assert len(registry.attempts) == 2


@pytest.mark.parametrize("field", ["tool_contract_version", "evidence_version"])
def test_version_drift_rejected_before_replay(field):
    with pytest.raises(FrozenFixtureError, match="version mismatch"):
        FrozenAgentToolRegistry(world(**{field: "old"}))


def test_ambiguous_or_invalid_recordings_fail_during_setup():
    entries = [recording(), recording(arguments={"include_limit_down": False}, key="other")]
    with pytest.raises(FrozenFixtureError, match="ambiguous"):
        FrozenAgentToolRegistry(world(entries))
    with pytest.raises(FrozenFixtureError, match="invalid recording"):
        FrozenAgentToolRegistry(world([recording(arguments={"unknown": 1})]))


@pytest.mark.parametrize("payload", [[], [{"trade_date": "2026-09-11", "probability": 0.5}]])
def test_native_list_payloads_replay_without_wrapping(payload):
    registry = FrozenAgentToolRegistry(world([recording("daily_board_promotion", payload=payload)]))
    _, actual, state = invoke(registry, "daily_board_promotion")
    assert actual == payload
    assert state == "ok"


def test_real_runtime_tool_threads_receive_anchor_and_record_both_calls():
    registry = FrozenAgentToolRegistry(world([
        recording(), recording("stock_news", {"symbol": "000001"}, key="news"),
    ]))
    pending = [dict(name="market_summary", args={}, id="a", signature="a"),
               dict(name="stock_news", args={"symbol": "000001"}, id="b", signature="b")]
    runtime = Run(AgentChatRequest(session_id="test", message="test"), registry, None, [], None)
    with query_reference_date_override(date(2026, 5, 15)):
        with registry.anchored():
            result = runtime.tools_node({"pending": pending, "observations": []})
        assert current_query_reference_date() == date(2026, 5, 15)
    assert len(result["observations"]) == 2
    assert all(value["result_state"] == "ok" for _, value in result["observations"])
    assert {attempt["reference_date"] for attempt in registry.attempts} == {"2026-09-13"}
    assert len(runtime.evidence.current_records()) == 2
