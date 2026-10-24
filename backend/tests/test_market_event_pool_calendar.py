"""Market event pool availability now follows the trading calendar."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.agents.tools import AgentToolRegistry
from app.models import MarketCalendarFacts
from app.services.sample_data import SAMPLE_EVENTS


SHANGHAI = ZoneInfo("Asia/Shanghai")
BASE = date(2026, 5, 15)
SATURDAY = date(2026, 5, 16)


def calendar_facts(
    *,
    today: date,
    status: str,
    verified: bool = True,
    latest: date | None = BASE,
) -> MarketCalendarFacts:
    return MarketCalendarFacts(
        as_of=datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI),
        today=today,
        latest_closed_trade_date=latest,
        previous_trade_date=None,
        next_trade_date=None,
        session_status=status,
        close_time="15:00",
        calendar_source="test-calendar",
        calendar_verified=verified,
        calendar_coverage_start=latest,
        calendar_coverage_end=today,
        local_data_through=BASE,
        data_missing=[] if verified else ["calendar gap"],
    )


def registry() -> AgentToolRegistry:
    result = AgentToolRegistry.__new__(AgentToolRegistry)
    result.events = SAMPLE_EVENTS
    return result


def event_pool(monkeypatch, now: datetime, facts: MarketCalendarFacts, target: date):
    monkeypatch.setattr(
        "app.agents.tools.current_query_reference_time", lambda: now
    )
    monkeypatch.setattr(
        "app.agents.tools.build_market_calendar_facts", lambda **_kwargs: facts
    )
    return registry().market_event_pool(event_type="limit_up", trade_date=target)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SHANGHAI)


def test_non_trading_day_returns_explicit_availability(monkeypatch):
    result = event_pool(
        monkeypatch,
        at(SATURDAY, 10),
        calendar_facts(today=SATURDAY, status="non_trading_day"),
        SATURDAY,
    )

    payload = result.trace_output
    assert payload["availability"] == "non_trading_day"
    assert payload["items"] == []
    assert payload["calendar"]["verified"] is True
    assert "non_trading_day" in payload["data_missing"]
    assert "不是交易日" in result.summary


def test_open_session_is_not_closed(monkeypatch):
    result = event_pool(
        monkeypatch,
        at(BASE, 14),
        calendar_facts(today=BASE, status="open"),
        BASE,
    )

    assert result.trace_output["availability"] == "market_not_closed"
    assert result.trace_output["calendar"]["session_status"] == "open"


def test_closed_session_waits_for_data_settlement(monkeypatch):
    result = event_pool(
        monkeypatch,
        at(BASE, 15, 10),
        calendar_facts(today=BASE, status="closed"),
        BASE,
    )

    assert result.trace_output["availability"] == "closing_data_pending"
    assert "15:30" not in result.summary


def test_closed_session_after_settlement_queries_events(monkeypatch):
    result = event_pool(
        monkeypatch,
        at(BASE, 15, 31),
        calendar_facts(today=BASE, status="closed"),
        BASE,
    )

    payload = result.trace_output
    assert "availability" not in payload
    assert payload["matched_count"] > 0
    assert payload["data_missing"] == []
    assert payload["calendar"]["session_status"] == "closed"


def test_unverified_calendar_falls_back_with_explicit_warning(monkeypatch):
    result = event_pool(
        monkeypatch,
        at(BASE, 14),
        calendar_facts(today=BASE, status="unverified", verified=False, latest=None),
        BASE,
    )

    payload = result.trace_output
    assert payload["availability"] == "market_not_closed"
    assert any("未确认今天" in item for item in payload["data_missing"])


def test_future_date_is_rejected_without_calendar_fetch(monkeypatch):
    monkeypatch.setattr(
        "app.agents.tools.current_query_reference_time", lambda: at(BASE, 10)
    )

    def unexpected(**_kwargs):
        raise AssertionError("calendar should not be queried for a future date")

    monkeypatch.setattr("app.agents.tools.build_market_calendar_facts", unexpected)
    result = registry().market_event_pool(
        event_type="limit_up", trade_date=date(2026, 5, 18)
    )

    assert result.trace_output["availability"] == "requested_date_in_future"
    assert result.trace_output["calendar"] is None


def test_historical_date_does_not_query_calendar(monkeypatch):
    monkeypatch.setattr(
        "app.agents.tools.current_query_reference_time", lambda: at(date(2026, 5, 18), 10)
    )

    def unexpected(**_kwargs):
        raise AssertionError("calendar should not be queried for a historical date")

    monkeypatch.setattr("app.agents.tools.build_market_calendar_facts", unexpected)
    result = registry().market_event_pool(
        event_type="limit_up", trade_date=BASE
    )

    assert result.trace_output["matched_count"] > 0
    assert result.trace_output["calendar"] is None
