from datetime import date, datetime, timedelta, timezone

from app.models import StockDailyBar
from app.services.stock_position import classify_stock_position


def _bars(closes: list[float]) -> list[StockDailyBar]:
    start = date(2026, 1, 1)
    return [
        StockDailyBar(
            symbol="600001",
            trade_date=start + timedelta(days=index),
            open=close * 0.99,
            high=close * 1.02,
            low=close * 0.98,
            close=close,
            volume=1_000_000 + index * 1_000,
            amount=100_000_000,
            change_pct=None,
            source="test",
            created_at=datetime.now(timezone.utc),
        )
        for index, close in enumerate(closes)
    ]


def _linear(start: float, end: float, count: int) -> list[float]:
    if count <= 1:
        return [end]
    return [start + (end - start) * index / (count - 1) for index in range(count)]


def test_position_requires_enough_pre_board_bars() -> None:
    bars = _bars([10, 10.1, 11.1])

    result = classify_stock_position(bars, bars[-1].trade_date)

    assert result.primary.regime == "unclassified"
    assert result.confidence < 0.2


def test_classifies_oversold_rebound() -> None:
    bars = _bars([*_linear(20, 8, 120), 8.8])

    result = classify_stock_position(bars, bars[-1].trade_date)

    assert result.primary.regime == "oversold_rebound"
    assert result.metrics["position_120_pct"] is not None
    assert result.metrics["position_120_pct"] < 20


def test_classifies_low_base_breakout() -> None:
    bars = _bars([
        *_linear(15, 10, 85),
        *[10 + (index % 3) * 0.03 for index in range(35)],
        11.1,
    ])

    result = classify_stock_position(bars, bars[-1].trade_date)

    assert result.primary.regime == "low_base_breakout"
    assert "突破20日平台" in result.tags


def test_classifies_v_reversal() -> None:
    bars = _bars([
        *[10.0] * 60,
        *_linear(10, 16, 45),
        *_linear(15.5, 11, 15),
        12.5,
    ])

    result = classify_stock_position(bars, bars[-1].trade_date)

    assert result.primary.regime == "v_reversal"
    assert result.metrics["pullback_from_wave_peak_pct"] < -18


def test_classifies_second_wave() -> None:
    bars = _bars([
        *[10.0] * 60,
        *_linear(10, 16, 25),
        *_linear(15.8, 13, 15),
        *_linear(13.1, 14.5, 20),
        16.2,
    ])

    result = classify_stock_position(bars, bars[-1].trade_date)

    assert result.primary.regime == "second_wave"
    assert result.metrics["prior_wave_gain_pct"] >= 25
