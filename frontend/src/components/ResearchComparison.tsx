import type { ReviewFeatureStudy } from "../types";

export const studyRate = (value: number | null) => value === null ? "样本不足" : `${(value * 100).toFixed(1)}%`;

export function ResearchComparison({ study, market = false }: { study: ReviewFeatureStudy; market?: boolean }) {
  const positive = market ? "达到3板及以上" : "↑ 表现较好";
  const negative = market ? "未达到3板" : "↓ 表现较差";
  return (
    <div className={`research-comparison ${market ? "market-comparison" : "candidate-comparison"}`}>
      <div className="research-group-heads">
        <div className="research-positive"><strong>{positive}</strong><b>{study.positive_count}<small> 个样本</small></b><span>{market ? "该轮严格1→2→3" : "首板至观察日累计上涨"}</span></div>
        <div className="research-negative"><strong>{negative}</strong><b>{study.negative_count}<small> 个样本</small></b><span>{market ? "同窗口成熟首板对照" : "首板至观察日累计下跌"}</span></div>
      </div>
      <p className="research-basis">{study.basis}</p>
      <p className="research-excluded">未入组 {study.excluded_count} 个样本；{market ? "待观察或行情不完整，不记作未晋级。" : "包括零收益或无法完整观察的样本，不并入较差组。"}</p>
      {study.features.length ? (
        <table className="research-contrast-table">
          <caption>{market ? "全市场首板特征对照" : "候选首板特征对照"}</caption>
          <thead><tr><th scope="col">首板特征</th><th scope="col" className="research-positive">{positive}</th><th scope="col" className="research-negative">{negative}</th></tr></thead>
          <tbody>{study.features.map(feature => (
            <tr key={feature.key}>
              <th scope="row">{feature.label}</th>
              <td className="research-positive"><FeatureValue featureKey={feature.key} summary={feature.positive_summary} detail={feature.positive_detail} validCount={feature.positive_valid_count} sampleSize={study.positive_count} /></td>
              <td className="research-negative"><FeatureValue featureKey={feature.key} summary={feature.negative_summary} detail={feature.negative_detail} validCount={feature.negative_valid_count} sampleSize={study.negative_count} /></td>
            </tr>
          ))}</tbody>
        </table>
      ) : <p>暂缺可比较的首板特征，等待数据补齐。</p>}
    </div>
  );
}

function FeatureValue({ featureKey, summary, detail, validCount, sampleSize }: {
  featureKey: string; summary: string; detail: string; validCount: number; sampleSize: number;
}) {
  const distribution = featureKey === "position_label" || featureKey === "break_count";
  return <>
    <strong>{summary}</strong>
    {distribution || !detail ? <small>有效 {validCount}/{sampleSize} 个样本</small> : null}
    {detail ? distribution
      ? <details className="research-distribution"><summary>完整分布</summary><span>{detail}</span></details>
      : <span>{detail}</span> : null}
  </>;
}
