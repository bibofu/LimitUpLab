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
  FirstBoardCriticResponse,
  FirstBoardDiscoveryResponse,
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
  StockKLineBar,
  StockNewsFacts,
  StockPositionAssessment,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";
const inflightGetRequests = new Map<string, Promise<unknown>>();

/** Fetch JSON from the backend and surface non-2xx responses as errors. */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
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
  const pending = request<T>(path).finally(() => {
    if (inflightGetRequests.get(path) === pending) {
      inflightGetRequests.delete(path);
    }
  });
  inflightGetRequests.set(path, pending);
  return pending;
}

export function fetchMarketSummary() {
  return request<MarketSummary>("/api/market/overview");
}

export function fetchDragonTigerReview(tradeDate?: string) {
  const query = tradeDate ? `?trade_date=${encodeURIComponent(tradeDate)}` : "";
  return request<DragonTigerReviewResponse>(`/api/market/dragon-tiger${query}`);
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

export function fetchRecentLimitUpEvents(days = 5) {
  return request<LimitUpEvent[]>(`/api/limit-up/recent?days=${days}`);
}

export function fetchContinuationStats() {
  return request<ContinuationStat[]>("/api/analysis/continuation");
}

export function fetchDailyBoardPromotion(days = 5) {
  return request<DailyBoardPromotionStat[]>(
    `/api/analysis/daily-promotion?days=${days}`,
  );
}

export function fetchFailedRateStats() {
  return request<FailedRateStat[]>("/api/analysis/failed-rate");
}

export function fetchPostPerformanceStats() {
  return request<PostPerformanceStat[]>("/api/analysis/post-performance");
}

export function fetchStockEvent(symbol: string, tradeDate?: string) {
  const query = tradeDate ? `?trade_date=${encodeURIComponent(tradeDate)}` : "";
  return request<LimitUpEvent>(`/api/stocks/${symbol}/event${query}`);
}

export function fetchStockKLine(symbol: string, days = 60) {
  return dedupedGet<StockKLineBar[]>(`/api/stocks/${symbol}/kline?days=${days}`);
}

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

export function fetchStockNews(symbol: string, name?: string, limit = 3) {
  const params = new URLSearchParams({ days: "7", limit: String(limit) });
  if (name) {
    params.set("name", name);
  }
  return request<StockNewsFacts>(`/api/stocks/${symbol}/news?${params.toString()}`);
}

export function fetchStockPosition(symbol: string, tradeDate: string) {
  return request<StockPositionAssessment>(
    `/api/stocks/${symbol}/position?trade_date=${encodeURIComponent(tradeDate)}`,
  );
}

export function fetchStockLatestClose(symbol: string) {
  return request<StockCloseSnapshot>(`/api/stocks/${symbol}/latest-close`);
}

export function fetchStockTradingDayKLine(
  symbol: string,
  period = 5,
  tradeDate?: string,
) {
  const params = new URLSearchParams({ period: String(period) });
  if (tradeDate) {
    params.set("trade_date", tradeDate);
  }
  return dedupedGet<StockIntradayKLineBar[]>(
    `/api/stocks/${symbol}/trading-day-kline?${params.toString()}`,
  );
}

export function fetchFirstBoardRatings(tradeDate?: string, fullPool = false) {
  const params = new URLSearchParams();
  if (tradeDate) params.set("trade_date", tradeDate);
  if (fullPool) params.set("full_pool", "true");
  const query = params.size > 0 ? `?${params.toString()}` : "";
  return request<FirstBoardRatingsResponse>(`/api/agents/first-board-ratings${query}`);
}

export function fetchFirstBoardDiscovery(dataAsOf?: string) {
  const query = dataAsOf ? `?data_as_of=${encodeURIComponent(dataAsOf)}` : "";
  return request<FirstBoardDiscoveryResponse>(
    `/api/agents/first-board-discovery${query}`,
  );
}

export function fetchRecommendationIntelligence() {
  return request<RecommendationIntelligenceResponse>(
    "/api/agents/recommendation-intelligence",
  );
}

export function fetchFinanceNews(page = 1, pageSize = 10) {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  return request<FinanceNewsPage>(`/api/market/news?${params.toString()}`);
}

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

export function fetchDailyReviewSnapshots(limit = 20) {
  return request<DailyReviewSnapshotsResponse>(
    `/api/agents/review-snapshots?limit=${limit}`,
  );
}

export function fetchScoringErrorDiagnostic(endDate?: string, topK = 10) {
  const query = new URLSearchParams({ top_k: String(topK) });
  if (endDate) {
    query.set("end_date", endDate);
  }
  return request<ScoringErrorDiagnosticResponse>(
    `/api/agents/scoring-error-diagnostic?${query.toString()}`,
  );
}

export function fetchChatSessions(limit = 30) {
  return request<ChatSessionsResponse>(`/api/agents/chat/sessions?limit=${limit}`);
}

export function fetchChatSession(sessionId: string) {
  return request<ChatSessionDetail>(`/api/agents/chat/sessions/${sessionId}`);
}

export function createChatSession(title?: string) {
  return request<ChatSessionDetail>("/api/agents/chat/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title || null }),
  });
}

export function renameChatSession(sessionId: string, title: string) {
  return request<ChatSessionDetail>(`/api/agents/chat/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

export function deleteChatSession(sessionId: string) {
  return request<{ deleted: boolean }>(`/api/agents/chat/sessions/${sessionId}`, {
    method: "DELETE",
  });
}

export function fetchFirstBoardCritic(symbol: string, tradeDate?: string) {
  const params = new URLSearchParams({ symbol });
  if (tradeDate) {
    params.set("trade_date", tradeDate);
  }
  return request<FirstBoardCriticResponse>(
    `/api/agents/first-board-critic?${params.toString()}`,
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
