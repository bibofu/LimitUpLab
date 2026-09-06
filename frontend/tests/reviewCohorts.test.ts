import assert from "node:assert/strict";
import test from "node:test";
import { summarizeReviewPromotion } from "../src/reviewCohorts.ts";

test("switching cohorts excludes other dates and immature outcomes from all metrics", () => {
  const comparisons = [
    { trade_date: "2026-09-01", next_trade_date: "2026-09-02", outcome_ready: true,
      top_pick_sample_size: 10, top_pick_promoted_count: 2, top_pick_promotion_rate: 0.2,
      market_first_board_sample_size: 50, market_promoted_count: 5, market_promotion_rate: 0.1, promotion_rate_delta: 0.1 },
    { trade_date: "2026-09-02", next_trade_date: "2026-09-03", outcome_ready: true,
      top_pick_sample_size: 10, top_pick_promoted_count: 9, top_pick_promotion_rate: 0.9,
      market_first_board_sample_size: 50, market_promoted_count: 20, market_promotion_rate: 0.4, promotion_rate_delta: 0.5 },
    { trade_date: "2026-09-03", next_trade_date: null, outcome_ready: false,
      top_pick_sample_size: 10, top_pick_promoted_count: 0, top_pick_promotion_rate: null,
      market_first_board_sample_size: 50, market_promoted_count: 0, market_promotion_rate: null, promotion_rate_delta: null },
  ];
  const result = summarizeReviewPromotion([{ trade_date: "2026-09-01" }, { trade_date: "2026-09-03" }], comparisons);
  assert.equal(result.top_pick_promotion_sample_size, 10);
  assert.equal(result.top_pick_promotion_rate, 0.2);
  assert.equal(result.market_promotion_sample_size, 50);
  assert.equal(result.market_promotion_rate, 0.1);
  assert.equal(result.promotion_rate_delta, 0.1);
  assert.equal(summarizeReviewPromotion([], comparisons).top_pick_promotion_rate, null);
});
