import type { AnswerFeedbackVote } from "../types";

export const FEEDBACK_REASON_OPTIONS = [
  { value: "data_error", label: "数据错误" },
  { value: "wrong_scope", label: "日期或对象不对" },
  { value: "missing_source", label: "缺少来源" },
  { value: "incomplete", label: "回答不完整" },
  { value: "display", label: "展示有问题" },
  { value: "other", label: "其他" },
] as const;

export type FeedbackReason = (typeof FEEDBACK_REASON_OPTIONS)[number]["value"];

export interface AnswerFeedbackPayload {
  vote: AnswerFeedbackVote;
  reasons: FeedbackReason[];
  comment: string;
}

const MAX_REASONS = 5;
const MAX_COMMENT_LENGTH = 1000;
const CONTROL_CHARACTERS = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g;

/** Toggle one reason without exceeding the backend limit. */
export function toggleFeedbackReason(
  current: readonly FeedbackReason[],
  reason: FeedbackReason,
  limit = MAX_REASONS,
): FeedbackReason[] {
  if (current.includes(reason)) return current.filter(item => item !== reason);
  if (current.length >= limit) return [...current];
  return [...current, reason];
}

/** Bound and clean free text before it leaves the browser. */
export function normalizeFeedbackComment(value: string): string {
  return (value ?? "").replace(CONTROL_CHARACTERS, "").trim().slice(0, MAX_COMMENT_LENGTH);
}

/** A positive vote never carries stale downvote reasons or comments. */
export function buildFeedbackPayload(
  vote: AnswerFeedbackVote,
  reasons: readonly FeedbackReason[],
  comment: string,
): AnswerFeedbackPayload {
  if (vote === "up") return { vote, reasons: [], comment: "" };
  return {
    vote,
    reasons: [...new Set(reasons)],
    comment: normalizeFeedbackComment(comment),
  };
}
