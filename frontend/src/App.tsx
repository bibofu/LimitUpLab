import {
  Trash2,
  BarChart3,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  Flame,
  GitBranch,
  Landmark,
  Layers3,
  LineChart,
  LoaderCircle,
  MapPin,
  MessageCircle,
  Minus,
  Newspaper,
  PanelLeft,
  Pencil,
  Plus,
  RefreshCcw,
  Send,
  ShieldAlert,
  TrendingUp,
  X,
} from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";

import { AgentAnswerMarkdown } from "./components/AgentAnswerMarkdown";
import {
  MarketKLineChart,
  type MarketCandleBar,
} from "./components/MarketKLineChart";
import {
  deleteChatSession,
  createChatSession,
  fetchContinuedBoardEvents,
  fetchChatSession,
  fetchChatSessions,
  fetchDailyBoardPromotion,
  fetchDailyReviewSnapshots,
  fetchDragonTigerReview,
  fetchReviewAgentReport,
  fetchFirstBoardCritic,
  fetchFirstBoardDiscovery,
  fetchFirstBoardRatings,
  fetchFinanceNews,
  fetchRecommendationIntelligence,
  fetchFailedLimitUpEvents,
  fetchFirstBoardEvents,
  fetchMarketSummary,
  fetchRatingBacktest,
  fetchRatingEvaluation,
  fetchScoringErrorDiagnostic,
  fetchRecentLimitUpEvents,
  fetchStockKLine,
  fetchStockEvent,
  fetchStockLatestClose,
  fetchStockNews,
  fetchStockPosition,
  fetchStockTradingDayKLine,
  renameChatSession,
  streamAgentChatMessage,
} from "./api";
import type {
  AgentChatStreamStage,
  AgentStockMention,
  AgentEvaluationResponse,
  ChatSessionDetail,
  ChatSessionMessage,
  ChatSessionSummary,
  DailyBoardPromotionStat,
  DailyReviewSnapshotSummary,
  DragonTigerReviewResponse,
  FirstBoardCriticResponse,
  FirstBoardDiscoveryPattern,
  FirstBoardDiscoveryResponse,
  FirstBoardRating,
  FirstBoardRatingsResponse,
  FinanceNewsPage,
  FinanceNewsItem,
  LimitUpEvent,
  MarketSummary,
  RatingBacktestResponse,
  RecommendationIntelligenceItem,
  RecommendationIntelligenceResponse,
  ReviewAgentPick,
  ReviewAgentReportResponse,
  ReviewPromotionComparison,
  ScoringErrorDiagnosticResponse,
  StockCloseSnapshot,
  StockIntradayKLineBar,
  StockKLineBar,
  StockNewsFacts,
  StockPositionAssessment,
} from "./types";

type ViewKey = "overview" | "recommendation" | "review" | "pool" | "first" | "continued" | "failed" | "recent";
type StockListViewKey = "first" | "continued" | "failed";

interface DashboardData {
  summary: MarketSummary;
  firstBoard: LimitUpEvent[];
  continuedBoard: LimitUpEvent[];
  failed: LimitUpEvent[];
  recent: LimitUpEvent[];
  firstBoardRatings: FirstBoardRatingsResponse;
  dailyBoardPromotion: DailyBoardPromotionStat[];
  ratingBacktest: RatingBacktestResponse;
  ratingEvaluation: AgentEvaluationResponse;
}

interface ChatMessage {
  id: string;
  role: "user" | "agent";
  content: string;
  stockMentions: AgentStockMention[];
  status?: "success" | "error";
}

const ACTIVE_CHAT_SESSION_STORAGE_KEY = "limituplab.activeChatSession";

function restoredChatMessage(message: ChatSessionMessage): ChatMessage {
  return {
    id: message.message_id,
    role: message.role === "assistant" ? "agent" : "user",
    content: message.content,
    stockMentions: stockMentionsFromMetadata(message.metadata),
    status: message.status,
  };
}

function stockMentionsFromMetadata(metadata: Record<string, unknown>): AgentStockMention[] {
  const value = metadata.stock_mentions;
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is AgentStockMention => {
    if (!item || typeof item !== "object") {
      return false;
    }
    const candidate = item as Record<string, unknown>;
    return (
      typeof candidate.name === "string"
      && typeof candidate.symbol === "string"
      && (candidate.trade_date === null || typeof candidate.trade_date === "string")
    );
  });
}

function sessionTimeLabel(value: string) {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return "";
  }
  const now = new Date();
  if (timestamp.toDateString() === now.toDateString()) {
    return timestamp.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }
  return timestamp.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

const viewMeta: Record<ViewKey, { title: string; eyebrow: string }> = {
  overview: { title: "短线市场概况", eyebrow: "Overview" },
  recommendation: { title: "盘前推荐", eyebrow: "Pre-market Picks" },
  review: { title: "市场复盘", eyebrow: "Review" },
  pool: { title: "涨停池", eyebrow: "Limit-Up Pool" },
  first: { title: "首板票", eyebrow: "First Board" },
  continued: { title: "连板票", eyebrow: "Continued Board" },
  failed: { title: "炸板票", eyebrow: "Failed Limit-Up" },
  recent: { title: "近五日涨停票复盘", eyebrow: "Recent Limit-Up" },
};

function AgentChatDock({
  tradeDate,
  symbol,
}: {
  tradeDate: string;
  symbol?: string;
}) {
  /** Provide a lightweight tool-grounded Agent chat entry point. */

  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [streamStage, setStreamStage] = useState<AgentChatStreamStage>("planning");
  const [streamStatus, setStreamStatus] = useState("正在理解问题并规划工具");
  const [error, setError] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [sessionPanelOpen, setSessionPanelOpen] = useState(false);
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const initializedSessions = useRef(false);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const isConversationActive = sending || messages.length > 0;

  useEffect(() => {
    if (initializedSessions.current) {
      return;
    }
    initializedSessions.current = true;
    void initializeChatSessions();
  }, []);

  useEffect(() => {
    if (!sending) {
      setElapsedMs(0);
      return undefined;
    }
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      setElapsedMs(Date.now() - startedAt);
    }, 100);
    return () => window.clearInterval(timer);
  }, [sending]);

  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      container.scrollTop = container.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [messages, sending]);

  function applyChatSession(detail: ChatSessionDetail) {
    setSessionId(detail.session_id);
    window.localStorage.setItem(ACTIVE_CHAT_SESSION_STORAGE_KEY, detail.session_id);
    setMessages(detail.messages.map(restoredChatMessage));
    setError(null);
  }

  async function initializeChatSessions() {
    setSessionLoading(true);
    try {
      const response = await fetchChatSessions();
      if (response.sessions.length > 0) {
        const savedSessionId = window.localStorage.getItem(ACTIVE_CHAT_SESSION_STORAGE_KEY);
        const targetSession = response.sessions.find(
          (item) => item.session_id === savedSessionId,
        ) ?? response.sessions[0];
        const detail = await fetchChatSession(targetSession.session_id);
        setSessions(response.sessions);
        applyChatSession(detail);
      } else {
        const created = await createChatSession();
        setSessions([created]);
        applyChatSession(created);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "会话加载失败");
    } finally {
      setSessionLoading(false);
    }
  }

  async function refreshChatSessions() {
    const response = await fetchChatSessions();
    setSessions(response.sessions);
    return response.sessions;
  }

  async function openChatSession(targetSessionId: string) {
    if (sending || targetSessionId === sessionId) {
      setSessionPanelOpen(false);
      return;
    }
    setSessionLoading(true);
    try {
      const detail = await fetchChatSession(targetSessionId);
      applyChatSession(detail);
      setSessionPanelOpen(false);
      setEditingSessionId(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "会话恢复失败");
    } finally {
      setSessionLoading(false);
    }
  }

  async function startNewChatSession() {
    if (sending) {
      return;
    }
    setSessionLoading(true);
    try {
      const created = await createChatSession();
      setSessions((current) => [created, ...current]);
      applyChatSession(created);
      setSessionPanelOpen(false);
      setEditingSessionId(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "新建会话失败");
    } finally {
      setSessionLoading(false);
    }
  }

  async function saveSessionTitle(targetSessionId: string) {
    const title = editingTitle.trim();
    if (!title) {
      return;
    }
    try {
      const updated = await renameChatSession(targetSessionId, title);
      setSessions((current) => current.map((item) => (
        item.session_id === targetSessionId ? updated : item
      )));
      setEditingSessionId(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "会话重命名失败");
    }
  }

  async function deleteSession(targetSessionId: string) {
    if (sending) {
      return;
    }
    const targetSession = sessions.find((item) => item.session_id === targetSessionId);
    const confirmed = window.confirm(
      `确定删除会话“${targetSession?.title ?? "未命名会话"}”吗？删除后无法恢复。`,
    );
    if (!confirmed) {
      return;
    }
    setSessionLoading(true);
    try {
      await deleteChatSession(targetSessionId);
      const remaining = await refreshChatSessions();
      if (targetSessionId === sessionId) {
        if (remaining.length > 0) {
          const detail = await fetchChatSession(remaining[0].session_id);
          applyChatSession(detail);
        } else {
          const created = await createChatSession();
          setSessions([created]);
          applyChatSession(created);
        }
      }
      setEditingSessionId(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "会话删除失败");
    } finally {
      setSessionLoading(false);
    }
  }

  async function sendMessage(prompt?: string) {
    const trimmed = (prompt ?? message).trim();
    if (!trimmed || sending || sessionLoading || !sessionId) {
      return;
    }

    const userMessageId = `msg_${Date.now()}_${Math.random().toString(16).slice(2)}`;
    const userMessage: ChatMessage = {
      id: userMessageId,
      role: "user",
      content: trimmed,
      stockMentions: [],
    };
    setMessages((current) => [
      ...current,
      userMessage,
    ]);
    setMessage("");
    setSending(true);
    setStreamStage("planning");
    setStreamStatus("正在理解问题并规划工具");
    setError(null);

    try {
      const agentMessageId = `agent-${Date.now()}`;
      let receivedAnswer = false;
      const response = await streamAgentChatMessage({
        session_id: sessionId,
        message_id: userMessageId,
        message: trimmed,
        intent_hint: inferChatIntent(trimmed),
        symbol,
        page_context: {
          page: symbol ? "stock_detail" : "dashboard",
        },
      }, (event) => {
        if (event.event === "progress") {
          setStreamStage(event.data.stage);
          setStreamStatus(event.data.message);
          return;
        }
        if (event.event === "answer_delta") {
          const delta = event.data.delta;
          setStreamStage("answering");
          setStreamStatus("正在生成回答");
          setMessages((current) => {
            const existing = current.find((item) => item.id === agentMessageId);
            if (existing) {
              return current.map((item) => (
                item.id === agentMessageId
                  ? { ...item, content: item.content + delta }
                  : item
              ));
            }
            receivedAnswer = true;
            return [
              ...current,
              { id: agentMessageId, role: "agent", content: delta, stockMentions: [] },
            ];
          });
        }
      });
      setMessages((current) => {
        const existing = current.find((item) => item.id === agentMessageId);
        if (existing || receivedAnswer) {
          return current.map((item) => (
            item.id === agentMessageId
              ? {
                  ...item,
                  content: response.answer,
                  stockMentions: response.stock_mentions,
                }
              : item
          ));
        }
        return [
          ...current,
          {
            id: agentMessageId,
            role: "agent",
            content: response.answer,
            stockMentions: response.stock_mentions,
          },
        ];
      });
      void refreshChatSessions();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Agent 回答失败");
    } finally {
      setSending(false);
    }
  }

  const activeSession = sessions.find((item) => item.session_id === sessionId);

  return (
    <div className="agent-chat-dock">
      <div className={`agent-chat-workspace ${sessionPanelOpen ? "session-panel-open" : ""}`}>
        <aside aria-label="历史会话" className="chat-session-sidebar">
          <header>
            <div>
              <strong>历史会话</strong>
              <small>{sessions.length}</small>
            </div>
            <button
              aria-label="新建会话"
              className="icon-button compact"
              disabled={sending || sessionLoading}
              onClick={() => void startNewChatSession()}
              title="新建会话"
              type="button"
            >
              <Plus size={16} />
            </button>
          </header>

          <div className="chat-session-list">
            {sessionLoading && sessions.length === 0 ? (
              <div className="chat-session-loading">
                <LoaderCircle aria-hidden="true" size={16} />
                <span>正在加载会话</span>
              </div>
            ) : null}
            {sessions.map((session) => (
              <div
                className={`chat-session-item ${session.session_id === sessionId ? "active" : ""}`}
                key={session.session_id}
              >
                {editingSessionId === session.session_id ? (
                  <form
                    className="chat-session-rename"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void saveSessionTitle(session.session_id);
                    }}
                  >
                    <input
                      aria-label="会话标题"
                      autoFocus
                      maxLength={80}
                      onChange={(event) => setEditingTitle(event.target.value)}
                      value={editingTitle}
                    />
                    <button aria-label="保存标题" title="保存" type="submit">
                      <Check size={14} />
                    </button>
                    <button
                      aria-label="取消重命名"
                      onClick={() => setEditingSessionId(null)}
                      title="取消"
                      type="button"
                    >
                      <X size={14} />
                    </button>
                  </form>
                ) : (
                  <>
                    <button
                      className="chat-session-select"
                      disabled={sending}
                      onClick={() => void openChatSession(session.session_id)}
                      type="button"
                    >
                      <span>
                        <strong>{session.title}</strong>
                        <time dateTime={session.updated_at}>{sessionTimeLabel(session.updated_at)}</time>
                      </span>
                      <small>{session.last_message_preview ?? "暂无消息"}</small>
                    </button>
                    <div className="chat-session-actions">
                      {session.session_id === sessionId ? (
                        <button
                          aria-label="重命名当前会话"
                          onClick={() => {
                            setEditingSessionId(session.session_id);
                            setEditingTitle(session.title);
                          }}
                          title="重命名"
                          type="button"
                        >
                          <Pencil size={13} />
                        </button>
                      ) : null}
                      <button
                        aria-label={`删除会话 ${session.title}`}
                        disabled={sending || sessionLoading}
                        onClick={() => void deleteSession(session.session_id)}
                        title="删除会话"
                        type="button"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>
        </aside>

        <section className={`agent-chat-panel ${isConversationActive ? "is-active" : "is-idle"}`}>
        <header>
          <div>
            <MessageCircle size={18} />
            <strong>{activeSession?.title || "首板 Agent 工作台"}</strong>
          </div>
          <div className="agent-chat-header-actions">
            <button
              aria-label={sessionPanelOpen ? "关闭历史会话" : "打开历史会话"}
              className="icon-button compact session-history-toggle"
              onClick={() => setSessionPanelOpen((current) => !current)}
              title={sessionPanelOpen ? "关闭历史会话" : "历史会话"}
              type="button"
            >
              <PanelLeft size={16} />
            </button>
            <div className="agent-chat-context">
              <span>{tradeDate}</span>
              {symbol ? <span>{symbol}</span> : <span>全市场首板</span>}
            </div>
          </div>
        </header>

        <div
          aria-live="polite"
          className="agent-chat-messages"
          ref={messagesContainerRef}
        >
          {messages.map((item) => (
            <article
              className={`chat-message chat-${item.role} ${item.status === "error" ? "chat-error" : ""}`}
              key={item.id}
            >
              {item.role === "agent" ? (
                <div className="chat-markdown">
                  <AgentAnswerMarkdown
                    content={item.content}
                    stockMentions={item.stockMentions}
                  />
                </div>
              ) : (
                <p>{item.content}</p>
              )}
            </article>
          ))}
          {sending ? (
            <div className="chat-progress" role="status">
              <LoaderCircle aria-hidden="true" size={18} />
              <div>
                <strong>
                  {streamStage === "answering" ? "Agent 输出中" : "Agent 执行中"}
                  {" · "}{(elapsedMs / 1000).toFixed(1)}s
                </strong>
                <span>{streamStatus}</span>
              </div>
            </div>
          ) : null}
          {error ? <div className="chat-state error">{error}</div> : null}
        </div>

        <div className="agent-chat-prompts">
          {[
            "总结一下今天首板",
            "今天市场环境如何",
            "有哪些一进二候选推荐",
          ].map((prompt) => (
            <button
              disabled={sending || sessionLoading || !sessionId}
              key={prompt}
              type="button"
              onClick={() => void sendMessage(prompt)}
            >
              {prompt}
            </button>
          ))}
        </div>

        <form
          className="agent-chat-input"
          onSubmit={(event) => {
            event.preventDefault();
            void sendMessage();
          }}
        >
          <input
            disabled={sessionLoading || !sessionId}
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder={symbol ? "问当前股票评分、风险或走势" : "问今日涨停、评分或风险"}
          />
          <button
            aria-label="发送问题"
            className="icon-button"
            disabled={sending || sessionLoading || !sessionId || !message.trim()}
            title="发送"
            type="submit"
          >
            <Send size={17} />
          </button>
        </form>
      </section>
      </div>
    </div>
  );
}

function inferChatIntent(message: string) {
  /** Infer a deterministic tool hint before the backend performs final routing. */

  if (/开盘|收盘|集合竞价|交易时间/.test(message)) {
    return "market_schedule";
  }
  if (/市场|情绪|赚钱效应|亏钱效应|氛围/.test(message)) {
    return "market_context";
  }
  if (/风险|缺点|问题/.test(message)) {
    return "risk_summary";
  }
  if (/详细|解释|分析/.test(message)) {
    return "llm_explanation";
  }
  if (/为什么|评分|评级|高分|低分/.test(message)) {
    return "rating_explain";
  }
  if (/医药|医疗|制药|药业|生物|中药|相关|行业|题材/.test(message)) {
    return "first_board_filter";
  }
  if (/总结|今天|首板|候选|市场环境/.test(message)) {
    return "today_summary";
  }
  return undefined;
}

const routeToView: Record<string, ViewKey> = {
  "/": "overview",
  "/recommendations": "recommendation",
  "/review": "review",
  "/stocks/limit-up-pool": "pool",
  "/stocks/first-board": "first",
  "/stocks/continued-board": "continued",
  "/stocks/failed": "failed",
  "/stocks/recent-limit-up": "recent",
};

const stockListPaths = new Set([
  "/stocks/limit-up-pool",
  "/stocks/first-board",
  "/stocks/continued-board",
  "/stocks/failed",
  "/stocks/recent-limit-up",
]);

const primaryNavigation = [
  { to: "/", label: "首页", end: true },
  { to: "/recommendations", label: "盘前推荐", end: true },
  { to: "/review", label: "复盘", end: true },
  { to: "/stocks/limit-up-pool", label: "涨停池", end: true },
];

const agentWorkspaceHiddenPaths = new Set([
  "/stocks/first-board",
  "/stocks/continued-board",
  "/stocks/failed",
  "/stocks/recent-limit-up",
  "/stocks/limit-up-pool",
  "/recommendations",
  "/review",
]);

export function App() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const location = useLocation();
  const activeView = routeToView[location.pathname] ?? "overview";

  /** Load the dashboard summary and the list data needed by every route. */
  async function loadDashboard() {
    setLoading(true);
    setError(null);

    try {
      const [
        summary,
        firstBoard,
        continuedBoard,
        failed,
        recent,
        firstBoardRatings,
        dailyBoardPromotion,
        ratingBacktest,
        ratingEvaluation,
      ] = await Promise.all([
        fetchMarketSummary(),
        fetchFirstBoardEvents(),
        fetchContinuedBoardEvents(),
        fetchFailedLimitUpEvents(),
        fetchRecentLimitUpEvents(5),
        fetchFirstBoardRatings(),
        fetchDailyBoardPromotion(5),
        fetchRatingBacktest(),
        fetchRatingEvaluation(),
      ]);

      setData({
        summary,
        firstBoard,
        continuedBoard,
        failed,
        recent,
        firstBoardRatings,
        dailyBoardPromotion,
        ratingBacktest,
        ratingEvaluation,
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载数据失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadDashboard();
  }, []);

  if (loading) {
    return <ShellState label="正在加载 LimitUpLab 数据..." />;
  }

  if (error || !data) {
    return (
      <ShellState
        label="数据加载失败"
        detail={error ?? "请确认后端服务已经启动"}
        onRetry={loadDashboard}
      />
    );
  }

  const isStockDetail = location.pathname.startsWith("/stocks/")
    && !stockListPaths.has(location.pathname);
  const showAgentWorkspace = !isStockDetail
    && !agentWorkspaceHiddenPaths.has(location.pathname);
  const activeMeta = isStockDetail
    ? { title: "个股详情", eyebrow: "Stock Detail" }
    : viewMeta[activeView];

  return (
    <main className="app-shell">
      <header className="app-header">
        <Link aria-label="返回市场概况" className="app-brand" to="/">
          <span className="app-brand-mark"><Flame aria-hidden="true" size={19} /></span>
          <span>
            <strong>LimitUpLab</strong>
            <small>首板研究 Agent</small>
          </span>
        </Link>

        <nav aria-label="主导航" className="primary-navigation">
          {primaryNavigation.map((item) => (
            <NavLink
              className={({ isActive }) => (
                isActive
                || (item.to === "/stocks/limit-up-pool" && stockListPaths.has(location.pathname))
                  ? "active"
                  : undefined
              )}
              end={item.end}
              key={item.to}
              to={item.to}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="app-header-actions">
          <div className="data-as-of">
            <i aria-hidden="true" />
            <span>
              <small>数据日期</small>
              <strong>{data.summary.trade_date}</strong>
            </span>
          </div>
          <button
            aria-label="刷新全部数据"
            className="icon-button"
            onClick={loadDashboard}
            title="刷新数据"
            type="button"
          >
            <RefreshCcw size={18} />
          </button>
        </div>
      </header>

      {activeView !== "overview" || isStockDetail ? (
        <section className="topbar">
          <div>
            <p className="eyebrow">{activeMeta.eyebrow}</p>
            <h1>{activeMeta.title}</h1>
          </div>
        </section>
      ) : null}

      {activeView === "overview" && !isStockDetail ? (
        <MarketSnapshot summary={data.summary} />
      ) : null}

      {showAgentWorkspace ? (
        <AgentChatDock
          tradeDate={data.summary.trade_date}
        />
      ) : null}

      <Routes>
        <Route path="/" element={null} />
        <Route
          path="/recommendations"
          element={<PremarketRecommendations ratings={data.firstBoardRatings} />}
        />
        <Route path="/review" element={<ReviewDashboard data={data} />} />
        <Route path="/stocks/limit-up-pool" element={<LimitUpPool data={data} />} />
        <Route path="/stocks/first-board" element={<DetailView view="first" data={data} />} />
        <Route
          path="/stocks/continued-board"
          element={<DetailView view="continued" data={data} />}
        />
        <Route path="/stocks/failed" element={<DetailView view="failed" data={data} />} />
        <Route path="/stocks/recent-limit-up" element={<RecentLimitUp events={data.recent} />} />
        <Route path="/stocks/:symbol" element={<StockDetail data={data} />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </main>
  );
}

function MarketSnapshot({ summary }: { summary: MarketSummary }) {
  return (
    <section className="market-snapshot">
      <div className="market-snapshot-indices">
        <time className="market-snapshot-date" dateTime={summary.trade_date}>
          {summary.trade_date}
        </time>
        {summary.indices.map((index) => (
          <article key={index.symbol}>
            <span>{index.name}</span>
            <div>
              <strong>{index.close.toFixed(2)}</strong>
              <b className={index.change_pct >= 0 ? "positive" : "negative"}>
                {formatSigned(index.change_pct, 2)}%
              </b>
            </div>
          </article>
        ))}
        {summary.indices.length === 0 ? <span>指数数据暂不可用</span> : null}
      </div>

      <nav aria-label="今日涨停概览" className="market-snapshot-entries">
        <div className="market-snapshot-ceiling">
          <span>最高连板</span>
          <strong>{summary.max_board_height}<small>板</small></strong>
        </div>
        <Link to="/stocks/first-board">
          <span><Flame size={15} />首板</span>
          <strong>{summary.first_board_count}<small>只</small></strong>
        </Link>
        <Link to="/stocks/continued-board">
          <span><Layers3 size={15} />连板</span>
          <strong>{summary.continued_board_count}<small>只</small></strong>
        </Link>
      </nav>
    </section>
  );
}

function PremarketRecommendations({ ratings }: { ratings: FirstBoardRatingsResponse }) {
  /** Keep the two pre-market strategies distinct while sharing one workspace. */

  const [mode, setMode] = useState<"discovery" | "relay">("discovery");
  const [discovery, setDiscovery] = useState<FirstBoardDiscoveryResponse | null>(null);
  const [discoveryError, setDiscoveryError] = useState<string | null>(null);
  const [discoveryLoading, setDiscoveryLoading] = useState(true);
  const [intelligence, setIntelligence] = useState<RecommendationIntelligenceResponse | null>(null);

  useEffect(() => {
    let active = true;
    void fetchFirstBoardDiscovery()
      .then((response) => {
        if (active) setDiscovery(response);
      })
      .catch((caught: unknown) => {
        if (active) {
          setDiscoveryError(caught instanceof Error ? caught.message : "首板挖掘数据加载失败");
        }
      })
      .finally(() => {
        if (active) setDiscoveryLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    const refresh = () => {
      void fetchRecommendationIntelligence()
        .then((response) => {
          if (active) setIntelligence(response);
        })
        .catch(() => {
          // The immutable recommendation snapshots remain usable while the
          // background intelligence worker is warming up or temporarily stale.
        });
    };
    refresh();
    const timer = window.setInterval(refresh, 30 * 60 * 1000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <section className="premarket-workspace">
      <div aria-label="盘前策略" className="strategy-switch" role="tablist">
        <button
          aria-selected={mode === "discovery"}
          className={mode === "discovery" ? "active" : undefined}
          onClick={() => setMode("discovery")}
          role="tab"
          type="button"
        >
          <TrendingUp size={16} />
          首板挖掘
        </button>
        <button
          aria-selected={mode === "relay"}
          className={mode === "relay" ? "active" : undefined}
          onClick={() => setMode("relay")}
          role="tab"
          type="button"
        >
          <Layers3 size={16} />
          一进二接力
        </button>
      </div>
      {intelligence ? (
        <div className="recommendation-refresh-status">
          <RefreshCcw size={14} />
          行情、新闻及财报更新于 {formatStockNewsTime(intelligence.refreshed_at)}
          <span>每 {intelligence.interval_minutes} 分钟刷新</span>
        </div>
      ) : null}
      <RecommendationNewsBoard />
      {mode === "discovery" ? (
        <FirstBoardDiscoveryPanel
          data={discovery}
          error={discoveryError}
          intelligence={intelligence}
          loading={discoveryLoading}
        />
      ) : (
        <FirstBoardRatingPanel intelligence={intelligence} ratings={ratings} />
      )}
    </section>
  );
}

interface RecommendationNewsViewItem {
  key: string;
  title: string;
  summary: string;
  publishedAt: string;
  source: string;
  url: string;
  category: string;
}

function RecommendationNewsBoard() {
  /** Paginate the factual 24-hour market feed without involving the LLM. */

  const [page, setPage] = useState(1);
  const [news, setNews] = useState<FinanceNewsPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    const refresh = (showLoading: boolean) => {
      if (showLoading) setLoading(true);
      void fetchFinanceNews(page)
        .then((response) => {
          if (!active) return;
          setNews(response);
          setFailed(false);
          setPage(response.page);
        })
        .catch(() => {
          if (active) setFailed(true);
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    };
    refresh(true);
    const timer = window.setInterval(() => refresh(false), 5 * 60 * 1000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [page]);

  const visibleNews = useMemo(
    () => (news?.items ?? []).map(marketNewsViewItem),
    [news],
  );
  const pageNumbers = news ? paginationWindow(news.page, news.total_pages) : [];

  return (
    <section className="recommendation-news-board" aria-label="盘前实时新闻">
      <header className="recommendation-news-header">
        <div>
          <Newspaper size={18} />
          <span>
            <strong>实时新闻</strong>
            <small>
              {news
                ? `近 24 小时 · ${news.sources.join(" · ")} · 共 ${news.total} 条`
                : "近 24 小时 · 5 分钟自动更新"}
            </small>
          </span>
        </div>
        <span className="recommendation-news-refresh">5 分钟自动更新</span>
      </header>
      {loading && !news ? (
        <div className="recommendation-news-state">
          <LoaderCircle className="state-spinner" size={18} />
          正在获取最新新闻...
        </div>
      ) : null}
      {failed ? (
        <div className="recommendation-news-state">财经快讯暂时没有加载成功。</div>
      ) : null}
      {!loading && !failed && visibleNews.length === 0 ? (
        <div className="recommendation-news-state">
          近 24 小时没有获取到市场快讯。
        </div>
      ) : null}
      {visibleNews.length > 0 ? (
        <div className="recommendation-news-list">
          {visibleNews.map((item) => (
            <article className="recommendation-news-item" key={item.key}>
              <time dateTime={item.publishedAt}>{formatRecommendationNewsTime(item.publishedAt)}</time>
              <div className="recommendation-news-body">
                <a href={item.url} target="_blank" rel="noreferrer">
                  <strong>{item.title}</strong>
                  <ExternalLink size={13} aria-hidden="true" />
                </a>
                <span>{item.source} · {item.category}</span>
              </div>
            </article>
          ))}
        </div>
      ) : null}
      {news && news.total_pages > 1 ? (
        <footer className="recommendation-news-pagination" aria-label="市场快讯分页">
          <span>第 {news.page} / {news.total_pages} 页</span>
          <div>
            <button
              aria-label="上一页"
              disabled={news.page <= 1}
              onClick={() => setPage((value) => Math.max(1, value - 1))}
              title="上一页"
              type="button"
            >
              <ChevronLeft size={15} />
            </button>
            {pageNumbers.map((pageNumber) => (
              <button
                aria-current={pageNumber === news.page ? "page" : undefined}
                className={pageNumber === news.page ? "active" : undefined}
                key={pageNumber}
                onClick={() => setPage(pageNumber)}
                type="button"
              >
                {pageNumber}
              </button>
            ))}
            <button
              aria-label="下一页"
              disabled={news.page >= news.total_pages}
              onClick={() => setPage((value) => Math.min(news.total_pages, value + 1))}
              title="下一页"
              type="button"
            >
              <ChevronRight size={15} />
            </button>
          </div>
        </footer>
      ) : null}
    </section>
  );
}

function marketNewsViewItem(item: FinanceNewsItem): RecommendationNewsViewItem {
  return {
    key: `${item.source}-${item.url}-${item.published_at}`,
    title: item.title,
    summary: item.summary,
    publishedAt: item.published_at,
    source: item.source,
    url: item.url,
    category: item.category,
  };
}

function paginationWindow(current: number, total: number): number[] {
  const visible = Math.min(5, total);
  const start = Math.max(1, Math.min(current - 2, total - visible + 1));
  return Array.from({ length: visible }, (_, index) => start + index);
}

function formatRecommendationNewsTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value.slice(5, 16).replace("T", " ");
  const now = new Date();
  const sameDay = parsed.toDateString() === now.toDateString();
  return new Intl.DateTimeFormat("zh-CN", {
    month: sameDay ? undefined : "2-digit",
    day: sameDay ? undefined : "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function ReviewDashboard({ data }: { data: DashboardData }) {
  /** Keep market-promotion and prediction follow-up in one review workspace. */

  return (
    <>
      <HighScoreReviewPanel
        backtest={data.ratingBacktest}
        evaluation={data.ratingEvaluation}
        latestTradeDate={data.summary.trade_date}
      />
      <DailyBoardPromotionPanel stats={data.dailyBoardPromotion} />
      <DragonTigerReviewPanel tradeDate={data.summary.trade_date} />
    </>
  );
}

type DragonTigerFilter = "all" | "organization" | "hot_money";

function DragonTigerReviewPanel({ tradeDate }: { tradeDate: string }) {
  /** Load post-close Dragon-Tiger facts without delaying the rest of the dashboard. */

  const [data, setData] = useState<DragonTigerReviewResponse | null>(null);
  const [filter, setFilter] = useState<DragonTigerFilter>("all");
  const [expanded, setExpanded] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setData(null);
    setError(null);
    void fetchDragonTigerReview(tradeDate)
      .then((response) => {
        if (active) {
          setData(response);
        }
      })
      .catch(() => {
        if (active) {
          setError("龙虎榜数据暂时没有加载成功");
        }
      });
    return () => {
      active = false;
    };
  }, [reloadToken, tradeDate]);

  const filteredItems = useMemo(() => {
    const items = data?.items ?? [];
    if (filter === "organization") {
      return items.filter((item) => item.organization_net_buy_amount !== null);
    }
    if (filter === "hot_money") {
      return items.filter((item) => item.hot_money_net_buy_amount !== null);
    }
    return items;
  }, [data, filter]);
  const visibleItems = expanded ? filteredItems : filteredItems.slice(0, 20);

  function selectFilter(nextFilter: DragonTigerFilter) {
    setFilter(nextFilter);
    setExpanded(false);
  }

  return (
    <section className="dragon-tiger-section">
      <Panel
        title="龙虎榜"
        icon={<Landmark size={18} />}
        actions={(
          <div className="dragon-tiger-filters" role="tablist" aria-label="龙虎榜类型">
            {([
              ["all", "全部"],
              ["organization", "机构"],
              ["hot_money", "游资"],
            ] as const).map(([value, label]) => (
              <button
                type="button"
                role="tab"
                aria-selected={filter === value}
                className={filter === value ? "active" : ""}
                key={value}
                onClick={() => selectFilter(value)}
              >
                {label}
              </button>
            ))}
          </div>
        )}
      >
        {!data && !error ? (
          <div className="dragon-tiger-state">
            <LoaderCircle className="state-spinner" size={22} />
            <span>正在获取 {tradeDate} 龙虎榜...</span>
          </div>
        ) : null}
        {error ? (
          <div className="dragon-tiger-state">
            <span>{error}</span>
            <button type="button" onClick={() => setReloadToken((value) => value + 1)}>
              <RefreshCcw size={15} />
              重试
            </button>
          </div>
        ) : null}
        {data ? (
          <div className="dragon-tiger-content">
            <div className="dragon-tiger-metrics">
              <span><small>交易日</small><strong>{data.trade_date}</strong></span>
              <span><small>上榜股票</small><strong>{data.stock_count}<em>只</em></strong></span>
              <span><small>净流入股票</small><strong className="positive">{data.net_inflow_count}<em>只</em></strong></span>
              <span><small>机构席位</small><strong>{data.organization_count}<em>只</em></strong></span>
              <span><small>游资席位</small><strong>{data.hot_money_count}<em>只</em></strong></span>
            </div>

            {filteredItems.length > 0 ? (
              <>
                <div className="table-wrap dragon-tiger-table-wrap">
                  <table className="dragon-tiger-table">
                    <thead>
                      <tr>
                        <th>股票</th>
                        <th>涨跌幅</th>
                        <th>净买额</th>
                        <th>机构净额</th>
                        <th>游资净额</th>
                        <th>上榜信息</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleItems.map((item) => {
                        const stockIdentity = (
                          <>
                            <strong>{item.name}</strong>
                            <small>{item.symbol}{item.hot_rank ? ` · 人气 ${item.hot_rank}` : ""}</small>
                          </>
                        );
                        return (
                          <tr key={item.symbol}>
                            <td className="dragon-tiger-stock">
                              {item.detail_trade_date ? (
                                <Link to={stockDetailPath(item.symbol, item.detail_trade_date)}>
                                  {stockIdentity}
                                  <ChevronRight size={15} aria-hidden="true" />
                                </Link>
                              ) : (
                                <div>{stockIdentity}</div>
                              )}
                            </td>
                            <td className={numberTone(item.change_pct)}>
                              {formatOptionalPercent(item.change_pct)}
                            </td>
                            <td className={numberTone(item.net_buy_amount)}>
                              {formatNetAmount(item.net_buy_amount)}
                            </td>
                            <td className={numberTone(item.organization_net_buy_amount)}>
                              {formatNetAmount(item.organization_net_buy_amount)}
                            </td>
                            <td className={numberTone(item.hot_money_net_buy_amount)}>
                              {formatNetAmount(item.hot_money_net_buy_amount)}
                            </td>
                            <td className="dragon-tiger-reason">
                              <strong>{item.limit_reason || "上榜原因待补充"}</strong>
                              <small>
                                {item.concepts.slice(0, 3).join(" · ") || "题材待补充"}
                                {item.range_days && item.range_days > 1 ? ` · ${item.range_days}日统计` : ""}
                              </small>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                {filteredItems.length > 20 ? (
                  <div className="dragon-tiger-expand">
                    <button type="button" onClick={() => setExpanded((value) => !value)}>
                      {expanded ? "收起榜单" : `查看全部 ${filteredItems.length} 只`}
                    </button>
                  </div>
                ) : null}
              </>
            ) : (
              <div className="dragon-tiger-state">当前分类暂无龙虎榜股票。</div>
            )}
          </div>
        ) : null}
      </Panel>
    </section>
  );
}

function DailyBoardPromotionPanel({ stats }: { stats: DailyBoardPromotionStat[] }) {
  /** Let users inspect five daily promotion cohorts and their successful stocks. */

  const recentStats = useMemo(() => stats.slice(-5), [stats]);
  const displayStats = useMemo(() => [...recentStats].reverse(), [recentStats]);
  const latestDate = recentStats[recentStats.length - 1]?.trade_date ?? "";
  const [selectedDate, setSelectedDate] = useState(latestDate);

  useEffect(() => {
    if (!recentStats.some((item) => item.trade_date === selectedDate)) {
      setSelectedDate(latestDate);
    }
  }, [latestDate, recentStats, selectedDate]);

  const selectedIndex = recentStats.findIndex((item) => item.trade_date === selectedDate);
  const selected = recentStats[selectedIndex] ?? recentStats[recentStats.length - 1];
  const previous = selectedIndex > 0 ? recentStats[selectedIndex - 1] : undefined;
  if (!selected) {
    return (
      <section className="promotion-section">
        <Panel title="每日连板晋级率" icon={<GitBranch size={18} />}>
          <div className="empty-state">相邻交易日收盘数据不足，暂时无法计算晋级率。</div>
        </Panel>
      </section>
    );
  }
  const change = previous ? selected.probability - previous.probability : null;

  return (
    <section className="promotion-section">
      <Panel title="每日连板晋级率" icon={<GitBranch size={18} />}>
        <div className="promotion-panel">
          <div className="promotion-latest">
            <div>
              <span>{selected.trade_date} 晋级观察</span>
              <strong>{formatEmpiricalRate(selected.probability)}</strong>
              <small>
                {selected.previous_trade_date} 封板 {selected.sample_size} 只，
                当日收盘晋级 {selected.promoted_count} 只
              </small>
            </div>
            <div className="promotion-cohort-summary">
              <span>
                <small>首板→二板</small>
                <b>{formatEmpiricalRate(selected.first_board_probability)}</b>
                <em>{selected.first_board_promoted_count}/{selected.first_board_sample_size}</em>
              </span>
              <span>
                <small>连板梯队晋级</small>
                <b>{formatEmpiricalRate(selected.continued_board_probability)}</b>
                <em>{selected.continued_board_promoted_count}/{selected.continued_board_sample_size}</em>
              </span>
              <span>
                <small>较前一日</small>
                <b className={(change ?? 0) >= 0 ? "positive" : "negative"}>
                  {change === null ? "暂无" : `${formatSigned(change * 100, 1)} 个百分点`}
                </b>
                <em>总晋级率变化</em>
              </span>
            </div>
          </div>

          <div className="promotion-history" aria-label="近5个交易日晋级率">
            {displayStats.map((item) => (
              <button
                type="button"
                className={`promotion-day ${item.trade_date === selected.trade_date ? "active" : ""}`}
                key={item.trade_date}
                aria-pressed={item.trade_date === selected.trade_date}
                onClick={() => setSelectedDate(item.trade_date)}
              >
                <div>
                  <time dateTime={item.trade_date}>{item.trade_date.slice(5)}</time>
                  <strong>{formatEmpiricalRate(item.probability)}</strong>
                </div>
                <div className="promotion-rate-track" aria-hidden="true">
                  <span style={{ width: `${Math.max(item.probability * 100, 2)}%` }} />
                </div>
                <small>
                  晋级 {item.promoted_count}/{item.sample_size} · 首→二 {item.first_board_promoted_count}/{item.first_board_sample_size}
                </small>
              </button>
            ))}
          </div>

          <div className="promotion-success-section">
            <div className="promotion-success-heading">
              <div>
                <strong>{selected.trade_date} 晋级成功</strong>
                <small>点击股票查看 K 线、封板信息和首板位置判断</small>
              </div>
              <b>{selected.promoted_stocks.length} 只</b>
            </div>
            {selected.promoted_stocks.length > 0 ? (
              <div className="promotion-stock-list">
                {selected.promoted_stocks.map((stock) => (
                  <Link
                    className="promotion-stock-row"
                    to={stockDetailPath(stock.symbol, selected.trade_date)}
                    key={stock.symbol}
                  >
                    <div>
                      <strong>{stock.name}</strong>
                      <small>{stock.symbol} · {stock.industry || "行业待补充"}</small>
                    </div>
                    <span>{stock.from_board_height}→{stock.to_board_height}板</span>
                    <small>{stock.concept || "题材待补充"}</small>
                    <em>首封 {stock.first_limit_time.slice(0, 5)} · 炸板 {stock.break_count}</em>
                    <ChevronRight size={17} aria-hidden="true" />
                  </Link>
                ))}
              </div>
            ) : (
              <div className="promotion-success-empty">当日没有识别到收盘晋级成功的股票。</div>
            )}
          </div>
        </div>
      </Panel>
    </section>
  );
}

function HighScoreReviewPanel({
  backtest,
  evaluation,
  latestTradeDate,
}: {
  backtest: RatingBacktestResponse;
  evaluation: AgentEvaluationResponse;
  latestTradeDate: string;
}) {
  const [report, setReport] = useState<ReviewAgentReportResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeReviewSelection, setActiveReviewSelection] = useState<string | null>(null);
  const [snapshotDates, setSnapshotDates] = useState<DailyReviewSnapshotSummary[]>([]);
  const [selectedAsOfDate, setSelectedAsOfDate] = useState(latestTradeDate);
  const [scoringDiagnostic, setScoringDiagnostic] = useState<ScoringErrorDiagnosticResponse | null>(null);

  useEffect(() => {
    let active = true;
    void fetchDailyReviewSnapshots()
      .then((response) => {
        if (!active) return;
        setSnapshotDates(response.snapshots);
        setSelectedAsOfDate((current) => (
          response.snapshots.some((item) => item.as_of_date === current)
            ? current
            : response.snapshots[0]?.as_of_date ?? latestTradeDate
        ));
      })
      .catch(() => {
        if (active) setSnapshotDates([]);
      });
    return () => {
      active = false;
    };
  }, [latestTradeDate]);

  useEffect(() => {
    void loadReview(selectedAsOfDate);
  }, [selectedAsOfDate]);

  async function loadReview(endDate: string) {
    setRunning(true);
    setError(null);
    setScoringDiagnostic(null);
    try {
      const [response, diagnostic] = await Promise.all([
        fetchReviewAgentReport({
          end_date: endDate,
          top_per_day: 10,
          follow_days: 5,
          use_llm: false,
        }),
        fetchScoringErrorDiagnostic(endDate).catch(() => null),
      ]);
      setReport(response);
      setScoringDiagnostic(diagnostic);
      const grouped = groupReviewPicksByDate(response.reviewed_picks);
      const trackDates = buildReviewTrackDates(grouped, response.end_date);
      setActiveReviewSelection((current) => (
        current && (
          trackDates.includes(current)
          || current === REVIEW_SUCCESS_SELECTION
          || current === REVIEW_MISS_SELECTION
        )
          ? current
          : trackDates[0] ?? REVIEW_SUCCESS_SELECTION
      ));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Review Agent 复盘失败");
    } finally {
      setRunning(false);
    }
  }

  const readyCount = report
    ? report.sample_size - report.pending_count
    : 0;
  const successRate = readyCount > 0 && report
    ? report.success_count / readyCount
    : null;
  const evaluationReadyRate = evaluation.prediction_count > 0
    ? evaluation.outcome_ready_count / evaluation.prediction_count
    : 0;
  const backtestReadyRate = backtest.sample_size > 0
    ? backtest.outcome_ready_count / backtest.sample_size
    : 0;
  const reviewedPicks = report?.reviewed_picks ?? [];
  const reviewDates = groupReviewPicksByDate(reviewedPicks);
  const trackDates = report ? buildReviewTrackDates(reviewDates, report.end_date) : [];
  const trackedSampleSize = trackDates.reduce(
    (total, tradeDate) => total + (reviewDates[tradeDate]?.length ?? 0),
    0,
  );
  const trackedPendingCount = trackDates.reduce(
    (total, tradeDate) => total + (reviewDates[tradeDate] ?? []).filter((item) => !item.outcome_ready).length,
    0,
  );
  const trackedReadyCount = trackedSampleSize - trackedPendingCount;
  const trackedSuccessCount = trackDates.reduce(
    (total, tradeDate) => total + (reviewDates[tradeDate] ?? []).filter(
      (item) => (latestTrackedReturn(item) ?? 0) > 0,
    ).length,
    0,
  );
  const trackedSuccessRate = trackedReadyCount > 0 ? trackedSuccessCount / trackedReadyCount : null;
  const promotionComparisons = (report?.promotion_comparisons ?? []).reduce<
    Record<string, ReviewPromotionComparison>
  >((items, item) => {
    items[item.trade_date] = item;
    return items;
  }, {});
  const successfulPicks = sortReviewPicksForSummary(
    reviewedPicks.filter((item) => (latestTrackedReturn(item) ?? 0) > 0),
    "desc",
  );
  const failedPicks = sortReviewPicksForSummary(
    reviewedPicks.filter((item) => (latestTrackedReturn(item) ?? 0) < 0),
    "asc",
  );
  const selectedReviewSelection = activeReviewSelection && (
    trackDates.includes(activeReviewSelection)
    || activeReviewSelection === REVIEW_SUCCESS_SELECTION
    || activeReviewSelection === REVIEW_MISS_SELECTION
  )
    ? activeReviewSelection
    : trackDates[0] ?? REVIEW_SUCCESS_SELECTION;
  const selectedPicks = selectedReviewSelection === REVIEW_SUCCESS_SELECTION
    ? successfulPicks
    : selectedReviewSelection === REVIEW_MISS_SELECTION
      ? failedPicks
      : reviewDates[selectedReviewSelection] ?? [];

  return (
    <Panel title="高分票追踪复盘" icon={<TrendingUp size={18} />}>
      <div className="review-agent-panel">
        <div className="review-agent-hero">
          <div>
            <span>High Score Review</span>
            <strong>
              {report
                ? `${report.start_date} 至 ${report.end_date}`
                : `${evaluation.start_date} 至 ${evaluation.end_date || latestTradeDate}`}
            </strong>
            <p>
              {report
                ? `过去 5 个交易日，每天一个卡片；点进去看当日 Top10 到 ${report.end_date} 收盘的走势。`
                : "自动加载过去 5 个交易日的每日 Top10 评分票，点击日期卡片查看到最新收盘的兑现情况。"}
            </p>
          </div>
        </div>

        {error ? <p className="review-agent-error">{error}</p> : null}

        <div className="review-date-selector">
          <label htmlFor="review-as-of-date">复盘截止日</label>
          <select
            id="review-as-of-date"
            onChange={(event) => setSelectedAsOfDate(event.target.value)}
            value={selectedAsOfDate}
          >
            {(snapshotDates.length > 0
              ? snapshotDates
              : [{ as_of_date: latestTradeDate } as DailyReviewSnapshotSummary]
            ).map((item) => (
              <option key={item.as_of_date} value={item.as_of_date}>
                {item.as_of_date}
              </option>
            ))}
          </select>
          <span>{snapshotDates.length > 0 ? "已固化复盘" : "当前动态复盘"}</span>
        </div>

        {running && !report ? (
          <div className="review-agent-empty">
            <strong>正在加载过去 5 个交易日 Top10 追踪</strong>
            <p>加载完成后可以按日期查看当天预测 Top10 到最新收盘的走势。</p>
          </div>
        ) : report ? (
          <>
            <ReviewConclusion report={report} trackDates={trackDates} />
            {scoringDiagnostic ? (
              <ScoringErrorDiagnosticPanel diagnostic={scoringDiagnostic} />
            ) : null}
            <div className="review-agent-metrics">
              <span>
                <small>样本</small>
                <strong>{trackedSampleSize}</strong>
              </span>
              <span>
                <small>日期卡片</small>
                <strong>{trackDates.length}</strong>
              </span>
              <span>
                <small>Top10 1进2</small>
                <strong>{formatEmpiricalRate(report.top_pick_promotion_rate)}</strong>
                <em>{report.top_pick_promoted_count}/{report.top_pick_promotion_sample_size}</em>
              </span>
              <span>
                <small>同期全部首板</small>
                <strong>{formatEmpiricalRate(report.market_promotion_rate)}</strong>
                <em>{report.market_promoted_count}/{report.market_promotion_sample_size}</em>
              </span>
              <span>
                <small>相对全市场</small>
                <strong className={(report.promotion_rate_delta ?? 0) >= 0 ? "positive" : "negative"}>
                  {report.promotion_rate_delta === null
                    ? "暂无"
                    : `${formatSigned(report.promotion_rate_delta * 100, 1)} 个百分点`}
                </strong>
              </span>
              <span>
                <small>收益兑现率</small>
                <strong>{trackedSuccessRate === null ? "暂无" : formatPercent(trackedSuccessRate)}</strong>
              </span>
            </div>

            <DailyTopReview
              activeSelection={selectedReviewSelection}
              adjustmentSuggestions={report.adjustment_suggestions}
              failedPatterns={report.failed_patterns}
              failedPicks={failedPicks}
              groupedPicks={reviewDates}
              onSelect={setActiveReviewSelection}
              picks={selectedPicks}
              promotionComparisons={promotionComparisons}
              successfulPatterns={report.successful_patterns}
              successfulPicks={successfulPicks}
              trackDates={trackDates}
            />
          </>
        ) : (
          <>
            <div className="review-agent-metrics">
              <span>
                <small>预测快照</small>
                <strong>{evaluation.prediction_count}</strong>
              </span>
              <span>
                <small>走势覆盖</small>
                <strong>{formatPercent(evaluationReadyRate)}</strong>
              </span>
              <span>
                <small>误判</small>
                <strong>{evaluation.label_counts.miss ?? 0}</strong>
              </span>
              <span>
                <small>漏判</small>
                <strong>{evaluation.label_counts.false_negative ?? 0}</strong>
              </span>
              <span>
                <small>目标口径</small>
                <strong>5日 x Top10</strong>
              </span>
              <span>
                <small>回测覆盖</small>
                <strong>{formatPercent(backtestReadyRate)}</strong>
              </span>
            </div>

            <div className="review-agent-empty">
              <strong>暂无 Top10 追踪明细</strong>
              <p>当前只能看到已有预测概览，明细加载失败时请检查后端 Review Agent 接口。</p>
            </div>
          </>
        )}
      </div>
    </Panel>
  );
}

function ReviewConclusion({
  report,
  trackDates,
}: {
  report: ReviewAgentReportResponse;
  trackDates: string[];
}) {
  /** Turn the persisted review facts into one scannable daily conclusion. */

  const trackedPicks = report.reviewed_picks.filter((item) => (
    trackDates.includes(item.trade_date)
  ));
  const trackedReturns = trackedPicks
    .map(latestTrackedReturn)
    .filter((value): value is number => value !== null);
  const averageReturn = trackedReturns.length > 0
    ? trackedReturns.reduce((total, value) => total + value, 0) / trackedReturns.length
    : null;
  const positiveRate = trackedReturns.length > 0
    ? trackedReturns.filter((value) => value > 0).length / trackedReturns.length
    : null;
  const drawdowns = trackedPicks
    .map((item) => item.max_drawdown_from_next_open_3d)
    .filter((value): value is number => value !== null);
  const averageDrawdown = drawdowns.length > 0
    ? drawdowns.reduce((total, value) => total + value, 0) / drawdowns.length
    : null;
  const cacheReadyCount = trackedPicks.filter((item) => item.post_bar_cache_complete).length;
  const promotionDelta = report.promotion_rate_delta;
  const verdict = promotionDelta === null
    ? { tone: "neutral", text: "1进2对照仍在等待成熟样本" }
    : promotionDelta >= 0.03
      ? { tone: "positive", text: "Top10 的1进2表现优于同期全部首板" }
      : promotionDelta <= -0.03
        ? { tone: "negative", text: "Top10 的1进2表现弱于同期全部首板" }
        : { tone: "neutral", text: "Top10 与同期全部首板接近，暂未形成明显优势" };

  return (
    <section className="review-conclusion" aria-label="本次复盘结论">
      <header>
        <div>
          <span>本次复盘结论</span>
          <strong>{verdict.text}</strong>
        </div>
        <b className={verdict.tone}>{report.end_date}</b>
      </header>
      <div className="review-conclusion-metrics">
        <span>
          <small>各自最新收盘收益均值</small>
          <strong className={numberTone(averageReturn)}>{formatOptionalPercent(averageReturn)}</strong>
        </span>
        <span>
          <small>上涨样本占比</small>
          <strong>{positiveRate === null ? "暂无" : formatPercent(positiveRate)}</strong>
        </span>
        <span>
          <small>Top10 相对全市场</small>
          <strong className={numberTone(promotionDelta)}>
            {promotionDelta === null
              ? "待确认"
              : `${formatSigned(promotionDelta * 100, 1)} 个百分点`}
          </strong>
        </span>
        <span>
          <small>三日平均最大回撤</small>
          <strong className={numberTone(averageDrawdown)}>{formatOptionalPercent(averageDrawdown)}</strong>
        </span>
        <span>
          <small>五日走势缓存</small>
          <strong>{cacheReadyCount}/{trackedPicks.length}</strong>
        </span>
      </div>
      <div className="review-conclusion-evidence">
        <article>
          <small>表现较好特征</small>
          <p>{report.successful_patterns[0] ?? "成熟样本不足，暂不归纳共同特征。"}</p>
        </article>
        <article>
          <small>表现较差特征</small>
          <p>{report.failed_patterns[0] ?? "成熟样本不足，暂不归纳风险特征。"}</p>
        </article>
        <article>
          <small>下一轮观察</small>
          <p>{report.adjustment_suggestions[0] ?? report.main_findings[0] ?? "继续积累样本并保持当前评分口径。"}</p>
        </article>
      </div>
    </section>
  );
}

function ScoringErrorDiagnosticPanel({
  diagnostic,
}: {
  diagnostic: ScoringErrorDiagnosticResponse;
}) {
  /** Expose promotion errors and ablation evidence without changing Champion. */

  const actionableFactors = diagnostic.factors
    .filter((item) => item.recommendation !== "neutral")
    .slice(0, 4);
  const factors = actionableFactors.length > 0
    ? actionableFactors
    : diagnostic.factors.slice(0, 4);
  const sampleWarning = diagnostic.trade_date_count < 20
    ? `当前只有 ${diagnostic.trade_date_count} 个结果完整交易日，所有方向仅进入影子验证。`
    : "调整方向仍需通过后续交易日的样本外验证。";

  return (
    <section className="scoring-error-diagnostic" aria-label="评分改进诊断">
      <header>
        <div>
          <span>评分改进诊断</span>
          <strong>{diagnostic.findings[0] ?? "等待结果完整样本"}</strong>
        </div>
        <b>Champion 保持不变</b>
      </header>

      <div className="scoring-error-summary">
        <span>
          <small>高分误选</small>
          <strong>{diagnostic.false_positive_count}</strong>
          <em>Top10 未晋级</em>
        </span>
        <span>
          <small>晋级漏选</small>
          <strong>{diagnostic.false_negative_count}</strong>
          <em>Top10 之外晋级</em>
        </span>
        <span>
          <small>有效交易日</small>
          <strong>{diagnostic.trade_date_count}</strong>
          <em>{diagnostic.pool_sample_size} 个首板样本</em>
        </span>
        <span>
          <small>相对全市场</small>
          <strong className={numberTone(diagnostic.promotion_rate_delta)}>
            {diagnostic.promotion_rate_delta === null
              ? "暂无"
              : `${formatSigned(diagnostic.promotion_rate_delta * 100, 1)} 个百分点`}
          </strong>
          <em>Top10 减全市场</em>
        </span>
      </div>

      <div className="scoring-error-body">
        <div className="scoring-factor-diagnosis">
          <strong>值得继续验证的因子方向</strong>
          {factors.map((item) => (
            <article key={item.factor_key}>
              <div>
                <span>{item.factor_name}</span>
                <b className={`factor-action ${item.recommendation}`}>
                  {item.recommendation === "increase"
                    ? "观察上调"
                    : item.recommendation === "decrease"
                      ? "观察下调"
                      : "暂不调整"}
                </b>
              </div>
              <p>{item.evidence}</p>
            </article>
          ))}
        </div>

        <div className="scoring-error-cases">
          <ErrorCaseList
            title="典型高分误选"
            items={diagnostic.false_positive_samples.slice(0, 3)}
          />
          <ErrorCaseList
            title="典型晋级漏选"
            items={diagnostic.false_negative_samples.slice(0, 3)}
          />
        </div>
      </div>

      <p className="scoring-shadow-note">{sampleWarning}</p>
    </section>
  );
}

function ErrorCaseList({
  title,
  items,
}: {
  title: string;
  items: ScoringErrorDiagnosticResponse["false_positive_samples"];
}) {
  return (
    <section>
      <strong>{title}</strong>
      {items.length > 0 ? items.map((item) => (
        <Link
          key={`${item.trade_date}-${item.symbol}`}
          to={stockDetailPath(item.symbol, item.trade_date)}
        >
          <span>{item.name}<small>{item.symbol}</small></span>
          <b>第 {item.rank} 名</b>
          <em>{item.leading_factors.slice(0, 2).join(" · ")}</em>
        </Link>
      )) : <p>当前没有可展示的成熟样本。</p>}
    </section>
  );
}

const REVIEW_SUCCESS_SELECTION = "review-success";
const REVIEW_MISS_SELECTION = "review-miss";

function DailyTopReview({
  activeSelection,
  adjustmentSuggestions,
  failedPatterns,
  failedPicks,
  groupedPicks,
  onSelect,
  picks,
  promotionComparisons,
  successfulPatterns,
  successfulPicks,
  trackDates,
}: {
  activeSelection: string;
  adjustmentSuggestions: string[];
  failedPatterns: string[];
  failedPicks: ReviewAgentPick[];
  groupedPicks: Record<string, ReviewAgentPick[]>;
  onSelect: (selection: string) => void;
  picks: ReviewAgentPick[];
  promotionComparisons: Record<string, ReviewPromotionComparison>;
  successfulPatterns: string[];
  successfulPicks: ReviewAgentPick[];
  trackDates: string[];
}) {
  const isSuccessView = activeSelection === REVIEW_SUCCESS_SELECTION;
  const isMissView = activeSelection === REVIEW_MISS_SELECTION;
  const isPerformanceView = isSuccessView || isMissView;
  const activePatterns = isSuccessView ? successfulPatterns : isMissView ? failedPatterns : [];
  const title = isSuccessView ? "表现较好" : isMissView ? "表现较差" : activeSelection;
  const description = isSuccessView
    ? `共 ${successfulPicks.length} 只，按首板至最新收盘收益率从高到低展示前 10 只`
    : `共 ${failedPicks.length} 只，按首板至最新收盘收益率从低到高展示前 10 只`;
  const visiblePicks = picks.slice(0, 10);
  const promotionComparison = isPerformanceView
    ? undefined
    : promotionComparisons[activeSelection];

  return (
    <div className="daily-top-review">
      <div className="daily-top-cards">
        {trackDates.map((tradeDate) => {
          const dailyPicks = groupedPicks[tradeDate] ?? [];
          const readyCount = dailyPicks.filter((item) => item.post_bar_cache_complete).length;
          const promotion = promotionComparisons[tradeDate];
          return (
          <button
            className={tradeDate === activeSelection ? "active" : ""}
            key={tradeDate}
            type="button"
            onClick={() => onSelect(tradeDate)}
          >
            <span>{tradeDate}</span>
            <strong>Top10 追踪</strong>
            <small>{readyCount} / {dailyPicks.length} 缓存已同步</small>
            <b>
              {promotion?.outcome_ready
                ? `1进2 ${promotion.top_pick_promoted_count}/${promotion.top_pick_sample_size} · 全部 ${promotion.market_promoted_count}/${promotion.market_first_board_sample_size}`
                : "1进2等待下一交易日"}
            </b>
          </button>
          );
        })}
        <button
          className={`review-outcome-option outcome-success ${isSuccessView ? "active" : ""}`}
          type="button"
          onClick={() => onSelect(REVIEW_SUCCESS_SELECTION)}
        >
          <span>跨日期复盘</span>
          <strong className="review-outcome-label"><TrendingUp size={16} />表现较好</strong>
          <small>{successfulPicks.length} 只已兑现样本</small>
          <b>收益最高 Top10</b>
        </button>
        <button
          className={`review-outcome-option outcome-miss ${isMissView ? "active" : ""}`}
          type="button"
          onClick={() => onSelect(REVIEW_MISS_SELECTION)}
        >
          <span>跨日期复盘</span>
          <strong className="review-outcome-label"><ShieldAlert size={16} />表现较差</strong>
          <small>{failedPicks.length} 只误判样本</small>
          <b>收益最低 Top10</b>
        </button>
      </div>

      <section className="daily-top-body">
        {isPerformanceView ? (
          <div className="daily-top-title">
            <div>
              <strong>{title}</strong>
              <span>{description}</span>
            </div>
            <span>
              {visiblePicks.filter((item) => item.post_bar_cache_complete).length} / {visiblePicks.length} 缓存已同步
            </span>
          </div>
        ) : null}

        {promotionComparison ? (
          <div className="review-promotion-comparison">
            <span>
              <small>预测 Top10 1进2</small>
              <strong>{formatEmpiricalRate(promotionComparison.top_pick_promotion_rate)}</strong>
              <em>{promotionComparison.top_pick_promoted_count}/{promotionComparison.top_pick_sample_size}</em>
            </span>
            <span>
              <small>当天全部首板 1进2</small>
              <strong>{formatEmpiricalRate(promotionComparison.market_promotion_rate)}</strong>
              <em>{promotionComparison.market_promoted_count}/{promotionComparison.market_first_board_sample_size}</em>
            </span>
            <span>
              <small>Top10 相对全市场</small>
              <strong className={(promotionComparison.promotion_rate_delta ?? 0) >= 0 ? "positive" : "negative"}>
                {promotionComparison.promotion_rate_delta === null
                  ? "等待次日收盘"
                  : `${formatSigned(promotionComparison.promotion_rate_delta * 100, 1)} 个百分点`}
              </strong>
              <em>{promotionComparison.next_trade_date ?? "下一交易日待确认"}</em>
            </span>
          </div>
        ) : null}

        {isPerformanceView && activePatterns.length > 0 ? (
          <div className={`review-pattern-summary ${isSuccessView ? "summary-success" : "summary-miss"}`}>
            <strong>{isSuccessView ? "表现较好股票的共同特征" : "表现较差股票的共同特征"}</strong>
            <ul>
              {activePatterns.slice(0, 3).map((pattern) => <li key={pattern}>{pattern}</li>)}
            </ul>
            {isMissView && adjustmentSuggestions.length > 0 ? (
              <p><b>评分改进：</b>{adjustmentSuggestions[0]}</p>
            ) : null}
          </div>
        ) : null}

        <ReviewPickTable
          picks={visiblePicks}
          limit={10}
          showLatestReturn={isPerformanceView}
          showTradeDate={isPerformanceView}
        />
      </section>
    </div>
  );
}

function groupReviewPicksByDate(picks: ReviewAgentPick[]) {
  return picks.reduce<Record<string, ReviewAgentPick[]>>((groups, pick) => {
    groups[pick.trade_date] = groups[pick.trade_date] ?? [];
    groups[pick.trade_date].push(pick);
    groups[pick.trade_date].sort((left, right) => right.score - left.score);
    return groups;
  }, {});
}

function latestTrackedReturn(pick: ReviewAgentPick) {
  const latestBar = pick.post_bars.reduce<ReviewAgentPick["post_bars"][number] | null>(
    (latest, bar) => (
      bar.return_from_base_pct !== null && (!latest || bar.trade_date > latest.trade_date)
        ? bar
        : latest
    ),
    null,
  );
  return latestBar?.return_from_base_pct ?? null;
}

function sortReviewPicksForSummary(
  picks: ReviewAgentPick[],
  direction: "asc" | "desc",
) {
  return [...picks].sort((left, right) => {
    const leftReturn = latestTrackedReturn(left);
    const rightReturn = latestTrackedReturn(right);
    if (leftReturn === null && rightReturn !== null) return 1;
    if (leftReturn !== null && rightReturn === null) return -1;
    if (leftReturn !== null && rightReturn !== null && leftReturn !== rightReturn) {
      return direction === "desc" ? rightReturn - leftReturn : leftReturn - rightReturn;
    }
    return (
      right.trade_date.localeCompare(left.trade_date)
      || right.score - left.score
      || left.symbol.localeCompare(right.symbol)
    );
  });
}

function buildReviewTrackDates(
  groupedPicks: Record<string, ReviewAgentPick[]>,
  latestTradeDate: string,
) {
  return Object.keys(groupedPicks)
    .filter((tradeDate) => tradeDate < latestTradeDate)
    .sort()
    .reverse()
    .slice(0, 5);
}

function ReviewInsightCard({
  title,
  items,
  tone = "neutral",
}: {
  title: string;
  items: string[];
  tone?: "neutral" | "good" | "risk" | "warn";
}) {
  return (
    <article className={`review-insight-card review-${tone}`}>
      <strong>{title}</strong>
      {items.length > 0 ? (
        <ul>
          {items.slice(0, 5).map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : (
        <p>暂无足够样本。</p>
      )}
    </article>
  );
}

function ReviewToolTraceCard({ traces }: { traces: ReviewAgentReportResponse["tool_results"] }) {
  return (
    <article className="review-insight-card review-tools">
      <strong>工具链</strong>
      <div className="review-tool-chain">
        {traces.length > 0 ? (
          traces.map((trace) => (
            <span key={trace.name}>{trace.name}</span>
          ))
        ) : (
          <span>暂无工具 trace</span>
        )}
      </div>
    </article>
  );
}

function ReviewPickTable({
  picks,
  limit = 10,
  showLatestReturn = false,
  showTradeDate = false,
}: {
  picks: ReviewAgentPick[];
  limit?: number;
  showLatestReturn?: boolean;
  showTradeDate?: boolean;
}) {
  const visible = picks.slice(0, limit);

  if (visible.length === 0) {
    return <div className="review-agent-empty">暂无高分票追踪样本。</div>;
  }

  return (
    <div className="review-pick-table">
      <div className="review-pick-table-head">
        <span>股票</span>
        <span>评分</span>
        <span>结论</span>
        <span>{showLatestReturn ? "首板至今" : "次日开收"}</span>
        <span>走势追踪</span>
      </div>
      {visible.map((pick) => (
        <Link
          className={`review-pick-row pick-${pick.evaluation_label}`}
          key={`${pick.trade_date}-${pick.symbol}`}
          to={stockDetailPath(pick.symbol, pick.trade_date)}
        >
          <strong>
            {pick.name}
            <small>
              {showTradeDate ? `${pick.trade_date} / ` : ""}
              {pick.symbol} / {pick.prediction_source === "live" ? "实时预测" : "历史回测"}
            </small>
          </strong>
          <span>{pick.score.toFixed(1)} / {pick.rating}</span>
          <span className="review-pick-verdict">
            {showLatestReturn
              ? trackedReturnLabel(latestTrackedReturn(pick))
              : reviewLabelCopy(pick.evaluation_label)}
            <small className={pick.promoted_to_second_board ? "promoted" : ""}>
              {!pick.outcome_ready
                ? "1进2待确认"
                : pick.promoted_to_second_board
                  ? "已晋级二板"
                  : "未晋级二板"}
            </small>
          </span>
          <span>
            {formatOptionalPercent(
              showLatestReturn ? latestTrackedReturn(pick) : pick.next_open_to_close_pct,
            )}
          </span>
          <ReviewPostBars
            bars={pick.post_bars}
            expectedCount={pick.expected_post_bar_count}
          />
        </Link>
      ))}
    </div>
  );
}

function ReviewPostBars({
  bars,
  expectedCount,
}: {
  bars: ReviewAgentPick["post_bars"];
  expectedCount: number;
}) {
  return (
    <div className="review-post-bars">
      {Array.from({ length: 6 }, (_, index) => {
        const bar = bars[index];
        const label = index === 0 ? "首板" : `D+${index}`;
        if (!bar) {
          const cacheMissing = index < expectedCount;
          return (
            <span
              className={cacheMissing ? "cache-missing" : "awaiting-close"}
              key={`${label}-empty`}
              title={cacheMissing ? "该交易日行情缓存缺失" : "等待后续交易日收盘"}
            >
              <small>{label}</small>
              <strong>{cacheMissing ? "缺缓存" : "待收盘"}</strong>
            </span>
          );
        }
        return (
          <span
            className={
              bar.return_from_base_pct === null
                ? ""
                : bar.return_from_base_pct >= 0
                  ? "positive"
                  : "negative"
            }
            key={bar.trade_date}
            title={`${bar.trade_date} 收盘 ${bar.close.toFixed(2)}`}
          >
            <small>{label}</small>
            <strong>{formatOptionalPercent(bar.return_from_base_pct)}</strong>
          </span>
        );
      })}
    </div>
  );
}

function reviewLabelCopy(label: string) {
  const labels: Record<string, string> = {
    success: "兑现",
    partial: "部分兑现",
    miss: "失败",
    avoid_success: "规避有效",
    false_negative: "漏判",
    pending: "待观察",
  };
  return labels[label] ?? label;
}

function trackedReturnLabel(value: number | null) {
  if (value === null) return "待观察";
  if (value > 0) return "累计上涨";
  if (value < 0) return "累计下跌";
  return "累计持平";
}

function FirstBoardRatingPanel({
  ratings,
  intelligence,
}: {
  ratings: FirstBoardRatingsResponse;
  intelligence: RecommendationIntelligenceResponse | null;
}) {
  /** Render the first-board rating summary generated from deterministic facts. */

  const topCandidates = ratings.candidates.slice(0, 10);
  const topCandidate = topCandidates[0];

  return (
    <Panel title="一进二接力" icon={<BarChart3 size={18} />}>
      <div className="rating-summary-panel">
        <div className="rating-summary-facts">
          <span>{ratings.trade_date}</span>
          <span>{ratings.snapshot_source === "live" ? "已固化预测" : "动态研究结果"}</span>
          {ratings.data_as_of ? <span>数据截止 {ratings.data_as_of}</span> : null}
          <strong>{ratings.candidates.length} 只入池</strong>
          <strong>{ratings.filtered_out.length} 只过滤</strong>
          <strong>{topCandidate ? `${topCandidate.rating} / ${topCandidate.score.toFixed(1)}` : "暂无候选"}</strong>
        </div>
        {topCandidates.length > 0 ? (
          <div className="rating-top-list">
            {topCandidates.map((candidate, index) => {
              const live = recommendationIntelligenceFor(
                intelligence,
                "relay",
                candidate.facts.symbol,
              );
              return <Link
                className="rating-top-card"
                key={`${candidate.facts.trade_date}-${candidate.facts.symbol}`}
                to={stockDetailPath(
                  candidate.facts.symbol,
                  candidate.facts.trade_date,
                  candidate.facts.name,
                )}
              >
                <header>
                  <div>
                    <span>Top {index + 1}</span>
                    <strong>{candidate.facts.name}</strong>
                    <small>{candidate.facts.symbol} / {candidate.facts.industry}</small>
                  </div>
                  <div className="rating-top-score">
                    <b>{candidate.score.toFixed(1)}</b>
                    <span className={`rating-badge rating-${candidate.rating.toLowerCase()}`}>
                      {candidate.rating}
                    </span>
                  </div>
                </header>

                <div className="rating-top-facts">
                  <Fact label="首封" value={candidate.facts.first_limit_time.slice(0, 5)} />
                  <Fact label="炸板" value={`${candidate.facts.break_count}`} />
                  <Fact label="换手" value={`${candidate.facts.turnover_rate.toFixed(1)}%`} />
                  <Fact label="成交额" value={formatAmount(candidate.facts.amount)} />
                  <Fact label="置信度" value={formatPercent(candidate.confidence)} />
                </div>

                <RecommendationLiveEvidence item={live} />

                {candidate.facts.enrichment?.position ? (
                  <div className="rating-position-compact">
                    <div>
                      <MapPin size={15} />
                      <strong>{candidate.facts.enrichment.position.primary.label}</strong>
                    </div>
                    <span>匹配 {candidate.facts.enrichment.position.primary.score.toFixed(0)}</span>
                    <small>{candidate.facts.enrichment.position.tags.slice(0, 2).join(" / ")}</small>
                  </div>
                ) : null}

                <section className="rating-top-reasons">
                  <strong>评分高的原因</strong>
                  <ul>
                    {candidate.reasons.slice(0, 3).map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                </section>

                {candidate.risks.length > 0 ? (
                  <p className="rating-top-risk">
                    风险观察：{candidate.risks.slice(0, 2).join("；")}
                  </p>
                ) : null}
              </Link>;
            })}
          </div>
        ) : (
          <div className="rating-summary-copy">
            <p>当前交易日没有满足过滤条件的首板候选。</p>
          </div>
        )}
      </div>
    </Panel>
  );
}

function FirstBoardDiscoveryPanel({
  data,
  error,
  intelligence,
  loading,
}: {
  data: FirstBoardDiscoveryResponse | null;
  error: string | null;
  intelligence: RecommendationIntelligenceResponse | null;
  loading: boolean;
}) {
  if (loading) {
    return (
      <Panel title="首板挖掘" icon={<TrendingUp size={18} />}>
        <div className="discovery-state"><LoaderCircle className="spin" size={20} />正在读取最新挖掘快照</div>
      </Panel>
    );
  }
  if (error || !data) {
    return (
      <Panel title="首板挖掘" icon={<TrendingUp size={18} />}>
        <div className="discovery-state discovery-state-error">
          <ShieldAlert size={20} />
          <div><strong>暂时没有可用的首板挖掘结果</strong><span>{error ?? "请先执行每日数据更新"}</span></div>
        </div>
      </Panel>
    );
  }

  return (
    <Panel title="首板挖掘" icon={<TrendingUp size={18} />}>
      <div className="rating-summary-panel discovery-panel">
        <div className="discovery-header">
          <div>
            <strong>{data.target_trade_date ? `${data.target_trade_date} 观察池` : "下一交易日观察池"}</strong>
            <span>基于 {data.data_as_of} 收盘后的强题材与新闻催化构建候选池，再按量价结构精排</span>
          </div>
          <div className="rating-summary-facts">
            <span>全市场 {data.universe_count}</span>
            <span>硬过滤后 {data.eligible_count}</span>
            <span>精排 {data.recalled_count}</span>
            <strong>Top {data.candidates.length}</strong>
          </div>
        </div>
        {data.themes.length > 0 ? (
          <div className="discovery-themes" aria-label="当日热门题材">
            <strong>当日强题材</strong>
            <div>
              {data.themes.map((theme) => (
                <span key={`${theme.category}-${theme.name}`}>
                  {theme.name} <b>{formatSigned(theme.change_pct, 1)}%</b>
                  {theme.news_headlines.length > 0 ? <small>有催化</small> : null}
                </span>
              ))}
            </div>
          </div>
        ) : null}

        {data.candidates.length > 0 ? (
          <div className="rating-top-list discovery-list">
            {data.candidates.map((candidate, index) => {
              const live = recommendationIntelligenceFor(
                intelligence,
                "discovery",
                candidate.facts.symbol,
              );
              return <Link
                className="rating-top-card discovery-card"
                key={`${data.data_as_of}-${candidate.facts.symbol}`}
                to={stockDetailPath(candidate.facts.symbol, data.data_as_of, candidate.facts.name)}
              >
                <header>
                  <div>
                    <span>Top {index + 1} · {discoveryPatternLabel(candidate.facts.pattern)}</span>
                    <strong>{candidate.facts.name}</strong>
                    <small>{candidate.facts.symbol} / 收盘 {candidate.facts.close.toFixed(2)}</small>
                  </div>
                  <div className="rating-top-score">
                    <b>{candidate.score.toFixed(1)}</b>
                    <span className={`rating-badge rating-${candidate.rating.toLowerCase()}`}>
                      {candidate.rating}
                    </span>
                  </div>
                </header>
                <div className="rating-top-facts">
                  <Fact label="核心题材" value={candidate.facts.themes[0]?.name ?? "暂无"} />
                  <Fact label="题材涨幅" value={candidate.facts.themes[0] ? `${formatSigned(candidate.facts.themes[0].change_pct, 1)}%` : "暂无"} />
                  <Fact label="热股榜" value={candidate.facts.popularity_rank ? `第 ${candidate.facts.popularity_rank} 名` : "未进Top100"} />
                  <Fact label="当日涨幅" value={`${formatSigned(candidate.facts.change_pct, 1)}%`} />
                  <Fact label="近5日" value={formatNullableSigned(candidate.facts.return_5d_pct)} />
                  <Fact label="量比" value={candidate.facts.volume_ratio_5d?.toFixed(2) ?? "暂无"} />
                </div>
                <section className="discovery-catalyst">
                  <strong>题材与催化</strong>
                  <p>
                    {candidate.facts.news_catalysts[0]
                      ?? `${candidate.facts.themes[0]?.name ?? "相关题材"}当日走强，暂未匹配到明确新闻催化`}
                  </p>
                </section>
                <RecommendationLiveEvidence item={live} />
                <section className="rating-top-reasons">
                  <strong>入选依据</strong>
                  <ul>
                    {candidate.reasons.slice(0, 3).map((reason) => <li key={reason}>{reason}</li>)}
                  </ul>
                </section>
                {candidate.risks.length > 0 ? (
                  <p className="rating-top-risk">待验证：{candidate.risks.slice(0, 2).join("；")}</p>
                ) : null}
              </Link>;
            })}
          </div>
        ) : (
          <div className="empty-state">本期没有满足数据和流动性要求的观察标的。</div>
        )}
        <p className="discovery-disclaimer">
          这是热门题材、新闻催化与量价结构的研究排序，不代表次日涨停概率。
        </p>
      </div>
    </Panel>
  );
}

function discoveryPatternLabel(pattern: FirstBoardDiscoveryPattern) {
  const labels: Record<FirstBoardDiscoveryPattern, string> = {
    low_base_breakout: "低位突破",
    trend_acceleration: "趋势加速",
    oversold_rebound: "超跌反弹",
    second_wave: "二波观察",
    range_breakout: "区间突破",
    unclassified: "结构观察",
  };
  return labels[pattern];
}

function recommendationIntelligenceFor(
  response: RecommendationIntelligenceResponse | null,
  strategy: "discovery" | "relay",
  symbol: string,
) {
  return response?.items.find(
    (item) => item.strategy === strategy && item.symbol === symbol,
  ) ?? null;
}

function RecommendationLiveEvidence({
  item,
}: {
  item: RecommendationIntelligenceItem | null;
}) {
  if (!item) return null;
  const report = item.financial_report;
  const news = item.latest_news[0];
  return (
    <section className="recommendation-live-evidence">
      <div className="recommendation-live-quote">
        <strong>最新动态</strong>
        <span>{item.current_price !== null ? item.current_price.toFixed(2) : "价格暂无"}</span>
        <b className={(item.change_pct ?? 0) >= 0 ? "positive" : "negative"}>
          {item.change_pct !== null ? `${formatSigned(item.change_pct, 2)}%` : "涨幅暂无"}
        </b>
      </div>
      {news ? (
        <p className="recommendation-live-news">
          <Newspaper size={14} />
          <span>{news.title}</span>
          <small>{formatStockNewsTime(news.published_at)}</small>
        </p>
      ) : null}
      {report ? (
        <div className="recommendation-live-financial">
          <span>{report.fiscal_year} {report.fiscal_period}</span>
          <span>营收同比 {formatNullableSigned(report.operating_income_yoy_pct)}</span>
          <span>归母净利同比 {formatNullableSigned(report.net_profit_yoy_pct)}</span>
        </div>
      ) : null}
    </section>
  );
}

function formatNullableSigned(value: number | null) {
  return value === null ? "暂无" : `${formatSigned(value, 1)}%`;
}

function RatingBacktestPanel({ backtest }: { backtest: RatingBacktestResponse }) {
  /** Show whether the current first-board scoring rubric has worked recently. */

  const readyRate = backtest.sample_size > 0
    ? backtest.outcome_ready_count / backtest.sample_size
    : 0;

  return (
    <Panel title="评分回测与自我评价" icon={<BarChart3 size={18} />}>
      <div className="backtest-panel">
        <div className="backtest-summary">
          <div>
            <span>回测区间</span>
            <strong>
              {backtest.start_date} 至 {backtest.end_date}
            </strong>
          </div>
          <div>
            <span>样本数量</span>
            <strong>{backtest.sample_size} 个</strong>
          </div>
          <div>
            <span>走势覆盖</span>
            <strong>{formatPercent(readyRate)}</strong>
          </div>
          <div>
            <span>生成方式</span>
            <strong>{backtest.generated_by}</strong>
          </div>
        </div>

        <div className="backtest-buckets">
          {backtest.buckets.map((bucket) => (
            <article className="backtest-bucket" key={bucket.rating}>
              <header>
                <span className={`rating-badge rating-${bucket.rating.toLowerCase()}`}>
                  {bucket.rating}
                </span>
                <div>
                  <strong>{bucket.outcome_ready_count} / {bucket.sample_size}</strong>
                  <span>已完成走势样本</span>
                </div>
              </header>
              <dl>
                <div>
                  <dt>开盘后最高</dt>
                  <dd>{formatOptionalPercent(bucket.avg_next_open_to_high_pct)}</dd>
                </div>
                <div>
                  <dt>开盘后收盘</dt>
                  <dd>{formatOptionalPercent(bucket.avg_next_open_to_close_pct)}</dd>
                </div>
                <div>
                  <dt>次日上涨率</dt>
                  <dd>
                    {bucket.next_open_to_close_positive_rate === null
                      ? "暂无"
                      : formatPercent(bucket.next_open_to_close_positive_rate)}
                  </dd>
                </div>
                <div>
                  <dt>大跌率</dt>
                  <dd>
                    {bucket.next_open_to_close_large_loss_rate === null
                      ? "暂无"
                      : formatPercent(bucket.next_open_to_close_large_loss_rate)}
                  </dd>
                </div>
              </dl>
            </article>
          ))}
        </div>

        <div className="backtest-insights">
          <section>
            <h3>回测观察</h3>
            {backtest.observations.length > 0 ? (
              <ul>
                {backtest.observations.map((observation) => (
                  <li key={observation}>{observation}</li>
                ))}
              </ul>
            ) : (
              <p>暂无足够样本形成稳定观察。</p>
            )}
            {backtest.warnings.length > 0 ? (
              <div className="backtest-warnings">
                {backtest.warnings.map((warning) => (
                  <span key={warning}>{warning}</span>
                ))}
              </div>
            ) : null}
          </section>

          <section>
            <h3>高分弱表现样本</h3>
            {backtest.failure_samples.length > 0 ? (
              <div className="failure-sample-list">
                {backtest.failure_samples.slice(0, 4).map((sample) => (
                  <article className="failure-sample-card" key={`${sample.symbol}-${sample.trade_date}`}>
                    <header>
                      <div>
                        <strong>{sample.name}</strong>
                        <span>{sample.symbol} / {sample.trade_date}</span>
                      </div>
                      <span className={`rating-badge rating-${sample.rating.toLowerCase()}`}>
                        {sample.rating}
                      </span>
                    </header>
                    <dl>
                      <div>
                        <dt>评分</dt>
                        <dd>{sample.score.toFixed(1)}</dd>
                      </div>
                      <div>
                        <dt>次日开收</dt>
                        <dd>{formatOptionalPercent(sample.next_open_to_close_pct)}</dd>
                      </div>
                      <div>
                        <dt>三日开收</dt>
                        <dd>{formatOptionalPercent(sample.three_day_open_to_close_pct)}</dd>
                      </div>
                    </dl>
                    <p>{sample.risks[0] ?? sample.reasons[0] ?? "需要结合分时与题材强度复盘。"}</p>
                  </article>
                ))}
              </div>
            ) : (
              <p>当前没有可展示的高分弱表现样本。</p>
            )}
          </section>
        </div>
      </div>
    </Panel>
  );
}

function RatingEvaluationPanel({ evaluation }: { evaluation: AgentEvaluationResponse }) {
  /** Render saved prediction review results from the Evaluation Agent. */

  const readyRate = evaluation.prediction_count > 0
    ? evaluation.outcome_ready_count / evaluation.prediction_count
    : 0;
  const priorityItems = evaluation.evaluations.slice(0, 6);

  return (
    <Panel title="Evaluation Agent 预测复盘" icon={<ShieldAlert size={18} />}>
      <div className="evaluation-panel">
        <div className="evaluation-summary">
          <div>
            <span>复盘区间</span>
            <strong>{evaluation.start_date} 至 {evaluation.end_date}</strong>
          </div>
          <div>
            <span>预测快照</span>
            <strong>
              实时 {evaluation.source_counts.live ?? 0} / 回测 {evaluation.source_counts.historical_backtest ?? 0}
            </strong>
          </div>
          <div>
            <span>Outcome 覆盖</span>
            <strong>{formatPercent(readyRate)}</strong>
          </div>
          <div>
            <span>误判 / 漏判</span>
            <strong>
              {evaluation.label_counts.miss ?? 0} / {evaluation.label_counts.false_negative ?? 0}
            </strong>
          </div>
        </div>

        <div className="evaluation-labels">
          {evaluationLabelOrder.map((label) => (
            <div className={`evaluation-label label-${label}`} key={label}>
              <span>{evaluationLabelCopy[label]}</span>
              <strong>{evaluation.label_counts[label] ?? 0}</strong>
            </div>
          ))}
        </div>

        <div className="evaluation-insights">
          <section>
            <h3>复盘摘要</h3>
            {evaluation.summary.length > 0 ? (
              <ul>
                {evaluation.summary.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            ) : (
              <p>暂无可展示的复盘摘要。</p>
            )}
            {evaluation.warnings.length > 0 ? (
              <div className="backtest-warnings">
                {evaluation.warnings.map((warning) => (
                  <span key={warning}>{warning}</span>
                ))}
              </div>
            ) : null}
          </section>

          <section>
            <h3>重点样本</h3>
            {priorityItems.length > 0 ? (
              <div className="evaluation-card-list">
                {priorityItems.map((item) => (
                  <article
                    className={`evaluation-card label-${item.evaluation_label}`}
                    key={item.prediction_id}
                  >
                    <header>
                      <div>
                        <strong>{item.name}</strong>
                        <span>
                          {item.symbol} / {item.trade_date} / {item.prediction_source === "live" ? "实时预测" : "历史回测"}
                        </span>
                      </div>
                      <b>{evaluationLabelCopy[item.evaluation_label]}</b>
                    </header>
                    <dl>
                      <div>
                        <dt>评分</dt>
                        <dd>{item.rating} / {item.score.toFixed(1)}</dd>
                      </div>
                      <div>
                        <dt>晋级二板</dt>
                        <dd>{item.promoted_to_second_board ? "是" : "否"}</dd>
                      </div>
                      <div>
                        <dt>次日开收</dt>
                        <dd>{formatOptionalPercent(item.next_open_to_close_pct)}</dd>
                      </div>
                      <div>
                        <dt>三日最大回撤</dt>
                        <dd>{formatOptionalPercent(item.max_drawdown_from_next_open_3d)}</dd>
                      </div>
                    </dl>
                    <p>{item.lesson}</p>
                    <p>{item.scoring_suggestion}</p>
                  </article>
                ))}
              </div>
            ) : (
              <p>暂无重点复盘样本。</p>
            )}
          </section>
        </div>
      </div>
    </Panel>
  );
}

const evaluationLabelOrder = [
  "success",
  "partial",
  "miss",
  "false_negative",
  "avoid_success",
  "pending",
] as const;

const evaluationLabelCopy: Record<(typeof evaluationLabelOrder)[number], string> = {
  success: "成功",
  partial: "部分验证",
  miss: "误判",
  false_negative: "漏判",
  avoid_success: "规避有效",
  pending: "待验证",
};

function DetailView({ view, data }: { view: StockListViewKey; data: DashboardData }) {
  /** Render one of the latest-day stock list views. */

  const eventsByView: Record<StockListViewKey, LimitUpEvent[]> = {
    first: data.firstBoard,
    continued: data.continuedBoard,
    failed: data.failed,
  };

  if (view === "first") {
    return (
      <Panel title="首板智能评级" icon={detailIcon(view)}>
        <FirstBoardRatingTable ratings={data.firstBoardRatings.candidates} />
      </Panel>
    );
  }

  return (
    <Panel title={viewMeta[view].title} icon={detailIcon(view)}>
      <StockTable events={eventsByView[view]} variant={view} />
    </Panel>
  );
}

function RecentLimitUp({ events }: { events: LimitUpEvent[] }) {
  /** Group recent events by persisted trading date for review. */

  const dateGroups = useMemo(() => {
    const grouped = events.reduce<Record<string, LimitUpEvent[]>>((groups, event) => {
      groups[event.trade_date] = groups[event.trade_date] ?? [];
      groups[event.trade_date].push(event);
      return groups;
    }, {});
    return Object.entries(grouped).sort(([left], [right]) => right.localeCompare(left));
  }, [events]);
  const [expandedDates, setExpandedDates] = useState<string[]>(() => (
    dateGroups[0] ? [dateGroups[0][0]] : []
  ));
  const allExpanded = expandedDates.length === dateGroups.length;

  function toggleDate(tradeDate: string) {
    setExpandedDates((current) => (
      current.includes(tradeDate)
        ? current.filter((item) => item !== tradeDate)
        : [...current, tradeDate]
    ));
  }

  return (
    <div className="recent-groups">
      <div className="recent-groups-toolbar">
        <div>
          <strong>最近 {dateGroups.length} 个交易日</strong>
          <span>共 {events.length} 条封板记录，默认展开最新交易日</span>
        </div>
        <div className="recent-groups-actions">
          <button
            type="button"
            onClick={() => setExpandedDates(dateGroups.map(([date]) => date))}
            disabled={allExpanded}
          >
            <ChevronDown size={16} aria-hidden="true" />
            全部展开
          </button>
          <button type="button" onClick={() => setExpandedDates([])} disabled={expandedDates.length === 0}>
            <Minus size={16} aria-hidden="true" />
            全部收起
          </button>
        </div>
      </div>

      {dateGroups.map(([tradeDate, items], index) => {
        const expanded = expandedDates.includes(tradeDate);
        const firstBoardCount = items.filter((item) => item.board_height === 1).length;
        const continuedBoardCount = items.length - firstBoardCount;
        const maxBoardHeight = Math.max(...items.map((item) => item.board_height));
        const contentId = `recent-limit-up-${tradeDate}`;
        return (
          <section className={`recent-date-group ${expanded ? "expanded" : ""}`} key={tradeDate}>
            <button
              aria-controls={contentId}
              aria-expanded={expanded}
              className="recent-date-toggle"
              type="button"
              onClick={() => toggleDate(tradeDate)}
            >
              <span className="recent-date-primary">
                {expanded
                  ? <ChevronDown size={19} aria-hidden="true" />
                  : <ChevronRight size={19} aria-hidden="true" />}
                <strong>{tradeDate}</strong>
                {index === 0 ? <em>最新</em> : null}
              </span>
              <span className="recent-date-summary">
                <b>{items.length} 只</b>
                <small>首板 {firstBoardCount}</small>
                <small>连板 {continuedBoardCount}</small>
                <small>最高 {maxBoardHeight} 板</small>
              </span>
            </button>
            {expanded ? (
              <div className="recent-date-content" id={contentId}>
                <StockTable events={items} variant="recent" />
              </div>
            ) : null}
          </section>
        );
      })}
    </div>
  );
}

function LimitUpPool({ data }: { data: DashboardData }) {
  /** Group the four limit-up datasets behind one focused navigation page. */

  const entries = [
    {
      to: "/stocks/first-board",
      label: "首板",
      count: `${data.firstBoard.length} 只`,
      description: "查看当日首次涨停股票与 Agent 评分",
      icon: <Flame size={18} />,
    },
    {
      to: "/stocks/continued-board",
      label: "连板",
      count: `${data.continuedBoard.length} 只`,
      description: "查看当日二板及以上连板梯队",
      icon: <Layers3 size={18} />,
    },
    {
      to: "/stocks/failed",
      label: "炸板",
      count: `${data.failed.length} 只`,
      description: "查看盘中触板但未能封住的股票",
      icon: <ShieldAlert size={18} />,
    },
    {
      to: "/stocks/recent-limit-up",
      label: "近五日涨停票",
      count: `${data.recent.length} 条`,
      description: "按交易日回看最近五日涨停记录",
      icon: <TrendingUp size={18} />,
    },
  ];

  return (
    <nav className="overview-grid" aria-label="涨停池分类">
      {entries.map((entry) => (
        <Link className="entry-card" key={entry.to} to={entry.to}>
          <div className="metric-icon" aria-hidden="true">{entry.icon}</div>
          <span>{entry.label}</span>
          <strong>{entry.count}</strong>
          <p>{entry.description}</p>
        </Link>
      ))}
    </nav>
  );
}

function FirstBoardRatingTable({ ratings }: { ratings: FirstBoardRating[] }) {
  /** Render first-board candidates sorted by agent score. */

  const navigate = useNavigate();

  if (ratings.length === 0) {
    return <div className="empty-state">当前交易日没有满足过滤条件的首板候选。</div>;
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>股票</th>
            <th>评分</th>
            <th>评级</th>
            <th>置信度</th>
            <th>首封</th>
            <th>炸板</th>
            <th>成交额</th>
            <th>换手</th>
            <th>行业热度</th>
            <th>理由 / 风险</th>
          </tr>
        </thead>
        <tbody>
          {ratings.map((rating) => (
            <tr
              className="stock-row"
              key={`${rating.facts.trade_date}-${rating.facts.symbol}`}
              onClick={() => navigate(stockDetailPath(
                rating.facts.symbol,
                rating.facts.trade_date,
              ))}
              onKeyDown={(keyboardEvent) => {
                if (keyboardEvent.key === "Enter" || keyboardEvent.key === " ") {
                  keyboardEvent.preventDefault();
                  navigate(stockDetailPath(
                    rating.facts.symbol,
                    rating.facts.trade_date,
                  ));
                }
              }}
              tabIndex={0}
            >
              <td>
                <strong>{rating.facts.name}</strong>
                <span>{rating.facts.symbol}</span>
              </td>
              <td>
                <strong>{rating.score.toFixed(1)}</strong>
              </td>
              <td>
                <span className={`rating-badge rating-${rating.rating.toLowerCase()}`}>
                  {rating.rating}
                </span>
              </td>
              <td>{formatPercent(rating.confidence)}</td>
              <td>{rating.facts.first_limit_time.slice(0, 5)}</td>
              <td>{rating.facts.break_count}</td>
              <td>{formatAmount(rating.facts.amount)}</td>
              <td>{rating.facts.turnover_rate.toFixed(1)}%</td>
              <td>{rating.facts.same_industry_limit_up_count} 只</td>
              <td>
                <strong>{rating.reasons.slice(0, 2).join("；")}</strong>
                <span>{rating.risks.slice(0, 2).join("；")}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function StockTable({
  events,
  variant,
}: {
  events: LimitUpEvent[];
  variant: ViewKey;
}) {
  /** Shared clickable table for all stock-list routes. */

  const navigate = useNavigate();

  function openStock(symbol: string, tradeDate: string) {
    navigate(stockDetailPath(symbol, tradeDate));
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>股票</th>
            <th>日期</th>
            <th>高度</th>
            <th>首次封板</th>
            <th>最后封板</th>
            <th>{variant === "failed" ? "回封状态" : "封板次数"}</th>
            <th>炸板</th>
            <th>成交额</th>
            <th>换手</th>
            <th>题材</th>
          </tr>
        </thead>
        <tbody>
          {events.map((event) => (
            <tr
              className="stock-row"
              key={`${event.trade_date}-${event.symbol}`}
              onClick={() => openStock(event.symbol, event.trade_date)}
              onKeyDown={(keyboardEvent) => {
                if (keyboardEvent.key === "Enter" || keyboardEvent.key === " ") {
                  keyboardEvent.preventDefault();
                  openStock(event.symbol, event.trade_date);
                }
              }}
              tabIndex={0}
            >
              <td>
                <strong>{event.name}</strong>
                <span>{event.symbol}</span>
              </td>
              <td>{event.trade_date}</td>
              <td>{event.closed_limit ? `${event.board_height} 板` : "未封板"}</td>
              <td>{event.first_limit_time.slice(0, 5)}</td>
              <td>{event.last_limit_time.slice(0, 5)}</td>
              <td>{variant === "failed" ? (event.closed_limit ? "回封" : "未回封") : event.seal_count}</td>
              <td>{event.break_count}</td>
              <td>{formatAmount(event.amount)}</td>
              <td>{event.turnover_rate.toFixed(1)}%</td>
              <td>
                <strong>{event.concept}</strong>
                <span>{event.industry}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StockDetail({ data }: { data: DashboardData }) {
  /** Render one stock's event facts together with daily and intraday K-lines. */

  const { symbol = "" } = useParams();
  const [searchParams] = useSearchParams();
  const requestedTradeDate = searchParams.get("trade_date") ?? undefined;
  const linkedStockName = searchParams.get("name")?.trim() ?? "";
  const [stockEvent, setStockEvent] = useState<LimitUpEvent | null>(null);
  const [stockEventLoading, setStockEventLoading] = useState(true);
  const [stockEventError, setStockEventError] = useState<string | null>(null);
  const [firstBoardRating, setFirstBoardRating] = useState<FirstBoardRating | null>(null);
  const [kline, setKline] = useState<StockKLineBar[]>([]);
  const [tradingDayKline, setTradingDayKline] = useState<StockIntradayKLineBar[]>([]);
  const [chartMode, setChartMode] = useState<"daily" | "intraday">("daily");
  const [latestClose, setLatestClose] = useState<StockCloseSnapshot | null>(null);
  const [stockNews, setStockNews] = useState<StockNewsFacts | null>(null);
  const [position, setPosition] = useState<StockPositionAssessment | null>(null);
  const [critic, setCritic] = useState<FirstBoardCriticResponse | null>(null);
  const [klineLoading, setKlineLoading] = useState(true);
  const [klineError, setKlineError] = useState<string | null>(null);
  const [tradingDayLoading, setTradingDayLoading] = useState(true);
  const [tradingDayError, setTradingDayError] = useState<string | null>(null);
  const [latestCloseLoading, setLatestCloseLoading] = useState(true);
  const [latestCloseError, setLatestCloseError] = useState<string | null>(null);
  const [stockNewsLoading, setStockNewsLoading] = useState(true);
  const [positionLoading, setPositionLoading] = useState(true);
  const [positionError, setPositionError] = useState<string | null>(null);
  const [criticLoading, setCriticLoading] = useState(false);
  const resolvedTradeDate = stockEvent?.trade_date
    ?? requestedTradeDate
    ?? data.summary.trade_date;

  useEffect(() => {
    let active = true;
    setStockEvent(null);
    setStockEventLoading(true);
    setStockEventError(null);
    fetchStockEvent(symbol, requestedTradeDate)
      .then((event) => {
        if (active) {
          setStockEvent(event);
        }
      })
      .catch((caught) => {
        if (active) {
          setStockEventError(caught instanceof Error ? caught.message : "加载涨停事件失败");
        }
      })
      .finally(() => {
        if (active) {
          setStockEventLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [requestedTradeDate, symbol]);

  useEffect(() => {
    if (stockEventLoading) {
      return;
    }
    let active = true;
    setStockNews(null);
    setStockNewsLoading(true);
    fetchStockNews(symbol, stockEvent?.name || linkedStockName || undefined, 3)
      .then((news) => {
        if (active) {
          setStockNews(news);
        }
      })
      .catch(() => {
        if (active) {
          setStockNews(null);
        }
      })
      .finally(() => {
        if (active) {
          setStockNewsLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [linkedStockName, stockEvent?.name, stockEventLoading, symbol]);

  useEffect(() => {
    if (stockEventLoading) {
      return;
    }
    const tradeDate = resolvedTradeDate;
    let active = true;

    setKline([]);
    setTradingDayKline([]);
    setPosition(null);
    setLatestClose(null);
    setLatestCloseLoading(true);
    setLatestCloseError(null);
    fetchStockLatestClose(symbol)
      .then((snapshot) => {
        if (active) {
          setLatestClose(snapshot);
        }
      })
      .catch((caught) => {
        if (active) {
          setLatestClose(null);
          setLatestCloseError(caught instanceof Error ? caught.message : "加载最新收盘数据失败");
        }
      })
      .finally(() => {
        if (active) {
          setLatestCloseLoading(false);
        }
      });

    setKlineLoading(true);
    setKlineError(null);
    fetchStockKLine(symbol, 60)
      .then((bars) => {
        if (active) {
          setKline(bars);
        }
      })
      .catch((caught) => {
        if (active) {
          setKlineError(caught instanceof Error ? caught.message : "加载 60 日 K 线失败");
        }
      })
      .finally(() => {
        if (active) {
          setKlineLoading(false);
        }
      });

    if (stockEvent) {
      setPositionLoading(true);
      setPositionError(null);
      fetchStockPosition(symbol, tradeDate)
        .then((assessment) => {
          if (active) {
            setPosition(assessment);
          }
        })
        .catch((caught) => {
          if (active) {
            setPosition(null);
            setPositionError(caught instanceof Error ? caught.message : "加载首板位置判断失败");
          }
        })
        .finally(() => {
          if (active) {
            setPositionLoading(false);
          }
        });
    } else {
      setPositionLoading(false);
      setPositionError(null);
    }

    setTradingDayLoading(true);
    setTradingDayError(null);
    fetchStockTradingDayKLine(symbol, 1, tradeDate)
      .then((bars) => {
        if (active) {
          setTradingDayKline(bars);
        }
      })
      .catch((caught) => {
        if (active) {
          setTradingDayError(caught instanceof Error ? caught.message : "加载交易日走势失败");
        }
      })
      .finally(() => {
        if (active) {
          setTradingDayLoading(false);
        }
      });

    const cachedRating = stockEvent && data.firstBoardRatings.trade_date === tradeDate
      ? data.firstBoardRatings.candidates.find(
          (rating) => rating.facts.symbol === symbol,
        ) ?? null
      : null;
    setFirstBoardRating(cachedRating);
    if (stockEvent) {
      fetchFirstBoardRatings(tradeDate)
        .then((ratings) => {
          if (active) {
            setFirstBoardRating(
              ratings.candidates.find((rating) => rating.facts.symbol === symbol) ?? null,
            );
          }
        })
        .catch(() => {
          if (active && !cachedRating) {
            setFirstBoardRating(null);
          }
        });
    }

    return () => {
      active = false;
    };
  }, [
    data.firstBoardRatings,
    resolvedTradeDate,
    stockEvent,
    stockEventLoading,
    symbol,
  ]);

  useEffect(() => {
    if (!firstBoardRating) {
      setCritic(null);
      setCriticLoading(false);
      return;
    }

    setCritic(null);
    setCriticLoading(true);
    fetchFirstBoardCritic(symbol, firstBoardRating.facts.trade_date)
      .then(setCritic)
      .catch(() => setCritic(null))
      .finally(() => setCriticLoading(false));
  }, [firstBoardRating, symbol]);

  const intradayReferencePrice = useMemo(() => {
    const eventIndex = kline.findIndex(
      (bar) => bar.trade_date === resolvedTradeDate,
    );
    return eventIndex > 0 ? kline[eventIndex - 1].close : null;
  }, [kline, resolvedTradeDate]);

  if (stockEventLoading) {
    return <ShellState label="正在加载个股详情..." />;
  }

  return (
    <div className="stock-detail">
      <section className="stock-hero">
        <div>
          <p className="eyebrow">{resolvedTradeDate}</p>
          <h2>{stockEvent?.name || linkedStockName || symbol}</h2>
          <span>{symbol}</span>
        </div>
        <div className="stock-status">
          <strong>
            {stockEvent
              ? stockEvent.closed_limit ? `${stockEvent.board_height} 板` : "炸板"
              : "行情详情"}
          </strong>
          <span>
            {stockEvent
              ? stockEvent.closed_limit ? "已封板" : "盘中触板未回封"
              : stockEventError ? "当前不在本地涨停事件库" : "基础行情"}
          </span>
        </div>
      </section>

      <LatestCloseStrip
        snapshot={latestClose}
        loading={latestCloseLoading}
        error={latestCloseError}
      />

      {stockEvent ? (
        <Panel title="封板信息" icon={<Flame size={18} />}>
          <div className="stock-facts">
            <Fact label="首次封板" value={stockEvent.first_limit_time.slice(0, 5)} />
            <Fact label="最后封板" value={stockEvent.last_limit_time.slice(0, 5)} />
            <Fact label="封板次数" value={`${stockEvent.seal_count}`} />
            <Fact label="炸板次数" value={`${stockEvent.break_count}`} />
            <Fact label="成交额" value={formatAmount(stockEvent.amount)} />
            <Fact label="换手率" value={`${stockEvent.turnover_rate.toFixed(1)}%`} />
            <Fact label="行业" value={stockEvent.industry} />
            <Fact label="题材" value={stockEvent.concept || "暂无"} />
          </div>
        </Panel>
      ) : null}

      <section className="stock-market-chart">
        <Panel
          title="行情走势"
          icon={<LineChart size={18} />}
          actions={(
            <div className="chart-mode-switch" aria-label="行情周期">
              <button
                type="button"
                aria-pressed={chartMode === "daily"}
                onClick={() => setChartMode("daily")}
              >
                日 K · 60日
              </button>
              <button
                type="button"
                aria-pressed={chartMode === "intraday"}
                onClick={() => setChartMode("intraday")}
              >
                分时
              </button>
            </div>
          )}
        >
          {chartMode === "daily" ? (
            klineLoading ? (
              <div className="chart-state">正在加载 60 日 K 线...</div>
            ) : klineError ? (
              <div className="chart-state">{klineError}</div>
            ) : (
              <MarketKLineChart
                bars={toDailyCandleBars(kline)}
                emptyLabel="暂无 60 日 K 线数据"
                mode="daily"
              />
            )
          ) : tradingDayLoading ? (
            <div className="chart-state">正在加载交易日走势...</div>
          ) : tradingDayError ? (
            <div className="chart-state">{tradingDayError}</div>
          ) : (
            <MarketKLineChart
              bars={toIntradayCandleBars(tradingDayKline)}
              emptyLabel="暂无交易日走势数据"
              mode="intraday"
              referencePrice={intradayReferencePrice}
            />
          )}
        </Panel>
      </section>

      <StockNewsPanel news={stockNews} loading={stockNewsLoading} />

      {stockEvent ? (
        <StockPositionPanel
          position={position}
          tradeDate={stockEvent.trade_date}
          loading={positionLoading}
          error={positionError}
        />
      ) : null}

      {firstBoardRating || criticLoading || critic ? (
        <section className="stock-agent-grid">
          {firstBoardRating ? <FirstBoardRatingDetail rating={firstBoardRating} /> : null}
          {criticLoading || critic ? (
            <FirstBoardCriticPanel
              data={critic}
              loading={criticLoading}
            />
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

function StockNewsPanel({
  news,
  loading,
}: {
  news: StockNewsFacts | null;
  loading: boolean;
}) {
  if (!loading && (!news || news.items.length === 0)) {
    return null;
  }

  return (
    <Panel
      title="个股资讯"
      icon={<Newspaper size={18} />}
      actions={<span className="stock-news-window">近 7 日</span>}
    >
      {loading ? (
        <div className="stock-news-loading">正在获取个股资讯...</div>
      ) : (
        <div className="stock-news-list">
          {news?.items.slice(0, 3).map((item) => (
            <a
              className="stock-news-item"
              href={item.url}
              key={`${item.published_at}-${item.title}`}
              rel="noreferrer"
              target="_blank"
            >
              <time dateTime={item.published_at}>{formatStockNewsTime(item.published_at)}</time>
              <div>
                <strong>{item.title}</strong>
                <span>{item.source}</span>
              </div>
              <ExternalLink aria-hidden="true" size={16} />
            </a>
          ))}
        </div>
      )}
    </Panel>
  );
}

function formatStockNewsTime(value: string) {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return "时间未知";
  }
  return timestamp.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}


function StockPositionPanel({
  position,
  tradeDate,
  loading,
  error,
}: {
  position: StockPositionAssessment | null;
  tradeDate: string;
  loading: boolean;
  error: string | null;
}) {
  if (loading) {
    return (
      <Panel title="首板位置判断" icon={<MapPin size={18} />}>
        <div className="rating-detail-empty">正在分析价格位置...</div>
      </Panel>
    );
  }

  if (error || !position) {
    return (
      <Panel title="首板位置判断" icon={<MapPin size={18} />}>
        <div className="rating-detail-empty">{error ?? "暂无足够 K 线判断当前位置。"}</div>
      </Panel>
    );
  }

  return (
    <Panel title="首板位置判断" icon={<MapPin size={18} />}>
      <StockPositionDetail position={position} tradeDate={tradeDate} />
    </Panel>
  );
}


function FirstBoardCriticPanel({
  data,
  loading,
}: {
  data: FirstBoardCriticResponse | null;
  loading: boolean;
}) {
  /** Render critic-side review that challenges the original rating. */

  const verdictCopy = {
    supportive: "证据较稳",
    cautious: "需要谨慎",
    fragile: "结论偏脆弱",
  };

  if (loading) {
    return (
      <Panel title="Critic 复核" icon={<ShieldAlert size={18} />}>
        <div className="rating-detail-empty">正在复核评分可靠性...</div>
      </Panel>
    );
  }

  if (!data) {
    return null;
  }

  return (
    <Panel title="Critic 复核" icon={<ShieldAlert size={18} />}>
      <div className="critic-detail">
        <div className={`critic-verdict critic-${data.verdict}`}>
          <div>
            <span>复核结论</span>
            <strong>{verdictCopy[data.verdict]}</strong>
          </div>
          <div>
            <span>置信度建议</span>
            <strong>
              {formatPercent(data.original_confidence)} {"->"} {formatPercent(data.suggested_confidence)}
            </strong>
          </div>
        </div>

        <TagSection title="支持证据" items={data.support_evidence} tone="good" />
        <TagSection title="反向证据" items={data.counter_evidence} tone="risk" />
        {data.missing_data.length > 0 ? (
          <TagSection title="缺失数据" items={data.missing_data} tone="muted" />
        ) : null}
        <TagSection title="复盘问题" items={data.review_questions} tone="muted" />

        {data.critic_warnings.length > 0 ? (
          <div className="critic-warnings">
            {data.critic_warnings.map((warning) => (
              <span key={warning}>{warning}</span>
            ))}
          </div>
        ) : null}
      </div>
    </Panel>
  );
}

function FirstBoardRatingDetail({ rating }: { rating: FirstBoardRating }) {
  /** Render explainable first-board score details for the selected stock. */

  const boardPatternScore = rating.score_breakdown.find((item) => item.name === "上板形态");
  const marketCapScore = rating.score_breakdown.find((item) => item.name === "市值偏好");
  const floatMarketCap = rating.facts.enrichment?.float_market_cap;
  const capRule = floatMarketCap === null || floatMarketCap === undefined
    ? "流通市值数据缺失"
    : floatMarketCap <= 5_000_000_000
      ? "低市值档，因子加分"
      : floatMarketCap > 50_000_000_000
        ? "高市值档，因子降分"
        : "中间市值档，按区间评分";

  return (
    <Panel title="Agent 评分拆解" icon={<BarChart3 size={18} />}>
      <div className="rating-detail">
        <div className="rating-detail-head">
          <span className={`rating-badge rating-${rating.rating.toLowerCase()}`}>
            {rating.rating}
          </span>
          <div>
            <strong>{rating.score.toFixed(1)}</strong>
            <span>置信度 {formatPercent(rating.confidence)}</span>
          </div>
        </div>

        <div className="rating-rule-signals" aria-label="评分规则信号">
          <span>
            <small>上板形态</small>
            <strong>
              {rating.facts.is_one_word_board
                ? "一字板，形态评分降档"
                : "盘中上板，换手承接更充分"}
            </strong>
            {boardPatternScore ? (
              <em>{boardPatternScore.score.toFixed(1)} / {boardPatternScore.max_score.toFixed(1)}</em>
            ) : null}
          </span>
          <span>
            <small>市值偏好</small>
            <strong>{capRule}</strong>
            {marketCapScore ? (
              <em>{marketCapScore.score.toFixed(1)} / {marketCapScore.max_score.toFixed(1)}</em>
            ) : null}
          </span>
        </div>

        {rating.facts.enrichment ? (
          <div className="rating-enrichment-facts">
            <span>
              <small>近20日涨幅</small>
              <strong>{formatOptionalPercent(rating.facts.enrichment.return_20d_pct)}</strong>
            </span>
            <span>
              <small>距60日高点</small>
              <strong>{formatOptionalPercent(rating.facts.enrichment.distance_60d_high_pct)}</strong>
            </span>
            <span>
              <small>流通市值</small>
              <strong>
                {rating.facts.enrichment.float_market_cap === null
                  ? "暂无"
                  : formatAmount(rating.facts.enrichment.float_market_cap)}
              </strong>
            </span>
            <span>
              <small>上市日期</small>
              <strong>{rating.facts.enrichment.listing_date ?? "早期上市"}</strong>
            </span>
            <span>
              <small>龙虎榜</small>
              <strong>{rating.facts.enrichment.dragon_tiger_on_list ? "上榜" : "未上榜"}</strong>
            </span>
            <span>
              <small>东财人气</small>
              <strong>
                {rating.facts.enrichment.popularity_rank === null
                  ? "Top100 外"
                  : `第 ${rating.facts.enrichment.popularity_rank}`}
              </strong>
            </span>
          </div>
        ) : null}

        <div className="rating-detail-section">
          <h3>评分项</h3>
          <div className="score-breakdown-list">
            {rating.score_breakdown.map((item) => (
              <div className="score-breakdown-item" key={item.name}>
                <div>
                  <strong>{item.name}</strong>
                  <span>{item.evidence.join("；")}</span>
                </div>
                <b>{item.score.toFixed(1)} / {item.max_score.toFixed(1)}</b>
              </div>
            ))}
          </div>
        </div>

        <TagSection title="主要理由" items={rating.reasons} tone="good" />
        <TagSection title="风险观察" items={rating.risks} tone="risk" />
        {rating.facts.data_missing.length > 0 ? (
          <TagSection title="缺失数据" items={rating.facts.data_missing} tone="muted" />
        ) : null}
      </div>
    </Panel>
  );
}

function StockPositionDetail({
  position,
  tradeDate,
}: {
  position: StockPositionAssessment;
  tradeDate: string;
}) {
  return (
    <section className="stock-position-detail">
      <header>
        <div>
          <span>{tradeDate} 收盘 · {position.bar_count} 根日 K</span>
        </div>
        <strong>{position.primary.label}</strong>
        <b>匹配度 {position.primary.score.toFixed(0)}</b>
      </header>
      <div className="stock-position-tags">
        {position.tags.map((tag) => <span key={tag}>{tag}</span>)}
      </div>
      <ul>
        {position.evidence.map((item) => <li key={item}>{item}</li>)}
      </ul>
      {position.alternatives.length > 0 ? (
        <small>
          次选：{position.alternatives.map((item) => `${item.label} ${item.score.toFixed(0)}`).join("；")}
        </small>
      ) : null}
    </section>
  );
}

function TagSection({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: "good" | "risk" | "muted";
}) {
  return (
    <div className="rating-detail-section">
      <h3>{title}</h3>
      <div className="tag-list">
        {items.map((item) => (
          <span className={`detail-tag tag-${tone}`} key={item}>{item}</span>
        ))}
      </div>
    </div>
  );
}
function LatestCloseStrip({
  snapshot,
  loading,
  error,
}: {
  snapshot: StockCloseSnapshot | null;
  loading: boolean;
  error: string | null;
}) {
  /** Show latest available after-close price data without blocking charts. */

  if (loading) {
    return <div className="latest-close-strip">正在加载最新收盘数据...</div>;
  }

  if (error || !snapshot) {
    return <div className="latest-close-strip muted">暂无最新收盘数据</div>;
  }

  return (
    <section className="latest-close-strip">
      <div>
        <span>最新收盘</span>
        <strong>{snapshot.close.toFixed(2)}</strong>
      </div>
      <div>
        <span>涨跌幅</span>
        <strong className={snapshot.change_pct !== null && snapshot.change_pct >= 0 ? "positive" : "negative"}>
          {snapshot.change_pct === null ? "暂无" : `${formatSigned(snapshot.change_pct, 2)}%`}
        </strong>
      </div>
      <div>
        <span>涨跌额</span>
        <strong>
          {snapshot.change === null ? "暂无" : formatSigned(snapshot.change, 2)}
        </strong>
      </div>
      <div>
        <span>交易日</span>
        <strong>{snapshot.trade_date}</strong>
      </div>
    </section>
  );
}
function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="fact-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function toDailyCandleBars(bars: StockKLineBar[]): MarketCandleBar[] {
  /** Convert API daily K-line bars into chart-friendly candle bars. */

  return bars.map((bar) => ({
    time: bar.trade_date,
    label: bar.trade_date,
    open: bar.open,
    close: bar.close,
    high: bar.high,
    low: bar.low,
    volume: bar.volume,
  }));
}

function toIntradayCandleBars(bars: StockIntradayKLineBar[]): MarketCandleBar[] {
  /** Convert API intraday bars into chart-friendly candle bars. */

  return bars.map((bar) => ({
    time: Math.floor(new Date(bar.timestamp).getTime() / 1000),
    label: bar.timestamp.slice(11, 16),
    open: bar.open,
    close: bar.close,
    high: bar.high,
    low: bar.low,
    volume: bar.volume,
    amount: bar.amount,
  }));
}

function ShellState({
  label,
  detail,
  onRetry,
}: {
  label: string;
  detail?: string;
  onRetry?: () => void;
}) {
  return (
    <main aria-live="polite" className="state-shell">
      <div>
        <p className="eyebrow">LimitUpLab</p>
        {!onRetry ? <LoaderCircle aria-hidden="true" className="state-spinner" size={24} /> : null}
        <h1>{label}</h1>
        {detail ? <p>{detail}</p> : null}
        {onRetry ? (
          <button className="primary-button" onClick={onRetry}>
            <RefreshCcw size={16} />
            重试
          </button>
        ) : null}
      </div>
    </main>
  );
}

function Panel({
  title,
  icon,
  actions,
  children,
}: {
  title: string;
  icon: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      <header>
        <div>
          {icon}
          <h2>{title}</h2>
        </div>
        {actions}
      </header>
      {children}
    </section>
  );
}

function detailIcon(view: ViewKey) {
  if (view === "first") {
    return <Flame size={18} />;
  }
  if (view === "continued") {
    return <Layers3 size={18} />;
  }
  if (view === "recent") {
    return <TrendingUp size={18} />;
  }
  return <ShieldAlert size={18} />;
}

function formatPercent(value: number) {
  return `${Math.round(value * 100)}%`;
}

function stockDetailPath(symbol: string, tradeDate?: string, name?: string) {
  const path = `/stocks/${encodeURIComponent(symbol)}`;
  const params = new URLSearchParams();
  if (tradeDate) params.set("trade_date", tradeDate);
  if (name) params.set("name", name);
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

function formatEmpiricalRate(value: number | null | undefined) {
  return value === null || value === undefined ? "暂无" : `${(value * 100).toFixed(1)}%`;
}

function formatAmount(value: number) {
  return `${(value / 100_000_000).toFixed(1)} 亿`;
}

function formatNetAmount(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "暂无";
  }
  const absolute = Math.abs(value);
  if (absolute >= 100_000_000) {
    return `${formatSigned(value / 100_000_000, 2)} 亿`;
  }
  return `${formatSigned(value / 10_000, 0)} 万`;
}

function numberTone(value: number | null | undefined) {
  if (value === null || value === undefined || value === 0) {
    return "";
  }
  return value > 0 ? "positive" : "negative";
}

function formatOptionalPercent(value: number | null | undefined) {
  return value === null || value === undefined ? "暂无" : `${formatSigned(value, 1)}%`;
}

function formatSigned(value: number, decimals = 1) {
  return value > 0 ? `+${value.toFixed(decimals)}` : value.toFixed(decimals);
}
