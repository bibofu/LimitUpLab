import type { ReviewAgentPick, ReviewPromotionComparison } from "./types";

/** Aggregate only the dates in the visible cohort, never the report-wide totals. */
export function summarizeReviewPromotion(
  picks: Pick<ReviewAgentPick, "trade_date">[],
  comparisons: ReviewPromotionComparison[],
) {
  const dates = new Set(picks.map((pick) => pick.trade_date));
  const selected = comparisons.filter((item) => dates.has(item.trade_date) && item.outcome_ready);
  const total = (key: "top_pick_sample_size" | "top_pick_promoted_count" | "market_first_board_sample_size" | "market_promoted_count") =>
    selected.reduce((sum, item) => sum + item[key], 0);
  const topSamples = total("top_pick_sample_size");
  const topPromoted = total("top_pick_promoted_count");
  const marketSamples = total("market_first_board_sample_size");
  const marketPromoted = total("market_promoted_count");
  const topRate = topSamples ? topPromoted / topSamples : null;
  const marketRate = marketSamples ? marketPromoted / marketSamples : null;
  return {
    top_pick_promotion_sample_size: topSamples,
    top_pick_promoted_count: topPromoted,
    top_pick_promotion_rate: topRate,
    market_promotion_sample_size: marketSamples,
    market_promoted_count: marketPromoted,
    market_promotion_rate: marketRate,
    promotion_rate_delta: topRate !== null && marketRate !== null ? topRate - marketRate : null,
  };
}
