import { marketSummarySnapshot } from "../utils/marketSnapshots";
import { usePublicSnapshot } from "./usePublicSnapshot";

export function useMarketSummary({ enabled }: { enabled: boolean }) {
  return usePublicSnapshot(marketSummarySnapshot, enabled);
}

export { refreshRecommendationSnapshots } from "../utils/marketSnapshots";
