"""Provider normalization stays in the child; unavailable dates are not guessed."""

from contextlib import nullcontext
from datetime import date
from unittest.mock import Mock

import pandas as pd
import pytest

from app.collectors import trading_calendar_collector as calendar


@pytest.fixture(autouse=True)
def offline_cli(monkeypatch):
    monkeypatch.setattr(calendar, "HithinkFinanceCollector", Mock(side_effect=RuntimeError("offline CLI")))


def test_child_fetch_normalizes_full_calendar(monkeypatch):
    monkeypatch.setattr(calendar, "without_proxy", nullcontext)
    monkeypatch.setattr(calendar.ak, "tool_trade_date_hist_sina", Mock(return_value=pd.DataFrame({
        "trade_date": [date(2026, 10, 8), "2026/09/30", "2026-09-30"],
    })))
    assert calendar._fetch_trade_dates() == (date(2026, 9, 30), date(2026, 10, 8))


@pytest.mark.parametrize("frame", [None, pd.DataFrame(), pd.DataFrame({"other": [1]})])
def test_child_rejects_missing_calendar(monkeypatch, frame):
    monkeypatch.setattr(calendar, "without_proxy", nullcontext)
    monkeypatch.setattr(calendar.ak, "tool_trade_date_hist_sina", Mock(return_value=frame))
    with pytest.raises(RuntimeError, match="no rows"):
        calendar._fetch_trade_dates()


def test_calendar_snapshot_records_cli_source_when_cli_covers(monkeypatch):
    collector = Mock()
    collector.timeout_seconds = 15.0
    collector.collect_trading_calendar.return_value = [
        date(2026, 9, 30), date(2026, 10, 8),
    ]
    monkeypatch.setattr(
        calendar, "HithinkFinanceCollector", Mock(return_value=collector)
    )
    snapshot = calendar._fetch_trade_calendar(date(2026, 9, 30), date(2026, 10, 8))
    assert snapshot.source == "hithink-finance.market.calendar"
    assert snapshot.coverage_verified is True
    assert snapshot.dates == (date(2026, 9, 30), date(2026, 10, 8))


def test_calendar_snapshot_records_sina_source_and_coverage(monkeypatch):
    monkeypatch.setattr(calendar, "without_proxy", nullcontext)
    monkeypatch.setattr(calendar.ak, "tool_trade_date_hist_sina", Mock(return_value=pd.DataFrame({
        "trade_date": ["2026-09-30", "2026-10-08", "2026-10-09"],
    })))
    snapshot = calendar._fetch_trade_calendar(date(2026, 9, 30), date(2026, 10, 8))
    assert snapshot.source == "akshare.tool_trade_date_hist_sina"
    assert snapshot.coverage_verified is True
    assert snapshot.warnings == ()


def test_calendar_snapshot_flags_incomplete_coverage(monkeypatch):
    monkeypatch.setattr(calendar, "without_proxy", nullcontext)
    monkeypatch.setattr(calendar.ak, "tool_trade_date_hist_sina", Mock(return_value=pd.DataFrame({
        "trade_date": ["2026-09-30"],
    })))
    snapshot = calendar._fetch_trade_calendar(date(2026, 9, 30), date(2026, 10, 8))
    assert snapshot.coverage_verified is False
    assert "未覆盖" in snapshot.warnings[0]
