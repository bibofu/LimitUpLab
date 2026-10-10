import { LoaderCircle, RefreshCcw } from "lucide-react";
import type { ReviewAgentReportResponse } from "../types";

interface ReviewSummaryProps {
  report: ReviewAgentReportResponse;
  generating: boolean;
  error: string | null;
  onRegenerate: () => void;
}

export function ReviewSummary({ report, generating, error, onRegenerate }: ReviewSummaryProps) {
  const mode = report.generation_mode ?? "legacy";
  const source = mode === "llm" ? "LLM 生成" : mode === "deterministic" ? "规则回退" : "历史来源未标识";
  const sections = [
    ["主要发现", report.main_findings],
    ["评分偏差观察", report.scoring_bias],
    ["待验证的调整假设", report.adjustment_suggestions],
  ] as const;

  return (
    <section className="review-summary" aria-label="复盘总结" aria-busy={generating}>
      <div className="review-summary-heading">
        <div>
          <strong>复盘总结</strong>
          <span>{source}{mode === "llm" && report.llm_model ? ` · ${report.llm_model}` : ""}</span>
        </div>
        <button type="button" disabled={generating} onClick={onRegenerate}>
          {generating ? <LoaderCircle size={14} className="state-spinner" /> : <RefreshCcw size={14} />}
          {generating ? "生成中…" : error || mode !== "llm" ? "重试模型总结" : "重新生成"}
        </button>
      </div>
      <p>默认启用模型总结；仅用于复盘观察和提出待验证假设，不会自动修改评分、权重或预测记录。</p>
      {generating ? <p role="status">正在生成模型总结，统计与追踪仍可查看；下方保留已有总结。</p> : null}
      {error ? <p className="review-agent-error" role="alert">{error}；已保留现有统计和总结，可手动重试。</p> : null}
      {report.generation_note ? <p>{report.generation_note}</p> : null}
      <div className="review-summary-sections">
        {sections.map(([title, items]) => (
          <div key={title}>
            <h3>{title}</h3>
            {items.length ? <ul>{items.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul>
              : <p>暂无可核验结论。</p>}
          </div>
        ))}
      </div>
    </section>
  );
}
