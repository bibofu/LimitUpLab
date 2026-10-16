"""CLI precedence preserves date completeness, time budgets and request sharing."""

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from app.collectors import market_index_collector as indices
from app.collectors import trading_calendar_collector as calendar


END = date(2026, 10, 15)
START = END - timedelta(days=2)
ROWS = [{"date": START + timedelta(days=i), "close": 100 + i} for i in range(3)]


@pytest.fixture(autouse=True)
def offline_providers(monkeypatch):
    for module in (indices, calendar):
        monkeypatch.setattr(module, "HithinkFinanceCollector", Mock(side_effect=AssertionError("offline")))
    for provider in ("_tencent_rows", "_eastmoney_rows", "_sina_rows"):
        monkeypatch.setattr(indices, provider, Mock(side_effect=AssertionError("unexpected fallback")))
    monkeypatch.setattr(calendar.ak, "tool_trade_date_hist_sina", Mock(side_effect=AssertionError("unexpected fallback")))
    monkeypatch.setattr(calendar, "_cache", None)
    monkeypatch.setattr(calendar, "_inflight", None)
    monkeypatch.setattr(calendar, "_inflight_range", None)
    monkeypatch.delenv("LIMITUPLAB_MARKET_INDEX_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("LIMITUPLAB_TRADING_CALENDAR_TIMEOUT_SECONDS", raising=False)


@pytest.mark.parametrize("kind", ["snapshot", "trend"])
def test_index_cli_first_with_existing_date_validation(monkeypatch, kind):
    monkeypatch.setattr(indices, "_hithink_rows", Mock(return_value=ROWS))
    if kind == "snapshot":
        result = indices._collect_market_index("上证指数", "000001.SH", "sh000001", END)
        assert result.close == 102
    else:
        result = indices._collect_market_index_trend(
            name="上证指数", display_symbol="000001.SH", akshare_symbol="sh000001", days=3, end_date=END,
        )
        assert len(result.points) == 3
    assert result.source == "hithink-finance.index.history"
    indices._tencent_rows.assert_not_called()
    indices._eastmoney_rows.assert_not_called()
    indices._sina_rows.assert_not_called()


@pytest.mark.parametrize("rows", [ROWS[:-1], ROWS[-1:], []])
@pytest.mark.parametrize("kind", ["snapshot", "trend"])
def test_stale_or_short_cli_index_falls_back(monkeypatch, rows, kind):
    monkeypatch.setattr(indices, "_hithink_rows", Mock(return_value=rows))
    indices._tencent_rows.return_value = ROWS
    indices._tencent_rows.side_effect = None
    if kind == "snapshot":
        result = indices._collect_market_index("上证指数", "000001.SH", "sh000001", END)
    else:
        result = indices._collect_market_index_trend(
            name="上证指数", display_symbol="000001.SH", akshare_symbol="sh000001", days=3, end_date=END,
        )
    assert result.source == "tencent_index_daily_spot"


def test_cli_index_timeout_keeps_existing_fallback_order(monkeypatch):
    monkeypatch.setattr(indices, "_hithink_rows", Mock(side_effect=TimeoutError("CLI timeout")))
    indices._eastmoney_rows.return_value = ROWS
    indices._eastmoney_rows.side_effect = None
    result = indices._collect_market_index("上证指数", "000001.SH", "sh000001", END)
    assert result.source == "eastmoney_index_daily"
    indices._tencent_rows.assert_called_once()
    indices._sina_rows.assert_not_called()


@pytest.mark.parametrize("budget,configured,expected", [("30", 15.0, 15.0), ("10", 15.0, 5.0), ("2", 15.0, 1.0), ("30", 7.0, 7.0)])
def test_index_cli_receives_history_window_and_bounded_timeout(monkeypatch, budget, configured, expected):
    monkeypatch.setenv("LIMITUPLAB_MARKET_INDEX_TIMEOUT_SECONDS", budget)
    cli = Mock(timeout_seconds=configured)
    cli.collect_index_history.return_value = SimpleNamespace(items=[
        SimpleNamespace(trade_date=row["date"], close=row["close"]) for row in ROWS
    ])
    factory = Mock(return_value=cli)
    monkeypatch.setattr(indices, "HithinkFinanceCollector", factory)
    assert indices._hithink_rows("sh000001", END) == ROWS
    factory.assert_called_once_with()
    assert cli.timeout_seconds == expected
    cli.collect_index_history.assert_called_once_with(
        "000001.SH", start_date=END - timedelta(days=60), end_date=END,
    )


@pytest.mark.parametrize("kind", ["snapshot", "trend"])
def test_three_indices_share_cli_wait_time_by_running_concurrently(monkeypatch, kind):
    barrier = Barrier(3)

    def rows(*_):
        barrier.wait(timeout=3)
        return ROWS

    monkeypatch.setattr(indices, "_hithink_rows", rows)
    result = indices._collect_market_indices(END) if kind == "snapshot" else indices._collect_market_index_trends(3, END).indices
    assert [item.symbol for item in result] == [item[1] for item in indices.INDEX_SPECS]


@pytest.mark.parametrize("configured", [7.0, 15.0])
def test_calendar_prefers_cli_when_requested_window_is_covered(monkeypatch, configured):
    cli = Mock(timeout_seconds=configured)
    cli.collect_trading_calendar.return_value = [END, START, START]
    factory = Mock(return_value=cli)
    monkeypatch.setattr(calendar, "HithinkFinanceCollector", factory)
    assert calendar._fetch_trade_dates(START, END) == (START, END)
    factory.assert_called_once_with()
    assert cli.timeout_seconds == configured
    calendar.ak.tool_trade_date_hist_sina.assert_not_called()


@pytest.mark.parametrize("cli_days", [[START], [END], [], [START, END]])
def test_calendar_uses_sina_for_older_future_or_incomplete_cli_range(monkeypatch, cli_days):
    cli = Mock(timeout_seconds=15.0)
    cli.collect_trading_calendar.return_value = cli_days
    monkeypatch.setattr(calendar, "HithinkFinanceCollector", Mock(return_value=cli))
    older, future = START - timedelta(days=400), END + timedelta(days=20)
    calendar.ak.tool_trade_date_hist_sina.side_effect = None
    calendar.ak.tool_trade_date_hist_sina.return_value = pd.DataFrame({"trade_date": [older, START, END, future]})
    assert calendar._fetch_trade_dates(older, future) == (older, START, END, future)
    calendar.ak.tool_trade_date_hist_sina.assert_called_once()


def test_calendar_timeout_allows_sina_fallback_within_batch_budget(monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_TRADING_CALENDAR_TIMEOUT_SECONDS", "2")
    cli = Mock(timeout_seconds=15.0)
    cli.collect_trading_calendar.side_effect = TimeoutError("CLI timeout")
    factory = Mock(return_value=cli)
    monkeypatch.setattr(calendar, "HithinkFinanceCollector", factory)
    calendar.ak.tool_trade_date_hist_sina.side_effect = None
    calendar.ak.tool_trade_date_hist_sina.return_value = pd.DataFrame({"trade_date": [START, END]})
    assert calendar._fetch_trade_dates(START, END) == (START, END)
    factory.assert_called_once_with()
    assert cli.timeout_seconds == 1.0


def test_calendar_waiter_fetches_its_uncovered_range_after_shared_cli_result(monkeypatch):
    entered, release, waiter_entered = Event(), Event(), Event()
    older = START - timedelta(days=400)

    class ObservedFuture(Future):
        def result(self, timeout=None):
            waiter_entered.set()
            return super().result(timeout=timeout)

    def worker(function, start, end, budget, **kwargs):
        if start == START:
            entered.set()
            assert release.wait(3)
            return (START, END)
        return (older, START, END)

    run = Mock(side_effect=worker)
    monkeypatch.setattr(calendar, "Future", ObservedFuture)
    monkeypatch.setattr(calendar, "run_in_killable_process", run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(calendar.collect_a_share_trade_dates, START, END)
        try:
            assert entered.wait(3)
            second = pool.submit(calendar.collect_a_share_trade_dates, older, END)
            assert waiter_entered.wait(3)
        finally:
            release.set()
        assert first.result(timeout=3) == [START, END]
        assert second.result(timeout=3) == [older, START, END]
    assert run.call_count == 2


def test_calendar_waiter_range_fallback_keeps_original_deadline(monkeypatch):
    clock = [0.0]
    older = START - timedelta(days=400)

    def waited_result(**kwargs):
        clock[0] = 20.0
        calendar._inflight = None
        return (START, END)

    monkeypatch.setattr(calendar, "monotonic", lambda: clock[0])
    monkeypatch.setattr(calendar, "_inflight", Mock(result=Mock(side_effect=waited_result)))
    monkeypatch.setattr(calendar, "_inflight_range", (START, END))
    run = Mock(return_value=(older, START, END))
    monkeypatch.setattr(calendar, "run_in_killable_process", run)
    assert calendar.collect_a_share_trade_dates(older, END) == [older, START, END]
    run.assert_called_once_with(calendar._fetch_trade_dates, older, END, 10.0, timeout_seconds=10.0)


def test_reversed_calendar_range_never_starts_provider(monkeypatch):
    run = Mock()
    monkeypatch.setattr(calendar, "run_in_killable_process", run)
    with pytest.raises(ValueError, match="start date"):
        calendar.collect_a_share_trade_dates(END, START)
    run.assert_not_called()
