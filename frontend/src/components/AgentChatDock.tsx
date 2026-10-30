import {
  ArrowDown,
  Check,
  LoaderCircle,
  MessageCircle,
  PanelLeft,
  Pause,
  Pencil,
  Plus,
  Play,
  RefreshCcw,
  Send,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  cancelAgentChatRun,
  controlAgentChatRun,
  createChatSession,
  deleteChatSession,
  fetchChatBootstrap,
  fetchAgentChatRun,
  fetchChatSession,
  fetchChatSessions,
  renameChatSession,
  streamAgentChatMessage,
  streamAgentChatRun,
} from "../api";
import type {
  AgentChatRequest,
  AgentChatResponse,
  AgentChatStreamEvent,
  AgentChatStreamResult,
  AgentChatStreamStage,
  AgentDisplayBlock,
  AgentStockMention,
  AgentRunCommand,
  AgentRunState,
  AnswerFeedbackValue,
  ChatSessionDetail,
  ChatSessionMessage,
  ChatSessionSummary,
} from "../types";
import { AgentAnswerMarkdown } from "./AgentAnswerMarkdown";
import { AgentChatProgress } from "./AgentChatProgress";
import { AgentDisplayBlocks } from "./AgentDisplayBlocks";
import { AnswerFeedback } from "./AnswerFeedback";
import { createAgentAnswerBuffer } from "../utils/agentAnswerBuffer";
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
  displayBlocks?: AgentDisplayBlock[];
  status?: "success" | "error";
  suggestedQuestions?: string[];
  taskLabel?: string;
  feedback?: AnswerFeedbackValue | null;
  provisional?: boolean;
}

const ACTIVE_CHAT_SESSION_STORAGE_KEY = "limituplab.activeChatSession";
const DEFAULT_PROMPT_SUGGESTIONS = ["最新的财经新闻", "市场最高标是哪个", "最新的龙虎榜情况"];
const SAVED_DEFAULT_PROMPT_SUGGESTIONS = [
  "按板块归纳最新首板候选",
  "这些候选的共同风险和数据缺口有哪些？",
  "复盘最近 5 个交易日的一进二晋级率",
];

/** Refresh known saved defaults without rewriting answers or custom follow-ups. */
function currentPromptSuggestions(message: ChatMessage | undefined): string[] {
  const saved = message?.suggestedQuestions ?? [];
  const matches = (questions: string[]) => questions.length === saved.length
    && questions.every((question, index) => question === saved[index]);
  if (message?.stockMentions.length === 1) {
    const stock = message.stockMentions[0];
    const subject = `${stock.name}（${stock.symbol}）`;
    const rating = `${subject}的评分依据是什么？`;
    const risk = `${subject}的主要风险和数据缺口是什么？`;
    const trend = `${subject}最近 20 个交易日走势如何？`;
    const generic = SAVED_DEFAULT_PROMPT_SUGGESTIONS[0];
    if ([[rating, risk, trend], [risk, trend, generic], [rating, trend, generic]].some(matches)) {
      return [`${subject}的近期走势如何？`];
    }
  }
  return matches(SAVED_DEFAULT_PROMPT_SUGGESTIONS) ? DEFAULT_PROMPT_SUGGESTIONS : saved;
}

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

function displayDuplicateListLabels(blocks?: AgentDisplayBlock[]): string[] {
  const labels = new Set<string>();
  for (const block of blocks ?? []) {
    for (const row of block.rows) {
      const values = block.columns
        .map(column => row[column])
        .filter(value => value !== null && value !== undefined && value !== "");
      const texts = values.map(value => String(value));
      texts.forEach(value => labels.add(value));
      if (texts.length > 1) labels.add(texts.join(" "));
      const name = row.name === null || row.name === undefined ? "" : String(row.name);
      const symbol = row.symbol === null || row.symbol === undefined ? "" : String(row.symbol);
      if (name && symbol) {
        labels.add(name);
        labels.add(symbol);
        labels.add(`${symbol} ${name}`);
        labels.add(`${name} ${symbol}`);
        labels.add(`${name}（${symbol}）`);
      }
    }
  }
  return [...labels];
}

function displayBlocksFromMetadata(value: unknown): AgentDisplayBlock[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is AgentDisplayBlock => {
    if (!item || typeof item !== "object") return false;
    const block = item as Partial<AgentDisplayBlock>;
    return block.kind === "table"
      && Array.isArray(block.columns)
      && Array.isArray(block.rows)
      && typeof block.source_tool === "string";
  });
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
    displayBlocks: displayBlocksFromMetadata(message.metadata.display_blocks),
    status: message.status,
    suggestedQuestions: stringArray(message.metadata.suggested_questions),
    taskLabel: taskStatusLabel(message.metadata.task_status),
    feedback: message.feedback ?? null,
  };
}

/**
 * Extract the final response fields needed by the rendered chat message.
 */
function responseMessageMetadata(response: AgentChatResponse): Partial<ChatMessage> {
  return {
    runId: response.run_id,
    stockMentions: response.stock_mentions,
    displayBlocks: response.display_blocks,
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
  const [activeRun, setActiveRun] = useState<AgentRunState | null>(null);
  const runRef = useRef<AgentRunState | null>(null);
  const selectedSession = useRef<string | null>(null);
  const subscription = useRef({ generation: 0, controller: null as AbortController | null, cursor: 0 });
  const commandBusy = useRef<string | null>(null);
  const settledRuns = useRef(new Set<string>());
  const commandRetry = useRef<{ runId: string; action: "pause" | "resume"; command: AgentRunCommand } | null>(null);
  const [controlAction, setControlAction] = useState<"pause" | "resume" | null>(null);
  const [cancelRequested, setCancelRequested] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [revealingMessageId, setRevealingMessageId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
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
  const answerBufferRef = useRef<ReturnType<typeof createAgentAnswerBuffer> | null>(null);
  const provisionalMessageRef = useRef<string | null>(null);
  const [showLatest, setShowLatest] = useState(false);
  const isConversationActive = sending || messages.length > 0;
  const activeRunId = activeRun?.run_id;

  useEffect(() => () => {
    answerBufferRef.current?.dispose();
    answerBufferRef.current = null;
  }, []);

  useEffect(() => () => {
    subscription.current.generation++;
    subscription.current.controller?.abort();
  }, []);

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
    detachSubscription();
    selectedSession.current = detail.session_id;
    setRevealingMessageId(null);
    followAnswerRef.current = true;
    setShowLatest(false);
    setSessionId(detail.session_id);
    rememberActiveChatSession(() => window.localStorage.setItem(ACTIVE_CHAT_SESSION_STORAGE_KEY, detail.session_id));
    setMessage(drafts.current.get(detail.session_id) ?? "");
    setMessages(detail.messages.map(restoredChatMessage));
    setError(null);
    setFailedPrompt(null);
    failedRequest.current = null;
    runRef.current = detail.active_run ?? null;
    setActiveRun(detail.active_run ?? null);
    commandRetry.current = null;
    setControlAction(null);
    setCancelRequested(false);
    if (detail.active_run && (detail.active_run.run_status !== "paused" || detail.active_run.cancel_requested)) void watchRun(detail.active_run);
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
   * Switch the visible subscription without changing a background run's execution intent.
   */
  async function openChatSession(targetSessionId: string) {
    if (sessionLoading) return;
    if (targetSessionId === sessionId) {
      setSessionPanelOpen(false);
      return;
    }
    const release = operationLock.current.acquire();
    if (!release) return;
    detachSubscription();
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
    if (sessionLoading) return;
    const release = operationLock.current.acquire();
    if (!release) return;
    detachSubscription();
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
    if (!activeRunId || cancelRequested || commandBusy.current === activeRunId) return;
    const targetSession = selectedSession.current;
    commandBusy.current = activeRunId;
    setCancelRequested(true);
    try {
      await cancelAgentChatRun(activeRunId);
      if (targetSession !== selectedSession.current || runRef.current?.run_id !== activeRunId) return;
      setStreamStatus("已请求取消，等待任务收尾");
      // A paused or disconnected run has no open stream to deliver cancellation.
      if (!subscription.current.controller) {
        const current = await fetchAgentChatRun(activeRunId);
        if (targetSession === selectedSession.current && applyRunState(current)) void watchRun(current);
      }
    } catch (caught) {
      if (targetSession !== selectedSession.current) return;
      setCancelRequested(false);
      setError(caught instanceof Error ? caught.message : "取消请求失败");
    } finally {
      if (commandBusy.current === activeRunId) commandBusy.current = null;
    }
  }

  function detachSubscription() {
    subscription.current.generation++;
    subscription.current.controller?.abort();
    subscription.current.controller = null;
    answerBufferRef.current?.dispose();
    answerBufferRef.current = null;
    const draft = provisionalMessageRef.current;
    provisionalMessageRef.current = null;
    if (draft) setMessages(current => current.filter(item => item.id !== draft));
    setSending(false);
  }

  function applyRunState(run: AgentRunState) {
    if (run.session_id !== selectedSession.current || settledRuns.current.has(run.run_id)) return false;
    const previous = runRef.current;
    if (previous?.run_id === run.run_id) {
      if (run.state_revision < previous.state_revision) return false;
      if (["completed", "cancelled", "failed"].includes(previous.run_status) && run.run_status !== previous.run_status) return false;
      // The revision orders control intents; acknowledgement within an intent
      // advances pausing->paused and resuming->running without incrementing it.
      if (run.state_revision === previous.state_revision &&
        ((previous.run_status === "paused" && run.run_status === "pausing") ||
          (previous.run_status === "running" && run.run_status === "resuming"))) return false;
    }
    runRef.current = run;
    setActiveRun(run);
    const labels: Partial<Record<AgentRunState["run_status"], string>> = {
      pausing: "正在暂停，当前步骤保存后生效", paused: "已暂停，可从保存的进度继续",
      resuming: "正在恢复已保存的进度", interrupted: "任务已中断，可继续原任务",
    };
    if (run.cancel_requested) setStreamStatus("正在结束任务，等待正式结果");
    else if (labels[run.run_status]) setStreamStatus(labels[run.run_status]!);
    return true;
  }

  function publishResponse(response: AgentChatResponse, prompt?: string, messageId?: string) {
    const completedRun = response.run_id ?? runRef.current?.run_id;
    if (completedRun) settledRuns.current.add(completedRun);
    provisionalMessageRef.current = null;
    const id = response.run_id ? `assistant_${response.run_id}` : `agent-${messageId}`;
    setMessages(current => {
      const index = current.findIndex(item => item.role === "agent" &&
        (item.id === id || Boolean(response.run_id && item.runId === response.run_id)));
      const result: ChatMessage = { id, role: "agent", content: response.answer,
        stockMentions: response.stock_mentions, ...responseMessageMetadata(response) };
      return index < 0 ? [...current, result] : current.map((item, position) => position === index ? result : item);
    });
    runRef.current = null;
    setActiveRun(null);
    failedRequest.current = null;
    commandRetry.current = null;
    setCancelRequested(false);
    const canRestart = response.task_status === "error" || response.task_status === "cancelled";
    setRevealingMessageId(canRestart ? null : id);
    setFailedPrompt(canRestart ? prompt ?? null : null);
    setError(canRestart ? (response.task_status === "cancelled" ? "任务已取消，可重新提问。" : "本次研究未完成，可重新提问。") : null);
    void refreshChatSessions().catch(() => { /* History can be refreshed separately from committed delivery. */ });
  }

  async function receiveRun(start: (handler: (event: AgentChatStreamEvent) => void,
    options: { signal: AbortSignal; after: number }) => Promise<AgentChatStreamResult>,
    targetSession: string, prompt?: string, request?: AgentChatRequest, after = 0) {
    detachSubscription();
    const generation = subscription.current.generation;
    const controller = new AbortController();
    subscription.current.controller = controller;
    subscription.current.cursor = after;
    const current = () => generation === subscription.current.generation && targetSession === selectedSession.current;
    runStartedAt.current = Date.now();
    setSending(true);
    let draftId: string | null = null;
    let stockMentions: AgentStockMention[] = [];
    const draftMessageId = () => (runRef.current?.run_id ? `assistant_${runRef.current.run_id}`
      : request?.message_id ? `agent-${request.message_id}` : null);
    const buffer = createAgentAnswerBuffer(content => {
      const id = draftId;
      if (!current() || !id) return;
      if (!content) {
        setMessages(currentMessages => currentMessages.filter(item => item.id !== id));
        return;
      }
      const draft: ChatMessage = { id, role: "agent", content, stockMentions, provisional: true };
      setMessages(currentMessages => currentMessages.some(item => item.id === id)
        ? currentMessages.map(item => item.id === id ? draft : item)
        : [...currentMessages, draft]);
    });
    answerBufferRef.current = buffer;
    const removeDraft = () => {
      const id = draftId;
      provisionalMessageRef.current = null;
      if (id) setMessages(currentMessages => currentMessages.filter(item => item.id !== id));
    };
    try {
      const response = await start(event => {
        if (!current()) return;
        if (event.id !== undefined) subscription.current.cursor = Math.max(subscription.current.cursor, event.id);
        if (event.event === "accepted" && !runRef.current) applyRunState({
          run_id: event.data.run_id, session_id: targetSession, run_status: "running",
          state_revision: event.data.state_revision ?? 0, last_event_id: 0,
          can_pause: false, can_resume: false, can_cancel: true,
        });
        if (event.event === "run_state") applyRunState(event.data);
        if (event.event === "progress" || event.event === "status" || event.event === "tool_start"
          || event.event === "tool_end" || event.event === "warning") {
          setStreamStage(event.data.stage);
          if (!["pausing", "resuming"].includes(runRef.current?.run_status ?? "")) setStreamStatus(event.data.message);
        }
        if (event.event === "answer_start") {
          const id = draftMessageId();
          if (!id) return;
          draftId = id;
          provisionalMessageRef.current = id;
          stockMentions = event.data.stock_mentions ?? [];
          setStreamStage("answering");
          setStreamStatus("正在生成回答，完成后以正式答复为准");
          buffer.start(event.data.revision);
        }
        if (event.event === "answer_delta") {
          if (!draftId) {
            const id = draftMessageId();
            if (!id) return;
            draftId = id;
            provisionalMessageRef.current = id;
            setStreamStage("answering");
            setStreamStatus("正在生成回答，完成后以正式答复为准");
          }
          try {
            buffer.append(event.data.offset, event.data.delta);
          } catch {
            // A gap means the local buffer missed a frame. Drop it and let the
            // transport reconnect; the server replays the current revision.
            buffer.resync();
            throw new Error("回答片段不连续，正在重新读取");
          }
        }
        if (event.event === "answer_reset") {
          buffer.reset();
          setStreamStage("checking");
          setStreamStatus(event.data.message);
        }
      }, { signal: controller.signal, after });
      buffer.dispose();
      if (answerBufferRef.current === buffer) answerBufferRef.current = null;
      if (!current()) return;
      if ("paused" in response) {
        removeDraft();
        applyRunState(response.run);
        setError(null);
      } else {
        removeDraft();
        publishResponse(response, prompt, request?.message_id);
      }
    } catch (caught) {
      buffer.dispose();
      if (answerBufferRef.current === buffer) answerBufferRef.current = null;
      if (!current() || controller.signal.aborted) return;
      removeDraft();
      setError(caught instanceof Error ? caught.message : "连接中断，请重新连接原任务");
      const run = runRef.current;
      if (run) {
        setFailedPrompt(request ? prompt ?? null : null);
        if (request) failedRequest.current = request;
        try {
          const latest = await fetchAgentChatRun(run.run_id);
          if (!current()) return;
          if (!applyRunState(latest)) return;
          if (latest.response) publishResponse(latest.response, prompt, request?.message_id);
          else if (["pausing", "paused", "resuming"].includes(latest.run_status)) {
            setFailedPrompt(null);
            failedRequest.current = null;
            if (latest.run_status === "paused" && !latest.cancel_requested) setError(null);
          }
        } catch { /* Keep the owned run available for read-only reconnection. */ }
      } else if (request) {
        setFailedPrompt(prompt ?? null);
        failedRequest.current = request;
      }
    } finally {
      buffer.dispose();
      if (answerBufferRef.current === buffer) answerBufferRef.current = null;
      if (current()) {
        subscription.current.controller = null;
        setSending(false);
        setCancelRequested(false);
      }
    }
  }

  async function watchRun(run: AgentRunState) {
    if (settledRuns.current.has(run.run_id)) return;
    if (run.response) { publishResponse(run.response); return; }
    if (run.run_status === "paused" && !run.cancel_requested) return;
    setError(null);
    await receiveRun((handler, options) => streamAgentChatRun(run, handler, options),
      run.session_id, undefined, undefined, run.last_event_id);
  }

  async function controlRun(action: "pause" | "resume") {
    const run = runRef.current;
    if (!run || commandBusy.current === run.run_id) return;
    const targetSession = selectedSession.current;
    commandBusy.current = run.run_id;
    setControlAction(action);
    const retry = commandRetry.current;
    const command = retry?.runId === run.run_id && retry.action === action ? retry.command : {
      operation_id: globalThis.crypto?.randomUUID?.() ?? `operation_${Date.now()}_${Math.random().toString(16).slice(2)}`,
      expected_state_revision: run.state_revision,
    };
    commandRetry.current = { runId: run.run_id, action, command };
    try {
      const result = await controlAgentChatRun(run.run_id, action, command);
      if (targetSession !== selectedSession.current) return;
      commandRetry.current = null;
      if (!applyRunState(result)) return;
      setError(null);
      if (result.response) { detachSubscription(); publishResponse(result.response); }
      else if (result.run_status === "paused" && !result.cancel_requested) detachSubscription();
      else if (action === "resume" || !subscription.current.controller) void watchRun(result);
    } catch (caught) {
      if (targetSession !== selectedSession.current) return;
      if (caught instanceof HttpResponseError) commandRetry.current = null;
      setError(caught instanceof Error ? caught.message : "任务控制请求失败，请重试");
      try {
        const latest = await fetchAgentChatRun(run.run_id);
        if (targetSession !== selectedSession.current) return;
        if (!applyRunState(latest)) return;
        if (latest.response) { detachSubscription(); publishResponse(latest.response); }
      } catch { /* An uncertain command keeps its operation_id for the next explicit retry. */ }
    } finally {
      if (commandBusy.current === run.run_id) commandBusy.current = null;
      if (targetSession === selectedSession.current) setControlAction(null);
    }
  }

  /** Preserve failed requests for retry; publish the verified response exactly once. */
  async function sendMessage(prompt?: string) {
    const trimmed = (prompt ?? message).trim();
    const retry = failedRequest.current?.session_id === sessionId && failedRequest.current.message === trimmed
      ? failedRequest.current : null;
    const retriesOriginalRun = retry && !["pausing", "paused", "resuming"].includes(runRef.current?.run_status ?? "");
    if (!trimmed || sending || subscription.current.controller || (runRef.current && !retriesOriginalRun) || sessionLoading || !sessionId) {
      return;
    }
    const release = operationLock.current.acquire();
    if (!release) return;
    setRevealingMessageId(null);

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
    setCancelRequested(false);
    const agentMessageId = `agent-${userMessageId}`;
    setMessages(current => current.filter(item => item.id !== agentMessageId));
    release();
    await receiveRun((handler, options) => streamAgentChatMessage(payload, handler, options), sessionId, trimmed, payload);
  }

  const activeSession = sessions.find((item) => item.session_id === sessionId);
  const latestAgentMessage = [...messages].reverse().find((item) => item.role === "agent");
  const promptSuggestions = currentPromptSuggestions(latestAgentMessage);

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
              disabled={sessionLoading}
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
                      disabled={sessionLoading}
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

        <div className="agent-chat-viewport">
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
          {sessionId && messages.length === 0 && !activeRun && !sending && !sessionLoading && !sessionError && !error ? (
            <div className="chat-state" role="status" aria-label="新会话提示">
              <strong>开始一段研究对话</strong>
              <p>在下方输入问题，开始研究。</p>
            </div>
          ) : null}
          {messages.map((item) => (
            <article
              className={`chat-message chat-${item.role} ${item.status === "error" ? "chat-error" : ""} ${item.provisional ? "chat-provisional" : ""} ${item.role === "agent" && item.runId ? "chat-has-feedback" : ""}`}
              key={item.id}
            >
              {item.role === "agent" ? (
                <>
                  <div className="chat-markdown">
                    {item.provisional || item.taskLabel ? <small className="chat-task-status">
                      {item.provisional ? "生成中，待校验" : item.taskLabel}
                    </small> : null}
                    <AgentAnswerMarkdown
                      content={item.content}
                      stockMentions={item.stockMentions}
                      animate={revealingMessageId === item.id}
                      suppressTables={Boolean(item.displayBlocks?.length)}
                      duplicateListLabels={displayDuplicateListLabels(item.displayBlocks)}
                      onRevealProgress={followRevealedAnswer}
                    />
                    <AgentDisplayBlocks blocks={item.displayBlocks} />
                  </div>
                  {item.runId ? (
                    <AnswerFeedback runId={item.runId} initial={item.feedback} />
                  ) : null}
                </>
              ) : (
                <p>{item.content}</p>
              )}
            </article>
          ))}
          {sending ? (
            <AgentChatProgress stage={streamStage} message={streamStatus} startedAt={runStartedAt.current} />
          ) : null}
          {activeRun && !sending ? (
            <div className="chat-state chat-retry-state" role="status" aria-label="任务状态">
              <span>{activeRun.cancel_requested ? "正在结束任务，等待正式结果。"
                : activeRun.run_status === "paused" ? "任务已暂停，可继续原任务；结束任务后可发送新问题。"
                : activeRun.run_status === "interrupted" ? "任务已中断，已保存的进度仍然保留。"
                  : "当前任务尚未结束，可以重新连接查看进度。"}</span>
              {!activeRun.can_resume && (activeRun.run_status !== "paused" || activeRun.cancel_requested) ? (
                <button type="button" onClick={() => void watchRun(activeRun)} disabled={sessionLoading}>
                  <RefreshCcw aria-hidden="true" size={13} />重新连接
                </button>
              ) : null}
            </div>
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
          <button aria-label="回到最新消息" className="chat-scroll-latest" title="回到最新消息" type="button" onClick={() => {
            followAnswerRef.current = true;
            setShowLatest(false);
            const container = messagesContainerRef.current;
            if (container) container.scrollTop = container.scrollHeight;
          }}>
            <ArrowDown aria-hidden="true" size={18} />
          </button>
        ) : null}
        </div>

        {promptSuggestions.length > 0 ? <div className="agent-chat-prompts">
          {promptSuggestions.slice(0, 3).map((prompt) => (
            <button
              disabled={sending || Boolean(activeRun) || sessionLoading || !sessionId}
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
          {activeRun ? <div className="chat-run-controls">
            {activeRun.can_pause || activeRun.run_status === "pausing" ? (
              <button aria-label="暂停任务" title="暂停任务" className="icon-button" type="button"
                disabled={!activeRun.can_pause || controlAction !== null || cancelRequested}
                aria-busy={controlAction === "pause" || activeRun.run_status === "pausing"}
                onClick={() => void controlRun("pause")}>
                {controlAction === "pause" || activeRun.run_status === "pausing"
                  ? <LoaderCircle aria-hidden="true" className="chat-cancel-spinner" size={18} />
                  : <Pause aria-hidden="true" size={18} />}
              </button>
            ) : null}
            {activeRun.can_resume || activeRun.run_status === "resuming" ? (
              <button aria-label="继续任务" title="继续任务" className="icon-button" type="button"
                disabled={!activeRun.can_resume || controlAction !== null || cancelRequested}
                aria-busy={controlAction === "resume" || activeRun.run_status === "resuming"}
                onClick={() => void controlRun("resume")}>
                {controlAction === "resume" || activeRun.run_status === "resuming"
                  ? <LoaderCircle aria-hidden="true" className="chat-cancel-spinner" size={18} />
                  : <Play aria-hidden="true" size={18} />}
              </button>
            ) : null}
            <button
              aria-label={cancelRequested ? "正在结束任务" : "结束任务"}
              aria-busy={cancelRequested}
              className="icon-button chat-cancel"
              title={cancelRequested ? "正在结束任务" : "结束任务"}
              type="button"
              disabled={cancelRequested || !activeRun.can_cancel || controlAction !== null}
              onClick={() => void cancelCurrentRun()}
            >
              {cancelRequested
                ? <LoaderCircle aria-hidden="true" className="chat-cancel-spinner" size={18} />
                : <Square aria-hidden="true" fill="currentColor" size={18} strokeWidth={0} />}
            </button>
          </div> : (
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
