"""Relay readiness excludes known out-of-universe gaps, not provider failures."""

from datetime import date, time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.collectors import preferred_limit_up_collector as preferred
from app.models import LimitUpEvent


DAY = date(2026, 10, 20)


def fact(symbol, name="普通样本"):
    return SimpleNamespace(
        symbol=symbol, name=name, board_height=1, limit_up_reason=None,
        seal_amount=None, break_count=None, amount=None, turnover_rate=None,
    )


def snapshot(*items):
    return SimpleNamespace(trade_date=DAY, items=list(items))


def event(symbol, *, name="普通样本", closed=True):
    return LimitUpEvent(
        symbol=symbol, name=name, trade_date=DAY,
        first_limit_time=time(9, 40), last_limit_time=time(14, 30),
        seal_count=3, break_count=2, closed_limit=closed, board_height=1,
        amount=100_000_000, turnover_rate=5, industry="电力", concept="",
        next_open_pct=0, next_high_pct=0, next_close_pct=0,
        three_day_return_pct=0, five_day_return_pct=0, continued_next_day=False,
    )


@pytest.fixture
def sources(monkeypatch):
    ths = SimpleNamespace(
        collect_all_limit_up_pool=MagicMock(return_value=snapshot()),
        collect_all_limit_break_pool=MagicMock(return_value=snapshot()),
    )
    closed, failed = MagicMock(return_value=[]), MagicMock(return_value=[])
    monkeypatch.setattr(preferred, "HithinkFinanceCollector", lambda: ths)
    monkeypatch.setattr(preferred, "_collect_closed_limit_up_events", closed)
    monkeypatch.setattr(preferred, "_collect_failed_limit_up_events", failed)
    return ths, closed, failed


def collect():
    return preferred.collect_limit_up_events("20261020")


def test_missing_two_st_rows_preserve_raw_failure_but_allow_relay(sources):
    ths, closed, _ = sources
    ths.collect_all_limit_up_pool.return_value = snapshot(
        fact("600001"), fact("000639", "ST西王"), fact("600302", "ST标准"),
    )
    closed.return_value = [event("600001")]
    result = collect()
    assert result.status == "partial" and result.source_errors
    assert result.relay_inputs_complete is True
    assert {row.symbol for row in result.payload} == {"600001"}
    assert all(symbol in " ".join(result.relay_excluded_gaps)
               for symbol in ("000639", "600302"))


@pytest.mark.parametrize("symbol", ["430001", "830001", "920001"])
def test_missing_beijing_row_does_not_block_relay(sources, symbol):
    ths, closed, _ = sources
    ths.collect_all_limit_up_pool.return_value = snapshot(
        fact("600001"), fact(symbol, "北交样本"),
    )
    closed.return_value = [event("600001")]
    result = collect()
    assert result.status == "partial"
    assert result.relay_inputs_complete is True
    assert symbol in " ".join(result.relay_excluded_gaps)


def test_mixed_st_and_ordinary_gaps_still_block_relay(sources):
    ths, closed, _ = sources
    ths.collect_all_limit_up_pool.return_value = snapshot(
        fact("600001"), fact("600002"), fact("000639", "ST西王"),
    )
    closed.return_value = [event("600001")]
    result = collect()
    assert result.status == "partial"
    assert result.relay_inputs_complete is False
    assert "600002" in " ".join(result.source_errors)
    assert "600002" not in " ".join(result.relay_excluded_gaps)


@pytest.mark.parametrize("symbol,name", [
    ("000639", "*ST样本"), ("600302", "退市样本"), ("920001", "北交样本"),
])
def test_failed_pool_uses_same_scope_for_missing_rows(sources, symbol, name):
    ths, _, failed = sources
    ths.collect_all_limit_break_pool.return_value = snapshot(
        fact("600001"), fact(symbol, name),
    )
    failed.return_value = [event("600001", closed=False)]
    result = collect()
    assert result.status == "partial" and result.relay_inputs_complete is True
    assert symbol in " ".join(result.relay_excluded_gaps)
    assert [(row.symbol, row.closed_limit) for row in result.payload] == [
        ("600001", False),
    ]


@pytest.mark.parametrize("pool", ["closed", "failed"])
def test_whole_supplement_timeout_is_not_an_excluded_symbol_gap(sources, pool):
    ths, closed, failed = sources
    remote = (ths.collect_all_limit_up_pool if pool == "closed"
              else ths.collect_all_limit_break_pool)
    remote.return_value = snapshot(fact("600001"), fact("000639", "ST西王"))
    (closed if pool == "closed" else failed).side_effect = TimeoutError("EM timeout")
    result = collect()
    assert result.relay_inputs_complete is False
    assert "EM timeout" in " ".join(result.source_errors)


def test_complete_st_and_beijing_events_are_preserved(sources):
    ths, closed, _ = sources
    rows = [fact("000639", "ST西王"), fact("920001", "北交样本")]
    ths.collect_all_limit_up_pool.return_value = snapshot(*rows)
    closed.return_value = [event(row.symbol, name=row.name) for row in rows]
    result = collect()
    assert result.status == "ok" and result.relay_inputs_complete is True
    assert {row.symbol for row in result.payload} == {"000639", "920001"}
    assert result.relay_excluded_gaps == ()


def test_complete_fallback_remains_ready(sources):
    ths, closed, _ = sources
    ths.collect_all_limit_up_pool.side_effect = TimeoutError("THS timeout")
    closed.return_value = [event("600001")]
    result = collect()
    assert result.status == "ok" and result.relay_inputs_complete is True
    assert result.pool_sources["closed_limit_pool"] == "akshare-eastmoney.fallback"
