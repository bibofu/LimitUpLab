import {
  ArrowLeft,
  BarChart3,
  Flame,
  Layers3,
  LineChart,
  LoaderCircle,
  MessageCircle,
  RefreshCcw,
  Send,
  ShieldAlert,
  TrendingUp,
  WalletCards,
} from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  Link,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import remarkGfm from "remark-gfm";

import {
  MarketKLineChart,
  type MarketCandleBar,
} from "./components/MarketKLineChart";
import {
  fetchContinuedBoardEvents,
  fetchAgentDataHealth,
  fetchAgentEvalReport,
  fetchAgentRuns,
  fetchAgentSystemHealth,
  fetchDailyPipelineStatus,
  fetchReviewAgentReport,
  fetchFirstBoardCritic,
  fetchFirstBoardRatings,
  fetchFirstBoardSimilarCases,
  fetchFailedLimitUpEvents,
  fetchFirstBoardEvents,
  fetchMarketSummary,
  fetchPredictionQualityAudit,
  fetchRatingBacktest,
  fetchRatingEvaluation,
  fetchRecentLimitUpEvents,
  fetchStockKLine,
  fetchStockLatestClose,
  fetchStockTradingDayKLine,
  streamAgentChatMessage,
} from "./api";
import type {
  AgentChatResponse,
  AgentChatStreamStage,
  AgentDataHealthResponse,
  AgentEvalCaseReport,
  AgentEvalReportResponse,
  AgentEvidenceCard,
  AgentEvaluationResponse,
  AgentRunSummary,
  AgentSystemHealthResponse,
  DailyPipelineStatusResponse,
  FirstBoardCriticResponse,
  FirstBoardRating,
  FirstBoardRatingsResponse,
  LimitUpEvent,
  MarketSummary,
  PredictionQualityAuditResponse,
  RatingBacktestResponse,
  ReviewAgentPick,
  ReviewAgentReportResponse,
  SimilarFirstBoardCasesResponse,
  StockCloseSnapshot,
  StockIntradayKLineBar,
  StockKLineBar,
} from "./types";

type ViewKey = "overview" | "first" | "continued" | "failed" | "recent";

interface DashboardData {
  summary: MarketSummary;
  firstBoard: LimitUpEvent[];
  continuedBoard: LimitUpEvent[];
  failed: LimitUpEvent[];
  recent: LimitUpEvent[];
  firstBoardRatings: FirstBoardRatingsResponse;
  agentDataHealth: AgentDataHealthResponse;
  systemHealth: AgentSystemHealthResponse;
  dailyPipelineStatus: DailyPipelineStatusResponse;
  predictionQualityAudit: PredictionQualityAuditResponse;
  ratingBacktest: RatingBacktestResponse;
  ratingEvaluation: AgentEvaluationResponse;
}

interface ChatMessage {
  id: string;
  role: "user" | "agent";
  content: string;
  meta?: AgentChatResponse;
}

function formatTracePayload(payload: Record<string, unknown>) {
  return JSON.stringify(payload, null, 2);
}

function formatMetricLabel(key: string) {
  const labels: Record<string, string> = {
    trade_date: "交易日",
    candidate_count: "候选数",
    matched_count: "匹配数",
    event_count: "事件数",
    first_board_count: "首板",
    continued_board_count: "连板",
    failed_count: "炸板",
    limit_up_count: "涨停",
    recall_count: "召回",
    case_count: "案例",
    outcome_ready_count: "有走势",
    universe_count: "样本池",
    score: "评分",
    rating: "评级",
    confidence: "置信度",
    verdict: "结论",
    status: "状态",
  };
  return labels[key] ?? key;
}

function formatMetricValue(value: string | number | boolean | null) {
  if (value === null) {
    return "无";
  }
  if (typeof value === "boolean") {
    return value ? "是" : "否";
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
  return value;
}

const healthCopy = {
  healthy: { label: "数据健康", detail: "工具数据链完整" },
  partial: { label: "部分可用", detail: "部分相似案例或走势缓存缺失" },
  missing: { label: "数据缺失", detail: "需要运行每日更新流水线" },
};

const viewMeta: Record<ViewKey, { title: string; eyebrow: string }> = {
  overview: { title: "短线市场概况", eyebrow: "Overview" },
  first: { title: "首板票", eyebrow: "First Board" },
  continued: { title: "连板票", eyebrow: "Continued Board" },
  failed: { title: "炸板票", eyebrow: "Failed Limit-Up" },
  recent: { title: "近三日涨停票复盘", eyebrow: "Recent Limit-Up" },
};

function AgentChatDock({
  tradeDate,
  symbol,
  dataHealth,
  systemHealth,
  dailyPipelineStatus,
}: {
  tradeDate: string;
  symbol?: string;
  dataHealth: AgentDataHealthResponse;
  systemHealth: AgentSystemHealthResponse;
  dailyPipelineStatus: DailyPipelineStatusResponse;
}) {
  /** Provide a lightweight tool-grounded Agent chat entry point. */

  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [streamStage, setStreamStage] = useState<AgentChatStreamStage>("planning");
  const [streamStatus, setStreamStatus] = useState("正在理解问题并规划工具");
  const [error, setError] = useState<string | null>(null);
  const [runs, setRuns] = useState<AgentRunSummary[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "welcome",
      role: "agent",
      content: "可以问我：总结今天首板、为什么当前股票评分高、主要风险是什么、有没有历史相似案例。",
    },
  ]);
  const [sessionId] = useState(() => `chat-${Date.now()}-${Math.random().toString(16).slice(2)}`);

  useEffect(() => {
    void refreshAgentRuns();
  }, [sessionId]);

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

  async function refreshAgentRuns() {
    try {
      const response = await fetchAgentRuns(sessionId, 6);
      setRuns(response.runs);
    } catch {
      setRuns([]);
    }
  }

  async function sendMessage() {
    const trimmed = message.trim();
    if (!trimmed || sending) {
      return;
    }

    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      content: trimmed,
    };
    setMessages((current) => [...current, userMessage]);
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
        message: trimmed,
        intent_hint: inferChatIntent(trimmed),
        trade_date: tradeDate,
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
              { id: agentMessageId, role: "agent", content: delta },
            ];
          });
        }
      });
      setMessages((current) => {
        const existing = current.find((item) => item.id === agentMessageId);
        if (existing || receivedAnswer) {
          return current.map((item) => (
            item.id === agentMessageId
              ? { ...item, content: response.answer, meta: response }
              : item
          ));
        }
        return [
          ...current,
          {
            id: agentMessageId,
            role: "agent",
            content: response.answer,
            meta: response,
          },
        ];
      });
      void refreshAgentRuns();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Agent 回答失败");
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="agent-chat-dock">
      <section className="agent-chat-panel">
        <header>
          <div>
            <MessageCircle size={18} />
            <strong>首板 Agent 工作台</strong>
          </div>
          <div className="agent-chat-context">
            <span>{tradeDate}</span>
            {symbol ? <span>{symbol}</span> : <span>全市场首板</span>}
          </div>
        </header>

        <div className={`agent-data-health health-${dataHealth.status}`}>
          <div>
            <strong>{healthCopy[dataHealth.status].label}</strong>
            <span>{healthCopy[dataHealth.status].detail}</span>
          </div>
          <div className="agent-health-metrics">
            <span>原始 {dataHealth.raw_event_count}</span>
            <span>特征 {dataHealth.first_board_feature_count}</span>
            <span>扩展 {dataHealth.enrichment_count}</span>
            <span>Top {dataHealth.top_candidates_checked}</span>
            <span>{dataHealth.post_bars_ready ? "走势已缓存" : "走势待补齐"}</span>
          </div>
          {dataHealth.warnings.length > 0 ? (
            <p>{dataHealth.warnings[0]}</p>
          ) : null}
        </div>

        <SystemHealthStrip
          systemHealth={systemHealth}
          dailyPipelineStatus={dailyPipelineStatus}
        />

        <AgentRunObserver runs={runs} />

        <div className="agent-chat-messages">
          {messages.map((item) => (
            <article className={`chat-message chat-${item.role}`} key={item.id}>
              {item.role === "agent" ? (
                <div className="chat-markdown">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {item.content}
                  </ReactMarkdown>
                </div>
              ) : (
                <p>{item.content}</p>
              )}
              {item.meta ? (
                <>
                  <div className="chat-message-meta">
                    {item.meta.run_id ? <span>{item.meta.run_id.slice(0, 12)}</span> : null}
                    <span>{item.meta.intent}</span>
                    {item.meta.tool_calls.map((tool) => (
                      <span key={tool}>{tool}</span>
                    ))}
                    {item.meta.performance.total_duration_ms > 0 ? (
                      <span>
                        总耗时 {(item.meta.performance.total_duration_ms / 1000).toFixed(1)}s
                        {" · "}Planner {(item.meta.performance.planner_duration_ms / 1000).toFixed(1)}s
                        {" · "}Answer {(item.meta.performance.answer_duration_ms / 1000).toFixed(1)}s
                      </span>
                    ) : null}
                  </div>
                  <AgentEvidencePanel response={item.meta} />
                </>
              ) : null}
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
              symbol ? "详细解释一下" : "总结一下今天首板",
              symbol ? "为什么评分高" : "哪些候选评分靠前",
              symbol ? "主要风险是什么" : "总结今天首板候选",
              symbol ? "有没有历史相似案例" : "今天市场环境如何",
              "A股几点开盘",
            ].map((prompt) => (
            <button
              key={prompt}
              type="button"
              onClick={() => setMessage(prompt)}
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
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder={symbol ? "问当前股票评分、风险或相似案例" : "问今天首板总结"}
          />
          <button className="icon-button" disabled={sending || !message.trim()} title="发送">
            <Send size={17} />
          </button>
        </form>
      </section>
    </div>
  );
}

function AgentEvidencePanel({ response }: { response: AgentChatResponse }) {
  const cards = response.evidence_cards ?? [];
  const confidence = buildAnswerConfidence(response);

  if (cards.length === 0 && response.tool_results.length === 0) {
    return null;
  }

  return (
    <section className="agent-evidence-panel">
      <div className="agent-evidence-header">
        <div>
          <strong>回答证据链</strong>
          <span>{confidence.label}</span>
        </div>
        <div className="agent-evidence-score">
          <span>{confidence.score}</span>
          <small>可信度</small>
        </div>
      </div>

      {confidence.notes.length > 0 ? (
        <div className="agent-evidence-notes">
          {confidence.notes.map((note) => (
            <span key={note}>{note}</span>
          ))}
        </div>
      ) : null}

      <PlannerPolicyView response={response} />

      <div className="agent-evidence-cards">
        {cards.map((card, index) => (
          <AgentEvidenceCardView card={card} key={`${card.title}-${index}`} />
        ))}
      </div>

      {response.tool_results.length > 0 ? (
        <details className="chat-tool-raw">
          <summary>开发 trace</summary>
          <div className="chat-tool-traces">
            {response.tool_results.map((tool) => (
              <details
                className={`chat-tool-trace trace-${tool.status}`}
                key={`${tool.name}-${tool.summary}`}
              >
                <summary>
                  <strong>{tool.name}</strong>
                  <span>{tool.status}</span>
                </summary>
                <p>{tool.summary}</p>
                {tool.error ? <p className="trace-error">{tool.error}</p> : null}
                <div className="trace-grid">
                  <section>
                    <span>输入</span>
                    <pre>{formatTracePayload(tool.input)}</pre>
                  </section>
                  {Object.keys(tool.output).length > 0 ? (
                    <section>
                      <span>输出摘要</span>
                      <pre>{formatTracePayload(tool.output)}</pre>
                    </section>
                  ) : null}
                </div>
              </details>
            ))}
          </div>
        </details>
      ) : null}
    </section>
  );
}

function PlannerPolicyView({ response }: { response: AgentChatResponse }) {
  const policy = response.tool_policy;
  const planner = policy?.planner_tool_calls ?? [];
  const final = policy?.final_tool_calls ?? [];
  const repaired = policy?.backend_repaired_tools ?? [];
  const shouldShow = planner.length > 0 || final.length > 0 || repaired.length > 0;

  if (!shouldShow) {
    return null;
  }

  return (
    <section className="planner-policy">
      <div>
        <span>Planner</span>
        <strong>{planner.length > 0 ? planner.join(" -> ") : "direct"}</strong>
      </div>
      <div>
        <span>Final</span>
        <strong>{final.length > 0 ? final.join(" -> ") : "direct"}</strong>
      </div>
      <div className={repaired.length > 0 ? "policy-repaired" : "policy-clean"}>
        <span>Repair</span>
        <strong>{repaired.length > 0 ? repaired.join(", ") : "none"}</strong>
      </div>
      {policy.safety_fallback_used ? <p>已触发模板/安全兜底。</p> : null}
      {policy.repair_reasons.length > 0 ? (
        <ul>
          {policy.repair_reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function AgentEvidenceCardView({ card }: { card: AgentEvidenceCard }) {
  const metrics = Object.entries(card.metrics ?? {});

  return (
    <article className={`agent-evidence-card evidence-${card.kind} evidence-${card.status}`}>
      <header>
        <strong>{card.title}</strong>
        <span>{card.status}</span>
      </header>
      <p>{card.summary}</p>
      {metrics.length > 0 ? (
        <div className="agent-evidence-metrics">
          {metrics.slice(0, 6).map(([key, value]) => (
            <span key={key}>
              <small>{formatMetricLabel(key)}</small>
              <strong>{formatMetricValue(value)}</strong>
            </span>
          ))}
        </div>
      ) : null}
      {card.facts.length > 0 ? (
        <ul>
          {card.facts.map((fact) => (
            <li key={fact}>{fact}</li>
          ))}
        </ul>
      ) : null}
      {card.source_tools.length > 0 ? (
        <div className="agent-evidence-sources">
          {card.source_tools.map((tool) => (
            <span key={tool}>{tool}</span>
          ))}
        </div>
      ) : null}
    </article>
  );
}

function buildAnswerConfidence(response: AgentChatResponse) {
  const cards = response.evidence_cards ?? [];
  const toolCount = response.tool_results.length;
  const errorCount = response.tool_results.filter((tool) => tool.status === "error").length;
  const skippedCount = response.tool_results.filter((tool) => tool.status === "skipped").length;
  const hasWarnings = response.warnings.length > 0;
  const hasStructuredEvidence = cards.some(
    (card) => card.kind !== "execution" && card.status === "success",
  );

  let score = 50;
  score += Math.min(toolCount, 4) * 10;
  if (hasStructuredEvidence) {
    score += 15;
  }
  score -= errorCount * 20;
  score -= skippedCount * 8;
  if (hasWarnings) {
    score -= 10;
  }
  score = Math.max(20, Math.min(95, score));

  const notes = [
    `${toolCount} 个工具`,
    hasStructuredEvidence ? "有结构化事实" : "证据较少",
    errorCount > 0 ? `${errorCount} 个工具失败` : "工具成功",
    hasWarnings ? "存在数据限制" : "无显著数据告警",
  ];

  return {
    score,
    label: score >= 80 ? "证据充分" : score >= 60 ? "可参考" : "需要谨慎",
    notes,
  };
}

function SystemHealthStrip({
  systemHealth,
  dailyPipelineStatus,
}: {
  systemHealth: AgentSystemHealthResponse;
  dailyPipelineStatus: DailyPipelineStatusResponse;
}) {
  const pipelineRun = dailyPipelineStatus.latest;
  const pipelineLabels = {
    running: "运行中",
    success: "成功",
    partial: "部分完成",
    error: "失败",
    skipped: "已跳过",
  };
  return (
    <section className={`system-health-strip system-${systemHealth.status}`}>
      <div>
        <strong>系统状态</strong>
        <span>{systemHealth.status === "healthy" ? "就绪" : systemHealth.status === "partial" ? "部分可用" : "缺失"}</span>
      </div>
      <div className="system-health-items">
        <span>数据 {systemHealth.latest_local_trade_date ?? "无"}</span>
        <span>{systemHealth.data_fresh ? "数据新鲜" : "数据待更新"}</span>
        <span>{systemHealth.llm_enabled && systemHealth.llm_provider_configured ? `LLM ${systemHealth.llm_model ?? "on"}` : "LLM 未就绪"}</span>
        <span>
          闭环 {pipelineRun
            ? `${pipelineRun.trade_date} ${pipelineLabels[pipelineRun.status]}`
            : "尚未运行"}
        </span>
        <span>
          Eval {systemHealth.offline_eval_passed === null ? "未跑" : systemHealth.offline_eval_passed ? "通过" : `${systemHealth.offline_eval_failed ?? 0} 失败`}
        </span>
      </div>
      {systemHealth.warnings.length > 0 ? <p>{systemHealth.warnings[0]}</p> : null}
      {pipelineRun?.error_message ? <p>{pipelineRun.error_message}</p> : null}
    </section>
  );
}

function AgentRunObserver({ runs }: { runs: AgentRunSummary[] }) {
  const latestRun = runs[0];
  const traceChain = latestRun?.tool_results.map((tool) => tool.name) ?? [];
  const hasWarnings = Boolean(latestRun?.warnings.length || latestRun?.error_message);

  return (
    <section className="agent-run-observer">
      <div className="agent-run-observer-header">
        <div>
          <strong>Agent 运行观察</strong>
          <span>
            {latestRun
              ? `${latestRun.status === "success" ? "成功" : "失败"} · ${latestRun.duration_ms}ms`
              : "等待首次对话"}
          </span>
        </div>
        {latestRun ? (
          <span className={`run-status run-${latestRun.status}`}>
            {latestRun.intent ?? "unknown"}
          </span>
        ) : null}
      </div>

      {latestRun ? (
        <>
          <div className="agent-run-chain">
            {(traceChain.length > 0 ? traceChain : latestRun.tool_calls).map((tool, index) => (
              <span key={`${latestRun.run_id}-${tool}-${index}`}>{tool}</span>
            ))}
            {traceChain.length === 0 && latestRun.tool_calls.length === 0 ? (
              <span>direct_answer</span>
            ) : null}
          </div>
          {hasWarnings ? (
            <p className="agent-run-warning">
              {latestRun.error_message ?? latestRun.warnings[0]}
            </p>
          ) : null}
        </>
      ) : null}

      {runs.length > 1 ? (
        <div className="agent-run-history">
          {runs.slice(1, 4).map((run) => (
            <span key={run.run_id}>
              {run.intent ?? "unknown"} · {run.tool_calls.length || run.tool_results.length} tools
            </span>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function inferChatIntent(message: string) {
  /** Infer a deterministic tool hint before the backend performs final routing. */

  if (
    /相似|历史|案例/.test(message)
    && /首板/.test(message)
    && /医药|医疗|制药|药业|生物|中药|相关|行业|题材/.test(message)
  ) {
    return "first_board_filter_similar";
  }
  if (/相似|历史|案例/.test(message)) {
    return "similar_cases";
  }
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
  "/stocks/first-board": "first",
  "/stocks/continued-board": "continued",
  "/stocks/failed": "failed",
  "/stocks/recent-limit-up": "recent",
};

const stockListPaths = new Set([
  "/stocks/first-board",
  "/stocks/continued-board",
  "/stocks/failed",
  "/stocks/recent-limit-up",
]);

const sentimentCopy = {
  heating: { label: "升温", detail: "连板梯队在抬升，风险偏好更积极" },
  diverging: { label: "分歧", detail: "涨停数量仍在，但封板稳定性需要观察" },
  cooling: { label: "退潮", detail: "炸板压力偏高，短线接力需要降速" },
};

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
        agentDataHealth,
        systemHealth,
        dailyPipelineStatus,
        predictionQualityAudit,
        ratingBacktest,
        ratingEvaluation,
      ] = await Promise.all([
        fetchMarketSummary(),
        fetchFirstBoardEvents(),
        fetchContinuedBoardEvents(),
        fetchFailedLimitUpEvents(),
        fetchRecentLimitUpEvents(3),
        fetchFirstBoardRatings(),
        fetchAgentDataHealth(),
        fetchAgentSystemHealth(false),
        fetchDailyPipelineStatus(5),
        fetchPredictionQualityAudit(),
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
        agentDataHealth,
        systemHealth,
        dailyPipelineStatus,
        predictionQualityAudit,
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
  const activeMeta = isStockDetail
    ? { title: "个股详情", eyebrow: "Stock Detail" }
    : viewMeta[activeView];

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">{activeMeta.eyebrow}</p>
          <h1>{activeMeta.title}</h1>
        </div>
        <div className="topbar-actions">
          {activeView !== "overview" || isStockDetail ? (
            <Link className="text-button" to="/">
              <ArrowLeft size={17} />
              返回概况
            </Link>
          ) : null}
          <button className="icon-button" onClick={loadDashboard} title="刷新数据">
            <RefreshCcw size={18} />
          </button>
        </div>
      </header>

      {!isStockDetail ? (
        <AgentChatDock
          tradeDate={data.summary.trade_date}
          dataHealth={data.agentDataHealth}
          systemHealth={data.systemHealth}
          dailyPipelineStatus={data.dailyPipelineStatus}
        />
      ) : null}

      <Routes>
        <Route path="/" element={<Overview data={data} />} />
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

function Overview({ data }: { data: DashboardData }) {
  /** Render the top-level after-close market review dashboard. */

  const sentiment = sentimentCopy[data.summary.sentiment];

  return (
    <>
      <section className="hero-summary">
        <div>
          <p className="eyebrow">{data.summary.trade_date}</p>
          <h2>{sentiment.label}</h2>
          <p>{sentiment.detail}</p>
        </div>
        <div className="hero-numbers">
          <span>最高连板</span>
          <strong>{data.summary.max_board_height} 板</strong>
        </div>
      </section>

      <section className="overview-grid">
        <EntryCard
          icon={<Flame size={20} />}
          label="首板票"
          value={`${data.summary.first_board_count} 只`}
          caption="查看今日首板个股和封板质量"
          to="/stocks/first-board"
        />
        <EntryCard
          icon={<Layers3 size={20} />}
          label="连板票"
          value={`${data.summary.continued_board_count} 只`}
          caption={`最高 ${data.summary.max_board_height} 板`}
          to="/stocks/continued-board"
        />
        <EntryCard
          icon={<ShieldAlert size={20} />}
          label="炸板票"
          value={`${data.summary.failed_count} 只`}
          caption={`炸板率 ${formatPercent(data.summary.failed_limit_up_rate)}`}
          to="/stocks/failed"
        />
        <EntryCard
          icon={<WalletCards size={20} />}
          label="涨停成交额"
          value={formatAmount(data.summary.total_amount)}
          caption="今日涨停池合计成交额"
        />
      </section>

      <section className="overview-content">
        <Panel title="大盘走势" icon={<LineChart size={18} />}>
          <div className="index-grid">
            {data.summary.indices.map((index) => (
              <article className="index-card" key={index.symbol}>
                <div>
                  <span>{index.name}</span>
                  <strong>{index.close.toFixed(2)}</strong>
                  <time dateTime={index.trade_date}>
                    {index.trade_date.slice(5)} 收盘
                  </time>
                </div>
                <Sparkline values={index.trend} />
                <b className={index.change_pct >= 0 ? "positive" : "negative"}>
                  {formatSigned(index.change_pct, 2)}%
                </b>
              </article>
            ))}
            {data.summary.indices.length === 0 ? (
              <div className="index-empty">指数数据暂不可用</div>
            ) : null}
          </div>
        </Panel>

        <Panel title="题材热度" icon={<BarChart3 size={18} />}>
          <div className="topic-list">
            {data.summary.hot_concepts.map((concept) => (
              <div className="topic-row" key={concept.name}>
                <strong>{concept.name}</strong>
                <span>
                  {concept.limit_up_count} 涨停 / {concept.failed_count} 炸板
                </span>
              </div>
            ))}
          </div>
        </Panel>
      </section>

      <FirstBoardRatingPanel ratings={data.firstBoardRatings} />

      <HighScoreReviewPanel
        backtest={data.ratingBacktest}
        evaluation={data.ratingEvaluation}
        latestTradeDate={data.summary.trade_date}
      />

      <PredictionQualityAuditPanel audit={data.predictionQualityAudit} />

      <AgentEvalPanel />

      <Link className="recent-entry" to="/stocks/recent-limit-up">
        <div>
          <TrendingUp size={22} />
          <strong>查看最近三个交易日涨停过的股票</strong>
        </div>
        <span>{data.recent.length} 个涨停事件</span>
      </Link>
    </>
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
  const [activeReviewDate, setActiveReviewDate] = useState<string | null>(null);

  useEffect(() => {
    void loadReview();
  }, [latestTradeDate]);

  async function loadReview() {
    setRunning(true);
    setError(null);
    try {
      const response = await fetchReviewAgentReport({
        top_per_day: 10,
        follow_days: 5,
        use_llm: false,
      });
      setReport(response);
      const grouped = groupReviewPicksByDate(response.reviewed_picks);
      const trackDates = buildReviewTrackDates(grouped, response.end_date);
      setActiveReviewDate((current) => (
        current && trackDates.includes(current) ? current : trackDates[0] ?? null
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
    (total, tradeDate) => total + (reviewDates[tradeDate] ?? []).filter((item) => item.evaluation_label === "success").length,
    0,
  );
  const trackedSuccessRate = trackedReadyCount > 0 ? trackedSuccessCount / trackedReadyCount : null;
  const selectedReviewDate = activeReviewDate && trackDates.includes(activeReviewDate)
    ? activeReviewDate
    : trackDates[0] ?? null;
  const selectedPicks = selectedReviewDate ? reviewDates[selectedReviewDate] : [];

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

        {running && !report ? (
          <div className="review-agent-empty">
            <strong>正在加载过去 5 个交易日 Top10 追踪</strong>
            <p>加载完成后可以按日期查看当天预测 Top10 到最新收盘的走势。</p>
          </div>
        ) : report ? (
          <>
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
                <small>每日数量</small>
                <strong>Top10</strong>
              </span>
              <span>
                <small>待观察</small>
                <strong>{trackedPendingCount}</strong>
              </span>
              <span>
                <small>兑现率</small>
                <strong>{trackedSuccessRate === null ? "暂无" : formatPercent(trackedSuccessRate)}</strong>
              </span>
              <span>
                <small>置信度</small>
                <strong>{formatPercent(report.confidence)}</strong>
              </span>
            </div>

            <DailyTopReview
              activeDate={selectedReviewDate}
              latestTradeDate={report.end_date}
              groupedPicks={reviewDates}
              onSelectDate={setActiveReviewDate}
              picks={selectedPicks}
              trackDates={trackDates}
            />

            {report.warnings.length > 0 ? (
              <div className="review-agent-warnings">
                {report.warnings.map((warning) => (
                  <span key={warning}>{warning}</span>
                ))}
              </div>
            ) : null}
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

function DailyTopReview({
  activeDate,
  latestTradeDate,
  groupedPicks,
  onSelectDate,
  picks,
  trackDates,
}: {
  activeDate: string | null;
  latestTradeDate: string;
  groupedPicks: Record<string, ReviewAgentPick[]>;
  onSelectDate: (tradeDate: string) => void;
  picks: ReviewAgentPick[];
  trackDates: string[];
}) {
  return (
    <div className="daily-top-review">
      <div className="daily-top-cards">
        {trackDates.map((tradeDate) => {
          const dailyPicks = groupedPicks[tradeDate] ?? [];
          const readyCount = dailyPicks.filter((item) => item.post_bar_cache_complete).length;
          const bestPick = dailyPicks[0];
          return (
          <button
            className={tradeDate === activeDate ? "active" : ""}
            key={tradeDate}
            type="button"
            onClick={() => onSelectDate(tradeDate)}
          >
            <span>{tradeDate}</span>
            <strong>Top10 追踪</strong>
            <small>{readyCount} / {dailyPicks.length} 缓存已同步</small>
            {bestPick ? <b>{bestPick.name} {bestPick.score.toFixed(1)}</b> : null}
          </button>
          );
        })}
      </div>

      <section className="daily-top-body">
        <div className="daily-top-title">
          <div>
            <strong>{activeDate ?? "暂无日期"}</strong>
            <span>当日预测 Top10，到 {latestTradeDate} 收盘为止的走势</span>
          </div>
          <span>
            {picks.filter((item) => item.post_bar_cache_complete).length} / {picks.length} 缓存已同步
          </span>
        </div>

        <ReviewPickTable picks={picks} />
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

function ReviewPickTable({ picks }: { picks: ReviewAgentPick[] }) {
  const visible = picks.slice(0, 10);

  if (visible.length === 0) {
    return <div className="review-agent-empty">暂无高分票追踪样本。</div>;
  }

  return (
    <div className="review-pick-table">
      <div className="review-pick-table-head">
        <span>股票</span>
        <span>评分</span>
        <span>结论</span>
        <span>次日开收</span>
        <span>走势追踪</span>
      </div>
      {visible.map((pick) => (
        <Link
          className={`review-pick-row pick-${pick.evaluation_label}`}
          key={`${pick.trade_date}-${pick.symbol}`}
          to={`/stocks/${pick.symbol}`}
        >
          <strong>
            {pick.name}
            <small>
              {pick.symbol} / {pick.prediction_source === "live" ? "实时预测" : "历史回测"}
            </small>
          </strong>
          <span>{pick.score.toFixed(1)} / {pick.rating}</span>
          <span>{reviewLabelCopy(pick.evaluation_label)}</span>
          <span>{formatOptionalPercent(pick.next_open_to_close_pct)}</span>
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

function PredictionQualityAuditPanel({
  audit,
}: {
  audit: PredictionQualityAuditResponse;
}) {
  const recentCoverage = audit.date_coverage.slice(-12);
  const policyStatus = audit.policy_status;
  const modelBenchmark = audit.benchmarks.find(
    (item) => item.benchmark === "audited_policy_top_k",
  );
  const earlyBenchmark = audit.benchmarks.find(
    (item) => item.benchmark === "early_seal_top_k",
  );
  const modelDelta = modelBenchmark?.avg_next_open_to_close_pct !== null
    && modelBenchmark?.avg_next_open_to_close_pct !== undefined
    && earlyBenchmark?.avg_next_open_to_close_pct !== null
    && earlyBenchmark?.avg_next_open_to_close_pct !== undefined
    ? modelBenchmark.avg_next_open_to_close_pct - earlyBenchmark.avg_next_open_to_close_pct
    : null;

  return (
    <Panel title="预测质量审计与评分 v3" icon={<BarChart3 size={18} />}>
      <div className="quality-audit-panel">
        <div className="quality-audit-heading">
          <div>
            <span>Prediction Quality</span>
            <strong>{audit.start_date} 至 {audit.end_date}</strong>
            <p>
              按评分版本、预测来源和交易日成熟度去重审计，只在 Outcome 可用样本上比较同口径基线。
            </p>
          </div>
          <div className={policyStatus.promotion_eligible ? "quality-ready" : "quality-shadow"}>
            <small>v3 状态</small>
            <strong>{policyStatus.promotion_eligible ? "满足晋级门槛" : "影子验证中"}</strong>
            <span>
              {policyStatus.outcome_ready_trade_dates} / {policyStatus.required_trade_dates} 个结果日
            </span>
          </div>
        </div>

        <div className="quality-audit-metrics">
          <span>
            <small>原始预测行</small>
            <strong>{audit.raw_prediction_rows}</strong>
          </span>
          <span>
            <small>当前版本去重</small>
            <strong>{audit.canonical_prediction_count}</strong>
          </span>
          <span>
            <small>成熟预测日</small>
            <strong>{audit.next_day_mature_trade_date_count}</strong>
          </span>
          <span>
            <small>Top10 完整日</small>
            <strong>{audit.complete_next_day_trade_date_count}</strong>
          </span>
          <span>
            <small>次日覆盖</small>
            <strong>{formatPercent(audit.next_day_outcome_coverage_rate)}</strong>
          </span>
          <span>
            <small>相对早封基线</small>
            <strong className={modelDelta !== null && modelDelta >= 0 ? "positive" : "negative"}>
              {modelDelta === null ? "暂无" : `${formatSigned(modelDelta, 2)}%`}
            </strong>
          </span>
        </div>

        <div className="quality-readiness">
          <div>
            <span>v3 样本准备度</span>
            <strong>{formatPercent(policyStatus.readiness_rate)}</strong>
          </div>
          <div className="quality-readiness-track" aria-label="v3 样本准备度">
            <span style={{ width: `${Math.min(policyStatus.readiness_rate * 100, 100)}%` }} />
          </div>
          <p>{policyStatus.gate_reasons[0] ?? "等待下一次 walk-forward 评估。"}</p>
        </div>

        <div className="quality-benchmark-table">
          <div className="quality-benchmark-head">
            <span>比较口径</span>
            <span>样本</span>
            <span>次日开盘→收盘</span>
            <span>正收益率</span>
            <span>晋级率</span>
            <span>大跌率</span>
            <span>三日回撤</span>
          </div>
          {audit.benchmarks.map((item) => (
            <div className="quality-benchmark-row" key={item.benchmark}>
              <strong>{item.label}</strong>
              <span>{item.trade_date_count} 日 / {item.sample_size}</span>
              <span className={(item.avg_next_open_to_close_pct ?? 0) >= 0 ? "positive" : "negative"}>
                {formatOptionalPercent(item.avg_next_open_to_close_pct)}
              </span>
              <span>{item.positive_rate === null ? "暂无" : formatPercent(item.positive_rate)}</span>
              <span>
                {item.promoted_to_second_board_rate === null
                  ? "暂无"
                  : formatPercent(item.promoted_to_second_board_rate)}
              </span>
              <span>
                {item.large_loss_rate === null ? "暂无" : formatPercent(item.large_loss_rate)}
              </span>
              <span>{formatOptionalPercent(item.avg_max_drawdown_from_next_open_3d)}</span>
            </div>
          ))}
        </div>

        <div className="quality-date-coverage">
          <div className="quality-section-title">
            <strong>最近交易日 Outcome 覆盖</strong>
            <span>完整、部分、待回填和未成熟分开计算</span>
          </div>
          <div className="quality-date-strip">
            {recentCoverage.map((item) => (
              <div className={`coverage-${item.status}`} key={item.trade_date}>
                <span>{item.trade_date.slice(5)}</span>
                <strong>{item.next_day_ready_count}/{item.top_count}</strong>
                <small>{predictionCoverageCopy[item.status]}</small>
              </div>
            ))}
          </div>
        </div>

        <div className="quality-audit-notes">
          <section>
            <h3>审计结论</h3>
            {audit.findings.map((item) => <p key={item}>{item}</p>)}
          </section>
          <section>
            <h3>下一步动作</h3>
            {audit.recommendations.map((item) => <p key={item}>{item}</p>)}
          </section>
        </div>

        <div className="quality-policy-line">
          <span>Champion</span>
          <strong>{policyStatus.champion_version}</strong>
          <span>Latest Challenger</span>
          <strong>{policyStatus.latest_challenger_version ?? "尚未生成"}</strong>
        </div>

        <div className="quality-audit-warnings">
          {audit.warnings.map((warning) => <span key={warning}>{warning}</span>)}
        </div>
      </div>
    </Panel>
  );
}

const predictionCoverageCopy = {
  complete: "完整",
  partial: "部分",
  pending: "待回填",
  not_mature: "未成熟",
};

function AgentEvalPanel() {
  const [report, setReport] = useState<AgentEvalReportResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function runEval() {
    setRunning(true);
    setError(null);
    try {
      const response = await fetchAgentEvalReport();
      setReport(response);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Agent 评测失败");
    } finally {
      setRunning(false);
    }
  }

  const failedCases = report?.results.filter((item) => !item.passed) ?? [];
  const repairedCases = report?.results.filter((item) => item.backend_repaired_tools.length > 0) ?? [];

  return (
    <Panel title="Agent 质量评测" icon={<ShieldAlert size={18} />}>
      <div className="agent-eval-panel">
        <div className="agent-eval-summary">
          <div>
            <span>离线回归套件</span>
            <strong>
              {report ? `${Math.round(report.pass_rate * 100)}%` : "未运行"}
            </strong>
            <p>
              {report
                ? `${report.passed}/${report.total} 通过，${report.failed} 个失败`
                : "运行内置问题集，检查意图、工具调用、回答事实和安全边界。"}
            </p>
          </div>
          <button type="button" onClick={runEval} disabled={running}>
            {running ? "评测中..." : "运行评测"}
          </button>
        </div>

        {error ? <p className="agent-eval-error">{error}</p> : null}

        {report ? (
          <>
            <div className="agent-eval-metrics">
              <span>
                <small>用例数</small>
                <strong>{report.total}</strong>
              </span>
              <span>
                <small>通过</small>
                <strong>{report.passed}</strong>
              </span>
              <span>
                <small>失败</small>
                <strong>{report.failed}</strong>
              </span>
              <span>
                <small>后端修复</small>
                <strong>{repairedCases.length}</strong>
              </span>
            </div>

            {failedCases.length > 0 ? (
              <div className="agent-eval-section">
                <strong>失败用例</strong>
                {failedCases.map((item) => (
                  <AgentEvalCaseRow item={item} key={item.case_id} />
                ))}
              </div>
            ) : (
              <p className="agent-eval-pass">当前离线评测全部通过。</p>
            )}

            <div className="agent-eval-section">
              <strong>评测明细</strong>
              <div className="agent-eval-case-grid">
                {report.results.map((item) => (
                  <AgentEvalCaseRow compact item={item} key={item.case_id} />
                ))}
              </div>
            </div>
          </>
        ) : null}
      </div>
    </Panel>
  );
}

function AgentEvalCaseRow({
  item,
  compact = false,
}: {
  item: AgentEvalCaseReport;
  compact?: boolean;
}) {
  const tools = item.final_tool_calls.length > 0 ? item.final_tool_calls : item.trace_names;

  return (
    <article className={`agent-eval-case ${item.passed ? "case-pass" : "case-fail"}`}>
      <header>
        <strong>{item.case_id}</strong>
        <span>{item.passed ? "pass" : "fail"}</span>
      </header>
      <div className="agent-eval-case-tags">
        <span>{item.intent}</span>
        {tools.slice(0, compact ? 2 : 5).map((tool) => (
          <span key={`${item.case_id}-${tool}`}>{tool}</span>
        ))}
      {item.backend_repaired_tools.length > 0 ? (
        <span>repair {item.backend_repaired_tools.join(", ")}</span>
      ) : null}
      </div>
      {!compact || !item.passed ? (
        <>
          {item.failures.length > 0 ? (
            <ul>
              {item.failures.map((failure) => (
                <li key={failure}>{failure}</li>
              ))}
            </ul>
          ) : null}
          {item.repair_reasons.length > 0 ? (
            <ul className="agent-eval-repair-reasons">
              {item.repair_reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : null}
          <p>{item.answer_preview}</p>
        </>
      ) : null}
    </article>
  );
}

function FirstBoardRatingPanel({ ratings }: { ratings: FirstBoardRatingsResponse }) {
  /** Render the first-board rating summary generated from deterministic facts. */

  const topCandidates = ratings.candidates.slice(0, 5);
  const topCandidate = topCandidates[0];

  return (
    <Panel title="首板评级 Agent" icon={<BarChart3 size={18} />}>
      <div className="rating-summary-panel">
        <div className="rating-summary-facts">
          <span>{ratings.trade_date}</span>
          <strong>{ratings.candidates.length} 只入池</strong>
          <strong>{ratings.filtered_out.length} 只过滤</strong>
          <strong>{topCandidate ? `${topCandidate.rating} / ${topCandidate.score.toFixed(1)}` : "暂无候选"}</strong>
        </div>
        {topCandidates.length > 0 ? (
          <div className="rating-top-list">
            {topCandidates.map((candidate, index) => (
              <Link
                className="rating-top-card"
                key={`${candidate.facts.trade_date}-${candidate.facts.symbol}`}
                to={`/stocks/${candidate.facts.symbol}`}
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
                  <Fact label="市场情绪" value={sentimentCopy[candidate.facts.market_sentiment].label} />
                </div>

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
              </Link>
            ))}
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

function DetailView({ view, data }: { view: ViewKey; data: DashboardData }) {
  /** Render one of the latest-day stock list views. */

  const eventsByView = {
    first: data.firstBoard,
    continued: data.continuedBoard,
    failed: data.failed,
    overview: [],
    recent: [],
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

  const grouped = useMemo(() => {
    return events.reduce<Record<string, LimitUpEvent[]>>((groups, event) => {
      groups[event.trade_date] = groups[event.trade_date] ?? [];
      groups[event.trade_date].push(event);
      return groups;
    }, {});
  }, [events]);

  return (
    <div className="recent-groups">
      {Object.entries(grouped).map(([tradeDate, items]) => (
        <Panel key={tradeDate} title={tradeDate} icon={<TrendingUp size={18} />}>
          <StockTable events={items} variant="recent" />
        </Panel>
      ))}
    </div>
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
              onClick={() => navigate(`/stocks/${rating.facts.symbol}`)}
              onKeyDown={(keyboardEvent) => {
                if (keyboardEvent.key === "Enter" || keyboardEvent.key === " ") {
                  keyboardEvent.preventDefault();
                  navigate(`/stocks/${rating.facts.symbol}`);
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

  function openStock(symbol: string) {
    navigate(`/stocks/${symbol}`);
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
              onClick={() => openStock(event.symbol)}
              onKeyDown={(keyboardEvent) => {
                if (keyboardEvent.key === "Enter" || keyboardEvent.key === " ") {
                  keyboardEvent.preventDefault();
                  openStock(event.symbol);
                }
              }}
              tabIndex={0}
            >
              <td>
                <strong>{event.name}</strong>
                <span>{event.symbol}</span>
              </td>
              <td>{event.trade_date}</td>
              <td>{event.board_height} 板</td>
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
  const [kline, setKline] = useState<StockKLineBar[]>([]);
  const [tradingDayKline, setTradingDayKline] = useState<StockIntradayKLineBar[]>([]);
  const [chartMode, setChartMode] = useState<"daily" | "intraday">("daily");
  const [latestClose, setLatestClose] = useState<StockCloseSnapshot | null>(null);
  const [similarCases, setSimilarCases] = useState<SimilarFirstBoardCasesResponse | null>(null);
  const [critic, setCritic] = useState<FirstBoardCriticResponse | null>(null);
  const [klineLoading, setKlineLoading] = useState(true);
  const [klineError, setKlineError] = useState<string | null>(null);
  const [tradingDayLoading, setTradingDayLoading] = useState(true);
  const [tradingDayError, setTradingDayError] = useState<string | null>(null);
  const [latestCloseLoading, setLatestCloseLoading] = useState(true);
  const [latestCloseError, setLatestCloseError] = useState<string | null>(null);
  const [similarCasesLoading, setSimilarCasesLoading] = useState(false);
  const [similarCasesError, setSimilarCasesError] = useState<string | null>(null);
  const [criticLoading, setCriticLoading] = useState(false);
  const [criticError, setCriticError] = useState<string | null>(null);
  const events = useMemo(
    () => [...data.firstBoard, ...data.continuedBoard, ...data.failed, ...data.recent],
    [data],
  );
  const stockEvent = useMemo(() => {
    return events
      .filter((event) => event.symbol === symbol)
      .sort((left, right) => right.trade_date.localeCompare(left.trade_date))[0];
  }, [events, symbol]);
  const firstBoardRating = useMemo(() => {
    return data.firstBoardRatings.candidates.find(
      (rating) => rating.facts.symbol === symbol,
    ) ?? null;
  }, [data.firstBoardRatings.candidates, symbol]);

  useEffect(() => {
    const tradeDate = stockEvent?.trade_date;
    if (!tradeDate) {
      setKlineLoading(false);
      setTradingDayLoading(false);
      setLatestCloseLoading(false);
      setSimilarCasesLoading(false);
      setSimilarCases(null);
      setCriticLoading(false);
      setCritic(null);
      return;
    }

    setLatestCloseLoading(true);
    setLatestCloseError(null);
    fetchStockLatestClose(symbol)
      .then(setLatestClose)
      .catch((caught) => {
        setLatestClose(null);
        setLatestCloseError(caught instanceof Error ? caught.message : "加载最新收盘数据失败");
      })
      .finally(() => setLatestCloseLoading(false));

    setKlineLoading(true);
    setKlineError(null);
    fetchStockKLine(symbol, 60)
      .then(setKline)
      .catch((caught) => {
        setKlineError(caught instanceof Error ? caught.message : "加载 60 日 K 线失败");
      })
      .finally(() => setKlineLoading(false));

    setTradingDayLoading(true);
    setTradingDayError(null);
    fetchStockTradingDayKLine(symbol, 1)
      .then(setTradingDayKline)
      .catch((caught) => {
        setTradingDayError(caught instanceof Error ? caught.message : "加载交易日走势失败");
      })
      .finally(() => setTradingDayLoading(false));

    if (firstBoardRating) {
      setSimilarCasesLoading(true);
      setSimilarCasesError(null);
      fetchFirstBoardSimilarCases(symbol, firstBoardRating.facts.trade_date, 3)
        .then(setSimilarCases)
        .catch((caught) => {
          setSimilarCases(null);
          setSimilarCasesError(caught instanceof Error ? caught.message : "加载历史相似案例失败");
        })
        .finally(() => setSimilarCasesLoading(false));

      setCriticLoading(true);
      setCriticError(null);
      fetchFirstBoardCritic(symbol, firstBoardRating.facts.trade_date)
        .then(setCritic)
        .catch((caught) => {
          setCritic(null);
          setCriticError(caught instanceof Error ? caught.message : "加载 Critic 复核失败");
        })
        .finally(() => setCriticLoading(false));
    } else {
      setSimilarCases(null);
      setSimilarCasesLoading(false);
      setSimilarCasesError(null);
      setCritic(null);
      setCriticLoading(false);
      setCriticError(null);
    }
  }, [firstBoardRating, stockEvent?.trade_date, symbol]);

  if (!stockEvent) {
    return (
      <ShellState
        label="未找到这只股票"
        detail="请从涨停列表中选择一只股票进入详情"
      />
    );
  }

  return (
    <div className="stock-detail">
      <section className="stock-hero">
        <div>
          <p className="eyebrow">{stockEvent.trade_date}</p>
          <h2>{stockEvent.name}</h2>
          <span>{stockEvent.symbol}</span>
        </div>
        <div className="stock-status">
          <strong>{stockEvent.board_height} 板</strong>
          <span>{stockEvent.closed_limit ? "已封板" : "未回封"}</span>
        </div>
      </section>

      <LatestCloseStrip
        snapshot={latestClose}
        loading={latestCloseLoading}
        error={latestCloseError}
      />

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
            />
          )}
        </Panel>
      </section>

      <section className="stock-agent-grid">
        <FirstBoardRatingDetail rating={firstBoardRating} />
        <SimilarCasesPanel
          data={similarCases}
          loading={similarCasesLoading}
          error={similarCasesError}
        />
        <FirstBoardCriticPanel
          data={critic}
          loading={criticLoading}
          error={criticError}
        />
      </section>
    </div>
  );
}


function SimilarCasesPanel({
  data,
  loading,
  error,
}: {
  data: SimilarFirstBoardCasesResponse | null;
  loading: boolean;
  error: string | null;
}) {
  /** Render historical first-board similar cases for the selected target. */

  if (loading) {
    return (
      <Panel title="历史相似案例" icon={<Layers3 size={18} />}>
        <div className="rating-detail-empty">正在检索历史相似首板案例...</div>
      </Panel>
    );
  }

  if (error) {
    return (
      <Panel title="历史相似案例" icon={<Layers3 size={18} />}>
        <div className="rating-detail-empty">{error}</div>
      </Panel>
    );
  }

  if (!data) {
    return (
      <Panel title="历史相似案例" icon={<Layers3 size={18} />}>
        <div className="rating-detail-empty">当前股票暂无首板相似案例。</div>
      </Panel>
    );
  }

  return (
    <Panel title="历史相似案例" icon={<Layers3 size={18} />}>
      <div className="similar-cases">
        <div className="similar-cases-meta">
          <span>窗口 {data.window_days} 个交易日</span>
          <span>召回 {data.recall_count} 条</span>
        </div>
        {data.cases.length === 0 ? (
          <div className="rating-detail-empty">未找到足够相似的历史首板样本。</div>
        ) : (
          data.cases.slice(0, 3).map((item) => (
            <article className="similar-case-card" key={`${item.trade_date}-${item.symbol}`}>
              <header>
                <div>
                  <strong>{item.name}</strong>
                  <span>{item.symbol} / {item.trade_date}</span>
                </div>
                <b>{formatPercent(item.similarity)}</b>
              </header>

              <TagSection title="相似原因" items={item.reasons.length ? item.reasons : ["结构特征接近"]} tone="good" />
              {item.differences.length > 0 ? (
                <TagSection title="差异点" items={item.differences} tone="muted" />
              ) : null}

              <div className="case-outcome-grid">
                <Fact label="晋级二板" value={item.outcome?.promoted_to_second_board ? "是" : "否"} />
                <Fact label="次日最高" value={formatOptionalPercent(item.outcome?.next_high_pct)} />
                <Fact label="三日最高" value={formatOptionalPercent(item.outcome?.three_day_high_pct)} />
                <Fact label="三日回撤" value={formatOptionalPercent(item.outcome?.max_drawdown_3d)} />
              </div>

              {item.post_bars.length > 0 ? (
                <div className="post-bars-mini">
                  {item.post_bars.map((bar) => (
                    <div key={bar.trade_date}>
                      <span>{bar.trade_date.slice(5)}</span>
                      <strong>{bar.close.toFixed(2)}</strong>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="case-note">暂无首板后走势缓存。</p>
              )}
            </article>
          ))
        )}
      </div>
    </Panel>
  );
}

function FirstBoardCriticPanel({
  data,
  loading,
  error,
}: {
  data: FirstBoardCriticResponse | null;
  loading: boolean;
  error: string | null;
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

  if (error) {
    return (
      <Panel title="Critic 复核" icon={<ShieldAlert size={18} />}>
        <div className="rating-detail-empty">{error}</div>
      </Panel>
    );
  }

  if (!data) {
    return (
      <Panel title="Critic 复核" icon={<ShieldAlert size={18} />}>
        <div className="rating-detail-empty">当前股票暂无 Critic 复核结果。</div>
      </Panel>
    );
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
          <div>
            <span>相似案例覆盖</span>
            <strong>
              {data.similar_case_outcome_ready_count} / {data.similar_case_count}
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

function FirstBoardRatingDetail({ rating }: { rating: FirstBoardRating | null }) {
  /** Render explainable first-board score details for the selected stock. */

  if (!rating) {
    return (
      <Panel title="Agent 评分拆解" icon={<BarChart3 size={18} />}>
        <div className="rating-detail-empty">
          当前股票不在首板评级候选池中。
        </div>
      </Panel>
    );
  }

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
    <main className="state-shell">
      <div>
        <p className="eyebrow">LimitUpLab</p>
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

function EntryCard({
  icon,
  label,
  value,
  caption,
  to,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  caption: string;
  to?: string;
}) {
  const content = (
    <>
      <div className="metric-icon">{icon}</div>
      <span>{label}</span>
      <strong>{value}</strong>
      <p>{caption}</p>
    </>
  );

  if (to) {
    return (
      <Link className="entry-card" to={to}>
        {content}
      </Link>
    );
  }

  return <article className="entry-card">{content}</article>;
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

function Sparkline({ values }: { values: number[] }) {
  /** Draw the compact index trend sparkline shown on overview cards. */

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const points = values
    .map((value, index) => {
      const x = (index / Math.max(values.length - 1, 1)) * 100;
      const y = 36 - ((value - min) / range) * 30;
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <svg className="sparkline" viewBox="0 0 100 40" role="img" aria-label="指数走势">
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="3" />
    </svg>
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

function formatAmount(value: number) {
  return `${(value / 100_000_000).toFixed(1)} 亿`;
}

function formatOptionalPercent(value: number | null | undefined) {
  return value === null || value === undefined ? "暂无" : `${formatSigned(value, 1)}%`;
}

function formatSigned(value: number, decimals = 1) {
  return value > 0 ? `+${value.toFixed(decimals)}` : value.toFixed(decimals);
}











