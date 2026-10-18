import {
  ArrowDown,
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
import { useCallback, useEffect, useRef, useState } from "react";

import {
  cancelAgentChatRun,
  createChatSession,
  deleteChatSession,
  fetchChatBootstrap,
  fetchChatSession,
  fetchChatSessions,
  renameChatSession,
  streamAgentChatMessage,
} from "../api";
import type {
  AgentChatRequest,
  AgentChatResponse,
  AgentChatStreamStage,
  AgentStockMention,
  ChatSessionDetail,
  ChatSessionMessage,
  ChatSessionSummary,
} from "../types";
import { AgentAnswerMarkdown } from "./AgentAnswerMarkdown";
import { AgentChatProgress } from "./AgentChatProgress";
import { AnswerFeedback } from "./AnswerFeedback";
import { useAnswerDelivery, useAnswerQuality } from "../hooks/useAnswerQuality";
import { qualityApi } from "../qualityApi";
import { answerHash } from "../utils/agentQuality";
import { taskStatusLabel } from "../utils/agentChatTransport";
import { HttpResponseError } from "../utils/jsonRequest";
import {
  createChatOperationLock,
  draftAfterChatSubmit,
  readActiveChatSession,
  rememberActiveChatSession,
  shouldSubmitChatKey,
} from "../utils/agentChatInteraction";

interface ChatMessage {
  id: string;
  runId?: string | null;
  role: "user" | "agent";
  content: string;
  stockMentions: AgentStockMention[];
  status?: "success" | "error";
  suggestedQuestions?: string[];
  taskLabel?: string;
}

const ACTIVE_CHAT_SESSION_STORAGE_KEY = "limituplab.activeChatSession";
const DEFAULT_PROMPT_SUGGESTIONS = ["最新的财经新闻", "市场最高标是哪个", "最新的龙虎榜情况"];
const SAVED_DEFAULT_PROMPT_SUGGESTIONS = [
  "按板块归纳最新首板候选",
  "这些候选的共同风险和数据缺口有哪些？",
  "复盘最近 5 个交易日的一进二晋级率",
];

/** Keep HTTP diagnostics out of the conversation while retaining a useful recovery action. */
function sessionErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof HttpResponseError) {
    if (error.status === 404 || error.status === 405) {
      return "会话服务暂不可用，请重新加载会话。";
    }
    if (error.status === 401 || error.status === 403) {
      return "暂时无法访问这段会话，请重新加载会话。";
    }
    if (error.status === 429) {
      return "会话请求较多，请稍后重试。";
    }
    if (error.status >= 500) {
      return "会话服务暂时异常，请稍后重试。";
    }
  }
  return `${fallback}，请稍后重试。`;
}

/**
 * Convert a persisted message and its metadata back into the chat UI representation.
 */
function restoredChatMessage(message: ChatSessionMessage): ChatMessage {
  return {
    id: message.message_id,
    runId: message.run_id,
    role: message.role === "assistant" ? "agent" : "user",
    content: message.content,
    stockMentions: stockMentionsFromMetadata(message.metadata),
    status: message.status,
    suggestedQuestions: stringArray(message.metadata.suggested_questions),
    taskLabel: taskStatusLabel(message.metadata.task_status),
  };
}

/**
 * Extract the final response fields needed by the rendered chat message.
 */
function responseMessageMetadata(response: AgentChatResponse): Partial<ChatMessage> {
  return {
    runId: response.run_id,
    stockMentions: response.stock_mentions,
    suggestedQuestions: response.suggested_questions,
    status: response.task_status === "error" ? "error" : "success",
    taskLabel: taskStatusLabel(response.task_status),
  };
}

/**
 * Keep string members from unknown persisted metadata before rendering them.
 */
function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
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
  const runStartedAt = useRef(0);
  const [streamStage, setStreamStage] = useState<AgentChatStreamStage>("preparing");
  const [streamStatus, setStreamStatus] = useState("正在连接研究助手");
  const [error, setError] = useState<string | null>(null);
  const [failedPrompt, setFailedPrompt] = useState<string | null>(null);
  const failedRequest = useRef<AgentChatRequest | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [cancelRequested, setCancelRequested] = useState(false);
  const userCancelledRun = useRef<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [revealingMessageId, setRevealingMessageId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [qualityRevision, setQualityRevision] = useState(0);
  const quality = useAnswerQuality(sessionId, qualityRevision);
  const acknowledgeDisplay = useAnswerDelivery();
  const [sessionLoading, setSessionLoading] = useState(true);
  const [sessionError, setSessionError] = useState<{
    message: string;
    retry: () => Promise<void>;
  } | null>(null);
  const [sessionPanelOpen, setSessionPanelOpen] = useState(false);
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const initializedSessions = useRef(false);
  const operationLock = useRef(createChatOperationLock());
  const drafts = useRef(new Map<string, string>());
  const composing = useRef(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const followAnswerRef = useRef(true);
  const [showLatest, setShowLatest] = useState(false);
  const isConversationActive = sending || messages.length > 0;

  useEffect(() => {
    if (initializedSessions.current) {
      return;
    }
    initializedSessions.current = true;
    void initializeChatSessions();
  }, []);

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight + 2, 160)}px`;
  }, [message]);

  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container || !followAnswerRef.current) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      container.scrollTop = container.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [messages, sending, streamStatus]);

  const followRevealedAnswer = useCallback(() => {
    const container = messagesContainerRef.current;
    if (container && followAnswerRef.current) container.scrollTop = container.scrollHeight;
  }, []);

  /**
   * Replace the active conversation with the fetched detail and remember its ID for reloads.
   */
  function applyChatSession(detail: ChatSessionDetail) {
    setRevealingMessageId(null);
    followAnswerRef.current = true;
    setShowLatest(false);
    setSessionId(detail.session_id);
    setQualityRevision(value => value + 1);
    rememberActiveChatSession(() => window.localStorage.setItem(ACTIVE_CHAT_SESSION_STORAGE_KEY, detail.session_id));
    setMessage(drafts.current.get(detail.session_id) ?? "");
    setMessages(detail.messages.map(restoredChatMessage));
    setError(null);
    setFailedPrompt(null);
    failedRequest.current = null;
    setActiveRunId(null);
    setCancelRequested(false);
  }

  /**
   * Restore the previously selected conversation when available, otherwise select or create a
   * usable session.
   */
  async function initializeChatSessions() {
    const release = operationLock.current.acquire();
    if (!release) return;
    setSessionLoading(true);
    setSessionError(null);
    try {
      const savedSessionId = readActiveChatSession(() => window.localStorage.getItem(ACTIVE_CHAT_SESSION_STORAGE_KEY));
      const response = await fetchChatBootstrap(savedSessionId);
      setSessions(response.sessions);
      if (response.selected_session) {
        const detail = response.selected_session;
        if (!response.sessions.some(item => item.session_id === detail.session_id)) {
          setSessions([detail, ...response.sessions]);
        }
        applyChatSession(detail);
      } else if (response.sessions.length > 0) {
        throw new Error("所选会话已发生变化，请重新加载会话");
      } else {
        const created = await createChatSession();
        setSessions([created]);
        applyChatSession(created);
      }
    } catch (caught) {
      setSessionError({
        message: sessionErrorMessage(caught, "会话加载失败"),
        retry: initializeChatSessions,
      });
    } finally {
      release();
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
    if (sending || sessionLoading) return;
    if (targetSessionId === sessionId) {
      setSessionPanelOpen(false);
      return;
    }
    const release = operationLock.current.acquire();
    if (!release) return;
    setRevealingMessageId(null);
    setSessionLoading(true);
    setSessionError(null);
    try {
      const detail = await fetchChatSession(targetSessionId);
      applyChatSession(detail);
      setSessionPanelOpen(false);
      setEditingSessionId(null);
    } catch (caught) {
      setSessionError({
        message: sessionErrorMessage(caught, "会话恢复失败"),
        retry: () => openChatSession(targetSessionId),
      });
    } finally {
      release();
      setSessionLoading(false);
    }
  }

  /**
   * Create and select a new server-backed conversation.
   */
  async function startNewChatSession() {
    if (sending || sessionLoading) return;
    const release = operationLock.current.acquire();
    if (!release) return;
    setRevealingMessageId(null);
    setSessionLoading(true);
    setSessionError(null);
    try {
      const created = await createChatSession();
      setSessions((current) => [created, ...current]);
      applyChatSession(created);
      setSessionPanelOpen(false);
      setEditingSessionId(null);
    } catch (caught) {
      setSessionError({
        message: sessionErrorMessage(caught, "新建会话失败"),
        retry: initializeChatSessions,
      });
    } finally {
      release();
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
      setSessions((current) => current.map((item) => (
        item.session_id === targetSessionId ? updated : item
      )));
      setEditingSessionId(null);
    } catch (caught) {
      setError(sessionErrorMessage(caught, "会话重命名失败"));
    }
  }

  /**
   * Delete the selected conversation and select or create a replacement when it was active.
   */
  async function deleteSession(targetSessionId: string) {
    if (sending || sessionLoading) {
      return;
    }
    const targetSession = sessions.find((item) => item.session_id === targetSessionId);
    const confirmed = window.confirm(
      `确定删除会话“${targetSession?.title ?? "未命名会话"}”吗？删除后无法恢复。`,
    );
    if (!confirmed) {
      return;
    }
    const release = operationLock.current.acquire();
    if (!release) return;
    setSessionLoading(true);
    setSessionError(null);
    try {
      await deleteChatSession(targetSessionId);
      drafts.current.delete(targetSessionId);
      if (targetSessionId === sessionId) {
        setSessionId(null);
        setMessages([]);
        setMessage("");
        setError(null);
        setFailedPrompt(null);
        failedRequest.current = null;
      }
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
      setSessionError({
        message: sessionErrorMessage(caught, "会话删除失败"),
        retry: initializeChatSessions,
      });
    } finally {
      release();
      setSessionLoading(false);
    }
  }

  async function cancelCurrentRun() {
    if (!activeRunId || cancelRequested) return;
    userCancelledRun.current = activeRunId;
    setCancelRequested(true);
    try {
      await cancelAgentChatRun(activeRunId);
      setStreamStatus("已请求取消，等待任务收尾");
    } catch (caught) {
      setCancelRequested(false);
      setError(caught instanceof Error ? caught.message : "取消请求失败");
    }
  }

  /** Preserve failed requests for retry; publish the verified response exactly once. */
  async function sendMessage(prompt?: string) {
    const trimmed = (prompt ?? message).trim();
    if (!trimmed || sending || sessionLoading || !sessionId) {
      return;
    }
    const release = operationLock.current.acquire();
    if (!release) return;
    setRevealingMessageId(null);

    const retry = failedRequest.current?.session_id === sessionId && failedRequest.current.message === trimmed
      ? failedRequest.current : null;
    const userMessageId = retry?.message_id ?? `msg_${Date.now()}_${Math.random().toString(16).slice(2)}`;
    const payload: AgentChatRequest = retry ?? {
      session_id: sessionId, message_id: userMessageId, message: trimmed,
      trade_date: tradeDate || undefined, symbol,
      page_context: { page: symbol ? "stock_detail" : "dashboard" },
    };
    const userMessage: ChatMessage = {
      id: userMessageId,
      role: "user",
      content: trimmed,
      stockMentions: [],
    };
    setMessages((current) => [
      ...current,
      ...(current.some(item => item.id === userMessageId) ? [] : [userMessage]),
    ]);
    const nextDraft = draftAfterChatSubmit(message, prompt);
    setMessage(nextDraft);
    drafts.current.set(sessionId, nextDraft);
    inputRef.current?.focus();
    followAnswerRef.current = true;
    setShowLatest(false);
    runStartedAt.current = Date.now();
    setSending(true);
    setStreamStage("preparing");
    setStreamStatus("正在连接研究助手");
    setError(null);
    setFailedPrompt(null);
    setActiveRunId(null);
    setCancelRequested(false);
    const agentMessageId = `agent-${userMessageId}`;
    userCancelledRun.current = null;
    let submittedRunId: string | null = null;
    setMessages(current => current.filter(item => item.id !== agentMessageId));
    try {
      const response = await streamAgentChatMessage(payload, event => {
        if (event.event === "accepted") {
          submittedRunId = event.data.run_id;
          setActiveRunId(event.data.run_id);
        }
        if (event.event === "progress") {
          setStreamStage(event.data.stage);
          setStreamStatus(event.data.message);
        }
      });
      if (response.run_id) {
        void answerHash(response.answer).then(hash => hash && qualityApi.delivery(response.run_id!, "received", hash)).catch(() => {});
      }
      setMessages(current => {
        const finalMessage: ChatMessage = {
          id: agentMessageId, role: "agent", content: response.answer,
          stockMentions: response.stock_mentions, ...responseMessageMetadata(response),
        };
        const existing = current.findIndex(item => item.id === agentMessageId);
        if (existing < 0) return [...current, finalMessage];
        return current.map((item, index) => index === existing ? finalMessage : item);
      });
      failedRequest.current = null;
      setQualityRevision(value => value + 1);
      setActiveRunId(null);
      const canRestart = response.task_status === "error" || response.task_status === "cancelled";
      setRevealingMessageId(canRestart ? null : agentMessageId);
      setFailedPrompt(canRestart ? trimmed : null);
      setError(canRestart
        ? (response.task_status === "cancelled" ? "任务已取消，可重新提问。" : "本次研究未完成，可重新提问。")
        : null);
      void refreshChatSessions().catch(caught => setSessionError({
        message: sessionErrorMessage(caught, "会话列表更新失败"),
        retry: initializeChatSessions,
      }));
    } catch (caught) {
      if (submittedRunId && userCancelledRun.current !== submittedRunId) {
        void qualityApi.delivery(submittedRunId, "interrupted", null).catch(() => {});
      }
      const errorMessage = caught instanceof Error ? caught.message : "Agent 回答失败";
      setError(errorMessage);
      setFailedPrompt(trimmed);
      failedRequest.current = payload;
    } finally {
      setActiveRunId(null);
      setCancelRequested(false);
      release();
      setSending(false);
    }
  }

  const activeSession = sessions.find((item) => item.session_id === sessionId);
  const latestAgentMessage = [...messages].reverse().find((item) => item.role === "agent");
  const savedSuggestions = latestAgentMessage?.suggestedQuestions ?? [];
  // Refresh the saved generic defaults without replacing contextual follow-ups.
  const promptSuggestions = savedSuggestions.length === SAVED_DEFAULT_PROMPT_SUGGESTIONS.length
    && savedSuggestions.every((prompt, index) => prompt === SAVED_DEFAULT_PROMPT_SUGGESTIONS[index])
    ? DEFAULT_PROMPT_SUGGESTIONS
    : savedSuggestions;

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
                      disabled={sending || sessionLoading}
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
                          disabled={sending || sessionLoading}
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

        <div className="agent-chat-conversation">
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
          </div>
        </header>

        <div
          aria-label="对话消息"
          className="agent-chat-messages"
          ref={messagesContainerRef}
          onScroll={event => {
            const element = event.currentTarget;
            followAnswerRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 64;
            setShowLatest(!followAnswerRef.current);
          }}
        >
          {sessionLoading ? (
            <div className="chat-session-loading" role="status">
              <LoaderCircle aria-hidden="true" size={16} />
              <span>正在加载会话</span>
            </div>
          ) : null}
          {sessionError ? (
            <div className="chat-state error chat-retry-state" role="alert">
              <span>{sessionError.message}</span>
              <button disabled={sending || sessionLoading} onClick={() => {
                const retry = sessionError.retry;
                setSessionError(null);
                void retry().catch(caught => setSessionError({
                  message: sessionErrorMessage(caught, "会话加载失败"),
                  retry,
                }));
              }} type="button">
                <RefreshCcw aria-hidden="true" size={13} />重新加载会话
              </button>
            </div>
          ) : null}
          {sessionId && messages.length === 0 && !sending && !sessionLoading && !sessionError && !error ? (
            <div className="chat-state" role="status" aria-label="新会话提示">
              <strong>开始一段研究对话</strong>
              <p>在下方输入问题，开始研究。</p>
            </div>
          ) : null}
          {messages.map((item) => (
            <article
              className={`chat-message chat-${item.role} ${item.status === "error" ? "chat-error" : ""}`}
              key={item.id}
            >
              {item.role === "agent" ? (
                <div className="chat-markdown">
                  {item.taskLabel ? <small className="chat-task-status">
                    {item.taskLabel}
                  </small> : null}
                  <AgentAnswerMarkdown
                    content={item.content}
                    stockMentions={item.stockMentions}
                    animate={revealingMessageId === item.id}
                    onRevealProgress={followRevealedAnswer}
                    onRevealComplete={() => { if (item.runId) void acknowledgeDisplay(item.runId, item.content); }}
                  />
                  {item.runId ? <AnswerFeedback key={item.runId} runId={item.runId}
                    initial={quality.runs[item.runId]?.feedback} ready={quality.ready} /> : null}
                </div>
              ) : (
                <p>{item.content}</p>
              )}
            </article>
          ))}
          {quality.error ? <div className="chat-state" role="status">
            回答反馈暂未载入。<button type="button" onClick={quality.retry}>重新加载反馈</button>
          </div> : null}
          {sending ? (
            <AgentChatProgress stage={streamStage} message={streamStatus} startedAt={runStartedAt.current} />
          ) : null}
          {error ? (
            <div className="chat-state error chat-retry-state" role="alert">
              <span>{error}</span>
              {failedPrompt ? (
                <button disabled={sending || sessionLoading} onClick={() => void sendMessage(failedPrompt)} type="button">
                  <RefreshCcw aria-hidden="true" size={13} />{failedRequest.current ? "重试上一个问题" : "重新提问"}
                </button>
              ) : null}
            </div>
          ) : null}
        </div>

        {showLatest ? (
          <button className="chat-scroll-latest" type="button" onClick={() => {
            followAnswerRef.current = true;
            setShowLatest(false);
            const container = messagesContainerRef.current;
            if (container) container.scrollTop = container.scrollHeight;
          }}>
            <ArrowDown aria-hidden="true" size={14} />回到最新消息
          </button>
        ) : null}

        {promptSuggestions.length > 0 ? <div className="agent-chat-prompts">
          {promptSuggestions.slice(0, 3).map((prompt) => (
            <button
              disabled={sending || sessionLoading || !sessionId}
              key={prompt}
              type="button"
              onClick={() => void sendMessage(prompt)}
            >
              {prompt}
            </button>
          ))}
        </div> : null}

        <form
          className="agent-chat-input"
          onSubmit={(event) => {
            event.preventDefault();
            void sendMessage();
          }}
        >
          <textarea
            aria-label="研究问题"
            ref={inputRef}
            rows={1}
            disabled={sessionLoading || !sessionId}
            value={message}
            onChange={(event) => {
              setMessage(event.target.value);
              if (sessionId) drafts.current.set(sessionId, event.target.value);
            }}
            onCompositionStart={() => { composing.current = true; }}
            onCompositionEnd={() => { composing.current = false; }}
            onKeyDown={(event) => {
              if (shouldSubmitChatKey(event.nativeEvent, composing.current)) {
                event.preventDefault();
                void sendMessage();
              }
            }}
            placeholder="问今日市场情况、个股详情等"
          />
          {sending && activeRunId ? (
            <button className="chat-cancel" type="button" disabled={cancelRequested} onClick={() => void cancelCurrentRun()}>
              {cancelRequested ? "正在取消" : "取消任务"}
            </button>
          ) : (
            <button
              aria-label="发送问题"
              className="icon-button"
              disabled={sending || sessionLoading || !sessionId || !message.trim()}
              title="发送"
              type="submit"
            >
              <Send size={17} />
            </button>
          )}
        </form>
      </section>
        <p className="agent-chat-disclaimer">内容由AI生成， 仅用于学习研究， 不构成任何投资建议</p>
        </div>
      </div>
    </div>
  );
}
