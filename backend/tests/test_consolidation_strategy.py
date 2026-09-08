from datetime import date, datetime, timedelta, timezone
import sqlite3

import pytest

from app.repositories.consolidation_repository import load_consolidation_pool
from app.services.consolidation import completed_date_limit, screen_consolidation


def fixture():
    dates = []
    day = date(2026, 7, 1)
    while len(dates) < 28:
        if day.weekday() < 5:
            dates.append(day.isoformat())
        day += timedelta(days=1)
    bars = []
    for i, day in enumerate(dates):
        bars.append(dict(symbol="600001", trade_date=day, open=10., high=10.1, low=9.9,
                         close=10., volume=1000., source="test.history"))
        if i == 24:
            bars[-1].update(open=10.1, high=11., low=10., close=11.)
        elif i > 24:
            bars[-1].update(open=11., high=11.3, low=10.9, close=11.1, volume=600.)
    events = [dict(symbol="999999", name="市场日期占位", trade_date=d, closed_limit=0) for d in dates]
    events.append(dict(symbol="600001", name="测试股份", trade_date=dates[24], closed_limit=1))
    now = datetime(2026, 9, 7, 9, tzinfo=timezone.utc)
    return events, bars, dates, date.fromisoformat(dates[-1]), now


def test_first_confirmation_and_continuing_observation_are_distinct():
    events, bars, dates, end, now = fixture()
    result = screen_consolidation(events, bars, dates, end, now)
    candidate = result.candidates[0]
    assert candidate.volume_ratio == .6
    assert candidate.state == "watching"
    assert candidate.confirmed_date.isoformat() == dates[26]
    assert result.pool_count == result.evaluated_count == 1
    assert result.evaluated_stocks == result.candidates
    assert screen_consolidation(events, bars, dates, date.fromisoformat(dates[26]), now).candidates[0].state == "new"


def test_confirmation_waits_until_both_volume_and_price_rules_hold():
    events, bars, dates, end, now = fixture()
    bars[25]["volume"], bars[26]["volume"], bars[27]["volume"] = 1000., 1000., 100.
    candidate = screen_consolidation(events, bars, dates, end, now).candidates[0]
    assert candidate.state == "new"
    assert candidate.confirmed_date == end
    assert candidate.volume_ratio == .7


@pytest.mark.parametrize("strategy", ["consolidation", "drawdown"])
def test_future_prices_and_events_cannot_change_historical_screen(strategy):
    events, bars, dates, end, now = fixture()
    before = screen_consolidation(events, bars, dates, end, now, strategy)
    events.append(dict(symbol="600001", name="ST未来名称", trade_date="2026-09-01", closed_limit=1))
    bars.append({**bars[-1], "trade_date": "2026-09-01", "close": 9999.})
    after = screen_consolidation(events, bars, [*dates, "2026-09-01"], end, now, strategy)
    assert before == after


def test_new_limit_up_resets_anchor():
    events, bars, dates, end, now = fixture()
    events.append(dict(symbol="600001", name="测试股份", trade_date=dates[-2], closed_limit=1))
    result = screen_consolidation(events, bars, dates, end, now)
    assert not result.candidates
    assert result.exclusions == {"age_outside_2_4": 1}


@pytest.mark.parametrize("change,reason", [
    ({"source": "another-source"}, "mixed_or_missing_source"),
    ({"volume": 0.}, "invalid_volume"),
    ({"volume": float("nan")}, "invalid_volume"),
    ({"close": None}, "missing_history20"),
    ({"volume": 3000.}, "volume_above_075"),
])
def test_failed_conditions_never_become_candidates(change, reason):
    events, bars, dates, end, now = fixture()
    bars[-1].update(change)
    result = screen_consolidation(events, bars, dates, end, now)
    assert not result.candidates
    assert result.exclusions[reason] == 1
    assert result.evaluated_count == len(result.evaluated_stocks)
    if reason == "volume_above_075":
        assert result.evaluated_stocks[0].state == "rejected"
        assert result.evaluated_stocks[0].confirmed_date is None
        assert result.evaluated_stocks[0].failed_conditions == [reason]
    else:
        assert result.evaluated_stocks == []


def test_tencent_history_and_close_snapshot_are_one_source_family():
    events, bars, dates, end, now = fixture()
    for bar in bars:
        bar["source"] = "akshare.stock_zh_a_hist_tx"
    bars[-1]["source"] = "tencent.qt.gtimg.cn"
    result = screen_consolidation(events, bars, dates, end, now)
    assert result.status == "ready"
    assert result.candidates
    assert "mixed_or_missing_source" not in result.exclusions


def test_evaluable_rejections_keep_facts_and_all_failed_conditions():
    events, bars, dates, end, now = fixture()
    for bar in bars[25:]:
        bar.update(open=11., high=12., low=10.7, close=12., volume=900.)
    result = screen_consolidation(events, bars, dates, end, now)
    assert result.candidates == []
    assert result.evaluated_count == 1
    stock = result.evaluated_stocks[0]
    assert stock.state == "rejected"
    assert stock.failed_conditions == ["range_above_8pct", "close_outside_band", "volume_above_075"]
    assert stock.range_pct == 12.1495
    assert stock.anchor_change_pct == 9.0909
    assert stock.volume_ratio == .9
    assert result.exclusions == {"range_above_8pct": 1}


def test_qualified_cards_precede_rejections_without_duplicating_stocks():
    events, bars, dates, end, now = fixture()
    bars += [{**bar, "symbol": "600002"} for bar in bars]
    events.append({**events[-1], "symbol": "600002"})
    for bar in bars:
        if bar["symbol"] == "600001" and bar["trade_date"] > dates[24]:
            bar["volume"] = 900.
    result = screen_consolidation(events, bars, dates, end, now)
    assert result.evaluated_count == 2
    assert [stock.symbol for stock in result.evaluated_stocks] == ["600002", "600001"]
    assert [stock.symbol for stock in result.candidates] == ["600002"]


def test_inclusive_price_and_volume_boundaries():
    events, bars, dates, end, now = fixture()
    for bar in bars[25:]:
        bar.update(open=10., high=10.692, low=9.9, close=9.9, volume=750.)
    candidate = screen_consolidation(events, bars, dates, end, now).candidates[0]
    assert candidate.range_pct == 8
    assert candidate.anchor_change_pct == -10
    assert candidate.volume_ratio == .75


@pytest.mark.parametrize("change,accepted", [
    (-.101, False), (-.10, True), (-.07, True), (.08, True), (.081, False),
])
def test_relative_close_band(change, accepted):
    events, bars, dates, end, now = fixture()
    close = 11. * (1 + change)
    for bar in bars[25:]:
        bar.update(open=close, high=close, low=close, close=close)
    result = screen_consolidation(events, bars, dates, end, now)
    assert bool(result.candidates) is accepted
    if not accepted:
        assert result.exclusions == {"close_outside_band": 1}


def test_missing_event_date_blocks_anchor_inference():
    events, bars, dates, end, now = fixture()
    events = [e for e in events if e["trade_date"] != dates[-6]]
    result = screen_consolidation(events, bars, dates, end, now)
    assert result.status == "data_missing"
    assert result.data_missing == ["recent_event_dates"]


def test_recent_limit_up_anchor_window_includes_seven_trading_days():
    events, bars, dates, end, now = fixture()
    for event in events:
        event["closed_limit"] = 0
    events.append(
        dict(
            symbol="600001",
            name="测试股份",
            trade_date=dates[-6],
            closed_limit=1,
        )
    )

    result = screen_consolidation(events, bars, dates, end, now)

    assert result.pool_count == 1
    assert result.exclusions == {"age_outside_2_4": 1}


def test_intraday_cutoff_and_timezone():
    before = datetime(2026, 9, 7, 7, 29, tzinfo=timezone.utc)
    after = datetime(2026, 9, 7, 7, 30, tzinfo=timezone.utc)
    assert completed_date_limit(before) == date(2026, 9, 6)
    assert completed_date_limit(after) == date(2026, 9, 7)
    events, bars, dates, _, _ = fixture()
    with pytest.raises(ValueError):
        screen_consolidation(events, bars, dates, date(2026, 9, 7), before)


def test_repository_is_read_only_and_does_not_use_later_data(tmp_path):
    events, bars, dates, end, now = fixture()
    path = tmp_path / "screen.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE limit_up_events(symbol TEXT,name TEXT,trade_date TEXT,closed_limit INTEGER)")
        conn.execute("CREATE TABLE stock_daily_bars(symbol TEXT,trade_date TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,source TEXT)")
        conn.executemany("INSERT INTO limit_up_events VALUES(:symbol,:name,:trade_date,:closed_limit)", events)
        conn.executemany("INSERT INTO stock_daily_bars VALUES(:symbol,:trade_date,:open,:high,:low,:close,:volume,:source)", bars)
    original = path.read_bytes()
    result = load_consolidation_pool(None, now, path)
    assert result.data_as_of == end
    assert len(result.candidates) == 1
    historical = load_consolidation_pool(date.fromisoformat(dates[26]), now, path)
    assert historical.candidates[0].state == "new"
    drawdown = load_consolidation_pool(None, now, path, strategy="drawdown")
    assert drawdown.strategy == "drawdown"
    assert drawdown.evaluated_count == 1
    assert drawdown.evaluated_stocks[0].peak_date.isoformat() == dates[26]
    assert path.read_bytes() == original
    with pytest.raises(ValueError):
        load_consolidation_pool(date(2026, 10, 1), now, path)


def test_absent_database_is_explicit(tmp_path):
    result = load_consolidation_pool(None, fixture()[-1], tmp_path / "absent.sqlite")
    assert result.status == "data_missing"
    assert result.data_missing == ["local_database"]


@pytest.mark.parametrize("drawdown,accepted", [(9.99, False), (10., True), (10.1, True)])
def test_drawdown_threshold_includes_boundary_without_volume_filter(drawdown, accepted):
    events, bars, dates, end, now = fixture()
    close = 11. * (1 - drawdown/100)
    for bar in bars[25:]:
        bar.update(open=close, high=close, low=close, close=close, volume=1500.)
    result = screen_consolidation(events, bars, dates, end, now, "drawdown")
    assert result.strategy_version == "drawdown_research_v0.3"
    assert result.evaluated_count == 1
    assert bool(result.candidates) == accepted
    stock = result.evaluated_stocks[0]
    assert stock.drawdown_pct == drawdown
    assert stock.peak_date.isoformat() == dates[24]
    assert stock.volume_ratio == 1.5
    if accepted:
        assert stock.confirmed_date.isoformat() == dates[25]
        assert stock.state == "watching"
    else:
        assert stock.failed_conditions == ["drawdown_below_10pct"]


def test_drawdown_reference_excludes_observation_day_high():
    events, bars, dates, end, now = fixture()
    for bar in bars[25:]:
        bar.update(open=11., high=11., low=11., close=11.)
    bars[-1]["high"] = 12.2
    stock = screen_consolidation(events, bars, dates, end, now, "drawdown").evaluated_stocks[0]
    assert stock.peak_price == 11.
    assert stock.peak_date.isoformat() == dates[26]
    assert stock.drawdown_pct == 0


def test_drawdown_large_decline_is_observed_with_risk_and_no_range_filter():
    events, bars, dates, end, now = fixture()
    for bar, close in zip(bars[25:], [9.9, 8.91, 8.5]):
        bar.update(open=close, high=close, low=close, close=close)
    stock = screen_consolidation(events, bars, dates, end, now, "drawdown").candidates[0]
    assert stock.drawdown_pct == 22.7273
    assert stock.range_pct > 8
    assert stock.anchor_change_pct < -10
    assert any("20%" in risk for risk in stock.risks)


def test_drawdown_data_quality_and_new_anchor_still_apply():
    events, bars, dates, end, now = fixture()
    bars[-1]["volume"] = None
    result = screen_consolidation(events, bars, dates, end, now, "drawdown")
    assert result.evaluated_stocks == []
    assert result.data_missing == ["invalid_volume"]
    events.append(dict(symbol="600001", name="测试股份", trade_date=dates[-1], closed_limit=1))
    result = screen_consolidation(events, bars, dates, end, now, "drawdown")
    assert result.exclusions == {"age_outside_1_4": 1}
