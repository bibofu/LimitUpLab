export interface ReviewOverviewResult {
  text: string;
  model: string;
  as_of_date: string;
}

export type ReviewOverviewEvent =
  | { event: "delta"; data: { text: string } }
  | { event: "progress" | "checking" | "reset"; data: { message: string } };

/** Decode real SSE chunks, including split UTF-8 and CRLF; never retry a paid request. */
export async function streamReviewOverview(
  baseUrl: string, endDate: string, onEvent: (event: ReviewOverviewEvent) => void,
  signal: AbortSignal, fetcher: typeof fetch = fetch,
): Promise<ReviewOverviewResult> {
  const response = await fetcher(`${baseUrl}/api/agents/review-overview/stream`, {
    method: "POST", credentials: "include", signal: AbortSignal.any([signal, AbortSignal.timeout(150_000)]),
    headers: { Accept: "text/event-stream", "Content-Type": "application/json" },
    body: JSON.stringify({ end_date: endDate }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : "总结请求失败，请重试。" );
  }
  if (!response.body) throw new Error("总结连接未建立，请重试。");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let lines: string[] = [];
  let completed: ReviewOverviewResult | null = null;
  let draftSize = 0;
  function consume() {
    const event = lines.find(line => line.startsWith("event:"))?.slice(6).trim();
    const dataLines = lines.filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart());
    lines = [];
    if (!event || !dataLines.length) return;
    const data = JSON.parse(dataLines.join("\n"));
    if (event === "error") throw new Error(typeof data.message === "string" ? data.message : "本次总结未完成，请重试。");
    if (event === "completed") {
      if (typeof data.text !== "string" || !data.text.trim() || typeof data.model !== "string"
          || data.as_of_date !== endDate) throw new Error("总结日期或内容未确认，请重试。");
      completed = data;
    } else if (event === "delta" && typeof data.text === "string") {
      draftSize += data.text.length;
      if (draftSize > 1800) throw new Error("总结内容异常，请重试。");
      onEvent({ event, data });
    } else if ((event === "progress" || event === "checking" || event === "reset") && typeof data.message === "string") {
      if (event === "reset") draftSize = 0;
      onEvent({ event, data });
    }
  }
  try {
    while (!completed) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let newline = buffer.indexOf("\n");
      while (newline >= 0) {
        const raw = buffer.slice(0, newline);
        const line = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
        buffer = buffer.slice(newline + 1);
        if (line === "") consume();
        else lines.push(line);
        if (completed) break;
        newline = buffer.indexOf("\n");
      }
      if (done) {
        if (buffer.trim()) lines.push(buffer);
        if (lines.length) consume();
        break;
      }
    }
    if (!completed) throw new Error("连接已中断，总结尚未完成，请重新生成。");
    return completed;
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
