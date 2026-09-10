import type { MarketCandleBar } from "./components/MarketKLineChart";
import type {
  StockIntradayHistoryResponse,
  StockIntradayKLineBar,
} from "./types";

/**
 * Adapt intraday records to chart bars, preserving input order and converting timestamps to
 * Unix seconds. Chronological sorting is handled by the chart component.
 */
export function toIntradayCandleBars(
  bars: StockIntradayKLineBar[],
  options?: { session?: string; referencePrice?: number | null },
): MarketCandleBar[] {
  return bars.map(/* Transform each entry in bars into the result used by toIntradayCandleBars. */ (bar) => ({
    time: Math.floor(new Date(bar.timestamp).getTime() / 1000),
    label: bar.timestamp.slice(5, 16).replace("T", " "),
    open: bar.open,
    close: bar.close,
    high: bar.high,
    low: bar.low,
    volume: bar.volume,
    amount: bar.amount,
    session: options?.session,
    referencePrice: options?.referencePrice ?? undefined,
  }));
}

/**
 * Adapt multi-day intraday records while retaining each day's reference price for correct
 * change labels.
 */
export function toFiveDayIntradayCandleBars(
  history: StockIntradayHistoryResponse | null,
): MarketCandleBar[] {
  if (!history) {
    return [];
  }
  return history.days.flatMap(/* Handle the callback from history.days.flatMap within toFiveDayIntradayCandleBars. */ (day) =>
    toIntradayCandleBars(day.bars, {
      session: day.trade_date,
      referencePrice: day.previous_close,
    }),
  );
}
