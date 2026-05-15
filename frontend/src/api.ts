import type {
  ContinuationStat,
  FailedRateStat,
  LimitUpEvent,
  MarketSummary,
  PostPerformanceStat,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

async function request<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${response.statusText}`);
  }

  return response.json() as Promise<T>;
}

export function fetchMarketSummary() {
  return request<MarketSummary>("/api/market/overview");
}

export function fetchLimitUpEvents() {
  return request<LimitUpEvent[]>("/api/limit-up/events");
}

export function fetchFirstBoardEvents() {
  return request<LimitUpEvent[]>("/api/limit-up/first-board");
}

export function fetchContinuedBoardEvents() {
  return request<LimitUpEvent[]>("/api/limit-up/continued-board");
}

export function fetchFailedLimitUpEvents() {
  return request<LimitUpEvent[]>("/api/limit-up/failed");
}

export function fetchRecentLimitUpEvents(days = 3) {
  return request<LimitUpEvent[]>(`/api/limit-up/recent?days=${days}`);
}

export function fetchContinuationStats() {
  return request<ContinuationStat[]>("/api/analysis/continuation");
}

export function fetchFailedRateStats() {
  return request<FailedRateStat[]>("/api/analysis/failed-rate");
}

export function fetchPostPerformanceStats() {
  return request<PostPerformanceStat[]>("/api/analysis/post-performance");
}
