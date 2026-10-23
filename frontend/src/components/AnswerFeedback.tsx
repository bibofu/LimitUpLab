import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useEffect, useState } from "react";

import {
  fetchAnswerFeedback,
  saveAnswerFeedback,
  type AnswerFeedbackValue,
} from "../api";

const REASONS = {
  data_error: "数据错误",
  wrong_scope: "日期或对象不对",
  missing_source: "缺少来源",
  incomplete: "回答不完整",
  other: "其他",
} as const;

type ReasonKey = keyof typeof REASONS;

const emptyDown: AnswerFeedbackValue = { vote: "down", reasons: [], comment: "" };

/** One answer's local like/dislike control; the backend stores it per run and owner. */
export function AnswerFeedback({ runId }: { runId: string }) {
  const [saved, setSaved] = useState<AnswerFeedbackValue | null>(null);
  const [draft, setDraft] = useState<AnswerFeedbackValue>(emptyDown);
  const [expanded, setExpanded] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    let active = true;
    setError(false);
    fetchAnswerFeedback(runId)
      .then(value => {
        if (!active) return;
        setSaved(value);
        if (value) setDraft(value);
      })
      .catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, [runId]);

  async function save(value: AnswerFeedbackValue, close = false) {
    if (pending) return;
    setPending(true);
    setError(false);
    try {
      const result = await saveAnswerFeedback(runId, value);
      setSaved(result);
      setDraft(result);
      if (close) setExpanded(false);
    } catch {
      setError(true);
    } finally {
      setPending(false);
    }
  }

  function vote(value: "up" | "down") {
    if (value === "up") {
      setExpanded(false);
      void save({ vote: "up", reasons: [], comment: "" });
      return;
    }
    setExpanded(true);
    void save({ ...draft, vote: "down" });
  }

  function reason(value: ReasonKey) {
    setDraft(current => ({
      ...current,
      reasons: current.reasons.includes(value)
        ? current.reasons.filter(item => item !== value)
        : [...current.reasons, value],
    }));
  }

  return (
    <div className="answer-feedback" aria-label="回答反馈">
      <div className="answer-feedback-actions">
        <button type="button" aria-label="点赞" aria-pressed={saved?.vote === "up"}
          disabled={pending} onClick={() => vote("up")}>
          <ThumbsUp size={13} aria-hidden="true" />
        </button>
        <button type="button" aria-label="点踩" aria-pressed={saved?.vote === "down"}
          disabled={pending} onClick={() => vote("down")}>
          <ThumbsDown size={13} aria-hidden="true" />
        </button>
      </div>
      {expanded ? (
        <fieldset disabled={pending}>
          <legend>哪里有问题？原因和说明均可跳过</legend>
          <div className="answer-feedback-reasons">
            {(Object.keys(REASONS) as ReasonKey[]).map(key => (
              <label key={key}>
                <input type="checkbox" checked={draft.reasons.includes(key)}
                  onChange={() => reason(key)} />
                {REASONS[key]}
              </label>
            ))}
          </div>
          <textarea aria-label="反馈补充说明" maxLength={1000} rows={2}
            value={draft.comment} placeholder="可补充期望的回答或问题位置，请勿填写敏感信息"
            onChange={event => setDraft(current => ({ ...current, comment: event.target.value }))} />
          <div className="answer-feedback-actions">
            <button type="button" onClick={() => void save({ ...draft, vote: "down" }, true)}>提交</button>
            <button type="button" onClick={() => setExpanded(false)}>收起</button>
          </div>
        </fieldset>
      ) : null}
      {error ? <div role="alert">反馈保存失败，请重试。</div> : null}
    </div>
  );
}
