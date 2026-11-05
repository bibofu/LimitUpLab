"""Daily percentages have an auditable baseline, independent of window returns."""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.models import StockKLineBar
from app.services import stock_kline


SOURCE = "hithink-finance.market.history.none"


def bars(count, *, source=SOURCE):
    return [StockKLineBar(trade_date=date(2026, 7, 1) + timedelta(days=index),
        open=10, high=12, low=9, close=10 + index / 10, volume=100, amount=10000,
        source=source) for index in range(count)]


def facts(monkeypatch, rows, *, days):
    requested = []
    def load(**kwargs):
        requested.append(kwargs["days"])
        return rows
    monkeypatch.setattr(stock_kline, "load_stock_kline_bars", load)
    repository = SimpleNamespace(list_daily_bars=lambda _: rows)
    result = stock_kline.build_stock_kline_facts(symbol="600123", days=days,
        end_date=rows[-1].trade_date, repository=repository)
    return result, requested


def test_display_window_keeps_its_first_daily_change_baseline(monkeypatch):
    rows = bars(21)
    result, requested = facts(monkeypatch, rows, days=5)
    first = result.bars[0]
    assert requested == [21] and len(result.bars) == 5
    assert first.previous_trade_date == rows[15].trade_date
    assert first.previous_close == rows[15].close
    assert first.close_to_close_pct == round((rows[16].close / rows[15].close - 1) * 100, 3)
    assert first.change_pct is None
    assert len(result.data_missing) == 5
    assert all(row.change_pct is None for row in rows), "Never mutate loaded/cache objects"


def test_maximum_window_fetches_one_extra_baseline_bar(monkeypatch):
    rows = bars(61)
    result, requested = facts(monkeypatch, rows, days=60)
    assert requested == [61] and len(result.bars) == 60
    assert result.bars[0].previous_trade_date == rows[0].trade_date
    assert result.bars[0].close_to_close_pct == 1
    assert result.bars[0].change_pct is None


def test_missing_first_baseline_is_explicit_without_zero_or_window_substitution(monkeypatch):
    rows = bars(5)
    result, _ = facts(monkeypatch, rows, days=5)
    assert result.bars[0].change_pct is None
    assert result.bars[0].close_to_close_pct is None
    assert result.bars[1].close_to_close_pct == 1
    assert len(result.data_missing) == 5 and rows[0].trade_date.isoformat() in result.data_missing[0]


def test_cache_with_missing_amount_is_refreshed_once_then_served(monkeypatch):
    rows = bars(2)
    missing = [row.model_copy(update={"amount": 0}) for row in rows]
    end_date = rows[-1].trade_date
    monkeypatch.setattr(stock_kline, "_recent_refresh_covers", lambda *args: False)
    assert not stock_kline._cache_is_usable(missing, 2, end_date, "600123")
    assert stock_kline._cache_is_usable(rows, 2, end_date, "600123")
    monkeypatch.setattr(stock_kline, "_recent_refresh_covers", lambda *args: True)
    assert stock_kline._cache_is_usable(missing, 2, end_date, "600123")


def test_missing_amount_is_explicit_and_not_reported_as_zero(monkeypatch):
    rows = [bar.model_copy(update={"amount": 0}) for bar in bars(2)]
    result, _ = facts(monkeypatch, rows, days=2)
    amount_notes = [item for item in result.data_missing if " amount:" in item]
    assert len(amount_notes) == 2
    assert all("不能用 0.0 替代" in item for item in amount_notes)


def test_source_percentage_survives_both_cache_conversions_and_takes_priority(monkeypatch):
    source_bar = bars(1, source="source-with-corporate-action-reference")[0].model_copy(
        update={"change_pct": 0.75})
    cached = stock_kline._to_daily_bar("600123", source_bar)
    restored = stock_kline._to_kline_bar(cached)
    assert cached.change_pct == restored.change_pct == 0.75
    result, _ = facts(monkeypatch, [restored], days=1)
    assert result.bars[0].change_pct == 0.75
    assert result.bars[0].change_pct_basis == "source" and result.data_missing == []


@pytest.mark.parametrize("updates", [
    {"source": "unknown"}, {"source": "qfq"}, {"close": 0}, {"close": float("nan")},
])
def test_missing_or_incompatible_baseline_does_not_create_daily_percentage(monkeypatch, updates):
    rows = bars(2)
    rows[0] = rows[0].model_copy(update=updates)
    result, _ = facts(monkeypatch, rows, days=1)
    assert result.bars[0].change_pct is None
    assert result.bars[0].close_to_close_pct is None
    assert result.data_missing


def test_finite_source_percentage_does_not_get_overwritten_by_a_price_jump(monkeypatch):
    rows = bars(2)
    rows[1] = rows[1].model_copy(update={"close": 5, "change_pct": -0.5})
    result, _ = facts(monkeypatch, rows, days=1)
    assert result.bars[0].change_pct == -0.5
    assert result.bars[0].close_to_close_pct == -50
