"""Unified daily A-share market-data provider with explicit source fallback."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from app.collectors.hithink_finance_collector import HithinkFinanceCollector
from app.collectors.stock_kline_collector import (
    collect_stock_kline as collect_tencent_history,
    collect_stock_spot_klines as collect_tencent_spot,
)
from app.models import StockKLineBar


HITHINK_HISTORY_SOURCE = "hithink-finance.market.history.none"
HITHINK_SNAPSHOT_SOURCE = "hithink-finance.market.snapshot"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_SHARES_PER_HAND = 100.0
HistoryFallback = Callable[[str, int, date | None], list[StockKLineBar]]
SpotFallback = Callable[[list[str], date], dict[str, StockKLineBar]]
logger = logging.getLogger(__name__)


class MarketDataProvider:
    """Read unadjusted daily data from hithink-finance, then named fallbacks."""

    # Initialize MarketDataProvider with the supplied dependencies and per-instance state.
    def __init__(
        self,
        *,
        hithink: HithinkFinanceCollector | None = None,
        history_fallback: HistoryFallback = collect_tencent_history,
        spot_fallback: SpotFallback = collect_tencent_spot,
    ) -> None:
        self.hithink = hithink or HithinkFinanceCollector()
        self.history_fallback = history_fallback
        self.spot_fallback = spot_fallback

    def collect_history(
        self,
        symbol: str,
        days: int = 5,
        end_date: date | None = None,
    ) -> list[StockKLineBar]:
        """Return unadjusted daily bars, preserving provider and amount metadata."""

        target_end_date = end_date or date.today()
        start_date = target_end_date - timedelta(days=max(days * 3, 15))
        try:
            history = self.hithink.collect_stock_history(
                _to_thscode(symbol),
                start_date=start_date,
                end_date=target_end_date,
                adjustment="none",
            )
            bars = [
                StockKLineBar(
                    trade_date=item.trade_date,
                    open=round(item.open, 2),
                    high=round(item.high, 2),
                    low=round(item.low, 2),
                    close=round(item.close, 2),
                    # The CLI returns shares; LimitUpLab stores A-share volume in hands.
                    volume=item.volume / _SHARES_PER_HAND,
                    amount=item.turnover,
                    source=HITHINK_HISTORY_SOURCE,
                )
                for item in history.items
            ][-days:]
            if not bars:
                raise ValueError("hithink-finance returned no daily bars")
            return bars
        except Exception as error:  # noqa: BLE001 - provider boundary owns fallback
            logger.warning(
                "hithink-finance history failed for %s; using Tencent fallback: %s",
                symbol,
                error,
            )
            return self.history_fallback(symbol, days, target_end_date)

    def collect_spot(
        self,
        symbols: list[str],
        trade_date: date,
    ) -> dict[str, StockKLineBar]:
        """Return same-day completed OHLC snapshots with a date-safety check."""

        normalized = sorted({_plain_symbol(symbol) for symbol in symbols})
        if not normalized:
            return {}
        try:
            snapshot = self.hithink.collect_market_snapshots(
                [_to_thscode(symbol) for symbol in normalized]
            )
            captured_date = snapshot.captured_at.astimezone(_SHANGHAI).date()
            if captured_date != trade_date:
                raise ValueError(
                    "snapshot assembly date does not match requested trade date"
                )
            bars: dict[str, StockKLineBar] = {}
            for item in snapshot.items:
                if None in (
                    item.open_price,
                    item.high_price,
                    item.low_price,
                    item.last_price,
                    item.volume,
                ):
                    continue
                bars[item.symbol] = StockKLineBar(
                    trade_date=trade_date,
                    open=round(item.open_price, 2),
                    high=round(item.high_price, 2),
                    low=round(item.low_price, 2),
                    close=round(item.last_price, 2),
                    volume=item.volume / _SHARES_PER_HAND,
                    amount=item.turnover or 0,
                    source=HITHINK_SNAPSHOT_SOURCE,
                )
            missing = [symbol for symbol in normalized if symbol not in bars]
            if missing:
                bars.update(self.spot_fallback(missing, trade_date))
            if not bars:
                raise ValueError("hithink-finance returned no complete spot bars")
            return bars
        except Exception as error:  # noqa: BLE001 - provider boundary owns fallback
            logger.warning(
                "hithink-finance snapshot failed; using Tencent fallback: %s",
                error,
            )
            return self.spot_fallback(normalized, trade_date)


_default_provider = MarketDataProvider()


# Fetch one stock's historical bars through the configured preferred/fallback source policy.
def collect_preferred_stock_kline(
    symbol: str,
    days: int = 5,
    end_date: date | None = None,
) -> list[StockKLineBar]:
    return _default_provider.collect_history(symbol, days, end_date)


# Fetch a batch of dated stock snapshot bars through the preferred/fallback source policy.
def collect_preferred_stock_spot_klines(
    symbols: list[str],
    trade_date: date,
) -> dict[str, StockKLineBar]:
    return _default_provider.collect_spot(symbols, trade_date)


# Strip provider-specific market decorations from a stock identifier.
def _plain_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if "." in value:
        value = value.split(".", 1)[0]
    if value.lower().startswith(("sh", "sz", "bj")):
        value = value[2:]
    if len(value) != 6 or not value.isdigit():
        raise ValueError("stock symbol must be a 6-digit A-share code")
    return value


# Translate a plain stock code to the exchange-qualified Tonghuashun identifier.
def _to_thscode(symbol: str) -> str:
    value = _plain_symbol(symbol)
    if value.startswith(("4", "8", "920")):
        suffix = "BJ"
    elif value.startswith(("5", "6", "9")):
        suffix = "SH"
    else:
        suffix = "SZ"
    return f"{value}.{suffix}"
