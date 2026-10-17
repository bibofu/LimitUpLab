import { useEffect, useRef, useState } from "react";
import { qualityRequest } from "../qualityApi";
import { HttpResponseError } from "../utils/jsonRequest";
import {
  caseStatuses, createAdminQualityClient, emptyCaseFilters, reviewDraft, reviewTransitions, severities, validateReview,
  type CaseDetail, type CaseFilters, type CaseReview, type CaseStatus, type JobHealth, type QualityCase, type Severity,
} from "../utils/adminQuality";
import "../qualityAdmin.css";

function failure(error: unknown) {
  if (error instanceof HttpResponseError && error.status === 409) return "记录已更新或状态转换冲突，请重新加载案例后审核。";
  if (error instanceof HttpResponseError && [401, 403].includes(error.status)) return "管理密钥无效或无权访问，请退出后重新验证。";
  return error instanceof Error && error.name !== "AbortError" ? error.message : "请求未完成，请重试。";
}
const textValue = (value: unknown) => typeof value === "string" ? value : "未记录";
const sourceLabel = (value: string) => ({ feedback: "用户反馈", deterministic: "规则检测", delivery: "交付回执", normal_sample: "正常样本抽查" }[value] ?? value);
const deliveryLabel = (value: string) => ({ unknown: "未知", received: "客户端已接收", displayed: "客户端已展示", interrupted: "客户端报告中断" }[value] ?? value);
const timeLabel = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false });

/** The administration credential is never persisted or placed in URLs. */
export function QualityReviewPage() {
  const [key, setKey] = useState("");
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const submitting = useRef(false);
  if (key) return <QualityWorkspace adminKey={key} onLogout={() => { setKey(""); setDraft(""); }} />;
  return <section className="quality-page quality-login">
    <h1>回答质量审核</h1><p>供维护者复核用户反馈和规则检测线索。管理密钥仅在当前页面内存中使用。</p>
    <form onSubmit={async event => {
      event.preventDefault(); if (submitting.current || !draft.trim()) return;
      submitting.current = true; setPending(true); setError("");
      try { await createAdminQualityClient(qualityRequest, draft.trim()).list(); setKey(draft.trim()); setDraft(""); }
      catch (caught) { setError(failure(caught)); }
      finally { submitting.current = false; setPending(false); }
    }}>
      <label>管理密钥<input type="password" autoComplete="off" required value={draft} onChange={event => setDraft(event.target.value)} /></label>
      <button type="submit" disabled={pending || !draft.trim()}>{pending ? "验证中…" : "进入审核"}</button>
      {error ? <p role="alert">{error}</p> : null}
    </form>
  </section>;
}

function QualityWorkspace({ adminKey, onLogout }: { adminKey: string; onLogout: () => void }) {
  const [filters, setFilters] = useState<CaseFilters>(emptyCaseFilters());
  const [query, setQuery] = useState<CaseFilters>(emptyCaseFilters());
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [list, setList] = useState<{ items: QualityCase[]; total: number } | null>(null);
  const [listError, setListError] = useState("");
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [detailError, setDetailError] = useState("");
  const [detailReload, setDetailReload] = useState(0);
  const [jobs, setJobs] = useState<JobHealth | null>(null);
  const [jobError, setJobError] = useState("");
  const [processing, setProcessing] = useState(false);
  const [processResult, setProcessResult] = useState("");
  const processor = useRef(false);
  const active = useRef<string | null>(null);
  active.current = selected;
  const api = createAdminQualityClient(qualityRequest, adminKey);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setListError("");
    api.list(query, offset, controller.signal).then(result => {
      if (!controller.signal.aborted) setList(result);
    }).catch(error => { if (!controller.signal.aborted) setListError(failure(error)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [adminKey, query, offset, refresh]);
  useEffect(() => {
    const controller = new AbortController(); setJobError("");
    api.jobs(controller.signal).then(result => { if (!controller.signal.aborted) setJobs(result); })
      .catch(error => { if (!controller.signal.aborted) setJobError(failure(error)); });
    return () => controller.abort();
  }, [adminKey, refresh]);
  useEffect(() => {
    const controller = new AbortController(); setDetail(null); setDetailError("");
    if (selected) api.detail(selected, controller.signal).then(result => {
      if (!controller.signal.aborted) setDetail(result);
    }).catch(error => { if (!controller.signal.aborted) setDetailError(failure(error)); });
    return () => controller.abort();
  }, [adminKey, selected, detailReload]);
  async function processJobs() {
    if (processor.current) return;
    processor.current = true; setProcessing(true); setJobError("");
    try { const result = await api.process(); setProcessResult(`本批处理 ${result.processed} 条，成功 ${result.succeeded} 条，失败 ${result.failed} 条。`); setRefresh(value => value + 1); }
    catch (error) { setJobError(failure(error)); }
    finally { processor.current = false; setProcessing(false); }
  }
  const filter = (name: keyof CaseFilters, value: string) => setFilters(current => ({ ...current, [name]: value }));
  return <section className="quality-page">
    <header className="quality-heading"><div><h1>回答质量审核</h1><p>检测信号与用户反馈均为待复核线索，审核结论独立保存。</p></div><button type="button" onClick={onLogout}>退出审核</button></header>
    <section className="quality-job-health" aria-label="检测任务状态">
      <span>{jobs ? `待处理 ${jobs.counts.pending ?? 0} · 执行中 ${jobs.counts.running ?? 0} · 已完成 ${jobs.counts.done ?? 0} · 失败待查 ${jobs.counts.dead ?? 0}` : "正在读取检测任务…"}</span>
      <button type="button" disabled={processing} onClick={() => void processJobs()}>{processing ? "处理任务中…" : "处理一批检测任务"}</button>
      <span role="status">{processResult}</span>
      {jobError ? <p role="alert">{jobError}<button type="button" onClick={() => setRefresh(value => value + 1)}>重试</button></p> : null}
      {jobs?.failed.length ? <details><summary>失败任务详情（{jobs.failed.length}）</summary><pre>{JSON.stringify(jobs.failed, null, 2)}</pre></details> : null}
    </section>
    <form className="quality-filters" onSubmit={event => { event.preventDefault(); setOffset(0); setQuery({ ...filters }); }}>
      <label>状态<select value={filters.status} onChange={event => filter("status", event.target.value)}><option value="">全部</option>{Object.entries(caseStatuses).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>来源<select value={filters.source} onChange={event => filter("source", event.target.value)}><option value="">全部</option>{["feedback", "deterministic", "delivery", "normal_sample"].map(value => <option key={value} value={value}>{sourceLabel(value)}</option>)}</select></label>
      <label>严重程度<select value={filters.severity} onChange={event => filter("severity", event.target.value)}><option value="">全部</option>{Object.entries(severities).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>症状<input value={filters.symptom} onChange={event => filter("symptom", event.target.value)} placeholder="精确匹配" /></label>
      <label>运行版本<input value={filters.version} onChange={event => filter("version", event.target.value)} placeholder="精确匹配" /></label>
      <button type="submit">筛选</button><button type="button" onClick={() => setRefresh(value => value + 1)}>刷新</button>
    </form>
    <div className="quality-columns">
      <section className="quality-case-list" aria-label="疑似问题列表" aria-busy={loading}>
        <p role="status">{loading ? "加载中…" : `共 ${list?.total ?? 0} 条线索`}</p>
        {listError ? <p role="alert">{listError}<button type="button" onClick={() => setRefresh(value => value + 1)}>重试</button></p> : null}
        {!loading && !listError && list?.total === 0 ? <p>当前筛选下没有待处理线索。</p> : null}
        {list?.items.map(item => <button className="quality-case-row" key={item.case_id} type="button" aria-pressed={selected === item.case_id} onClick={() => setSelected(item.case_id)}>
          <strong>{item.question || "问题未记录"}</strong>
          <span>{caseStatuses[item.status]} · {severities[item.severity]} · {(item.sources ?? []).map(sourceLabel).join(" / ")} · {item.signal_count} 个信号</span>
          <span>{item.symptom || "症状待确认"} · {timeLabel(item.updated_at)}</span>
          <small>{item.case_id}</small>
        </button>)}
        <div className="quality-pagination"><button type="button" disabled={loading || offset === 0} onClick={() => setOffset(value => Math.max(0, value - 20))}>上一页</button><span>第 {Math.floor(offset / 20) + 1} 页</span><button type="button" disabled={loading || offset + 20 >= (list?.total ?? 0)} onClick={() => setOffset(value => value + 20)}>下一页</button></div>
      </section>
      <section className="quality-case-detail" aria-label="案例证据与审核">
        {!selected ? <p>选择一条线索，核对原始回答、执行证据和审核历史。</p> : detailError ? <p role="alert">{detailError}<button type="button" onClick={() => setDetailReload(value => value + 1)}>重新加载案例</button></p> : detail?.case.case_id === selected ? <>
          <CaseEvidence detail={detail} />
          <QualityCaseReviewForm key={`${selected}:${detail.case.revision}`} item={detail.case} save={draft => api.review(selected, draft)} reload={() => setDetailReload(value => value + 1)} onSaved={result => {
            if (active.current === result.case.case_id) setDetail(result);
            setRefresh(value => value + 1);
          }} />
        </> : <p role="status">正在读取案例证据…</p>}
      </section>
    </div>
  </section>;
}

function JsonEvidence({ label, value }: { label: string; value: unknown }) {
  return <details><summary>{label}</summary><pre>{value == null ? "未记录" : JSON.stringify(value, null, 2)}</pre></details>;
}

function CaseEvidence({ detail }: { detail: CaseDetail }) {
  const { case: item, run } = detail;
  return <>
    <h2>{caseStatuses[item.status]} · {severities[item.severity]}</h2>
    <p className="quality-identifiers">{item.case_id}<br />运行 {item.run_id} · 修订 {item.revision}<br />版本 {item.version || "未记录"}</p>
    <p>执行状态：{run.execution_status ?? "未知"} · 任务结果：{textValue(run.response?.task_status)} · 交付：{deliveryLabel(run.delivery_status)}</p>
    <small>交付回执仅表示客户端状态，不代表用户已阅读。缺少回执保持未知。</small>
    <h3>用户问题</h3><p className="quality-prose">{textValue(run.request.message)}</p>
    <h3>服务端最终回答</h3><pre className="quality-answer">{textValue(run.response?.answer)}</pre>
    <JsonEvidence label="必要会话上下文" value={run.context} />
    <JsonEvidence label="当前用户反馈" value={run.feedback} />
    <h3>检测依据（{detail.signals.length}）</h3>
    {detail.signals.map(signal => <article className="quality-signal" key={signal.signal_id}>
      <strong>{signal.summary}</strong><small>{sourceLabel(signal.source)} · {signal.code} · {signal.detector_version} · {severities[signal.severity]}</small>
      <JsonEvidence label="查看定位证据" value={signal.evidence} />
    </article>)}
    <h3>执行与模型证据</h3>
    <JsonEvidence label="请求与解析条件" value={run.request} />
    <JsonEvidence label="工具调用结果" value={run.calls} />
    <JsonEvidence label="最终响应元数据" value={run.response} />
    <JsonEvidence label="执行检查点" value={run.checkpoint} />
    <JsonEvidence label="交付事件" value={run.delivery_events} />
    {detail.trace_snapshots.length ? detail.trace_snapshots.map(trace => <JsonEvidence key={trace.id} label={`${trace.stage} · ${timeLabel(trace.captured_at)}`} value={trace.payload} />) : <p>此运行没有留存证据快照，模型可见内容及版本缺口需要明确记录。</p>}
    <h3>审核历史（{detail.reviews.length}）</h3>
    {detail.reviews.length ? detail.reviews.map(review => <article className="quality-signal" key={review.revision}>
      <strong>{caseStatuses[review.from_status]} → {caseStatuses[review.to_status]} · 修订 {review.revision}</strong>
      <small>{review.actor_id} · {timeLabel(review.created_at)}</small><p className="quality-prose">{review.note}</p>
      <JsonEvidence label="字段变更" value={review.changes} />
    </article>) : <p>尚未人工审核。</p>}
  </>;
}

export function QualityCaseReviewForm({ item, save, onSaved, reload }: { item: QualityCase; save: (draft: CaseReview) => Promise<CaseDetail>; onSaved: (result: CaseDetail) => void; reload: () => void }) {
  const [draft, setDraft] = useState<CaseReview>(reviewDraft(item));
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const submitting = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const change = (name: keyof CaseReview, value: string) => setDraft(current => ({ ...current, [name]: value }));
  return <form className="quality-review-form" onSubmit={async event => {
    event.preventDefault(); if (submitting.current) return;
    const invalid = validateReview(draft); if (invalid) { setError(invalid); return; }
    submitting.current = true; setPending(true); setError("");
    try { const result = await save(draft); if (mounted.current) onSaved(result); }
    catch (caught) { if (mounted.current) setError(failure(caught)); }
    finally { submitting.current = false; if (mounted.current) setPending(false); }
  }}>
    <h3>人工复核与处理</h3><p>基于修订 {item.revision} 保存；本次审核说明将追加到历史。</p>
    <fieldset disabled={pending}>
      <div className="quality-review-grid">
        <label>处理状态<select value={draft.status} onChange={event => change("status", event.target.value as CaseStatus)}>{reviewTransitions(item.status).map(status => <option key={status} value={status}>{caseStatuses[status]}</option>)}</select></label>
        <label>严重程度<select value={draft.severity} onChange={event => change("severity", event.target.value as Severity)}>{Object.entries(severities).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>负责人<input maxLength={100} value={draft.assignee} onChange={event => change("assignee", event.target.value)} /></label>
        <label>修复版本<input maxLength={200} value={draft.fix_version} onChange={event => change("fix_version", event.target.value)} placeholder="提交或发布版本" /></label>
      </div>
      <label>症状<input maxLength={200} value={draft.symptom} onChange={event => change("symptom", event.target.value)} placeholder="例如：回答遗漏第二个对象" /></label>
      <label>根因<textarea maxLength={2000} rows={2} value={draft.root_cause} onChange={event => change("root_cause", event.target.value)} /></label>
      {draft.status === "duplicate" ? <label>关联案例 ID<input maxLength={80} value={draft.duplicate_of ?? ""} onChange={event => change("duplicate_of", event.target.value)} /></label> : null}
      <label>本次审核说明（必填）<textarea required maxLength={2000} rows={3} value={draft.note} onChange={event => change("note", event.target.value)} placeholder="记录复核依据、影响范围、处置或验证结果" /></label>
      <button type="submit">{pending ? "正在保存…" : "保存审核"}</button>
      <button type="button" onClick={reload}>重新加载案例</button>
    </fieldset>
    {error ? <p role="alert">{error}</p> : null}
  </form>;
}
