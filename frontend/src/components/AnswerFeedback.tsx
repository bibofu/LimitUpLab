import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { qualityApi } from "../qualityApi";
import {
  createFeedbackWriter, emptyFeedback, feedbackReasons, toggledFeedback,
  type AnswerFeedbackValue, type FeedbackReason, type FeedbackVote,
} from "../utils/agentQuality";

/** Local drafts survive errors; only an acknowledged save changes the recorded vote. */
export function AnswerFeedback({ runId, initial, ready }: {
  runId: string; initial: AnswerFeedbackValue | null | undefined; ready: boolean;
}) {
  const [saved, setSaved] = useState<AnswerFeedbackValue>(emptyFeedback());
  const [draft, setDraft] = useState<AnswerFeedbackValue>(emptyFeedback());
  const [expanded, setExpanded] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState<{ value: AnswerFeedbackValue; closeOnSuccess: boolean } | null>(null);
  const touched = useRef(false);
  const submitting = useRef(false);
  const mounted = useRef(true);
  const writer = useRef(createFeedbackWriter(async value => (await qualityApi.feedback(runId, value)).feedback));
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (ready && !touched.current) {
      setSaved(initial ?? emptyFeedback());
      setDraft(initial ?? emptyFeedback());
    }
  }, [initial, ready]);

  async function save(value: AnswerFeedbackValue, closeOnSuccess = false) {
    if (submitting.current) return;
    submitting.current = true;
    touched.current = true;
    setPending(true);
    setError(false);
    try {
      const result = await writer.current(value);
      if (result && mounted.current) {
        setSaved(result); setDraft(result); setRetry(null);
        if (closeOnSuccess) setExpanded(false);
      }
    } catch {
      if (mounted.current) { setError(true); setRetry({ value, closeOnSuccess }); }
    } finally { submitting.current = false; if (mounted.current) setPending(false); }
  }
  function vote(value: Exclude<FeedbackVote, null>) {
    const next = toggledFeedback(saved, value);
    setExpanded(next.vote === "down");
    void save(next);
  }
  function reason(value: FeedbackReason) {
    setDraft(current => ({ ...current, reasons: current.reasons.includes(value)
      ? current.reasons.filter(item => item !== value) : [...current.reasons, value] }));
  }
  return <div className="answer-feedback" aria-label="回答反馈">
    <div className="answer-feedback-actions">
      <button type="button" aria-label="点赞" disabled={!ready || pending} aria-pressed={saved.vote === "up"} onClick={() => vote("up")}>
        <ThumbsUp size={13} aria-hidden="true" />
      </button>
      <button type="button" aria-label="点踩" disabled={!ready || pending} aria-pressed={saved.vote === "down"} onClick={() => vote("down")}>
        <ThumbsDown size={13} aria-hidden="true" />
      </button>
    </div>
    {expanded ? <fieldset disabled={pending || !ready}>
      <legend>哪里有问题？原因和说明均可跳过</legend>
      <div className="answer-feedback-reasons">{Object.entries(feedbackReasons).map(([key, label]) => <label key={key}>
        <input type="checkbox" checked={draft.reasons.includes(key as FeedbackReason)} onChange={() => reason(key as FeedbackReason)} />{label}
      </label>)}</div>
      <textarea aria-label="反馈补充说明" maxLength={1000} rows={2} value={draft.comment}
        onChange={event => setDraft(current => ({ ...current, comment: event.target.value }))} placeholder="可补充期望的回答或问题位置，请勿填写敏感信息" />
      <div className="answer-feedback-actions">
        <button type="button" onClick={() => void save({ ...draft, vote: "down" }, true)}>提交</button>
        <button type="button" onClick={() => setExpanded(false)}>收起</button>
      </div>
    </fieldset> : null}
    {error ? <div role="alert">反馈保存失败，内容已保留。<button disabled={pending} type="button" onClick={() => retry && void save(retry.value, retry.closeOnSuccess)}>重试</button></div> : null}
  </div>;
}
