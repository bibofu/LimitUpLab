"""Local-first K-line loading and compact trend facts for Agent tools."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from statistics import mean

from app.collectors import collect_stock_kline, collect_stock_spot_klines
from app.models import StockDailyBar, StockKLineBar, StockKLineFacts
from app.repositories import SQLiteFirstBoardRepository


HistoryCollector = Callable[[str, int, date | None], list[StockKLineBar]]
SpotCollector = Callable[[list[str], date], dict[str, StockKLineBar]]


def load_stock_kline_bars(
    *,
    symbol: str,
    days: int,
    end_date: date,
    repository: SQLiteFirstBoardRepository | None = None,
    history_collector: HistoryCollector = collect_stock_kline,
    spot_collector: SpotCollector = collect_stock_spot_klines,
) -> list[StockKLineBar]:
    """Return cached daily bars, refreshing missing history and the end date."""

    if not (1 <= days <= 61):
        raise ValueError("days must be between 1 and 61")
    active_repository = repository or SQLiteFirstBoardRepository()
    cached = _cached_bars(active_repository, symbol, end_date)
    needs_history = len(cached) < days
    needs_end_date = not cached or cached[-1].trade_date < end_date
    collected: list[StockKLineBar] = []
    collection_error: Exception | None = None

    if needs_history or needs_end_date:
        try:
            collected = history_collector(symbol, days, end_date)
        except Exception as error:  # noqa: BLE001
            collection_error = error

    known_dates = {item.trade_date for item in cached}
    known_dates.update(item.trade_date for item in collected)
    if end_date not in known_dates or end_date == date.today():
        try:
            spot = spot_collector([symbol], end_date).get(symbol)
            if spot is not None:
                collected.append(spot)
        except Exception as error:  # noqa: BLE001
            collection_error = collection_error or error

    if collected:
        active_repository.upsert_daily_bars(
            [_to_daily_bar(symbol, item) for item in collected if item.trade_date <= end_date]
        )
    refreshed = _cached_bars(active_repository, symbol, end_date)
    if not refreshed:
        if collection_error is not None:
            raise collection_error
        raise ValueError(f"No K-line data available for {symbol} through {end_date.isoformat()}")
    return [_to_kline_bar(item) for item in refreshed[-days:]]


def build_stock_kline_facts(
    *,
    symbol: str,
    days: int = 20,
    end_date: date,
    repository: SQLiteFirstBoardRepository | None = None,
    history_collector: HistoryCollector = collect_stock_kline,
    spot_collector: SpotCollector = collect_stock_spot_klines,
) -> StockKLineFacts:
    """Load K-line bars and derive compact facts suitable for an LLM answer."""

    requested_days = max(1, min(days, 60))
    analysis_days = max(requested_days, 21)
    active_repository = repository or SQLiteFirstBoardRepository()
    analysis_bars = load_stock_kline_bars(
        symbol=symbol,
        days=analysis_days,
        end_date=end_date,
        repository=active_repository,
        history_collector=history_collector,
        spot_collector=spot_collector,
    )
    display_bars = analysis_bars[-requested_days:]
    persisted = _cached_bars(active_repository, symbol, end_date)
    source_by_date = {item.trade_date: item.source for item in persisted}
    latest = analysis_bars[-1]
    ma5 = _moving_average(analysis_bars, 5)
    ma10 = _moving_average(analysis_bars, 10)
    ma20 = _moving_average(analysis_bars, 20)

    return StockKLineFacts(
        symbol=symbol,
        requested_days=requested_days,
        requested_end_date=end_date,
        data_as_of=latest.trade_date,
        data_fresh=latest.trade_date == end_date,
        trend=_trend_label(latest.close, ma5, ma10, ma20),
        latest_close=latest.close,
        return_5d_pct=_period_return(analysis_bars, 5),
        return_10d_pct=_period_return(analysis_bars, 10),
        return_20d_pct=_period_return(analysis_bars, 20),
        ma5=ma5,
        ma10=ma10,
        ma20=ma20,
        volume_ratio_5d=_volume_ratio(analysis_bars),
        max_drawdown_pct=_max_drawdown(display_bars),
        sources=sorted(
            {
                source_by_date[item.trade_date]
                for item in display_bars
                if item.trade_date in source_by_date
            }
        ),
        bars=display_bars,
    )


def _cached_bars(
    repository: SQLiteFirstBoardRepository,
    symbol: str,
    end_date: date,
) -> list[StockDailyBar]:
    return [item for item in repository.list_daily_bars(symbol) if item.trade_date <= end_date]


def _to_daily_bar(symbol: str, bar: StockKLineBar) -> StockDailyBar:
    return StockDailyBar(
        symbol=symbol,
        trade_date=bar.trade_date,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        amount=0,
        change_pct=None,
        source="stock-kline-tool",
        created_at=datetime.now(timezone.utc),
    )


def _to_kline_bar(bar: StockDailyBar) -> StockKLineBar:
    return StockKLineBar(
        trade_date=bar.trade_date,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
    )


def _moving_average(bars: list[StockKLineBar], window: int) -> float | None:
    if len(bars) < window:
        return None
    return round(mean(item.close for item in bars[-window:]), 3)


def _period_return(bars: list[StockKLineBar], periods: int) -> float | None:
    if len(bars) <= periods or bars[-periods - 1].close == 0:
        return None
    return round((bars[-1].close / bars[-periods - 1].close - 1) * 100, 3)


def _volume_ratio(bars: list[StockKLineBar]) -> float | None:
    if len(bars) < 6:
        return None
    baseline = mean(item.volume for item in bars[-6:-1])
    return round(bars[-1].volume / baseline, 3) if baseline > 0 else None


def _max_drawdown(bars: list[StockKLineBar]) -> float | None:
    if not bars:
        return None
    peak = bars[0].high
    drawdown = 0.0
    for item in bars:
        peak = max(peak, item.high)
        if peak > 0:
            drawdown = min(drawdown, (item.low / peak - 1) * 100)
    return round(drawdown, 3)


def _trend_label(
    close: float,
    ma5: float | None,
    ma10: float | None,
    ma20: float | None,
) -> str:
    if ma5 is None or ma10 is None or ma20 is None:
        return "insufficient"
    if close >= ma5 >= ma10 >= ma20:
        return "rising"
    if close <= ma5 <= ma10 <= ma20:
        return "falling"
    return "oscillating"
