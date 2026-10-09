"""Dashboard provider calls have one bounded, isolated request lifecycle."""

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timedelta
import multiprocessing
from threading import Event
import time
from unittest.mock import Mock

import pytest

from app.collectors import market_index_collector as indices
from app.collectors import trading_calendar_collector as calendar
from app.collectors.process_timeout import run_in_killable_process
from app.services import promotion_calendar


START, END = date(2026, 9, 30), date(2026, 10, 8)


def _blocked_provider(*args):
    time.sleep(60)


@pytest.fixture(autouse=True)
def isolated_collectors(monkeypatch):
    indices._cache.clear()
    indices._trend_cache.clear()
    indices._inflight.clear()
    monkeypatch.setattr(calendar, "_cache", None)
    monkeypatch.setattr(calendar, "_inflight", None)
    monkeypatch.delenv("LIMITUPLAB_MARKET_INDEX_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("LIMITUPLAB_TRADING_CALENDAR_TIMEOUT_SECONDS", raising=False)
    # Any accidental provider access fails offline, including parent proxy locks.
    for module in (indices, calendar):
        monkeypatch.setattr(module, "without_proxy", Mock(side_effect=AssertionError("parent proxy lock")))
        monkeypatch.setattr(module, "run_in_killable_process", Mock(side_effect=AssertionError("supply offline worker")))
    yield
    indices._cache.clear()
    indices._trend_cache.clear()
    indices._inflight.clear()


def test_index_snapshot_and_trend_each_have_one_whole_batch_deadline(monkeypatch):
    run = Mock(side_effect=[[], "offline-trend"])
    monkeypatch.setattr(indices, "run_in_killable_process", run)
    assert indices.collect_market_indices(END) == []
    assert indices.collect_market_index_trends(days=30, end_date=END) == "offline-trend"
    assert run.call_count == 2
    assert run.call_args_list[0].args == (indices._collect_market_indices, END)
    assert run.call_args_list[1].args == (indices._collect_market_index_trends, 20, END)
    assert all(call.kwargs == {"timeout_seconds": 10.0} for call in run.call_args_list)


def test_calendar_full_history_uses_one_worker_and_filters_requested_range(monkeypatch):
    run = Mock(return_value=(START, END, END + timedelta(days=1)))
    monkeypatch.setattr(calendar, "run_in_killable_process", run)
    assert calendar.collect_a_share_trade_dates(START, END) == [START, END]
    assert calendar.collect_a_share_trade_dates(END, END) == [END]
    run.assert_called_once_with(calendar._fetch_trade_dates, timeout_seconds=8.0)


@pytest.mark.parametrize("kind", ["indices", "calendar"])
def test_public_collector_kills_blocked_worker_with_configured_budget(monkeypatch, kind):
    module = indices if kind == "indices" else calendar
    worker_name = "_collect_market_indices" if kind == "indices" else "_fetch_trade_dates"
    config_name = "LIMITUPLAB_MARKET_INDEX_TIMEOUT_SECONDS" if kind == "indices" else "LIMITUPLAB_TRADING_CALENDAR_TIMEOUT_SECONDS"
    monkeypatch.setattr(module, worker_name, _blocked_provider)
    monkeypatch.setattr(module, "run_in_killable_process", run_in_killable_process)
    monkeypatch.setenv(config_name, "0.2")
    before = {child.pid for child in multiprocessing.active_children()}
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="0.2 seconds"):
        if kind == "indices":
            indices.collect_market_indices(END)
        else:
            calendar.collect_a_share_trade_dates(START, END)
    assert time.monotonic() - started < 6
    assert {child.pid for child in multiprocessing.active_children()} == before


@pytest.mark.parametrize("invalid", ["invalid", "0", "-1", "nan", "inf", "-inf"])
@pytest.mark.parametrize("kind", ["indices", "calendar"])
def test_invalid_deadline_never_starts_a_worker(monkeypatch, invalid, kind):
    module = indices if kind == "indices" else calendar
    name = "LIMITUPLAB_MARKET_INDEX_TIMEOUT_SECONDS" if kind == "indices" else "LIMITUPLAB_TRADING_CALENDAR_TIMEOUT_SECONDS"
    monkeypatch.setenv(name, invalid)
    with pytest.raises(ValueError):
        if kind == "indices":
            indices.collect_market_indices(END)
        else:
            calendar.collect_a_share_trade_dates(START, END)
    module.run_in_killable_process.assert_not_called()


@pytest.mark.parametrize("kind", ["indices", "calendar"])
def test_timeout_is_not_cached_and_next_request_recovers(monkeypatch, kind):
    module = indices if kind == "indices" else calendar
    result = [] if kind == "indices" else (START, END)
    run = Mock(side_effect=[TimeoutError("provider deadline"), result])
    monkeypatch.setattr(module, "run_in_killable_process", run)
    call = (lambda: indices.collect_market_indices(END)) if kind == "indices" else (lambda: calendar.collect_a_share_trade_dates(START, END))
    with pytest.raises(TimeoutError, match="provider deadline"):
        call()
    assert call() == list(result)
    assert run.call_count == 2


def test_expired_index_success_is_not_returned_after_failure(monkeypatch):
    indices._cache[END] = (datetime.now() - timedelta(hours=1), ["stale"])
    run = Mock(side_effect=TimeoutError("offline"))
    monkeypatch.setattr(indices, "run_in_killable_process", run)
    with pytest.raises(TimeoutError):
        indices.collect_market_indices(END)
    assert END not in indices._cache


def test_calendar_expiration_and_missing_coverage_allow_refetch(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(calendar, "monotonic", lambda: clock[0])
    run = Mock(side_effect=[(START,), (START, END), TimeoutError("offline"), (START, END)])
    monkeypatch.setattr(calendar, "run_in_killable_process", run)
    assert calendar.collect_a_share_trade_dates(START, END) == [START]
    assert calendar.collect_a_share_trade_dates(START, END) == [START, END]
    clock[0] = 3600
    with pytest.raises(TimeoutError):
        calendar.collect_a_share_trade_dates(START, END)
    assert calendar.collect_a_share_trade_dates(START, END) == [START, END]
    assert run.call_count == 4


@pytest.mark.parametrize("kind", ["indices", "calendar"])
def test_concurrent_requests_share_worker(monkeypatch, kind):
    entered, release, waiter_entered = Event(), Event(), Event()
    module = indices if kind == "indices" else calendar

    class ObservedFuture(Future):
        def result(self, timeout=None):
            assert timeout is not None
            waiter_entered.set()
            return super().result(timeout=timeout)

    def worker(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return [] if kind == "indices" else (START, END)

    run = Mock(side_effect=worker)
    monkeypatch.setattr(module, "Future", ObservedFuture)
    monkeypatch.setattr(module, "run_in_killable_process", run)
    first_call = (lambda: indices.collect_market_indices(END)) if kind == "indices" else (lambda: calendar.collect_a_share_trade_dates(START, END))
    # Different calendar ranges still share the same full-history fetch.
    second_call = first_call if kind == "indices" else (lambda: calendar.collect_a_share_trade_dates(END, END))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(first_call)
        try:
            assert entered.wait(3)
            second = pool.submit(second_call)
            assert waiter_entered.wait(3)
        finally:
            release.set()
        assert first.result(timeout=3) == ([] if kind == "indices" else [START, END])
        assert second.result(timeout=3) == ([] if kind == "indices" else [END])
    assert run.call_count == 1


def test_index_capacity_rejects_new_keys_without_starting_more_workers():
    indices._inflight[("snapshot", START)] = Future()
    indices._inflight[("trend", (5, START))] = Future()
    with pytest.raises(RuntimeError, match="busy"):
        indices.collect_market_indices(END)
    indices.run_in_killable_process.assert_not_called()


def test_index_cache_capacity_is_bounded(monkeypatch):
    monkeypatch.setattr(indices, "_MAX_CACHE_ENTRIES", 2)
    monkeypatch.setattr(indices, "run_in_killable_process", Mock(return_value=[]))
    for offset in range(3):
        indices.collect_market_indices(END + timedelta(days=offset))
    assert len(indices._cache) == 2
    assert END not in indices._cache


def test_promotion_waiter_returns_explicit_missing_after_deadline(monkeypatch):
    key = (START, END)
    pending = Mock()
    pending.result.side_effect = TimeoutError("private provider detail")
    monkeypatch.setattr(promotion_calendar, "_cache", {})
    monkeypatch.setattr(promotion_calendar, "_inflight", {key: pending})
    result = promotion_calendar.load_promotion_calendar(*key)
    assert not result.trade_dates
    assert "超时" in result.warnings[0]
    assert "private" not in result.warnings[0]
    pending.result.assert_called_once_with(timeout=13.0)
    assert promotion_calendar._inflight[key] is pending
