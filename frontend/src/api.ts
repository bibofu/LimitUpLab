import type { ConsolidationPool } from "./consolidation";
import type {
  AgentChatRequest,
  AgentChatResponse,
  AgentChatStreamEvent,
  ChatSessionDetail,
  ChatSessionsResponse,
  DailyBoardPromotionStat,
  DailyReviewSnapshotsResponse,
  DragonTigerReviewResponse,
  ContinuationStat,
  FailedRateStat,
  FinanceNewsPage,
  FirstBoardRatingsResponse,
  LimitUpEvent,
  MarketSummary,
  PostPerformanceStat,
  RecommendationIntelligenceResponse,
  ReviewAgentReportResponse,
  ScoringErrorDiagnosticResponse,
  StockCloseSnapshot,
  StockDetailMarketData,
  StockIntradayKLineBar,
  StockIntradayHistoryResponse,
  StockKLineBar,
  StockNewsFacts,
  StockPositionAssessment,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";
const inflightGetRequests = new Map<string, Promise<unknown>>();
const resolvedGetRequests = new Map<
  string,
  { expiresAt: number; value: unknown }
>();

/** Fetch JSON from the backend and surface non-2xx responses as errors. */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    // The session cookie lets the server isolate history, memory and quotas by owner.
    credentials: "include",
    ...init,
  });

  if (!response.ok) {
    throw new Error(await responseErrorMessage(response, "请求失败"));
  }

  return response.json() as Promise<T>;
}

/** Reuse identical in-flight GETs, including React StrictMode development mounts. */
function dedupedGet<T>(path: string): Promise<T> {
  const existing = inflightGetRequests.get(path);
  if (existing) {
    return existing as Promise<T>;
  }
  const pending = request<T>(path).finally(/* Release request state after either success or failure. */ () => {
    if (inflightGetRequests.get(path) === pending) {
      inflightGetRequests.delete(path);
    }
  });
  inflightGetRequests.set(path, pending);
  return pending;
}

/** Reuse stable after-close facts across detail-page remounts in this browser tab. */
async function cachedGet<T>(path: string, ttlMs: number): Promise<T> {
  const cached = resolvedGetRequests.get(path);
  if (cached && cached.expiresAt > Date.now()) {
    return cached.value as T;
  }
  if (cached) resolvedGetRequests.delete(path);
  const value = await dedupedGet<T>(path);
  if (resolvedGetRequests.size >= 128) {
    const oldestKey = resolvedGetRequests.keys().next().value;
    if (oldestKey) resolvedGetRequests.delete(oldestKey);
  }
  resolvedGetRequests.set(path, { expiresAt: Date.now() + ttlMs, value });
  return value;
}

/**
 * Fetch market summary from the backend using the supplied query scope; return the typed
 * response promise.
 */
export function fetchMarketSummary() {
  return request<MarketSummary>("/api/market/overview");
}

/**
 * Fetch consolidation pool from the backend using the supplied query scope; return the typed
 * response promise.
 */
export function fetchConsolidationPool(dataAsOf?: string, strategy: ConsolidationPool["strategy"] = "consolidation") {
  const query = `?strategy=${strategy}${dataAsOf ? `&data_as_of=${encodeURIComponent(dataAsOf)}` : ""}`;
  return dedupedGet<ConsolidationPool>(`/api/strategies/consolidation${query}`);
}

/**
 * Fetch dragon tiger review from the backend using the supplied query scope; return the typed
 * response promise.
 */
export function fetchDragonTigerReview(tradeDate?: string) {
  const query = tradeDate ? `?trade_date=${encodeURIComponent(tradeDate)}` : "";
  return request<DragonTigerReviewResponse>(`/api/market/dragon-tiger${query}`);
}

/**
 * Fetch limit up events from the backend using the supplied query scope; return the typed
 * response promise.
 */
export function fetchLimitUpEvents() {
  return request<LimitUpEvent[]>("/api/limit-up/events");
}

/**
 * Fetch first board events from the backend using the supplied query scope; return the typed
 * response promise.
 */
export function fetchFirstBoardEvents() {
  return request<LimitUpEvent[]>("/api/limit-up/first-board");
}

/**
 * Fetch continued board events from the backend using the supplied query scope; return the
 * typed response promise.
 */
export function fetchContinuedBoardEvents() {
  return request<LimitUpEvent[]>("/api/limit-up/continued-board");
}

/**
 * Fetch failed limit up events from the backend using the supplied query scope; return the
 * typed response promise.
 */
export function fetchFailedLimitUpEvents() {
  return request<LimitUpEvent[]>("/api/limit-up/failed");
}

/**
 * Fetch recent limit up events from the backend using the supplied query scope; return the
 * typed response promise.
 */
export function fetchRecentLimitUpEvents(days = 7) {
  return request<LimitUpEvent[]>(`/api/limit-up/recent?days=${days}`);
}

/**
 * Fetch continuation stats from the backend using the supplied query scope; return the typed
 * response promise.
 */
export function fetchContinuationStats() {
  return request<ContinuationStat[]>("/api/analysis/continuation");
}

/**
 * Fetch daily board promotion from the backend using the supplied query scope; return the
 * typed response promise.
 */
export function fetchDailyBoardPromotion(days = 5) {
  return request<DailyBoardPromotionStat[]>(
    `/api/analysis/daily-promotion?days=${days}`,
  );
}

/**
 * Fetch failed rate stats from the backend using the supplied query scope; return the typed
 * response promise.
 */
export function fetchFailedRateStats() {
  return request<FailedRateStat[]>("/api/analysis/failed-rate");
}

/**
 * Fetch post performance stats from the backend using the supplied query scope; return the
 * typed response promise.
 */
export function fetchPostPerformanceStats() {
  return request<PostPerformanceStat[]>("/api/analysis/post-performance");
}

/**
 * Fetch stock event from the backend using the supplied query scope; return the typed response
 * promise.
 */
export function fetchStockEvent(symbol: string) {
  return request<LimitUpEvent>(`/api/stocks/${symbol}/event`);
}

/**
 * Fetch stock kline from the backend using the supplied query scope; return the typed response
 * promise.
 */
export function fetchStockKLine(symbol: string, days = 60) {
  return dedupedGet<StockKLineBar[]>(`/api/stocks/${symbol}/kline?days=${days}`);
}

/**
 * Fetch stock market data from the backend using the supplied query scope; return the typed
 * response promise.
 */
export function fetchStockMarketData(
  symbol: string,
  days = 60,
  positionTradeDate?: string,
) {
  const params = new URLSearchParams({ days: String(days) });
  if (positionTradeDate) {
    params.set("position_trade_date", positionTradeDate);
  }
  return dedupedGet<StockDetailMarketData>(
    `/api/stocks/${symbol}/market-data?${params.toString()}`,
  );
}

/**
 * Fetch stock news from the backend using the supplied query scope; return the typed response
 * promise.
 */
export function fetchStockNews(symbol: string, name?: string, limit = 3) {
  const params = new URLSearchParams({ days: "7", limit: String(limit) });
  if (name) {
    params.set("name", name);
  }
  return request<StockNewsFacts>(`/api/stocks/${symbol}/news?${params.toString()}`);
}

/**
 * Fetch stock position from the backend using the supplied query scope; return the typed
 * response promise.
 */
export function fetchStockPosition(symbol: string, tradeDate: string) {
  return request<StockPositionAssessment>(
    `/api/stocks/${symbol}/position?trade_date=${encodeURIComponent(tradeDate)}`,
  );
}

/**
 * Fetch stock latest close from the backend using the supplied query scope; return the typed
 * response promise.
 */
export function fetchStockLatestClose(symbol: string) {
  return request<StockCloseSnapshot>(`/api/stocks/${symbol}/latest-close`);
}

/**
 * Fetch stock trading day kline from the backend using the supplied query scope; return the
 * typed response promise.
 */
export function fetchStockTradingDayKLine(
  symbol: string,
  period = 5,
  tradeDate?: string,
) {
  const params = new URLSearchParams({ period: String(period) });
  if (tradeDate) {
    params.set("trade_date", tradeDate);
  }
  return cachedGet<StockIntradayKLineBar[]>(
    `/api/stocks/${symbol}/trading-day-kline?${params.toString()}`,
    30 * 60 * 1000,
  );
}

/**
 * Fetch stock intraday history from the backend using the supplied query scope; return the
 * typed response promise.
 */
export function fetchStockIntradayHistory(
  symbol: string,
  days = 5,
  period = 1,
  endDate?: string,
) {
  const params = new URLSearchParams({
    days: String(days),
    period: String(period),
  });
  if (endDate) {
    params.set("end_date", endDate);
  }
  return cachedGet<StockIntradayHistoryResponse>(
    `/api/stocks/${symbol}/intraday-history?${params.toString()}`,
    30 * 60 * 1000,
  );
}

/**
 * Fetch first board ratings from the backend using the supplied query scope; return the typed
 * response promise.
 */
export function fetchFirstBoardRatings(tradeDate?: string, fullPool = false) {
  const params = new URLSearchParams();
  if (tradeDate) params.set("trade_date", tradeDate);
  if (fullPool) params.set("full_pool", "true");
  const query = params.size > 0 ? `?${params.toString()}` : "";
  return request<FirstBoardRatingsResponse>(`/api/agents/first-board-ratings${query}`);
}

/**
 * Fetch recommendation intelligence from the backend using the supplied query scope; return
 * the typed response promise.
 */
export function fetchRecommendationIntelligence() {
  return request<RecommendationIntelligenceResponse>(
    "/api/agents/recommendation-intelligence",
  );
}

/**
 * Fetch finance news from the backend using the supplied query scope; return the typed
 * response promise.
 */
export function fetchFinanceNews(page = 1, pageSize = 10) {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  return request<FinanceNewsPage>(`/api/market/news?${params.toString()}`);
}

/**
 * Fetch review agent report from the backend using the supplied query scope; return the typed
 * response promise.
 */
export function fetchReviewAgentReport(params?: {
  start_date?: string;
  end_date?: string;
  min_score?: number;
  top_per_day?: number;
  follow_days?: number;
  use_llm?: boolean;
}) {
  const query = new URLSearchParams();
  if (params?.start_date) {
    query.set("start_date", params.start_date);
  }
  if (params?.end_date) {
    query.set("end_date", params.end_date);
  }
  if (params?.min_score !== undefined) {
    query.set("min_score", String(params.min_score));
  }
  if (params?.top_per_day !== undefined) {
    query.set("top_per_day", String(params.top_per_day));
  }
  if (params?.follow_days !== undefined) {
    query.set("follow_days", String(params.follow_days));
  }
  if (params?.use_llm !== undefined) {
    query.set("use_llm", params.use_llm ? "true" : "false");
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return request<ReviewAgentReportResponse>(`/api/agents/review-report${suffix}`);
}

/**
 * Fetch daily review snapshots from the backend using the supplied query scope; return the
 * typed response promise.
 */
export function fetchDailyReviewSnapshots(limit = 20) {
  return request<DailyReviewSnapshotsResponse>(
    `/api/agents/review-snapshots?limit=${limit}`,
  );
}

/**
 * Fetch scoring error diagnostic from the backend using the supplied query scope; return the
 * typed response promise.
 */
export function fetchScoringErrorDiagnostic(endDate?: string, topK = 10) {
  const query = new URLSearchParams({ top_k: String(topK) });
  if (endDate) {
    query.set("end_date", endDate);
  }
  return request<ScoringErrorDiagnosticResponse>(
    `/api/agents/scoring-error-diagnostic?${query.toString()}`,
  );
}

/**
 * Fetch chat sessions from the backend using the supplied query scope; return the typed
 * response promise.
 */
export function fetchChatSessions(limit = 30) {
  return request<ChatSessionsResponse>(`/api/agents/chat/sessions?limit=${limit}`);
}

/**
 * Fetch chat session from the backend using the supplied query scope; return the typed
 * response promise.
 */
export function fetchChatSession(sessionId: string) {
  return request<ChatSessionDetail>(`/api/agents/chat/sessions/${sessionId}`);
}

/**
 * Create a persisted conversation for the current anonymous owner.
 */
export function createChatSession(title?: string) {
  return request<ChatSessionDetail>("/api/agents/chat/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title || null }),
  });
}

/**
 * Persist the supplied title for the selected conversation.
 */
export function renameChatSession(sessionId: string, title: string) {
  return request<ChatSessionDetail>(`/api/agents/chat/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

/**
 * Delete the selected persisted conversation through the owner-scoped API.
 */
export function deleteChatSession(sessionId: string) {
  return request<{ deleted: boolean }>(`/api/agents/chat/sessions/${sessionId}`, {
    method: "DELETE",
  });
}

/**
 * Submit the non-streaming chat request and return the complete structured response.
 */
export function sendAgentChatMessage(payload: AgentChatRequest) {
  return request<AgentChatResponse>("/api/agents/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

/**
 * Read the POST response as an SSE stream. Network chunks may split records, so buffer until a
 * blank-line delimiter and return only the authoritative completed response.
 */
export async function streamAgentChatMessage(
  payload: AgentChatRequest,
  onEvent: (event: AgentChatStreamEvent) => void,
): Promise<AgentChatResponse> {
  const response = await fetch(`${API_BASE_URL}/api/agents/chat/stream`, {
    method: "POST",
    credentials: "include",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok || !response.body) {
    throw new Error(await responseErrorMessage(response, "Agent 请求失败"));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed: AgentChatResponse | null = null;

  /**
   * Parse one complete SSE record and dispatch it; keep final completion separate from
   * progress and draft text.
   */
  function consumeRecord(record: string) {
    let eventName = "message";
    const dataLines: string[] = [];
    for (const line of record.split("\n")) {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
    if (dataLines.length === 0) {
      return;
    }
    const event = {
      event: eventName,
      data: JSON.parse(dataLines.join("\n")),
    } as AgentChatStreamEvent;
    if (event.event === "error") {
      throw new Error(event.data.message);
    }
    if (event.event === "completed") {
      completed = event.data;
    }
    onEvent(event);
  }

  while (true) {
    // A network read is not an SSE message boundary. Preserve partial records
    // between reads, and let TextDecoder preserve split UTF-8 characters too.
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      consumeRecord(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }
    if (done) {
      if (buffer.trim()) {
        consumeRecord(buffer);
      }
      break;
    }
  }

  if (!completed) {
    // Partial prose is not a successful answer: validation or persistence may
    // still have been running when the connection ended.
    throw new Error("Agent stream ended before completion");
  }
  return completed;
}

/** Prefer a safe backend detail such as the user-facing 429 explanation. */
async function responseErrorMessage(
  response: Response,
  fallback: string,
): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string" && payload.detail.trim()) {
      return payload.detail.trim();
    }
  } catch {
    // Non-JSON gateway errors fall through to the status-based message.
  }
  return `${fallback}（${response.status} ${response.statusText}）`;
}
