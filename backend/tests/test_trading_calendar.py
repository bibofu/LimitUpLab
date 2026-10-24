"""Trading-calendar resolution and its Agent tool surface."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.agents.tools import AgentToolRegistry
from app.collectors.trading_calendar_collector import TradingCalendarSnapshot
from app.models import MarketCalendarFacts
from app.services.trading_calendar import build_market_calendar_facts


SHANGHAI = ZoneInfo("Asia/Shanghai")
THU = date(2026, 10, 22)
FRI = date(2026, 10, 23)
SAT = date(2026, 10, 24)
MON = date(2026, 10, 26)
TUE = date(2026, 10, 27)


def snapshot(
    dates: list[date],
    *,
    coverage: bool = True,
    source: str = "test-calendar",
    warnings: tuple[str, ...] = (),
) -> TradingCalendarSnapshot:
    return TradingCalendarSnapshot(
        dates=tuple(dates),
        source=source,
        query_start=date(2026, 8, 1),
        query_end=date(2026, 11, 5),
        coverage_verified=coverage,
        warnings=warnings,
    )


def facts_for(
    now: datetime,
    dates: list[date],
    *,
    coverage: bool = True,
    local_data_through: date | None = None,
) -> MarketCalendarFacts:
    return build_market_calendar_facts(
        now=now,
        loader=lambda _start, _end: snapshot(dates, coverage=coverage),
        local_data_through=local_data_through,
    )


def at(hour: int, minute: int = 0, day: date = MON) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SHANGHAI)


def test_trading_day_before_close_uses_previous_session():
    facts = facts_for(at(14, 59), [THU, FRI, MON, TUE])

    assert facts.session_status == "open"
    assert facts.latest_closed_trade_date == FRI
    assert facts.previous_trade_date == THU
    assert facts.next_trade_date == MON


def test_pre_open_still_uses_previous_session():
    facts = facts_for(at(9, 0), [THU, FRI, MON, TUE])

    assert facts.session_status == "pre_open"
    assert facts.latest_closed_trade_date == FRI
    assert facts.next_trade_date == MON


def test_close_time_switches_latest_session_to_today():
    facts = facts_for(at(15, 0), [THU, FRI, MON, TUE])

    assert facts.session_status == "closed"
    assert facts.latest_closed_trade_date == MON
    assert facts.previous_trade_date == FRI
    assert facts.next_trade_date == TUE
    assert facts.close_time == "15:00"


def test_weekend_is_explicit_non_trading_day():
    facts = facts_for(at(10, 0, day=SAT), [THU, FRI, MON, TUE])

    assert facts.session_status == "non_trading_day"
    assert facts.today == SAT
    assert facts.latest_closed_trade_date == FRI
    assert facts.next_trade_date == MON


def test_holiday_is_explicit_non_trading_day():
    facts = facts_for(
        datetime(2026, 10, 1, 10, 0, tzinfo=SHANGHAI),
        [date(2026, 9, 30), date(2026, 10, 9)],
    )

    assert facts.session_status == "non_trading_day"
    assert facts.latest_closed_trade_date == date(2026, 9, 30)
    assert facts.next_trade_date == date(2026, 10, 9)


def test_unverified_coverage_does_not_claim_today_status():
    facts = facts_for(at(10, 0), [THU, FRI], coverage=False)

    assert facts.session_status == "unverified"
    assert facts.calendar_verified is False
    assert facts.latest_closed_trade_date == FRI
    assert facts.next_trade_date is None
    assert any("覆盖不足" in item for item in facts.data_missing)


def test_today_membership_verifies_status_without_future_coverage():
    facts = facts_for(at(15, 1), [FRI, MON], coverage=False)

    assert facts.calendar_verified is True
    assert facts.session_status == "closed"
    assert facts.latest_closed_trade_date == MON
    assert facts.next_trade_date is None


def test_loader_failure_returns_unverified_without_guessing():
    def failing_loader(_start: date, _end: date) -> TradingCalendarSnapshot:
        raise RuntimeError("calendar offline")

    facts = build_market_calendar_facts(now=at(10, 0), loader=failing_loader)

    assert facts.session_status == "unverified"
    assert facts.calendar_verified is False
    assert facts.latest_closed_trade_date is None
    assert facts.calendar_source == "unavailable"
    assert any("交易日历不可用" in item for item in facts.data_missing)


def test_local_data_coverage_is_reported_separately():
    facts = facts_for(at(15, 1), [FRI, MON], local_data_through=FRI)

    assert facts.latest_closed_trade_date == MON
    assert facts.local_data_through == FRI


def test_tool_returns_calendar_facts(monkeypatch):
    facts = facts_for(at(15, 1), [FRI, MON], local_data_through=FRI)
    monkeypatch.setattr(
        "app.agents.tools.build_market_calendar_facts",
        lambda **_kwargs: facts,
    )
    registry = AgentToolRegistry.__new__(AgentToolRegistry)
    registry.events = []

    result = registry.trading_calendar()

    assert result.name == "trading_calendar"
    assert result.output is facts
    assert result.input == {}
    assert result.trace_output["latest_closed_trade_date"] == MON.isoformat()
    assert "最新已收盘交易日" in result.summary
    assert "本地涨停事件数据截至" in result.summary


@pytest.mark.parametrize("status", ["pre_open", "open", "closed", "non_trading_day", "unverified"])
def test_facts_model_documents_every_session_status(status: str):
    assert status in MarketCalendarFacts.model_fields["session_status"].annotation.__args__
