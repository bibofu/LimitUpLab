"""Local-first K-line loading and compact trend facts for Agent tools."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from statistics import mean
from time import monotonic

from app.collectors import (
    collect_stock_intraday_kline,
    collect_stock_kline,
    collect_stock_spot_klines,
)
from app.collectors.stock_kline_collector import build_stock_close_snapshot
from app.models import (
    StockDailyBar,
    StockDetailMarketData,
    StockIntradayHistoryDay,
    StockIntradayHistoryResponse,
    StockIntradayKLineBar,
    StockKLineBar,
    StockKLineFacts,
    StockPositionAssessment,
)
from app.repositories import SQLiteFirstBoardRepository
from app.services.stock_position import classify_stock_position


HistoryCollector = Callable[[str, int, date | None], list[StockKLineBar]]
SpotCollector = Callable[[list[str], date], dict[str, StockKLineBar]]
IntradayCollector = Callable[[str, date, int], list[StockIntradayKLineBar]]
_REFRESH_LOCKS = tuple(threading.Lock() for _ in range(64))
_INTRADAY_REFRESH_LOCKS = tuple(threading.Lock() for _ in range(64))
_REFRESH_ATTEMPT_TTL_SECONDS = 300.0
_refresh_attempts_lock = threading.Lock()
_refresh_attempts: dict[tuple[str, date], tuple[float, int]] = {}


def load_stock_intraday_bars(
    *,
    symbol: str,
    trade_date: date,
    period: int,
    repository: SQLiteFirstBoardRepository | None = None,
    collector: IntradayCollector = collect_stock_intraday_kline,
) -> list[StockIntradayKLineBar]:
    """Return persistent local intraday bars and coalesce cold cache fills."""

    if not (1 <= period <= 60):
        raise ValueError("period must be between 1 and 60")
    normalized_symbol = _normalize_cache_symbol(symbol)
    active_repository = repository or SQLiteFirstBoardRepository()
    cached = active_repository.list_intraday_bars(
        symbol=normalized_symbol,
        trade_date=trade_date,
        period_minutes=period,
    )
    if cached:
        return cached

    lock_index = hash((normalized_symbol, trade_date, period)) % len(
        _INTRADAY_REFRESH_LOCKS
    )
    with _INTRADAY_REFRESH_LOCKS[lock_index]:
        cached = active_repository.list_intraday_bars(
            symbol=normalized_symbol,
            trade_date=trade_date,
            period_minutes=period,
        )
        if cached:
            return cached

        collected = collector(normalized_symbol, trade_date, period)
        if collected:
            active_repository.replace_intraday_bars(
                symbol=normalized_symbol,
                trade_date=trade_date,
                period_minutes=period,
                bars=collected,
                source="sina-minute-direct",
            )
        return collected


def load_stock_intraday_history(
    *,
    symbol: str,
    trade_dates: list[date],
    period: int,
    daily_bars: list[StockKLineBar],
    repository: SQLiteFirstBoardRepository | None = None,
    collector: IntradayCollector = collect_stock_intraday_kline,
) -> StockIntradayHistoryResponse:
    """Load several completed sessions concurrently while preserving day-level errors."""

    if not trade_dates:
        raise ValueError("At least one trade date is required")
    if not (1 <= len(trade_dates) <= 10):
        raise ValueError("trade_dates must contain between 1 and 10 dates")
    if not (1 <= period <= 60):
        raise ValueError("period must be between 1 and 60")

    normalized_symbol = _normalize_cache_symbol(symbol)
    requested_dates = sorted(set(trade_dates))
    if len(requested_dates) != len(trade_dates):
        raise ValueError("trade_dates must not contain duplicates")
    active_repository = repository or SQLiteFirstBoardRepository()
    closes = {
        item.trade_date: item.close
        for item in sorted(daily_bars, key=lambda item: item.trade_date)
    }
    ordered_daily_dates = sorted(closes)
    previous_closes: dict[date, float | None] = {}
    for trade_date in requested_dates:
        earlier_dates = [item for item in ordered_daily_dates if item < trade_date]
        previous_closes[trade_date] = closes[earlier_dates[-1]] if earlier_dates else None

    def load_day(trade_date: date) -> StockIntradayHistoryDay:
        try:
            bars = load_stock_intraday_bars(
                symbol=normalized_symbol,
                trade_date=trade_date,
                period=period,
                repository=active_repository,
                collector=collector,
            )
        except Exception as error:  # noqa: BLE001 - retain partial multi-day results
            return StockIntradayHistoryDay(
                trade_date=trade_date,
                previous_close=previous_closes[trade_date],
                status="error",
                error=type(error).__name__,
            )
        return StockIntradayHistoryDay(
            trade_date=trade_date,
            previous_close=previous_closes[trade_date],
            status="complete" if bars else "missing",
            bars=bars,
        )

    with ThreadPoolExecutor(max_workers=min(5, len(requested_dates))) as executor:
        loaded = list(executor.map(load_day, requested_dates))

    missing_dates = [item.trade_date for item in loaded if item.status != "complete"]
    available_dates = [item.trade_date for item in loaded if item.bars]
    return StockIntradayHistoryResponse(
        symbol=normalized_symbol,
        requested_days=len(requested_dates),
        period_minutes=period,
        start_date=requested_dates[0],
        end_date=requested_dates[-1],
        data_as_of=max(available_dates) if available_dates else None,
        complete=not missing_dates,
        missing_trade_dates=missing_dates,
        days=loaded,
    )


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

    if not (1 <= days <= 125):
        raise ValueError("days must be between 1 and 125")
    active_repository = repository or SQLiteFirstBoardRepository()
    cached = _cached_bars(active_repository, symbol, end_date)
    if _cache_can_serve(cached, days, end_date) or (
        cached and _recent_refresh_covers(symbol, end_date, days)
    ):
        return [_to_kline_bar(item) for item in cached[-days:]]

    refresh_lock = _refresh_lock_for(symbol, end_date)
    with refresh_lock:
        cached = _cached_bars(active_repository, symbol, end_date)
        if _cache_can_serve(cached, days, end_date) or (
            cached and _recent_refresh_covers(symbol, end_date, days)
        ):
            return [_to_kline_bar(item) for item in cached[-days:]]

        collected: list[StockKLineBar] = []
        collection_error: Exception | None = None
        refresh_completed = False
        try:
            collected = history_collector(symbol, days, end_date)
            refresh_completed = True
        except Exception as error:  # noqa: BLE001
            collection_error = error

        known_dates = {item.trade_date for item in cached}
        known_dates.update(item.trade_date for item in collected)
        if end_date not in known_dates:
            try:
                spot = spot_collector([symbol], end_date).get(symbol)
                refresh_completed = True
                if spot is not None:
                    collected.append(spot)
            except Exception as error:  # noqa: BLE001
                collection_error = collection_error or error

        if collected:
            active_repository.upsert_daily_bars(
                [
                    _to_daily_bar(symbol, item)
                    for item in collected
                    if item.trade_date <= end_date
                ]
            )
        refreshed = _cached_bars(active_repository, symbol, end_date)
        if not refreshed:
            if collection_error is not None:
                raise collection_error
            raise ValueError(
                f"No K-line data available for {symbol} through {end_date.isoformat()}"
            )
        if refresh_completed:
            _record_refresh_attempt(symbol, end_date, days)
        return [_to_kline_bar(item) for item in refreshed[-days:]]


def load_stock_detail_market_data(
    *,
    symbol: str,
    days: int,
    end_date: date,
    position_trade_date: date | None = None,
    repository: SQLiteFirstBoardRepository | None = None,
    history_collector: HistoryCollector = collect_stock_kline,
    spot_collector: SpotCollector = collect_stock_spot_klines,
) -> StockDetailMarketData:
    """Load one reusable market-data bundle for the stock detail page."""

    if not (1 <= days <= 60):
        raise ValueError("days must be between 1 and 60")
    active_repository = repository or SQLiteFirstBoardRepository()
    position = None
    if position_trade_date is not None:
        enrichment = active_repository.get_enrichment(symbol, position_trade_date)
        position = enrichment.position if enrichment else None

    preload_days = days
    if position_trade_date == end_date and position is None:
        preload_days = 125
    bars = load_stock_kline_bars(
        symbol=symbol,
        days=preload_days,
        end_date=end_date,
        repository=active_repository,
        history_collector=history_collector,
        spot_collector=spot_collector,
    )
    display_bars = bars[-days:]
    latest_close = build_stock_close_snapshot(
        symbol=symbol,
        bars=bars,
        source="local-first-kline",
    )

    if position_trade_date is not None and position is None:
        if position_trade_date == end_date:
            position = classify_stock_position(
                [_to_daily_bar(symbol, item) for item in bars],
                position_trade_date,
            )
        else:
            position = load_stock_position_assessment(
                symbol=symbol,
                end_date=position_trade_date,
                repository=active_repository,
                history_collector=history_collector,
                spot_collector=spot_collector,
            )

    return StockDetailMarketData(
        symbol=symbol,
        requested_days=days,
        data_as_of=display_bars[-1].trade_date,
        kline=display_bars,
        latest_close=latest_close,
        position_trade_date=position_trade_date,
        position=position,
    )


def load_stock_position_assessment(
    *,
    symbol: str,
    end_date: date,
    repository: SQLiteFirstBoardRepository | None = None,
    history_collector: HistoryCollector = collect_stock_kline,
    spot_collector: SpotCollector = collect_stock_spot_klines,
) -> StockPositionAssessment:
    """Return a point-in-time position assessment for one stock detail page."""

    active_repository = repository or SQLiteFirstBoardRepository()
    enrichment = active_repository.get_enrichment(symbol, end_date)
    if enrichment and enrichment.position:
        return enrichment.position

    bars = load_stock_kline_bars(
        symbol=symbol,
        days=125,
        end_date=end_date,
        repository=active_repository,
        history_collector=history_collector,
        spot_collector=spot_collector,
    )
    daily_bars = [_to_daily_bar(symbol, item) for item in bars]
    return classify_stock_position(daily_bars, end_date)


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


def _normalize_cache_symbol(symbol: str) -> str:
    """Normalize prefixed and plain A-share codes to one persistent cache key."""

    value = symbol.strip().lower()
    if value.startswith(("sh", "sz")):
        value = value[2:]
    if len(value) != 6 or not value.isdigit():
        raise ValueError("stock symbol must be a 6-digit A-share code")
    return value


def _cache_can_serve(
    bars: list[StockDailyBar],
    days: int,
    end_date: date,
) -> bool:
    return bool(
        len(bars) >= days
        and bars[-1].trade_date >= end_date
    )


def _refresh_lock_for(symbol: str, end_date: date) -> threading.Lock:
    index = hash((symbol, end_date)) % len(_REFRESH_LOCKS)
    return _REFRESH_LOCKS[index]


def _recent_refresh_covers(symbol: str, end_date: date, days: int) -> bool:
    now = monotonic()
    key = (symbol, end_date)
    with _refresh_attempts_lock:
        attempt = _refresh_attempts.get(key)
        if attempt is None:
            return False
        attempted_at, requested_days = attempt
        if now - attempted_at > _REFRESH_ATTEMPT_TTL_SECONDS:
            _refresh_attempts.pop(key, None)
            return False
        return requested_days >= days


def _record_refresh_attempt(symbol: str, end_date: date, days: int) -> None:
    now = monotonic()
    key = (symbol, end_date)
    with _refresh_attempts_lock:
        previous = _refresh_attempts.get(key)
        covered_days = max(days, previous[1] if previous else 0)
        _refresh_attempts[key] = (now, covered_days)
        if len(_refresh_attempts) > 1_024:
            expired = [
                item_key
                for item_key, (attempted_at, _) in _refresh_attempts.items()
                if now - attempted_at > _REFRESH_ATTEMPT_TTL_SECONDS
            ]
            for item_key in expired:
                _refresh_attempts.pop(item_key, None)


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
