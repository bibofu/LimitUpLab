import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";

import { saveAnswerFeedback } from "../api";
import type { AnswerFeedbackValue, AnswerFeedbackVote } from "../types";
import {
  FEEDBACK_REASON_OPTIONS,
  buildFeedbackPayload,
  toggleFeedbackReason,
  type FeedbackReason,
} from "../utils/answerFeedback";

interface FeedbackDraft {
  reasons: FeedbackReason[];
  comment: string;
}

const EMPTY_DRAFT: FeedbackDraft = { reasons: [], comment: "" };

function draftFrom(value: AnswerFeedbackValue | null | undefined): FeedbackDraft {
  if (!value) return EMPTY_DRAFT;
  return {
    reasons: value.reasons.filter((item): item is FeedbackReason =>
      FEEDBACK_REASON_OPTIONS.some(option => option.value === item)),
    comment: value.comment,
  };
}

/** Compact like/dislike control; the backend stores one vote per run and owner. */
export function AnswerFeedback({
  runId,
  initial,
}: {
  runId: string;
  initial?: AnswerFeedbackValue | null;
}) {
  const [saved, setSaved] = useState<AnswerFeedbackValue | null>(initial ?? null);
  const [draft, setDraft] = useState<FeedbackDraft>(draftFrom(initial));
  const [expanded, setExpanded] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);

  async function persist(
    vote: AnswerFeedbackVote,
    reasons: readonly FeedbackReason[],
    comment: string,
    close: boolean,
  ) {
    if (pending) return;
    setPending(true);
    setError(false);
    try {
      const result = await saveAnswerFeedback(runId, buildFeedbackPayload(vote, reasons, comment));
      setSaved(result);
      if (result) setDraft(draftFrom(result));
      if (close) setExpanded(false);
    } catch {
      setError(true);
    } finally {
      setPending(false);
    }
  }

  function vote(value: AnswerFeedbackVote) {
    if (value === "up") {
      setExpanded(false);
      void persist("up", [], "", true);
      return;
    }
    setExpanded(true);
    if (saved?.vote !== "down") void persist("down", draft.reasons, draft.comment, false);
  }

  function reason(value: FeedbackReason) {
    setDraft(current => ({
      ...current,
      reasons: toggleFeedbackReason(current.reasons, value),
    }));
  }

  return (
    <div className="answer-feedback" aria-label="回答反馈">
      <div className="answer-feedback-actions">
        <button
          type="button"
          className="answer-feedback-vote"
          aria-label="点赞"
          aria-pressed={saved?.vote === "up"}
          disabled={pending}
          onClick={() => vote("up")}
        >
          <ThumbsUp size={20} aria-hidden="true" />
        </button>
        <button
          type="button"
          className="answer-feedback-vote"
          aria-label="点踩"
          aria-pressed={saved?.vote === "down"}
          disabled={pending}
          onClick={() => vote("down")}
        >
          <ThumbsDown size={20} aria-hidden="true" />
        </button>
      </div>
      {expanded ? (
        <fieldset disabled={pending}>
          <legend>哪里有问题？原因和说明均可跳过</legend>
          <div className="answer-feedback-reasons">
            {FEEDBACK_REASON_OPTIONS.map(option => (
              <label key={option.value}>
                <input
                  type="checkbox"
                  checked={draft.reasons.includes(option.value)}
                  onChange={() => reason(option.value)}
                />
                {option.label}
              </label>
            ))}
          </div>
          <textarea
            aria-label="反馈补充说明"
            maxLength={1000}
            rows={2}
            value={draft.comment}
            placeholder="可补充期望的回答或问题位置，请勿填写敏感信息"
            onChange={event => setDraft(current => ({ ...current, comment: event.target.value }))}
          />
          <div className="answer-feedback-actions">
            <button
              type="button"
              onClick={() => void persist("down", draft.reasons, draft.comment, true)}
            >
              提交
            </button>
            <button type="button" onClick={() => setExpanded(false)}>
              收起
            </button>
          </div>
        </fieldset>
      ) : null}
      {error ? <div role="alert">反馈保存失败，请重试。</div> : null}
    </div>
  );
}
