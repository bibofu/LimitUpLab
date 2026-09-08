import assert from "node:assert/strict";
import test from "node:test";
import {
  consolidationEmptyMessage,
  consolidationReason,
  observationDisplayStocks,
  premarketStrategyFromParam,
  type ConsolidationPool,
} from "../src/consolidation.ts";

test("missing data is not presented as a fully observed empty market", () => {
  assert.match(consolidationEmptyMessage({ status: "data_missing" } as ConsolidationPool), /不能据此判断全市场/);
  assert.match(consolidationEmptyMessage({ status: "empty" } as ConsolidationPool), /保持空池/);
  assert.equal(consolidationReason("mixed_or_missing_source"), "量价来源混用或缺失");
  assert.equal(consolidationReason("drawdown_below_10pct"), "较参考高点回撤不足 10%");
  assert.equal(consolidationReason("recent_event_dates"), "近 7 个交易日的涨停事件记录不完整");
});

test("all three pre-market strategies are top-level URL modes", () => {
  assert.equal(premarketStrategyFromParam(null), "relay");
  assert.equal(premarketStrategyFromParam("relay"), "relay");
  assert.equal(premarketStrategyFromParam("consolidation"), "consolidation");
  assert.equal(premarketStrategyFromParam("drawdown"), "drawdown");
  assert.equal(premarketStrategyFromParam("unknown"), "relay");
});

test("observation strategies only show qualified stocks when candidates exist", () => {
  const qualified = { symbol: "600001", state: "new" };
  const rejected = { symbol: "600002", state: "rejected" };
  const pool = {
    strategy: "consolidation",
    candidates: [qualified],
    evaluated_stocks: [qualified, rejected],
  } as ConsolidationPool;

  assert.deepEqual(
    observationDisplayStocks(pool).map((stock) => stock.symbol),
    ["600001"],
  );
});

test("an empty observation pool shows the six nearest rejected stocks", () => {
  const evaluated = Array.from({ length: 8 }, (_, index) => ({
    symbol: String(600001 + index),
    state: "rejected" as const,
    failed_conditions: ["drawdown_below_10pct"],
    drawdown_pct: index + 1,
  }));
  const pool = {
    strategy: "drawdown",
    candidates: [],
    evaluated_stocks: evaluated,
  } as ConsolidationPool;

  assert.deepEqual(
    observationDisplayStocks(pool).map((stock) => stock.symbol),
    ["600008", "600007", "600006", "600005", "600004", "600003"],
  );
});

test("consolidation near matches prioritize fewer failed rules, then threshold distance", () => {
  const pool = {
    strategy: "consolidation",
    candidates: [],
    evaluated_stocks: [
      { symbol: "600001", state: "rejected", failed_conditions: ["range_above_8pct"], range_pct: 12, anchor_change_pct: 0, volume_ratio: 0.7 },
      { symbol: "600002", state: "rejected", failed_conditions: ["volume_above_075"], range_pct: 8, anchor_change_pct: 0, volume_ratio: 0.8 },
      { symbol: "600003", state: "rejected", failed_conditions: ["range_above_8pct", "volume_above_075"], range_pct: 8.1, anchor_change_pct: 0, volume_ratio: 0.76 },
    ],
  } as ConsolidationPool;

  assert.deepEqual(
    observationDisplayStocks(pool).map((stock) => stock.symbol),
    ["600002", "600001", "600003"],
  );
});
