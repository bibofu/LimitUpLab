"""Provider normalization stays in the child; unavailable dates are not guessed."""

from contextlib import nullcontext
from datetime import date
from unittest.mock import Mock

import pandas as pd
import pytest

from app.collectors import trading_calendar_collector as calendar


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
