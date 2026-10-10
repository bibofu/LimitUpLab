import { useCallback, useEffect, useSyncExternalStore } from "react";
import { fetchReviewAgentReport } from "../api";
import { createReviewReportResource } from "../utils/reviewReportResource";
export type { ReviewSummaryStatus } from "../utils/reviewReportResource";

// The page uses a fixed Top10 / D+5 scope; each cutoff date has its own state and request.
const reviews = createReviewReportResource(fetchReviewAgentReport);

export function refreshReviewFacts() {
  return reviews.refreshFacts();
}

export function useReviewReport(endDate: string) {
  const subscribe = useCallback((listener: () => void) => reviews.subscribe(endDate, listener), [endDate]);
  const snapshot = useCallback(() => reviews.getState(endDate), [endDate]);
  const state = useSyncExternalStore(subscribe, snapshot, snapshot);
  useEffect(() => { void reviews.load(endDate); }, [endDate]);
  return {
    ...state,
    loading: state.loading || (!state.report && !state.error),
    retry: () => { void reviews.load(endDate, true); },
    regenerate: () => { void reviews.regenerate(endDate); },
  };
}
