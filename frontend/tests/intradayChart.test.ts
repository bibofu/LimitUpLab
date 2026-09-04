import assert from "node:assert/strict";
import test from "node:test";
import { performance } from "node:perf_hooks";

import { toFiveDayIntradayCandleBars } from "../src/intradayChart.ts";
import type { StockIntradayHistoryResponse } from "../src/types.ts";

test("prepares five one-minute sessions without a render-path bottleneck", () => {
  const history: StockIntradayHistoryResponse = {
    symbol: "002624",
    requested_days: 5,
    period_minutes: 1,
    start_date: "2026-08-31",
    end_date: "2026-09-04",
    data_as_of: "2026-09-04",
    complete: true,
    missing_trade_dates: [],
    days: ["2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"].map((tradeDate, dayIndex) => {
      return {
        trade_date: tradeDate,
        previous_close: 10 + dayIndex,
        status: "complete" as const,
        error: null,
        bars: Array.from({ length: 240 }, (_, minuteIndex) => ({
          timestamp: `${tradeDate}T${String(9 + Math.floor(minuteIndex / 60)).padStart(2, "0")}:${String(minuteIndex % 60).padStart(2, "0")}:00+08:00`,
          open: 10 + dayIndex,
          high: 10.2 + dayIndex,
          low: 9.8 + dayIndex,
          close: 10.1 + dayIndex,
          volume: 1_000 + minuteIndex,
          amount: (1_000 + minuteIndex) * (10.1 + dayIndex),
        })),
      };
    }),
  };

  const startedAt = performance.now();
  const bars = toFiveDayIntradayCandleBars(history);
  const elapsedMs = performance.now() - startedAt;

  assert.equal(bars.length, 1_200);
  assert.equal(bars[0].session, "2026-08-31");
  assert.equal(bars[0].referencePrice, 10);
  assert.equal(bars.at(-1)?.session, "2026-09-04");
  assert.ok(elapsedMs < 100, `chart data preparation took ${elapsedMs.toFixed(2)}ms`);
});
