"""Synthetic protocol-only unit fixtures; never real evaluation questions or Golden data."""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.agents import tools as tools_module
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.tools import AgentToolRegistry, V1_AGENT_PROFILE
from app.services.sample_data import SAMPLE_EVENTS


DAY = date(2026, 9, 21)


def protocol_events(count, *, closed=True, prefix="600", day=DAY):
    return [SAMPLE_EVENTS[0].model_copy(update={
        "symbol": f"{prefix}{i:03d}", "name": f"protocol-fixture-{i}",
        "trade_date": day, "closed_limit": closed, "board_height": 1,
        "industry": "protocol-industry", "concept": "protocol-concept",
    }) for i in range(count)]


def registry(events):
    # The production methods only need the in-memory event collection. Do not
    # construct repositories, collectors, sessions or any evaluation registry.
    result = AgentToolRegistry.__new__(AgentToolRegistry)
    result.events = events
    result.profile = V1_AGENT_PROFILE
    return result


@pytest.mark.parametrize("event_type", ["limit_up", "broken_board"])
@pytest.mark.parametrize("mode", ["count", "list", "summary", "ranking"])
@pytest.mark.parametrize("count", [0, 4, 100, 103])
@pytest.mark.parametrize("limit", [30, 100])
def test_full_match_count_survives_list_limit(event_type, mode, count, limit):
    closed = event_type == "limit_up"
    tools = registry(protocol_events(count, closed=closed))
    result = tools.market_event_pool(
        event_type=event_type, trade_date=DAY, result_mode=mode, limit=limit,
    )
    output = result.output
    expected_returned = 0 if mode == "count" else min(count, limit)
    assert output["matched_count"] == count
    assert output["returned_count"] == len(output["items"]) == expected_returned
    assert output["trade_date"] == DAY.isoformat()
    assert f"命中 {count} 只" in result.summary
    assert result.trace_output == output

    # Count-only is complete with no display rows; bounded lists must continue
    # to expose truncation through the existing evidence contract.
    store = EvidenceStore()
    key = store.add(tool="market_event_pool", payload=output, state="ok", arguments=result.input)
    expected_truncated = mode != "count" and count > limit
    assert store.view(key)["source_truncated"] is expected_truncated
    assert store.view(key)["result_state"] == ("partial" if expected_truncated else "ok")


@pytest.mark.parametrize("event_type", ["limit_up", "broken_board"])
def test_count_preserves_date_market_query_and_status_filters(event_type):
    closed = event_type == "limit_up"
    selected = protocol_events(103, closed=closed)
    tools = registry(selected + protocol_events(7, closed=not closed, prefix="601")
                     + protocol_events(9, closed=closed, prefix="300")
                     + protocol_events(11, closed=closed, day=DAY-timedelta(days=3)))
    all_selected = tools.market_event_pool(
        event_type=event_type, trade_date=DAY, market="main_board", result_mode="count",
    ).output
    assert all_selected["matched_count"] == 103
    one = tools.market_event_pool(
        event_type=event_type, trade_date=DAY, market="main_board", query="600102", result_mode="list",
    ).output
    assert one["matched_count"] == one["returned_count"] == 1
    assert one["items"][0]["symbol"] == "600102"
    assert one["items"][0]["closed_limit"] is closed
    missing = tools.market_event_pool(
        event_type=event_type, trade_date=DAY, query="absent-protocol-token", result_mode="count",
    ).output
    assert missing["matched_count"] == 0


@pytest.mark.parametrize("mode", ["count", "list", "summary", "ranking"])
def test_limit_down_keeps_complete_remote_count(monkeypatch, mode):
    snapshot = SimpleNamespace(trade_date=DAY, source="protocol-only-remote", items=[
        SimpleNamespace(symbol=f"600{i:03d}", name=f"protocol-fixture-{i}",
                        change_pct=-10.0, industry="protocol-industry") for i in range(103)
    ])
    monkeypatch.setattr(tools_module, "collect_limit_down_pool", lambda day: snapshot)
    output = registry([]).market_event_pool(
        event_type="limit_down", trade_date=DAY, result_mode=mode, limit=30,
    ).output
    assert output["matched_count"] == 103
    assert output["returned_count"] == (0 if mode == "count" else 30)


def test_remote_date_mismatch_still_rejected(monkeypatch):
    monkeypatch.setattr(tools_module, "collect_limit_down_pool", lambda day: SimpleNamespace(
        trade_date=DAY-timedelta(days=1), items=[], source="protocol-only-remote",
    ))
    with pytest.raises(ValueError, match="date does not match"):
        registry([]).market_event_pool(event_type="limit_down", trade_date=DAY, result_mode="count")
