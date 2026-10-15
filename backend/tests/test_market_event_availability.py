"""Synthetic clock/boundary fixtures, independent of real market counts."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.agents import query_contract, tools as tools_module
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.tools import AgentToolRegistry
from app.services.sample_data import SAMPLE_EVENTS


DAY = date(2026, 9, 21)
CN = ZoneInfo("Asia/Shanghai")


def registry():
    result = AgentToolRegistry.__new__(AgentToolRegistry)
    result.events = [SAMPLE_EVENTS[0].model_copy(update={"trade_date": DAY})]
    return result


@pytest.mark.parametrize("event_type", ["limit_up", "limit_down", "broken_board"])
@pytest.mark.parametrize("now,requested,reason", [
    (datetime(2026, 9, 21, 23, tzinfo=CN), date(2026, 9, 22), "requested_date_in_future"),
    (datetime(2026, 9, 21, 14, 59, tzinfo=CN), DAY, "market_not_closed"),
    (datetime(2026, 9, 21, 15, tzinfo=CN), DAY, "closing_data_pending"),
    (datetime(2026, 9, 21, 15, 29, tzinfo=CN), DAY, "closing_data_pending"),
])
def test_unavailable_completed_day_is_unknown_not_zero(monkeypatch, event_type, now, requested, reason):
    monkeypatch.setattr(tools_module, "current_query_reference_time", lambda: now)
    def forbidden(*args, **kwargs):
        pytest.fail("Unavailable date must not query data or silently fall back")
    monkeypatch.setattr(tools_module, "collect_limit_down_pool", forbidden)
    agent_tools = registry()
    agent_tools.limit_up_events = forbidden
    result = agent_tools.market_event_pool(event_type=event_type, trade_date=requested, result_mode="count")
    assert result.output["trade_date"] == requested.isoformat()
    assert result.output["matched_count"] is None
    assert result.output["availability"] == reason
    assert result.output["data_missing"] == [reason]
    assert result.trace().result.status == "partial"
    store = EvidenceStore()
    key = store.add(tool=result.name, payload=result.output, state=result.result_status, arguments=result.input)
    assert store.view(key)["result_state"] == "partial"
    assert store.view(key)["data_missing"] == [reason]


def test_completed_day_filter_can_still_truthfully_return_zero(monkeypatch):
    monkeypatch.setattr(tools_module, "current_query_reference_time", lambda: datetime(2026, 9, 21, 15, 30, tzinfo=CN))
    result = registry().market_event_pool(event_type="limit_up", trade_date=DAY, query="absent-fixture", result_mode="count")
    assert result.output["matched_count"] == 0
    assert result.trace().result.status == "empty"


def test_explicit_empty_date_metadata_does_not_fall_back_to_prior_date():
    requested = date(2026, 9, 22)
    result = registry().limit_up_events(trade_date=requested)
    assert result.output == []
    assert result.trace_output["trade_date"] == requested.isoformat()
    assert result.trace_output["start_trade_date"] is None
    assert result.trace_output["selected_trade_day_count"] == 0


def test_query_reference_date_tracks_shanghai_midnight(monkeypatch):
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 9, 21, 16, 1, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(query_contract, "datetime", Clock)
    assert query_contract.current_query_reference_date() == date(2026, 9, 22)
    assert query_contract.current_query_reference_time().utcoffset().total_seconds() == 28800
