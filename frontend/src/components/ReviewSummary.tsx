import { LoaderCircle, RefreshCcw } from "lucide-react";
import type { ReviewSummaryStatus } from "../hooks/useReviewReport";
import type { ReviewAgentReportResponse } from "../types";
import { ReviewDigestContent } from "./ReviewDigest";

interface ReviewSummaryProps {
  report: ReviewAgentReportResponse;
  summaryStatus: ReviewSummaryStatus;
  error: string | null;
  onRegenerate: () => void;
}

export function ReviewSummary({ report, summaryStatus, error, onRegenerate }: ReviewSummaryProps) {
  const digest = report.review_digest;
  const generating = summaryStatus === "generating";
  const failed = summaryStatus === "fallback" || summaryStatus === "error";
  const summarySource = report.generation_mode === "llm"
    ? summaryStatus === "ready" ? "AI解读" : "上次AI解读"
    : "本地说明";
  const cohortCount = (key: string) => {
    const count = report.time_cohort_counts?.[key];
    return count !== undefined ? `${count} 个` : report.time_audit_status === "checked" ? "0 个" : "未核验";
  };
  const statusText = {
    idle: "事实已就绪", generating: "正在生成解读", ready: "LLM 解读",
    fallback: "本次使用本地事实", error: "解读请求失败",
  }[summaryStatus];
  // A baseline or previous report's note is never the outcome of the active request.
  const failureReason = summaryStatus === "error" ? error
    : summaryStatus === "fallback" ? report.generation_note : null;
  const buttonText = generating ? "生成中…" : failed ? "重试解读"
    : summaryStatus === "ready" ? "重新生成" : "生成解读";
  const notice = generating ? digest ? "正在整理本期解读，已核对的事实和画像可先查看。" : "正在生成新报告。"
    : summaryStatus === "idle" && digest ? "本地事实已就绪，可生成本期画像解读。"
      : failed ? "本次解读未完成，已核对的事实仍可查看。" : null;

  return <section className="review-summary digest-summary" aria-label="复盘总结" aria-busy={generating}>
    <div className="review-summary-heading">
      <div><strong>复盘总结</strong><span>{digest ? statusText : "新报告待刷新"}{digest && summaryStatus === "ready" && report.llm_model ? ` · ${report.llm_model}` : ""}</span></div>
      <button type="button" disabled={generating} onClick={onRegenerate}>
        {generating ? <LoaderCircle size={14} className="state-spinner" /> : <RefreshCcw size={14} />}{buttonText}
      </button>
    </div>
    {notice ? <p className={failed ? "review-agent-error" : undefined} role={failed ? "alert" : "status"}>{notice}</p> : null}
    {failed && failureReason ? <details className="review-summary-reason"><summary>查看原因</summary><p>{failureReason}</p></details> : null}
    {digest ? <ReviewDigestContent digest={digest} summarySource={summarySource} />
      : <p>新报告待刷新：请刷新全部数据，或重新生成解读，获取当前窗口的四段复盘。</p>}
    <details className="review-summary-scope">
      <summary>样本口径与数据说明</summary>
      <p>本摘要的优秀候选上涨 ≥9.8%，较差候选下跌超过 5%；普通组与无法观察的样本分别保留。
        累计涨跌从首板收盘计算至截至日，观察天数可能不同；这是历史描述，不是未来概率或因果结论。</p>
      {digest?.notes.length ? <ul>{digest.notes.map((note, index) => <li key={index}>{note}</li>)}</ul> : null}
      <p>以下时间资格数量属于完整追踪报告（{report.sample_size} 条），可能与本摘要的候选窗口不同。
        现行盘前终选前向资格样本：{cohortCount("premarket_final")}；收盘基线：{cohortCount("close_baseline")}；
        旧版收盘：{cohortCount("legacy_close")}；历史补算：{cohortCount("historical_backtest")}。
        收盘及补算样本不计作现行盘前终选的前向验证。</p>
      <p>分布以各字段有效样本为分母，缺失不补零；候选画像与全部候选对照，市场高标仅描述本组。
        数字、样本资格和证据由本地代码生成，LLM 仅解释并选择证据，不改评分或预测记录。</p>
      {report.warnings.length ? <ul>{report.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul> : null}
    </details>
  </section>;
}
