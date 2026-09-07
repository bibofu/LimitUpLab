import assert from "node:assert/strict";
import test from "node:test";
import { selectReviewCohort, summarizeReviewPromotion } from "../src/reviewCohorts.ts";

test("default cohort skips a current-day-only group that has no review card", () => {
  const cohorts = ["close_baseline/v5", "legacy_close/v5", "historical_backtest/v5"];
  const picks = [
    { cohort: cohorts[0], tradeDate: "2026-09-07" },
    { cohort: cohorts[1], tradeDate: "2026-09-04" },
    { cohort: cohorts[2], tradeDate: "2026-09-03" },
  ];
  assert.equal(selectReviewCohort(cohorts, picks, "2026-09-07"), cohorts[1]);
  assert.equal(selectReviewCohort(cohorts, picks, "2026-09-07", cohorts[2]), cohorts[2]);
});

test("default cohort keeps the first authoritative group when it has a mature date", () => {
  const cohorts = ["premarket_final/v5", "legacy_close/v5"];
  const picks = [
    { cohort: cohorts[0], tradeDate: "2026-09-04" },
    { cohort: cohorts[1], tradeDate: "2026-09-03" },
  ];
  assert.equal(selectReviewCohort(cohorts, picks, "2026-09-07"), cohorts[0]);
});

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
