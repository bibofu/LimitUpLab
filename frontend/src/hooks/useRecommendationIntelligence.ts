import { useEffect, useState } from "react";

import { fetchRecommendationIntelligence } from "../api";
import type { RecommendationIntelligenceResponse } from "../types";


/**
 * Load and refresh candidate intelligence while keeping loading, failure and current-result
 * state together.
 */
export function useRecommendationIntelligence() {
  const [intelligence, setIntelligence] = useState<RecommendationIntelligenceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const refresh = () => {
      void fetchRecommendationIntelligence()
        .then((response) => {
          if (active) {
            setIntelligence(response);
            setError(null);
          }
        })
        .catch((caught: unknown) => {
          if (active) {
            setError(caught instanceof Error ? caught.message : "盘前动态榜加载失败");
          }
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    };
    refresh();
    const timer = window.setInterval(refresh, 60 * 1000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  return { intelligence, loading, error };
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
