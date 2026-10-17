import { useEffect, useRef, useState } from "react";
import type { AdminQualityClient, EvaluationHistory, JudgeConfig, QualityRunJobs } from "../utils/adminQuality";
import { judgeJobState, qualityJobAvailableAt, readJudgeSnapshot, watchJudge, type JudgeSnapshot, type JudgeWatchResult } from "../utils/qualityJudge";
import { QualityEvaluationReports } from "./QualityEvaluationReports";

type PanelState = {
  runId: string; config: JudgeConfig | null; evaluations: EvaluationHistory | null;
  jobs: QualityRunJobs | null; busy: boolean; error: string; message: string;
};
const empty = (runId: string): PanelState => ({ runId, config: null, evaluations: null, jobs: null, busy: true, error: "", message: "" });
const outcome: Record<JudgeWatchResult, string> = {
  done: "语义评审已完成，请结合报告进行人工复核。",
  failed: "评审任务未成功完成，请查看任务详情；这不代表回答错误。",
  deferred: "任务已延期，稍后可刷新状态查看结果。",
  waiting: "任务仍在等待或执行，自动刷新已暂停，可稍后刷新状态。",
  idle: "暂未找到任务，请刷新状态核对。",
};

export function QualityJudgePanel({ api, runId, onCompleted }: {
  api: AdminQualityClient; runId: string; onCompleted?: () => void;
}) {
  const [state, setState] = useState<PanelState>(empty(runId));
  const controller = useRef<AbortController | null>(null);
  const sequence = useRef(0);
  const submitting = useRef(false);
  const latest = useRef({ api, onCompleted });
  latest.current = { api, onCompleted };
  const shown = state.runId === runId ? state : empty(runId);

  async function observe(start: boolean, reset = false) {
    if (start && submitting.current) return;
    submitting.current = true;
    controller.current?.abort();
    const current = new AbortController(), version = ++sequence.current;
    controller.current = current;
    const update = (change: Partial<PanelState>) => {
      if (!current.signal.aborted && version === sequence.current) setState(previous => ({ ...previous, ...change, runId }));
    };
    if (reset) setState(empty(runId));
    update({ busy: true, error: "", message: "" });
    const snapshot = (value: JudgeSnapshot) => update(value);
    try {
      const config = await latest.current.api.judgeConfig(current.signal);
      if (current.signal.aborted) return;
      update({ config });
      let jobId: string | undefined;
      if (start) {
        if (!config.enabled) { update({ message: config.reason || "语义评审尚未启用。" }); return; }
        const result = await latest.current.api.judge(runId, current.signal);
        if (current.signal.aborted) return;
        jobId = result.job_id;
        update({ message: result.created ? "已提交评审任务，正在等待结果…" : "已有相同评审任务，正在读取结果…" });
      } else {
        const result = await readJudgeSnapshot(latest.current.api, runId, current.signal);
        if (current.signal.aborted) return;
        snapshot(result);
        jobId = result.jobs.items.find(job => job.job_kind === "llm_judge" && ["active", "deferred"].includes(judgeJobState(job)))?.id;
        if (!reset && !jobId && result.jobs.items.some(job => job.job_kind === "llm_judge")) latest.current.onCompleted?.();
      }
      if (jobId) {
        const result = await watchJudge(latest.current.api, runId, jobId, current.signal, snapshot);
        if (current.signal.aborted) return;
        update({ message: outcome[result] });
        if (result === "done" || result === "failed") latest.current.onCompleted?.();
      }
    } catch (error) {
      update({ error: error instanceof Error ? error.message : "请求未完成，请重试。" });
    } finally {
      if (version === sequence.current) { submitting.current = false; update({ busy: false }); }
    }
  }

  useEffect(() => {
    void observe(false, true);
    return () => { controller.current?.abort(); sequence.current++; submitting.current = false; };
  }, [runId]);
  const judgeJobs = shown.jobs?.items.filter(job => job.job_kind === "llm_judge") ?? [];
  return <section className="quality-judge-panel" aria-label="语义评审">
    <h3>LLM Judge 语义评审</h3>
    <p>仅由管理员手动发起。评审使用固定证据包，结果不会替代人工审核结论。</p>
    {shown.config ? <p>{shown.config.enabled ? `模型：${shown.config.model} · 每日调用上限 ${shown.config.daily_call_limit}` : shown.config.reason || "语义评审未启用，请检查服务端配置。"}</p> : null}
    <div className="quality-judge-actions">
      <button type="button" disabled={shown.busy || !shown.config?.enabled} onClick={() => void observe(true)}>发起语义评审</button>
      <button type="button" disabled={shown.busy} onClick={() => void observe(false)}>刷新评审状态</button>
    </div>
    <p role="status">{shown.message || (shown.busy ? "正在读取评审状态…" : "")}</p>
    {shown.error ? <p role="alert">{shown.error} 可刷新状态或重新发起；已有报告和审核草稿会保留。</p> : null}
    {judgeJobs.length ? <details><summary>语义评审任务（{judgeJobs.length}）</summary>
      {judgeJobs.map(job => <p key={job.id}>{({ pending: "等待执行", running: "执行中", done: "完成", dead: "失败待查", superseded: "已替代" })[job.status]} · 尝试 {job.attempts} 次 · {job.id}{job.last_error ? ` · ${job.last_error}` : ""}{job.status === "pending" ? ` · 最早执行时间 ${new Date(qualityJobAvailableAt(job.available_at)).toLocaleString("zh-CN")}` : ""}</p>)}
    </details> : null}
    <QualityEvaluationReports evaluations={shown.evaluations} />
  </section>;
}

export function ManualRunJudge({ api, onCompleted }: { api: AdminQualityClient; onCompleted: () => void }) {
  const [draft, setDraft] = useState("");
  const [runId, setRunId] = useState("");
  return <section className="quality-manual-run" aria-label="按运行 ID 评审">
    <form onSubmit={event => { event.preventDefault(); if (draft.trim()) setRunId(draft.trim()); }}>
      <label>运行 ID<input value={draft} maxLength={100} placeholder="输入回答的 run_id，无需已有问题案例" onChange={event => setDraft(event.target.value)} /></label>
      <button type="submit" disabled={!draft.trim()}>查看运行评审</button>
    </form>
    {runId ? <><p className="quality-identifiers">当前运行：{runId}</p><QualityJudgePanel key={runId} api={api} runId={runId} onCompleted={onCompleted} /></> : null}
  </section>;
}
