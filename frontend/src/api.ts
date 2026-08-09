import type {
  AgentChatRequest,
  AgentChatResponse,
  ContinuationStat,
  FailedRateStat,
  FirstBoardRatingsResponse,
  LimitUpEvent,
  MarketSummary,
  PostPerformanceStat,
  SimilarFirstBoardCasesResponse,
  StockCloseSnapshot,
  StockIntradayKLineBar,
  StockKLineBar,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

/** Fetch JSON from the backend and surface non-2xx responses as errors. */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, init);

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

export function fetchStockKLine(symbol: string, days = 5) {
  return request<StockKLineBar[]>(`/api/stocks/${symbol}/kline?days=${days}`);
}

export function fetchStockLatestClose(symbol: string) {
  return request<StockCloseSnapshot>(`/api/stocks/${symbol}/latest-close`);
}

export function fetchStockTradingDayKLine(symbol: string, period = 5) {
  return request<StockIntradayKLineBar[]>(
    `/api/stocks/${symbol}/trading-day-kline?period=${period}`,
  );
}

export function fetchFirstBoardRatings(tradeDate?: string) {
  const query = tradeDate ? `?trade_date=${tradeDate}` : "";
  return request<FirstBoardRatingsResponse>(`/api/agents/first-board-ratings${query}`);
}

export function fetchFirstBoardSimilarCases(symbol: string, tradeDate: string, limit = 5) {
  const params = new URLSearchParams({
    symbol,
    trade_date: tradeDate,
    limit: String(limit),
  });
  return request<SimilarFirstBoardCasesResponse>(
    `/api/agents/first-board-similar-cases?${params.toString()}`,
  );
}

export function sendAgentChatMessage(payload: AgentChatRequest) {
  return request<AgentChatResponse>("/api/agents/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}
