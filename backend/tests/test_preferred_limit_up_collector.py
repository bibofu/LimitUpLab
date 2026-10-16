from datetime import date, time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.collectors import akshare_limit_up_collector as ak_pool
from app.collectors import preferred_limit_up_collector as preferred
from app.models import LimitUpEvent


DAY = date(2026, 10, 15)


def event(symbol="600001", *, closed=True):
    return LimitUpEvent(
        symbol=symbol, name="东方财富名称", trade_date=DAY,
        first_limit_time=time(9, 40), last_limit_time=time(14, 30),
        seal_count=3, break_count=2, closed_limit=closed, board_height=1,
        amount=100_000_000, turnover_rate=5, industry="电力", concept="",
        next_open_pct=0, next_high_pct=0, next_close_pct=0,
        three_day_return_pct=0, five_day_return_pct=0, continued_next_day=False,
    )


def item(symbol="600001", **updates):
    fields = dict(
        symbol=symbol, name="同花顺名称", board_height=3,
        limit_up_reason="电力 / 绿电", seal_amount=99_000_000_000,
        break_count=None, amount=None, turnover_rate=None,
    )
    return SimpleNamespace(**(fields | updates))


def snapshot(*items, trade_date=DAY):
    return SimpleNamespace(trade_date=trade_date, items=list(items))


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
    return preferred.collect_limit_up_events("20261015")


def test_ths_membership_height_reason_and_name_override_supplement(sources):
    ths, closed, failed = sources
    ths.collect_all_limit_up_pool.return_value = snapshot(item())
    ths.collect_all_limit_break_pool.return_value = snapshot(item("600003"))
    closed.return_value = [event(), event("600002")]
    failed.return_value = [event("600003", closed=False), event("600004", closed=False)]
    result = collect()
    assert result.status == "ok"
    assert {row.symbol for row in result.payload} == {"600001", "600003"}
    row = next(row for row in result.payload if row.closed_limit)
    assert (row.name, row.board_height, row.concept) == ("同花顺名称", 3, "电力+绿电")
    assert row.amount == 100_000_000  # Seal-order money is not traded turnover.
    assert row.first_limit_time == time(9, 40)
    assert result.reason_enriched_count == 1
    closed.assert_called_once_with(DAY, "20261015", strict=True)


@pytest.mark.parametrize("values, expected", [
    ((0, 0.0, 0.0), (0, 0.0, 0.0)),
    ((4, 200_000_000, 8.5), (4, 200_000_000, 8.5)),
    ((None, None, None), (2, 100_000_000, 5.0)),
])
def test_break_pool_prefers_real_ths_values_including_zero(sources, values, expected):
    ths, _, failed = sources
    ths.collect_all_limit_break_pool.return_value = snapshot(item(
        break_count=values[0], amount=values[1], turnover_rate=values[2],
    ))
    failed.return_value = [event(closed=False)]
    result = collect()
    row, = result.payload
    assert (row.break_count, row.amount, row.turnover_rate) == expected
    assert row.board_height == 1 and not row.closed_limit
    assert row.seal_count == max(expected[0], 1)
    assert result.status == "ok"


@pytest.mark.parametrize("field, value", [
    ("break_count", 1.5), ("break_count", -1),
    ("amount", float("nan")), ("turnover_rate", float("inf")),
])
def test_invalid_ths_scoring_value_is_missing_not_silently_replaced(sources, field, value):
    ths, _, failed = sources
    ths.collect_all_limit_break_pool.return_value = snapshot(item(**{field: value}))
    failed.return_value = [event(closed=False)]
    result = collect()
    assert result.status == "partial"
    assert result.payload == []
    assert f"invalid THS {field}" in result.source_errors[0]


@pytest.mark.parametrize("closed_fails", [True, False])
def test_individual_pool_failure_falls_back_without_partial_status(sources, closed_fails):
    ths, closed, failed = sources
    remote = ths.collect_all_limit_up_pool if closed_fails else ths.collect_all_limit_break_pool
    remote.side_effect = TimeoutError("THS timeout")
    fallback = closed if closed_fails else failed
    fallback.return_value = [event(closed=closed_fails)]
    result = collect()
    assert result.status == "ok" and result.data_fresh
    assert not result.source_errors
    assert "THS timeout" in result.source_warnings[0]
    pool = "closed_limit_pool" if closed_fails else "failed_limit_pool"
    assert result.pool_sources[pool] == "akshare-eastmoney.fallback"
    assert result.payload[0].closed_limit == closed_fails


def test_successful_empty_ths_pools_are_authoritative(sources):
    _, closed, failed = sources
    closed.return_value = [event()]
    result = collect()
    assert result.status == "empty" and result.data_fresh
    assert result.payload == []
    closed.assert_not_called()
    failed.assert_not_called()


def test_missing_closed_supplement_is_partial_and_never_reclassified_failed(sources):
    ths, _, failed = sources
    ths.collect_all_limit_up_pool.return_value = snapshot(item())
    ths.collect_all_limit_break_pool.side_effect = RuntimeError("failed THS pool")
    failed.return_value = [event(closed=False), event("600002", closed=False)]
    result = collect()
    assert result.status == "partial"
    assert [row.symbol for row in result.payload] == ["600002"]
    assert "missing required Eastmoney fields" in result.source_errors[0]
    assert "600001" in result.source_errors[0]


def test_both_providers_unavailable_is_error(sources):
    ths, closed, failed = sources
    for source in (ths.collect_all_limit_up_pool, ths.collect_all_limit_break_pool, closed, failed):
        source.side_effect = RuntimeError("offline")
    result = collect()
    assert result.status == "error" and not result.data_fresh
    assert result.payload == []
    assert len(result.source_errors) == 2
    assert set(result.pool_sources.values()) == {"unavailable"}


def test_supplement_failure_is_explicit_and_preserves_other_pool(sources):
    ths, closed, failed = sources
    ths.collect_all_limit_up_pool.return_value = snapshot(item())
    ths.collect_all_limit_break_pool.return_value = snapshot(item("600002"))
    closed.side_effect = TimeoutError("EM timeout")
    failed.return_value = [event("600002", closed=False)]
    result = collect()
    assert result.status == "partial"
    assert [row.symbol for row in result.payload] == ["600002"]
    assert "required field supplement failed: EM timeout" in result.source_errors[0]


def test_date_mismatch_uses_fallback(sources):
    ths, closed, _ = sources
    ths.collect_all_limit_up_pool.return_value = snapshot(item(), trade_date=date(2026, 10, 14))
    closed.return_value = [event()]
    result = collect()
    assert result.status == "ok"
    assert "trade date does not match" in result.source_warnings[0]
    assert result.payload[0].board_height == 1


@pytest.mark.parametrize("field, invalid", [
    ("成交额", float("nan")), ("换手率", float("inf")),
    ("炸板次数", 1.5), ("炸板次数", -1), ("首次封板时间", None),
    ("所属行业", None),
])
@pytest.mark.parametrize("closed", [True, False])
def test_strict_supplement_rejects_missing_scoring_facts(monkeypatch, field, invalid, closed):
    row = {
        "代码": "600001", "名称": "测试", "首次封板时间": "094000",
        "最后封板时间": "143000", "炸板次数": 0, "连板数": 1,
        "成交额": 100_000_000, "换手率": 5, "所属行业": "电力",
        field: invalid,
    }
    frame = MagicMock()
    frame.iterrows.return_value = [(0, row)]
    monkeypatch.setattr(ak_pool, "_load_pool_frame", lambda *_: frame)
    loader = ak_pool._collect_closed_limit_up_events if closed else ak_pool._collect_failed_limit_up_events
    with pytest.raises((ValueError, TypeError)):
        loader(DAY, "20261015", strict=True)


@pytest.mark.parametrize("height", [0, -1, 1.5, float("nan")])
def test_strict_closed_pool_rejects_invalid_board_height(monkeypatch, height):
    frame = MagicMock()
    frame.iterrows.return_value = [(0, {
        "代码": "600001", "名称": "测试", "首次封板时间": "094000",
        "最后封板时间": "143000", "炸板次数": 0, "连板数": height,
        "成交额": 100_000_000, "换手率": 5, "所属行业": "电力",
    })]
    monkeypatch.setattr(ak_pool, "_load_pool_frame", lambda *_: frame)
    with pytest.raises(ValueError):
        ak_pool._collect_closed_limit_up_events(DAY, "20261015", strict=True)
