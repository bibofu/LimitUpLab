export type FeedbackVote = "up" | "down" | null;
export const feedbackReasons = {
  data_error: "数据不对", intent_error: "理解错误", incomplete: "漏答",
  unsupported: "依据不足", expression: "表达问题", slow: "响应太慢",
} as const;
export type FeedbackReason = keyof typeof feedbackReasons;
export interface AnswerFeedbackValue {
  vote: FeedbackVote;
  reasons: FeedbackReason[];
  comment: string;
}
export interface RunQuality {
  feedback: AnswerFeedbackValue | null;
  answer_hash: string | null;
  delivery_status: string;
}
export type SessionQuality = { runs: Record<string, RunQuality> };
export const emptyFeedback = (): AnswerFeedbackValue => ({ vote: null, reasons: [], comment: "" });

/** Switching or withdrawing a vote removes reasons that no longer apply. */
export function toggledFeedback(current: AnswerFeedbackValue, vote: Exclude<FeedbackVote, null>): AnswerFeedbackValue {
  const next = current.vote === vote ? null : vote;
  return { vote: next, reasons: next === "down" ? current.reasons : [], comment: next === "down" ? current.comment : "" };
}

/** Lock immediately, including two clicks before React can disable the controls. */
export function createFeedbackWriter(save: (value: AnswerFeedbackValue) => Promise<AnswerFeedbackValue>) {
  let pending = false;
  return async (value: AnswerFeedbackValue): Promise<AnswerFeedbackValue | undefined> => {
    if (pending) return undefined;
    pending = true;
    try { return await save(value); } finally { pending = false; }
  };
}

export async function answerHash(answer: string): Promise<string | null> {
  if (!globalThis.crypto?.subtle) return null;
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(answer));
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
}

export type QualityRequest = <T>(path: string, init?: RequestInit) => Promise<T>;
export function createQualityClient(request: QualityRequest) {
  const runPath = (id: string) => `/api/agents/chat/runs/${encodeURIComponent(id)}`;
  return {
    session: (id: string, signal?: AbortSignal) => request<SessionQuality>(
      `/api/agents/chat/sessions/${encodeURIComponent(id)}/quality`, { signal }),
    feedback: (id: string, value: AnswerFeedbackValue) => request<{ feedback: AnswerFeedbackValue }>(
      `${runPath(id)}/feedback`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) }),
    delivery: (id: string, event: "received" | "displayed" | "interrupted", hash: string) => request<unknown>(
      `${runPath(id)}/delivery`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ event, answer_hash: hash }) }),
  };
}
