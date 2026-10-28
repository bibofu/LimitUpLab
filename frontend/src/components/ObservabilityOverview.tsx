import type { ObservabilitySummary } from "../observabilityTypes";
import { FEEDBACK_REASON_OPTIONS } from "../utils/answerFeedback";
import {
  barWidth,
  formatCount,
  formatDuration,
  formatLatency,
  formatPercent,
  formatUsd,
} from "../utils/observability";
import { reviewSourceLabel, reviewStatusLabel } from "../utils/reviewAdmin";
import "../observability.css";

const REASON_LABELS = Object.fromEntries(
  FEEDBACK_REASON_OPTIONS.map(item => [item.value, item.label]),
) as Record<string, string>;

function Kpi({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="obs-kpi">
      <span>{label}</span>
      <strong>{value}</strong>
      {hint ? <small>{hint}</small> : null}
    </div>
  );
}

/** Read-only operational overview for the current period. */
export function ObservabilityOverview({ summary }: { summary: ObservabilitySummary }) {
  const totals = summary.totals;
  const maxRequests = Math.max(1, ...summary.buckets.map(item => item.request_count));
  const maxDown = Math.max(1, ...summary.buckets.map(item => item.down_count));
  const statuses = Object.entries(summary.case_status_counts)
    .sort((left, right) => right[1] - left[1]);
  const sources = Object.entries(summary.case_source_counts)
    .sort((left, right) => right[1] - left[1]);
  const jobStatuses = Object.entries(summary.job_status_counts)
    .sort((left, right) => right[1] - left[1]);

  return (
    <section className="obs-overview">
      <header className="obs-period">
        <div>
          <h2>近 {summary.days} 天概览</h2>
          <p>
            {summary.period_started_at.slice(0, 10)} 起 · 日界线 {summary.timezone} ·
            生成于 {summary.generated_at.replace("T", " ").slice(0, 19)}
          </p>
        </div>
      </header>

      <div className="obs-grid">
        <Kpi label="请求量" value={formatCount(totals.request_count)} hint={`成功 ${formatCount(totals.success_count)} · 错误 ${formatCount(totals.error_count)}`} />
        <Kpi label="回答量" value={formatCount(totals.answered_count)} hint={`部分完成 ${formatCount(totals.partial_count)}`} />
        <Kpi label="独立访客" value={formatCount(totals.distinct_owners)} hint={`会话 ${formatCount(totals.distinct_sessions)}`} />
        <Kpi label="总 token" value={formatCount(totals.total_tokens)} hint={`不完整 ${formatCount(totals.incomplete_token_count)} 次`} />
        <Kpi label="估算成本" value={formatUsd(totals.estimated_cost_usd)} hint="按明确单价估算" />
        <Kpi label="点赞率" value={formatPercent(totals.like_ratio)} hint={`${formatCount(totals.up_count)} 赞 / ${formatCount(totals.down_count)} 踩`} />
        <Kpi label="反馈覆盖" value={formatPercent(totals.feedback_coverage)} hint={`每百答 ${formatCount(totals.down_per_100_answers)} 次点踩`} />
        <Kpi label="抽检覆盖" value={formatPercent(totals.sample_coverage)} hint={`${formatCount(totals.sample_job_count)} 个抽样任务`} />
        <Kpi label="待处理案例" value={formatCount(totals.open_case_count)} hint={`已确认 ${formatCount(totals.confirmed_case_count)} · 已关闭 ${formatCount(totals.closed_case_count)}`} />
      </div>

      <div className="obs-grid obs-grid-secondary">
        <Kpi label="延迟 p50 / p95" value={`${formatLatency(totals.p50_duration_ms)} / ${formatLatency(totals.p95_duration_ms)}`} />
        <Kpi label="首次处理 / 关闭" value={`${formatDuration(totals.mtta_seconds)} / ${formatDuration(totals.mttr_seconds)}`} />
        <Kpi label="Judge 调用" value={formatCount(totals.judge_call_count)} hint={`成功 ${formatCount(totals.judge_succeeded_count)} · 错误 ${formatCount(totals.judge_error_count)}`} />
        <Kpi label="Judge token" value={formatCount(totals.judge_total_tokens)} hint={`成本 ${formatUsd(totals.judge_estimated_cost_usd)} · 定价覆盖 ${formatCount(totals.judge_priced_call_count)} 次`} />
      </div>

      <section className="obs-panel">
        <h3>每日趋势</h3>
        {summary.buckets.length === 0 ? <p className="admin-review-empty">暂无数据</p> : null}
        {summary.buckets.length > 0 ? (
          <table className="obs-table">
            <thead>
              <tr>
                <th>日期</th>
                <th>请求</th>
                <th>回答</th>
                <th>点踩</th>
                <th>token</th>
                <th>案例</th>
              </tr>
            </thead>
            <tbody>
              {summary.buckets.map(bucket => (
                <tr key={bucket.date}>
                  <td className="obs-date">{bucket.date.slice(5)}</td>
                  <td>
                    <div className="obs-bar-row">
                      <span className="obs-bar" style={{ width: `${barWidth(bucket.request_count, maxRequests)}%` }} />
                      <span>{formatCount(bucket.request_count)}</span>
                    </div>
                  </td>
                  <td>{formatCount(bucket.answered_count)}</td>
                  <td>
                    <div className="obs-bar-row">
                      <span className="obs-bar obs-bar-down" style={{ width: `${barWidth(bucket.down_count, maxDown)}%` }} />
                      <span>{formatCount(bucket.down_count)}</span>
                    </div>
                  </td>
                  <td>{formatCount(bucket.total_tokens)}</td>
                  <td>{formatCount(bucket.case_count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </section>

      <div className="obs-columns">
        <section className="obs-panel">
          <h3>点踩原因</h3>
          {summary.feedback_reasons.length === 0 ? <p className="admin-review-empty">暂无原因</p> : null}
          <ul className="obs-list">
            {summary.feedback_reasons.map(item => (
              <li key={item.reason}>
                <span>{REASON_LABELS[item.reason] ?? item.reason}</span>
                <strong>{formatCount(item.count)}</strong>
              </li>
            ))}
          </ul>
        </section>

        <section className="obs-panel">
          <h3>案例状态</h3>
          {statuses.length === 0 ? <p className="admin-review-empty">暂无案例</p> : null}
          <ul className="obs-list">
            {statuses.map(([status, count]) => (
              <li key={status}>
                <span>{reviewStatusLabel(status)}</span>
                <strong>{formatCount(count)}</strong>
              </li>
            ))}
          </ul>
        </section>

        <section className="obs-panel">
          <h3>案例来源</h3>
          {sources.length === 0 ? <p className="admin-review-empty">暂无案例</p> : null}
          <ul className="obs-list">
            {sources.map(([source, count]) => (
              <li key={source}>
                <span>{reviewSourceLabel(source)}</span>
                <strong>{formatCount(count)}</strong>
              </li>
            ))}
          </ul>
        </section>

        <section className="obs-panel">
          <h3>任务状态</h3>
          {jobStatuses.length === 0 ? <p className="admin-review-empty">暂无任务</p> : null}
          <ul className="obs-list">
            {jobStatuses.map(([status, count]) => (
              <li key={status}>
                <span>{status}</span>
                <strong>{formatCount(count)}</strong>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <section className="obs-panel obs-notes">
        <h3>口径说明</h3>
        <ul>
          {summary.notes.map(note => <li key={note}>{note}</li>)}
        </ul>
      </section>
    </section>
  );
}
