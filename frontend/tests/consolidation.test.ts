import assert from "node:assert/strict";
import test from "node:test";
import { consolidationEmptyMessage, consolidationReason, type ConsolidationPool } from "../src/consolidation.ts";

test("missing data is not presented as a fully observed empty market", () => {
  assert.match(consolidationEmptyMessage({ status: "data_missing" } as ConsolidationPool), /不能据此判断全市场/);
  assert.match(consolidationEmptyMessage({ status: "empty" } as ConsolidationPool), /保持空池/);
  assert.equal(consolidationReason("mixed_or_missing_source"), "量价来源混用或缺失");
  assert.equal(consolidationReason("drawdown_below_10pct"), "较参考高点回撤不足 10%");
});
