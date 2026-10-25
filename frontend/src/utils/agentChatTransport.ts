import type { AgentChatRequest, AgentChatStreamEvent, AgentChatStreamResult, AgentRunState } from "../types";

class ServerStreamError extends Error {}

export interface AgentStreamOptions { signal?: AbortSignal; after?: number }

/** One POST; a broken connection may reconnect once through the read-only GET. */
export async function streamChat(
  baseUrl: string,
  payload: AgentChatRequest,
  onEvent: (event: AgentChatStreamEvent) => void,
  fetcher: typeof fetch = fetch,
  options: AgentStreamOptions = {},
): Promise<AgentChatStreamResult> {
  return consumeStream(baseUrl, payload.session_id, null, onEvent, fetcher, options, {
    method: "POST", headers: { Accept: "text/event-stream", "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

/** Subscribe to an owned run without creating or resuming execution. */
export function streamExistingRun(baseUrl: string, run: AgentRunState,
  onEvent: (event: AgentChatStreamEvent) => void, fetcher: typeof fetch = fetch,
  options: AgentStreamOptions = {}): Promise<AgentChatStreamResult> {
  return consumeStream(baseUrl, run.session_id, run, onEvent, fetcher, options);
}

async function consumeStream(baseUrl: string, sessionId: string, initialRun: AgentRunState | null,
  onEvent: (event: AgentChatStreamEvent) => void, fetcher: typeof fetch,
  options: AgentStreamOptions, submission?: RequestInit): Promise<AgentChatStreamResult> {
  let runId = initialRun?.run_id ?? null;
  let cursor = options.after ?? 0;
  let stateRevision = initialRun?.state_revision ?? -1;
  const reconnectUrl = () => `${baseUrl}/api/agents/chat/runs/${encodeURIComponent(runId!)}/stream?after=${cursor}`;
  async function read(response: Response): Promise<AgentChatStreamResult> {
    if (!response.ok || !response.body) {
      let detail = "Agent 请求失败";
      try {
        const body = await response.json();
        if (typeof body.detail === "string") detail = body.detail;
      } catch { /* An HTML/proxy error must not leak into the answer. */ }
      throw new ServerStreamError(detail);
    }
    const headerId = response.headers.get("X-Agent-Run-Id");
    if (headerId) {
      runId = headerId;
      onEvent({ event: "accepted", data: { run_id: headerId, session_id: sessionId } });
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let completed: AgentChatStreamResult | null = null;
    function consume(record: string) {
      if (completed) return;
      let name = "message";
      let id: number | undefined;
      const lines: string[] = [];
      for (const line of record.split(/\r?\n/)) {
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) lines.push(line.slice(5).trimStart());
        else if (line.startsWith("id:")) id = Number(line.slice(3).trim());
      }
      if (!lines.length) return;
      if (id !== undefined && Number.isSafeInteger(id) && id <= cursor) return;
      const data = JSON.parse(lines.join("\n"));
      if (name === "error") throw new ServerStreamError(data.message || "任务已中断，可重试恢复");
      if (name === "accepted") runId = data.run_id;
      if (name === "completed") completed = data;
      if (name === "run_state") {
        if (data.run_id !== runId || data.session_id !== sessionId) throw new ServerStreamError("任务状态与当前会话不一致");
        if (data.state_revision < stateRevision) {
          if (id !== undefined && Number.isSafeInteger(id)) cursor = id;
          return;
        }
        stateRevision = data.state_revision;
        if (data.run_status === "paused") completed = { paused: true, run: data };
      }
      if (["accepted", "run_state", "progress", "status", "tool_start", "tool_end", "warning", "completed"].includes(name)) {
        onEvent({ event: name, data, id } as AgentChatStreamEvent);
      }
      // Old servers/replays may include candidate-answer events. Consume their IDs
      // without exposing drafts, so read-only reconnection still resumes correctly.
      if (id !== undefined && Number.isSafeInteger(id) && id >= cursor) cursor = id;
    }
    try {
      while (!completed) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        let boundary = /\r?\n\r?\n/.exec(buffer);
        while (boundary) {
          consume(buffer.slice(0, boundary.index));
          buffer = buffer.slice(boundary.index + boundary[0].length);
          boundary = /\r?\n\r?\n/.exec(buffer);
        }
        if (done) {
          if (buffer.trim()) consume(buffer);
          break;
        }
      }
      if (!completed) throw new Error("连接已中断，尚未收到最终结果");
      return completed;
    } finally {
      await reader.cancel().catch(() => undefined);
      reader.releaseLock();
    }
  }
  try {
    return await read(await fetcher(submission ? `${baseUrl}/api/agents/chat/stream` : reconnectUrl(), {
      credentials: "include", headers: { Accept: "text/event-stream" }, ...submission,
      signal: options.signal,
    }));
  } catch (error) {
    if (options.signal?.aborted || !runId || error instanceof ServerStreamError) throw error;
    onEvent({ event: "progress", data: { stage: "tools", message: "连接已断开，正在读取原任务进度" } });
    return read(await fetcher(reconnectUrl(), {
      credentials: "include", headers: { Accept: "text/event-stream" }, signal: options.signal,
    }));
  }
}

const LABELS: Record<string, string> = {
  complete: "研究已完成", partial: "查询已结束", empty: "查询结果为空", clarify: "待补充条件",
  refuse: "请求超出研究范围", error: "执行失败", cancelled: "已取消",
};

export function taskStatusLabel(value: unknown): string | undefined {
  return typeof value === "string" && Object.prototype.hasOwnProperty.call(LABELS, value) ? LABELS[value] : undefined;
}
