import type { MarketCandleBar } from "./components/MarketKLineChart";
import type {
  StockIntradayHistoryResponse,
  StockIntradayKLineBar,
} from "./types";

export function toIntradayCandleBars(
  bars: StockIntradayKLineBar[],
  options?: { session?: string; referencePrice?: number | null },
): MarketCandleBar[] {
  return bars.map((bar) => ({
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

export function toFiveDayIntradayCandleBars(
  history: StockIntradayHistoryResponse | null,
): MarketCandleBar[] {
  if (!history) {
    return [];
  }
  return history.days.flatMap((day) =>
    toIntradayCandleBars(day.bars, {
      session: day.trade_date,
      referencePrice: day.previous_close,
    }),
  );
}
