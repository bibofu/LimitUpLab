import { LoaderCircle, RefreshCcw } from "lucide-react";
import type { ReviewSummaryStatus } from "../hooks/useReviewReport";
import type { ReviewAgentReportResponse, ReviewFeatureCard } from "../types";
import { ResearchComparison } from "./ResearchComparison";
import { FeatureOutcomeRates, MarketLeaderReview } from "./MarketLeaderReview";

interface ReviewSummaryProps {
  report: ReviewAgentReportResponse;
  summaryStatus: ReviewSummaryStatus;
  error: string | null;
  onRegenerate: () => void;
}

const FEATURES = [
  ["position", "首板位置"],
  ["market_cap", "流通市值（中位数）"],
  ["first_seal", "首次封板时间（均值）"],
] as const;

export function ReviewSummary({ report, summaryStatus, error, onRegenerate }: ReviewSummaryProps) {
  const generating = summaryStatus === "generating";
  const failed = summaryStatus === "fallback" || summaryStatus === "error";
  const headline = report.summary_headline?.trim();
  const features = report.feature_summary;
  const research = report.feature_research;
  const insightScopes = [["candidate", "候选表现"], ["market", "全市场高标"], ["synthesis", "综合观察"]] as const;
  const insightSource = report.generation_mode === "llm"
    ? summaryStatus === "ready" ? "AI归纳" : "上次AI归纳"
    : report.generation_mode === "deterministic" ? "本地事实归纳" : "已存结论";
  const cohortCount = (key: string) => {
    const count = report.time_cohort_counts?.[key];
    return count !== undefined ? `${count} 个` : report.time_audit_status === "checked" ? "0 个" : "未核验";
  };
  const statusText = {
    idle: "特征已就绪", generating: "正在生成解读", ready: "LLM 解读",
    fallback: "本次使用本地统计", error: "解读请求失败",
  }[summaryStatus];
  // A baseline or previous report's note is never the outcome of the active request.
  const failureReason = summaryStatus === "error" ? error
    : summaryStatus === "fallback" ? report.generation_note : null;
  const buttonText = generating ? "生成中…" : failed ? "重试解读"
    : summaryStatus === "ready" ? "重新生成" : "生成解读";
  const notice = generating ? "正在整理本期解读，候选与全市场特征已可查看。"
    : summaryStatus === "idle" ? "本地事实已就绪，可生成候选与全市场的综合解读。"
      : failed ? "本次解读未完成，特征统计仍可查看。"
        : !report.summary_insights?.length && !headline ? "本期结论尚未齐备，可重新生成。" : null;

  return (
    <section className="review-summary" aria-label="复盘总结" aria-busy={generating}>
      <div className="review-summary-heading">
        <div>
          <strong>复盘总结</strong>
          <span>{statusText}{summaryStatus === "ready" && report.llm_model ? ` · ${report.llm_model}` : ""}</span>
        </div>
        <button type="button" disabled={generating} onClick={onRegenerate}>
          {generating ? <LoaderCircle size={14} className="state-spinner" /> : <RefreshCcw size={14} />}
          {buttonText}
        </button>
      </div>
      {notice ? <p className={failed ? "review-agent-error" : undefined} role={failed ? "alert" : "status"}>{notice}</p> : null}
      {report.summary_insights?.length ? (
        <section className="research-insights" aria-label="本期结论">
          <div className="research-section-heading"><h3>本期结论</h3><span>{insightSource}</span></div>
          <ol>{insightScopes.map(([scope, label]) => {
            const insight = report.summary_insights?.find(item => item.scope === scope);
            return insight ? <li key={scope}><span>{label}</span><div><strong>{insight.title}</strong><p>{insight.detail}</p></div></li> : null;
          })}</ol>
          {research?.cross_checks?.length ? <details className="review-direction-checks"><summary>查看跨样本方向核对</summary><ul>{research.cross_checks.map(check => <li key={check}>{check}</li>)}</ul></details> : null}
        </section>
      ) : !research && headline && (summaryStatus === "ready" || generating) ? (
        <p className="review-summary-headline"><b>{generating ? "上次解读：" : "一句话解读："}</b>{headline}</p>
      ) : null}
      {failed && failureReason ? (
        <details className="review-summary-reason"><summary>查看原因</summary><p>{failureReason}</p></details>
      ) : null}
      {research ? (
        <>
          <section className="research-section" aria-label="候选表现对照">
            <div className="research-section-heading"><h3>候选：表现较好与较差的首板，有哪些差异</h3><span>{report.start_date} — {report.end_date}</span></div>
            <ResearchComparison study={research.candidate} />
            <p>观察时长可能不同；这里比较已观察样本的特征，不把累计上涨占比当作未来走强概率。</p>
            <details className="research-rates research-candidate-rates">
              <summary>查看候选各特征的正收益比例</summary>
              <p>按首板至观察日累计涨跌统计，仅含该特征有值、结局可观察的候选。各项先展示样本最多的一档，小于5个样本不展示比例；不代表未来收益概率。</p>
              <FeatureOutcomeRates study={research.candidate} outcomeLabel="候选累计正收益" />
            </details>
          </section>
          <MarketLeaderReview research={research} />
        </>
      ) : <>
      <p>当前为旧版特征报告；刷新事实后可查看候选与全市场高标的完整对照。</p>
      <p className="review-feature-counts">
        {features ? `正收益组 ${features.positive_count} 个样本 · 负收益组 ${features.negative_count} 个样本` : "特征分组数据尚未齐备"}
      </p>
      <div className="review-feature-cards">
        {FEATURES.map(([key, label]) => (
          <FeatureCard key={key} label={label} card={features?.cards.find(card => card.key === key)} />
        ))}
      </div>
      </>}
      <details className="review-summary-scope">
        <summary>样本口径与数据说明</summary>
        <p>现行盘前终选前向资格样本：{cohortCount("premarket_final")}；
          收盘基线：{cohortCount("close_baseline")}；旧版收盘：{cohortCount("legacy_close")}；历史补算：{cohortCount("historical_backtest")}。
          收盘及补算样本不计作现行盘前终选的前向验证。</p>
        <p>按首板至 {report.end_date} 内最新可用收盘的涨跌分为正、负收益组，零收益和无法观察的样本不入组。
          这是组内特征对比，不是胜率或因果结论，与次日开盘至收盘的评价标签口径不同。</p>
        <p>每项特征分别排除缺失数据；全市场分档少于5个样本不展示比例。统计由本地代码计算，LLM 只补充解读，不会修改评分或预测记录。</p>
        {research?.notes.length ? <ul>{research.notes.map((note, index) => <li key={`${index}-${note}`}>{note}</li>)}</ul> : null}
        {report.warnings.length ? <ul>{report.warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul> : null}
      </details>
    </section>
  );
}

function FeatureCard({ label, card }: { label: string; card?: ReviewFeatureCard }) {
  const groups = [
    ["正收益组", card?.positive],
    ["负收益组", card?.negative],
  ] as const;

  return (
    <article className="review-feature-card" aria-label={label}>
      <h3>{label}</h3>
      <dl>
        {groups.map(([group, value]) => (
          <div key={group}>
            <dt>{group}</dt>
            <dd>
              <strong>{value?.text || "暂无数据"}</strong>
              {value ? <small>有效 {value.valid_count}/{value.sample_size} 个样本</small> : null}
              {value?.detail ? <small>{value.detail}</small> : null}
            </dd>
          </div>
        ))}
      </dl>
      <p>{card?.observation || "该特征尚无可用统计。"}</p>
    </article>
  );
}
