import {
  Check,
  LoaderCircle,
  MessageCircle,
  PanelLeft,
  Pencil,
  Plus,
  RefreshCcw,
  Send,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import {
  createChatSession,
  deleteChatSession,
  fetchChatSession,
  fetchChatSessions,
  renameChatSession,
  streamAgentChatMessage,
} from "../api";
import type {
  AgentChatResponse,
  AgentChatStreamStage,
  AgentStockMention,
  ChatSessionDetail,
  ChatSessionMessage,
  ChatSessionSummary,
} from "../types";
import { AgentAnswerMarkdown } from "./AgentAnswerMarkdown";

interface ChatMessage {
  id: string;
  role: "user" | "agent";
  content: string;
  stockMentions: AgentStockMention[];
  status?: "success" | "error";
  suggestedQuestions?: string[];
}

const ACTIVE_CHAT_SESSION_STORAGE_KEY = "limituplab.activeChatSession";

/**
 * Convert a persisted message and its metadata back into the chat UI representation.
 */
function restoredChatMessage(message: ChatSessionMessage): ChatMessage {
  return {
    id: message.message_id,
    role: message.role === "assistant" ? "agent" : "user",
    content: message.content,
    stockMentions: stockMentionsFromMetadata(message.metadata),
    status: message.status,
    suggestedQuestions: stringArray(message.metadata.suggested_questions),
  };
}

/**
 * Extract the final response fields needed by the rendered chat message.
 */
function responseMessageMetadata(response: AgentChatResponse): Partial<ChatMessage> {
  return {
    stockMentions: response.stock_mentions,
    suggestedQuestions: response.suggested_questions,
    status: "success",
  };
}

/**
 * Keep string members from unknown persisted metadata before rendering them.
 */
function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter(/* Keep only entries satisfying this predicate for stringArray. */ (item): item is string => typeof item === "string" && item.trim().length > 0)
    : [];
}

/**
 * Validate persisted stock-link metadata before using it for navigation.
 */
function stockMentionsFromMetadata(metadata: Record<string, unknown>): AgentStockMention[] {
  const value = metadata.stock_mentions;
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter(/* Keep only entries satisfying this predicate for stockMentionsFromMetadata. */ (item): item is AgentStockMention => {
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

/**
 * Format the conversation update time for the history sidebar.
 */
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

/**
 * Own conversation selection, message submission, SSE progress and final answer rendering. The
 * server owns persisted messages; local state provides responsive display while the request
 * runs.
 */
export function AgentChatDock({
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
  const [failedPrompt, setFailedPrompt] = useState<string | null>(null);
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

  useEffect(/* Synchronize AgentChatDock with its current dependencies; any returned callback releases this effect's resources or invalidates stale work. */ () => {
    if (initializedSessions.current) {
      return;
    }
    initializedSessions.current = true;
    void initializeChatSessions();
  }, []);

  useEffect(/* Synchronize AgentChatDock with its current dependencies; any returned callback releases this effect's resources or invalidates stale work. */ () => {
    if (!sending) {
      setElapsedMs(0);
      return undefined;
    }
    const startedAt = Date.now();
    const timer = window.setInterval(/* Handle the callback from window.setInterval within AgentChatDock. */ () => {
      setElapsedMs(Date.now() - startedAt);
    }, 100);
    return /* Release or invalidate the enclosing effect's work when dependencies change or the view unmounts. */ () => window.clearInterval(timer);
  }, [sending]);

  useEffect(/* Synchronize AgentChatDock with its current dependencies; any returned callback releases this effect's resources or invalidates stale work. */ () => {
    const container = messagesContainerRef.current;
    if (!container) {
      return;
    }
    const frame = window.requestAnimationFrame(/* Handle the callback from window.requestAnimationFrame within AgentChatDock. */ () => {
      container.scrollTop = container.scrollHeight;
    });
    return /* Release or invalidate the enclosing effect's work when dependencies change or the view unmounts. */ () => window.cancelAnimationFrame(frame);
  }, [messages, sending]);

  /**
   * Replace the active conversation with the fetched detail and remember its ID for reloads.
   */
  function applyChatSession(detail: ChatSessionDetail) {
    setSessionId(detail.session_id);
    window.localStorage.setItem(ACTIVE_CHAT_SESSION_STORAGE_KEY, detail.session_id);
    setMessages(detail.messages.map(restoredChatMessage));
    setError(null);
    setFailedPrompt(null);
  }

  /**
   * Restore the previously selected conversation when available, otherwise select or create a
   * usable session.
   */
  async function initializeChatSessions() {
    setSessionLoading(true);
    try {
      const response = await fetchChatSessions();
      if (response.sessions.length > 0) {
        const savedSessionId = window.localStorage.getItem(ACTIVE_CHAT_SESSION_STORAGE_KEY);
        const targetSession = response.sessions.find(
          /* Locate the entry matching the active identity/time used by initializeChatSessions. */ (item) => item.session_id === savedSessionId,
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

  /**
   * Refresh the sidebar's conversation summaries after a conversation mutation.
   */
  async function refreshChatSessions() {
    const response = await fetchChatSessions();
    setSessions(response.sessions);
    return response.sessions;
  }

  /**
   * Load the chosen conversation while preventing a switch during an active submission.
   */
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

  /**
   * Create and select a new server-backed conversation.
   */
  async function startNewChatSession() {
    if (sending) {
      return;
    }
    setSessionLoading(true);
    try {
      const created = await createChatSession();
      setSessions(/* Compute sessions from the latest React state to avoid overwriting intervening updates. */ (current) => [created, ...current]);
      applyChatSession(created);
      setSessionPanelOpen(false);
      setEditingSessionId(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "新建会话失败");
    } finally {
      setSessionLoading(false);
    }
  }

  /**
   * Persist the edited conversation title and update its sidebar entry.
   */
  async function saveSessionTitle(targetSessionId: string) {
    const title = editingTitle.trim();
    if (!title) {
      return;
    }
    try {
      const updated = await renameChatSession(targetSessionId, title);
      setSessions(/* Compute sessions from the latest React state to avoid overwriting intervening updates. */ (current) => current.map(/* Transform each entry in current into the result used by saveSessionTitle. */ (item) => (
        item.session_id === targetSessionId ? updated : item
      )));
      setEditingSessionId(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "会话重命名失败");
    }
  }

  /**
   * Delete the selected conversation and select or create a replacement when it was active.
   */
  async function deleteSession(targetSessionId: string) {
    if (sending) {
      return;
    }
    const targetSession = sessions.find(/* Locate the entry matching the active identity/time used by deleteSession. */ (item) => item.session_id === targetSessionId);
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

  /**
   * Submit a question with page context, append streamed draft text and replace it with the
   * validated final answer. On transport failure, remove the incomplete draft and retain the
   * prompt for retry.
   */
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
    setMessages(/* Compute messages from the latest React state to avoid overwriting intervening updates. */ (current) => [
      ...current,
      userMessage,
    ]);
    setMessage("");
    setSending(true);
    setStreamStage("planning");
    setStreamStatus("正在理解问题并规划工具");
    setError(null);
    setFailedPrompt(null);
    const agentMessageId = `agent-${Date.now()}`;

    try {
      let receivedAnswer = false;
      // The page supplies identity/date hints. The backend combines these with
      // the question and server-owned context before obtaining factual evidence.
      const response = await streamAgentChatMessage({
        session_id: sessionId,
        message_id: userMessageId,
        message: trimmed,
        intent_hint: inferChatIntent(trimmed),
        trade_date: tradeDate || undefined,
        symbol,
        page_context: {
          page: symbol ? "stock_detail" : "dashboard",
        },
      }, /* Handle progress and draft-answer events while awaiting the authoritative completed response. */ (event) => {
        if (event.event === "progress") {
          setStreamStage(event.data.stage);
          setStreamStatus(event.data.message);
          return;
        }
        if (event.event === "answer_delta") {
          // Draft fragments can be replaced if final validation selects a fallback.
          const delta = event.data.delta;
          setStreamStage("answering");
          setStreamStatus("正在生成回答");
          setMessages(/* Compute messages from the latest React state to avoid overwriting intervening updates. */ (current) => {
            const existing = current.find(/* Locate the entry matching the active identity/time used by sendMessage. */ (item) => item.id === agentMessageId);
            if (existing) {
              return current.map(/* Transform each entry in current into the result used by sendMessage. */ (item) => (
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
      setMessages(/* Compute messages from the latest React state to avoid overwriting intervening updates. */ (current) => {
        const existing = current.find(/* Locate the entry matching the active identity/time used by sendMessage. */ (item) => item.id === agentMessageId);
        if (existing || receivedAnswer) {
          return current.map(/* Transform each entry in current into the result used by sendMessage. */ (item) => (
            item.id === agentMessageId
              ? {
                  ...item,
                  // Replace rather than append: this is the authoritative answer.
                  content: response.answer,
                  ...responseMessageMetadata(response),
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
            ...responseMessageMetadata(response),
          },
        ];
      });
      void refreshChatSessions();
    } catch (caught) {
      const errorMessage = caught instanceof Error ? caught.message : "Agent 回答失败";
      setMessages(/* Compute messages from the latest React state to avoid overwriting intervening updates. */ (current) => current.filter(/* Keep only entries satisfying this predicate for sendMessage. */ (item) => item.id !== agentMessageId));
      setError(errorMessage);
      setFailedPrompt(trimmed);
    } finally {
      setSending(false);
    }
  }

  const activeSession = sessions.find(/* Locate the entry matching the active identity/time used by AgentChatDock. */ (item) => item.session_id === sessionId);
  const latestAgentMessage = [...messages].reverse().find(/* Locate the entry matching the active identity/time used by AgentChatDock. */ (item) => item.role === "agent");
  const promptSuggestions = latestAgentMessage?.suggestedQuestions?.length
    ? latestAgentMessage.suggestedQuestions
    : [
        "总结最新交易日的首板结构",
        "最新一进二 Top10 的主要风险有哪些？",
        "复盘最近 5 个交易日的一进二晋级率",
      ];

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
              onClick={/* Handle onClick for this control in AgentChatDock. */ () => void startNewChatSession()}
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
            {sessions.map(/* Transform each entry in sessions into the result used by AgentChatDock. */ (session) => (
              <div
                className={`chat-session-item ${session.session_id === sessionId ? "active" : ""}`}
                key={session.session_id}
              >
                {editingSessionId === session.session_id ? (
                  <form
                    className="chat-session-rename"
                    onSubmit={/* Handle onSubmit for this control in AgentChatDock. */ (event) => {
                      event.preventDefault();
                      void saveSessionTitle(session.session_id);
                    }}
                  >
                    <input
                      aria-label="会话标题"
                      autoFocus
                      maxLength={80}
                      onChange={/* Handle onChange for this control in AgentChatDock. */ (event) => setEditingTitle(event.target.value)}
                      value={editingTitle}
                    />
                    <button aria-label="保存标题" title="保存" type="submit">
                      <Check size={14} />
                    </button>
                    <button
                      aria-label="取消重命名"
                      onClick={/* Handle onClick for this control in AgentChatDock. */ () => setEditingSessionId(null)}
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
                      onClick={/* Handle onClick for this control in AgentChatDock. */ () => void openChatSession(session.session_id)}
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
                          onClick={/* Handle onClick for this control in AgentChatDock. */ () => {
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
                        onClick={/* Handle onClick for this control in AgentChatDock. */ () => void deleteSession(session.session_id)}
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
              onClick={/* Handle onClick for this control in AgentChatDock. */ () => setSessionPanelOpen(/* Compute session panel open from the latest React state to avoid overwriting intervening updates. */ (current) => !current)}
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
          {messages.map(/* Transform each entry in messages into the result used by AgentChatDock. */ (item) => (
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
          {error ? (
            <div className="chat-state error chat-retry-state">
              <span>{error}</span>
              {failedPrompt ? (
                <button disabled={sending || sessionLoading} onClick={/* Handle onClick for this control in AgentChatDock. */ () => void sendMessage(failedPrompt)} type="button">
                  <RefreshCcw aria-hidden="true" size={13} />重试上一个问题
                </button>
              ) : null}
            </div>
          ) : null}
        </div>

        <div className="agent-chat-prompts">
          {promptSuggestions.slice(0, 3).map(/* Transform each entry in promptSuggestions.slice(0, 3) into the result used by AgentChatDock. */ (prompt) => (
            <button
              disabled={sending || sessionLoading || !sessionId}
              key={prompt}
              type="button"
              onClick={/* Handle onClick for this control in AgentChatDock. */ () => void sendMessage(prompt)}
            >
              {prompt}
            </button>
          ))}
        </div>

        <form
          className="agent-chat-input"
          onSubmit={/* Handle onSubmit for this control in AgentChatDock. */ (event) => {
            event.preventDefault();
            void sendMessage();
          }}
        >
          <input
            disabled={sessionLoading || !sessionId}
            value={message}
            onChange={/* Handle onChange for this control in AgentChatDock. */ (event) => setMessage(event.target.value)}
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

/**
 * Provide a lightweight UI intent hint; the backend remains responsible for authoritative
 * planning.
 */
function inferChatIntent(message: string) {
  /** Infer a deterministic tool hint before the backend performs final routing. */

  if (
    /(?:几点|什么时候|何时).*(?:开盘|收盘)|(?:开盘|收盘).*(?:几点|什么时候|何时)|交易时间|开市时间|今天(?:开不开盘|是否开盘|开盘吗)|集合竞价时间/.test(
      message,
    )
  ) {
    return "market_schedule";
  }
  if (
    /(?:板块|行业|概念).*(?:表现|走势|行情|涨跌|强弱|领涨|领跌|涨得|跌得|排行|排名)|(?:表现|走势|行情|涨跌|强弱|领涨|领跌|涨得|跌得|排行|排名).*(?:板块|行业|概念)/.test(message)
  ) {
    return "sector_performance";
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
  if (
    /跌停/.test(message)
    && /哪些|有哪|名单|列出|列一下|几只|多少只|数量|统计|谁|^(今天|今日|最新)?跌停(股|票)?$/.test(message)
  ) {
    return "market_event_query";
  }
  if (/涨停|首板|连板|二板|三板|炸板|最高板/.test(message)) {
    return "limit_up_query";
  }
  if (/医药|医疗|制药|药业|生物|中药|相关|行业|题材/.test(message)) {
    return "first_board_filter";
  }
  if (/总结|今天|首板|候选|市场环境/.test(message)) {
    return "today_summary";
  }
  return undefined;
}
