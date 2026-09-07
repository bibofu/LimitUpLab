import assert from "node:assert/strict";
import test from "node:test";

import {
  canShowStrategyRanking,
  orderStrategies,
  STRATEGY_DISPLAY_ORDER,
} from "../src/strategyPresentation.ts";

test("strategy list keeps the product-defined order", () => {
  const shuffled = ["second_to_third", "relay_one_to_two", "high_drawdown"]
    .map((strategy_id) => ({ strategy_id }));
  assert.deepEqual(
    orderStrategies(shuffled).map((item) => item.strategy_id),
    STRATEGY_DISPLAY_ORDER.filter((strategyId) => shuffled.some((item) => item.strategy_id === strategyId)),
  );
});

test("only ranked research can render a position", () => {
  assert.equal(canShowStrategyRanking("ranked_research"), true);
  assert.equal(canShowStrategyRanking("observation_pool"), false);
});
