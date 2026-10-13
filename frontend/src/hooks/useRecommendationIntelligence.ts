import { recommendationSnapshot } from "../utils/marketSnapshots";
import { usePublicSnapshot } from "./usePublicSnapshot";
import type { RecommendationIntelligenceResponse } from "../types";


/**
 * Load and refresh candidate intelligence while keeping loading, failure and current-result
 * state together.
 */
export function useRecommendationIntelligence() {
  const state = usePublicSnapshot(recommendationSnapshot, true, 60_000);
  return { ...state, intelligence: state.data };
}


/**
 * Find the current intelligence item for the requested stock identity.
 */
export function recommendationIntelligenceFor(
  response: RecommendationIntelligenceResponse | null,
  strategy: "relay",
  symbol: string,
) {
  return response?.items.find(
    (item) => item.strategy === strategy && item.symbol === symbol,
  ) ?? null;
}
