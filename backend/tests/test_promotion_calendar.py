"""Promotion calendars use provider sessions, never a weekday-gap heuristic."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta
from threading import Event
from unittest.mock import Mock

import pytest

from app.services import promotion_calendar as calendar


START = date(2026, 9, 30)
END = date(2026, 10, 8)


@pytest.fixture(autouse=True)
def isolate_cache_and_network(monkeypatch):
    with calendar._lock:
        calendar._cache.clear()
        calendar._inflight.clear()
    collector = Mock(side_effect=AssertionError("test must supply an offline calendar"))
    monkeypatch.setattr(calendar, "collect_a_share_trade_dates", collector)
    yield collector
    with calendar._lock:
        calendar._cache.clear()
        calendar._inflight.clear()


def test_holiday_pair_uses_calendar_and_result_is_immutable(isolate_cache_and_network):
    collector = isolate_cache_and_network
    collector.side_effect = None
    collector.return_value = [END, START, START]

    result = calendar.load_promotion_calendar(START, END)

    assert result == calendar.PromotionCalendar((START, END))
    assert calendar.adjacent_trade_date_pairs(result.trade_dates) == {(START, END)}
    collector.assert_called_once_with(START, END)
    with pytest.raises(FrozenInstanceError):
        result.trade_dates = ()


def test_missing_observed_workday_does_not_join_nonadjacent_sessions(isolate_cache_and_network):
    monday, tuesday, wednesday = date(2026, 9, 28), date(2026, 9, 29), START
    collector = isolate_cache_and_network
    collector.side_effect = None
    collector.return_value = [monday, tuesday, wednesday]

    result = calendar.load_promotion_calendar(monday, wednesday)
    pairs = calendar.adjacent_trade_date_pairs(result.trade_dates)

    assert pairs == {(monday, tuesday), (tuesday, wednesday)}
    assert (monday, wednesday) not in pairs


@pytest.mark.parametrize("days", [[], [START], [END]])
def test_missing_calendar_endpoint_has_no_guessed_pairs(isolate_cache_and_network, days):
    collector = isolate_cache_and_network
    collector.side_effect = None
    collector.return_value = days

    result = calendar.load_promotion_calendar(START, END)

    assert result.trade_dates == ()
    assert "未覆盖" in result.warnings[0]
    assert calendar.adjacent_trade_date_pairs(result.trade_dates) == set()


@pytest.mark.parametrize("failed_result", [RuntimeError("private provider detail"), [], [START]])
def test_failed_calendar_is_not_cached_and_next_request_recovers(isolate_cache_and_network, failed_result):
    collector = isolate_cache_and_network
    collector.side_effect = [failed_result, [START, END]]

    failure = calendar.load_promotion_calendar(START, END)
    recovered = calendar.load_promotion_calendar(START, END)

    assert failure.trade_dates == () and failure.warnings
    assert "private provider detail" not in str(failure.warnings)
    assert recovered == calendar.PromotionCalendar((START, END))
    assert collector.call_count == 2


def test_success_is_cached_until_one_hour_after_fetch(monkeypatch, isolate_cache_and_network):
    clock = [100.0]
    monkeypatch.setattr(calendar, "monotonic", lambda: clock[0])
    collector = isolate_cache_and_network
    collector.side_effect = None
    collector.return_value = [START, END]
    first = calendar.load_promotion_calendar(START, END)
    clock[0] += 3599
    assert calendar.load_promotion_calendar(START, END) is first
    assert collector.call_count == 1

    clock[0] += 1
    assert calendar.load_promotion_calendar(START, END) == first
    assert collector.call_count == 2


def test_expired_success_is_not_used_when_calendar_refresh_fails(monkeypatch, isolate_cache_and_network):
    clock = [0.0]
    monkeypatch.setattr(calendar, "monotonic", lambda: clock[0])
    collector = isolate_cache_and_network
    collector.side_effect = [[START, END], RuntimeError("offline"), [START, END]]
    assert calendar.load_promotion_calendar(START, END).trade_dates
    clock[0] = 3600
    assert not calendar.load_promotion_calendar(START, END).trade_dates
    assert calendar.load_promotion_calendar(START, END).trade_dates == (START, END)
    assert collector.call_count == 3


def test_cache_has_bounded_capacity(monkeypatch, isolate_cache_and_network):
    monkeypatch.setattr(calendar, "_MAX_CACHE_ENTRIES", 2)
    collector = isolate_cache_and_network
    collector.side_effect = lambda start, end: [start, end]
    for offset in range(3):
        calendar.load_promotion_calendar(START + timedelta(days=offset), END + timedelta(days=offset))
    calendar.load_promotion_calendar(START, END)
    assert collector.call_count == 4


def test_concurrent_identical_requests_share_fetch_without_blocking_other_ranges(isolate_cache_and_network):
    entered, release, second_started = Event(), Event(), Event()
    other_start, other_end = date(2026, 9, 28), date(2026, 9, 29)

    def collect(start, end):
        if (start, end) == (START, END):
            entered.set()
            assert release.wait(3)
        return [start, end]

    def second_request():
        second_started.set()
        return calendar.load_promotion_calendar(START, END)

    collector = isolate_cache_and_network
    collector.side_effect = collect
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(calendar.load_promotion_calendar, START, END)
        try:
            assert entered.wait(3)
            second = pool.submit(second_request)
            assert second_started.wait(3)
            other = pool.submit(calendar.load_promotion_calendar, other_start, other_end)
            assert other.result(timeout=3) == calendar.PromotionCalendar((other_start, other_end))
        finally:
            release.set()
        assert first.result(timeout=3) == second.result(timeout=3) == calendar.PromotionCalendar((START, END))
    assert collector.call_count == 2


@pytest.mark.parametrize("invalid", ["2026-09-30", None, datetime(2026, 9, 30)])
def test_invalid_provider_date_returns_warning(isolate_cache_and_network, invalid):
    collector = isolate_cache_and_network
    collector.side_effect = None
    collector.return_value = [invalid, END]
    result = calendar.load_promotion_calendar(START, END)
    assert result.trade_dates == () and result.warnings


def test_single_day_and_reversed_range_do_not_request_network(isolate_cache_and_network):
    assert calendar.load_promotion_calendar(START, START) == calendar.PromotionCalendar((START,))
    assert calendar.adjacent_trade_date_pairs([START]) == set()
    invalid = calendar.load_promotion_calendar(END, START)
    assert invalid.trade_dates == () and invalid.warnings
    isolate_cache_and_network.assert_not_called()
