import { useEffect, useState, type FormEvent } from "react";

import {
  drainReviewJobs,
  exportReviewCase,
  fetchReviewCase,
  fetchReviewCases,
  fetchReviewFeedback,
  fetchReviewJobs,
  sampleReviewRuns,
  updateReviewCase,
} from "../api";
import type {
  ReviewCaseDetail,
  ReviewCaseSummary,
  ReviewFeedbackRow,
  ReviewJob,
  ReviewSeverity,
  ReviewSource,
  ReviewStatus,
} from "../reviewTypes";
import { FEEDBACK_REASON_OPTIONS } from "../utils/answerFeedback";
import {
  REVIEW_SEVERITY_OPTIONS,
  REVIEW_SOURCE_LABELS,
  REVIEW_STATUS_OPTIONS,
  buildReviewCaseUpdate,
  canExportReviewCase,
  formatReviewTime,
  formFromCase,
  reviewAuthFailure,
  reviewErrorMessage,
  reviewSeverityLabel,
  reviewSourceLabel,
  reviewStatusLabel,
  type ReviewCaseForm,
} from "../utils/reviewAdmin";
import "../adminReview.css";

type ReviewTab = "cases" | "feedback" | "jobs";

interface ReviewFilters {
  status: ReviewStatus | "";
  source: ReviewSource | "";
  severity: ReviewSeverity | "";
}

const EMPTY_FILTERS: ReviewFilters = { status: "", source: "", severity: "" };
const REASON_LABELS = Object.fromEntries(
  FEEDBACK_REASON_OPTIONS.map(item => [item.value, item.label]),
) as Record<string, string>;

function valueText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.length ? value.join("，") : "—";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

/** Hidden administrator workspace for triaging online answer feedback. */
export function AdminReviewPage() {
  const [adminKey, setAdminKey] = useState("");
  const [keyDraft, setKeyDraft] = useState("");
  const [tab, setTab] = useState<ReviewTab>("cases");
  const [filters, setFilters] = useState<ReviewFilters>(EMPTY_FILTERS);
  const [cases, setCases] = useState<ReviewCaseSummary[]>([]);
  const [feedback, setFeedback] = useState<ReviewFeedbackRow[]>([]);
  const [jobs, setJobs] = useState<ReviewJob[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ReviewCaseDetail | null>(null);
  const [form, setForm] = useState<ReviewCaseForm | null>(null);
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function loadWorkspace(key: string, current: ReviewFilters = filters) {
    setLoading(true);
    setError("");
    try {
      const [caseResponse, feedbackResponse, jobResponse] = await Promise.all([
        fetchReviewCases(key, { ...current, limit: 100 }),
        fetchReviewFeedback(key),
        fetchReviewJobs(key),
      ]);
      setCases(caseResponse.cases);
      setFeedback(feedbackResponse.feedback);
      setJobs(jobResponse.jobs);
    } catch (caught) {
      setError(reviewErrorMessage(caught));
      if (reviewAuthFailure(caught)) setAdminKey("");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (adminKey) void loadWorkspace(adminKey);
  }, [adminKey, filters.status, filters.source, filters.severity]);

  async function unlock(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const key = keyDraft.trim();
    if (!key) return;
    setLoading(true);
    setError("");
    try {
      await fetchReviewCases(key, { limit: 1 });
      setAdminKey(key);
    } catch (caught) {
      setError(reviewErrorMessage(caught));
    } finally {
      setLoading(false);
    }
  }

  function lock() {
    setAdminKey("");
    setKeyDraft("");
    setCases([]);
    setFeedback([]);
    setJobs([]);
    setSelectedId(null);
    setDetail(null);
    setForm(null);
    setError("");
    setNotice("");
  }

  async function openCase(caseId: string) {
    setSelectedId(caseId);
    setDetail(null);
    setForm(null);
    setNote("");
    setError("");
    setNotice("");
    try {
      const value = await fetchReviewCase(adminKey, caseId);
      setDetail(value);
      setForm(formFromCase(value));
    } catch (caught) {
      setError(reviewErrorMessage(caught));
    }
  }

  async function saveCase(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail || !form) return;
    const update = buildReviewCaseUpdate(detail, form, note);
    if (!update.note) {
      setError("请填写审核说明");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const updated = await updateReviewCase(adminKey, detail.case_id, update);
      setDetail(updated);
      setForm(formFromCase(updated));
      setNote("");
      setNotice("审核结论已保存");
      await loadWorkspace(adminKey);
    } catch (caught) {
      setError(reviewErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function downloadExport() {
    if (!detail) return;
    setBusy(true);
    setError("");
    try {
      const markdown = await exportReviewCase(adminKey, detail.case_id);
      const url = URL.createObjectURL(new Blob([markdown], { type: "text/markdown;charset=utf-8" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `${detail.case_id}.md`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
      setNotice("已导出 Markdown");
    } catch (caught) {
      setError(reviewErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function drainJobs() {
    setBusy(true);
    setError("");
    try {
      const result = await drainReviewJobs(adminKey, 10);
      setNotice(
        `处理 ${result.processed_jobs} 个任务，新增抽样 ${result.sampled_runs} 条，聚合 ${result.aggregated_signals} 条信号`,
      );
      await loadWorkspace(adminKey);
    } catch (caught) {
      setError(reviewErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function addSamples() {
    setBusy(true);
    setError("");
    try {
      const result = await sampleReviewRuns(adminKey, 5);
      setNotice(`已补充抽样 ${result.enqueued} 条；运行一次处理后进入案例队列`);
      await loadWorkspace(adminKey);
    } catch (caught) {
      setError(reviewErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  if (!adminKey) {
    return (
      <main className="admin-review admin-review-gate-wrap">
        <form className="admin-review-gate" onSubmit={unlock}>
          <p className="eyebrow">内部审核</p>
          <h1>回答反馈与 Badcase</h1>
          <p>使用管理员密钥进入。密钥只保存在当前页面内存，刷新后需要重新输入。</p>
          <input
            type="password"
            autoComplete="off"
            aria-label="管理员密钥"
            placeholder="X-LimitUpLab-Admin-Key"
            value={keyDraft}
            onChange={event => setKeyDraft(event.target.value)}
          />
          <button type="submit" disabled={loading || !keyDraft.trim()}>
            {loading ? "验证中…" : "进入审核台"}
          </button>
          {error ? <div className="admin-review-notice error" role="alert">{error}</div> : null}
        </form>
      </main>
    );
  }

  return (
    <main className="admin-review">
      <header className="admin-review-header">
        <div>
          <p className="eyebrow">内部审核</p>
          <h1>回答反馈与 Badcase</h1>
          <p>查看用户点踩、抽样回答和审核结论；不展示买入、卖出或仓位建议。</p>
        </div>
        <div className="admin-review-header-actions">
          <button type="button" onClick={() => void loadWorkspace(adminKey)} disabled={loading || busy}>
            刷新
          </button>
          <button type="button" onClick={lock}>锁定</button>
        </div>
      </header>

      <nav className="admin-review-tabs" aria-label="审核台页签">
        {([
          ["cases", `案例 ${cases.length}`],
          ["feedback", `反馈 ${feedback.length}`],
          ["jobs", `任务 ${jobs.length}`],
        ] as const).map(([value, label]) => (
          <button
            key={value}
            type="button"
            aria-pressed={tab === value}
            onClick={() => setTab(value)}
          >
            {label}
          </button>
        ))}
      </nav>

      {error ? <div className="admin-review-notice error" role="alert">{error}</div> : null}
      {notice ? <div className="admin-review-notice" role="status">{notice}</div> : null}

      {tab === "cases" ? (
        <section className="admin-review-cases">
          <div className="admin-review-list">
            <div className="admin-review-filters">
              <label>
                状态
                <select
                  value={filters.status}
                  onChange={event => setFilters(current => ({
                    ...current, status: event.target.value as ReviewStatus | "",
                  }))}
                >
                  <option value="">全部</option>
                  {REVIEW_STATUS_OPTIONS.map(option => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </label>
              <label>
                来源
                <select
                  value={filters.source}
                  onChange={event => setFilters(current => ({
                    ...current, source: event.target.value as ReviewSource | "",
                  }))}
                >
                  <option value="">全部</option>
                  {(Object.keys(REVIEW_SOURCE_LABELS) as ReviewSource[]).map(value => (
                    <option key={value} value={value}>{reviewSourceLabel(value)}</option>
                  ))}
                </select>
              </label>
              <label>
                严重度
                <select
                  value={filters.severity}
                  onChange={event => setFilters(current => ({
                    ...current, severity: event.target.value as ReviewSeverity | "",
                  }))}
                >
                  <option value="">全部</option>
                  {REVIEW_SEVERITY_OPTIONS.map(option => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </label>
            </div>

            {loading && cases.length === 0 ? <p className="admin-review-empty">加载中…</p> : null}
            {!loading && cases.length === 0 ? <p className="admin-review-empty">暂无案例</p> : null}
            {cases.length > 0 ? (
              <table className="admin-review-table">
                <thead>
                  <tr>
                    <th>状态</th>
                    <th>问题</th>
                    <th>来源</th>
                    <th>严重度</th>
                    <th>信号</th>
                    <th>更新时间</th>
                  </tr>
                </thead>
                <tbody>
                  {cases.map(item => (
                    <tr key={item.case_id} className={item.case_id === selectedId ? "selected" : ""}>
                      <td>
                        <span className={`admin-review-badge status-${item.status}`}>
                          {reviewStatusLabel(item.status)}
                        </span>
                        {item.has_new_signal ? (
                          <span className="admin-review-badge new-signal">新信号</span>
                        ) : null}
                      </td>
                      <td>
                        <button
                          type="button"
                          className="admin-review-link"
                          onClick={() => void openCase(item.case_id)}
                        >
                          {item.question_preview || item.case_id}
                        </button>
                      </td>
                      <td>{reviewSourceLabel(item.source)}</td>
                      <td>{reviewSeverityLabel(item.severity)}</td>
                      <td>{item.signal_count}</td>
                      <td>{formatReviewTime(item.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
          </div>

          <div className="admin-review-detail">
            {detail && form ? (
              <CaseDetail
                detail={detail}
                form={form}
                note={note}
                busy={busy}
                onForm={setForm}
                onNote={setNote}
                onSave={saveCase}
                onExport={downloadExport}
              />
            ) : (
              <p className="admin-review-empty">选择左侧案例查看回答、证据和审核历史。</p>
            )}
          </div>
        </section>
      ) : null}

      {tab === "feedback" ? <FeedbackPanel rows={feedback} /> : null}

      {tab === "jobs" ? (
        <JobsPanel jobs={jobs} busy={busy} onDrain={drainJobs} onSample={addSamples} />
      ) : null}
    </main>
  );
}

function CaseDetail({
  detail,
  form,
  note,
  busy,
  onForm,
  onNote,
  onSave,
  onExport,
}: {
  detail: ReviewCaseDetail;
  form: ReviewCaseForm;
  note: string;
  busy: boolean;
  onForm: (value: ReviewCaseForm) => void;
  onNote: (value: string) => void;
  onSave: (event: FormEvent<HTMLFormElement>) => void;
  onExport: () => void;
}) {
  function update(patch: Partial<ReviewCaseForm>) {
    onForm({ ...form, ...patch });
  }

  return (
    <article className="admin-review-case">
      <header>
        <div className="admin-review-case-meta">
          <span className={`admin-review-badge status-${detail.status}`}>
            {reviewStatusLabel(detail.status)}
          </span>
          <span className="admin-review-badge">{reviewSeverityLabel(detail.severity)}</span>
          <span className="admin-review-badge">{reviewSourceLabel(detail.source)}</span>
          <span className="admin-review-badge">修订 {detail.revision}</span>
          {detail.has_new_signal ? (
            <span className="admin-review-badge new-signal">有未读信号</span>
          ) : null}
        </div>
        <p className="admin-review-mono">
          {detail.case_id} · run_id={detail.run_id ?? "已删除"} · session={detail.session_id}
        </p>
      </header>

      <section>
        <h2>用户问题</h2>
        <p className="admin-review-question">{detail.question || "—"}</p>
      </section>

      <section>
        <h2>正式回答</h2>
        <pre className="admin-review-answer">{detail.answer || "—"}</pre>
      </section>

      <section>
        <h2>回答快照</h2>
        <dl className="admin-review-evidence">
          {Object.entries(detail.evidence).map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{valueText(value)}</dd>
            </div>
          ))}
        </dl>
        {detail.trace_url ? (
          <p>
            <a href={detail.trace_url} target="_blank" rel="noreferrer">打开 Langfuse trace</a>
          </p>
        ) : null}
      </section>

      <section>
        <h2>机器与用户信号</h2>
        {detail.signals.length === 0 ? <p className="admin-review-empty">无信号</p> : null}
        <ul className="admin-review-signals">
          {detail.signals.map(signal => (
            <li key={signal.signal_id}>
              <div>
                <strong>{signal.summary}</strong>
                <span className="admin-review-badge">{reviewSeverityLabel(signal.severity)}</span>
                <span className="admin-review-muted">
                  {signal.kind} · {signal.detector}/{signal.detector_version} ·{" "}
                  {formatReviewTime(signal.created_at)}
                </span>
              </div>
              {Object.keys(signal.details).length ? (
                <details>
                  <summary>详情</summary>
                  <pre>{JSON.stringify(signal.details, null, 2)}</pre>
                </details>
              ) : null}
            </li>
          ))}
        </ul>
      </section>

      <form className="admin-review-form" onSubmit={onSave}>
        <h2>审核结论</h2>
        <div className="admin-review-form-row">
          <label>
            状态
            <select
              value={form.status}
              onChange={event => update({ status: event.target.value as ReviewStatus })}
            >
              {REVIEW_STATUS_OPTIONS.map(option => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <label>
            严重度
            <select
              value={form.severity}
              onChange={event => update({ severity: event.target.value as ReviewSeverity })}
            >
              {REVIEW_SEVERITY_OPTIONS.map(option => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
        </div>
        <label>
          症状
          <textarea
            rows={2}
            value={form.symptom}
            maxLength={2000}
            onChange={event => update({ symptom: event.target.value })}
          />
        </label>
        <label>
          根因
          <textarea
            rows={2}
            value={form.root_cause}
            maxLength={4000}
            onChange={event => update({ root_cause: event.target.value })}
          />
        </label>
        <div className="admin-review-form-row">
          <label>
            修复提交
            <input
              value={form.fix_commit}
              maxLength={100}
              onChange={event => update({ fix_commit: event.target.value })}
            />
          </label>
          <label>
            修复版本
            <input
              value={form.fix_version}
              maxLength={100}
              onChange={event => update({ fix_version: event.target.value })}
            />
          </label>
        </div>
        <label>
          验证说明
          <textarea
            rows={2}
            value={form.verification}
            maxLength={4000}
            onChange={event => update({ verification: event.target.value })}
          />
        </label>
        <label>
          审核说明（必填）
          <textarea
            rows={2}
            value={note}
            maxLength={2000}
            required
            placeholder="记录本次操作依据；所有流转都会写入审核历史"
            onChange={event => onNote(event.target.value)}
          />
        </label>
        <div className="admin-review-form-actions">
          <button type="submit" disabled={busy}>保存审核</button>
          <button
            type="button"
            disabled={busy || !canExportReviewCase(detail.status)}
            onClick={onExport}
          >
            导出 Markdown
          </button>
        </div>
      </form>

      <section>
        <h2>审核历史</h2>
        {detail.events.length === 0 ? <p className="admin-review-empty">尚无人工操作</p> : null}
        <ol className="admin-review-events">
          {detail.events.map(event => (
            <li key={event.event_id}>
              <strong>{event.action}</strong>
              <span className="admin-review-muted">
                {event.from_status ? `${event.from_status} → ` : ""}
                {event.to_status ?? ""} · {formatReviewTime(event.created_at)} · {event.actor}
              </span>
              {event.note ? <p>{event.note}</p> : null}
            </li>
          ))}
        </ol>
      </section>
    </article>
  );
}

function FeedbackPanel({ rows }: { rows: ReviewFeedbackRow[] }) {
  return (
    <section className="admin-review-panel">
      <h2>最近反馈</h2>
      {rows.length === 0 ? <p className="admin-review-empty">暂无反馈</p> : null}
      {rows.length > 0 ? (
        <table className="admin-review-table">
          <thead>
            <tr>
              <th>投票</th>
              <th>问题</th>
              <th>原因</th>
              <th>补充说明</th>
              <th>交付状态</th>
              <th>时间</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(row => (
              <tr key={`${row.run_id}-${row.updated_at}`}>
                <td>
                  <span className={`admin-review-badge vote-${row.vote}`}>
                    {row.vote === "up" ? "点赞" : "点踩"}
                  </span>
                </td>
                <td>{row.question_preview || "—"}</td>
                <td>{row.reasons.map(item => REASON_LABELS[item] ?? item).join("，") || "—"}</td>
                <td>{row.comment || "—"}</td>
                <td>{row.task_status ?? "—"}</td>
                <td>{formatReviewTime(row.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}

function JobsPanel({
  jobs,
  busy,
  onDrain,
  onSample,
}: {
  jobs: ReviewJob[];
  busy: boolean;
  onDrain: () => void;
  onSample: () => void;
}) {
  const counts = jobs.reduce<Record<string, number>>((acc, job) => {
    acc[job.status] = (acc[job.status] ?? 0) + 1;
    return acc;
  }, {});
  return (
    <section className="admin-review-panel">
      <div className="admin-review-panel-head">
        <div>
          <h2>抽样与检查任务</h2>
          <p className="admin-review-muted">
            {["pending", "processing", "succeeded", "skipped", "failed", "deferred"]
              .map(status => `${status} ${counts[status] ?? 0}`)
              .join(" · ")}
          </p>
        </div>
        <div className="admin-review-form-actions">
          <button type="button" onClick={onDrain} disabled={busy}>处理一批</button>
          <button type="button" onClick={onSample} disabled={busy}>补充抽样</button>
        </div>
      </div>
      {jobs.length === 0 ? <p className="admin-review-empty">暂无任务</p> : null}
      {jobs.length > 0 ? (
        <table className="admin-review-table">
          <thead>
            <tr>
              <th>类型</th>
              <th>状态</th>
              <th>尝试</th>
              <th>run_id</th>
              <th>创建时间</th>
              <th>错误</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map(job => (
              <tr key={job.job_id}>
                <td>{job.kind}</td>
                <td>{job.status}</td>
                <td>{job.attempts}</td>
                <td className="admin-review-mono">{job.run_id}</td>
                <td>{formatReviewTime(job.created_at)}</td>
                <td>{job.error_message ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}
