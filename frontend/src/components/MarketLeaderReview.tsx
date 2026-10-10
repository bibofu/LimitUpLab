import { Link } from "react-router-dom";
import type { ReviewBucketStat, ReviewFeatureResearch, ReviewFeatureStudy } from "../types";
import { ResearchComparison, studyRate } from "./ResearchComparison";

const LEADER_STATUS: Record<string, string> = {
  verified: "连板链已核验", tracked: "连板链已核验", matched: "窗口内首板及连板链已核验", incomplete_chain: "后续行情缺失，板高仅计已核验部分",
  unresolved: "首板尚未核验", outside_window: "首板已核实但在窗口外，不入同期比例",
};
const MISSING_LABELS: Record<string, string> = {
  calendar_missing: "交易日历缺失", followup_unavailable: "后续观察日不足", event_date_missing: "当日行情缺失",
  height_inconsistent: "连板高度不一致", first_board_calendar_missing: "首板日期无法由日历定位",
  chain_event_missing: "连板链行情缺失", chain_height_mismatch: "连板链高度不一致", first_board_outside_window: "首板在观察窗口之外",
  first_board_position: "首板位置缺失", first_board_float_market_cap: "首板当日流通市值缺失",
  first_limit_time: "首次封板时间缺失", break_count: "炸板次数缺失", turnover_rate: "换手率缺失",
};
function missingLabel(value: string) {
  const [key, ...context] = value.split(":");
  return `${MISSING_LABELS[key] ?? key}${context.length ? `：${context.join(":")}` : ""}`;
}

function BucketComparison({ bucket, outcomeLabel }: { bucket: ReviewBucketStat; outcomeLabel: string }) {
  return (
    <div className="research-bucket">
      <strong>{bucket.label}</strong>
      <div><b>{studyRate(bucket.positive_rate)}</b><span>{bucket.positive_count}/{bucket.sample_size} 个{outcomeLabel}</span></div>
      <div><span>同指标有效基准 {studyRate(bucket.baseline_rate)}</span><small>{bucket.positive_rate === null || bucket.delta_pp === null ? "样本不足，暂不比较差值" : `相差 ${bucket.delta_pp > 0 ? "+" : ""}${bucket.delta_pp.toFixed(1)} 个百分点`}</small></div>
    </div>
  );
}

export function FeatureOutcomeRates({ study, outcomeLabel }: { study: ReviewFeatureStudy; outcomeLabel: string }) {
  return <>{study.features.map(feature => {
    // Keep source order for ties; never select a tiny bucket because its observed rate is high.
    const representative = feature.buckets.reduce<ReviewBucketStat | null>((chosen, bucket) => !chosen || bucket.sample_size > chosen.sample_size ? bucket : chosen, null);
    return (
      <section className="research-rate-feature" key={feature.key} aria-label={`${feature.label}的${outcomeLabel}比例`}>
        <h5>{feature.label}</h5>
        {representative ? <BucketComparison bucket={representative} outcomeLabel={outcomeLabel} /> : <p>暂无有效分档数据。</p>}
        {feature.buckets.length > 1 ? <details><summary>查看其余 {feature.buckets.length - 1} 档</summary>{feature.buckets.filter(bucket => bucket !== representative).map(bucket => <BucketComparison key={bucket.label} bucket={bucket} outcomeLabel={outcomeLabel} />)}</details> : null}
      </section>
    );
  })}</>;
}

export function MarketLeaderReview({ research }: { research: ReviewFeatureResearch }) {
  const market = research.market;
  return (
    <section className="research-section" aria-label="全市场高标回溯">
      <div className="research-section-heading"><h3>全市场：哪些首板后来达到3板及以上</h3><span>{research.market_start_date} — {research.market_end_date}</span></div>
      <p>近20个交易日内的首板起点；观察该轮是否严格从1板走到2板、3板。样本3板比例为 {studyRate(market.baseline_rate)}，用于本期回溯，不是未来预测。</p>
      <ResearchComparison study={market} market />
      <div className="research-rates">
        <h4>具有这些特征的首板，后来走强多少</h4>
        <p>各项先展示样本最多的一档；不是按比例挑选最好的一档。分母仅含该指标有值、观察成熟的首板，小于5个样本不展示比例。</p>
        <FeatureOutcomeRates study={market} outcomeLabel="首板达到3板" />
      </div>
      <details className="research-leaders">
        <summary>核对高标个股证据 · 检测 {research.market_detected_count} 个，已定位首板 {research.market_matched_count} 个</summary>
        <p>以下是本次检测到的高标个案；未定位或数据缺失的个案不能充当完整画像证据。</p>
        {research.market_leaders.length ? research.market_leaders.map(leader => (
          <article key={`${leader.symbol}-${leader.first_board_date ?? leader.latest_date}`}>
            <div className="research-leader-heading"><Link to={`/stocks/${encodeURIComponent(leader.symbol)}`}>{leader.name} <small>{leader.symbol}</small></Link><b>{leader.status === "unresolved" ? `来源报 ${leader.max_board_height} 板 / 待核验` : `该轮已核验最高 ${leader.max_board_height} 板`}</b></div>
            <p>首板 {leader.first_board_date ?? "待定位"} · 观察至 {leader.latest_date} · {LEADER_STATUS[leader.status] ?? "证据状态待核对"}</p>
            <dl>{[
              ["首板位置", leader.position_label ?? "缺失"],
              ["流通市值", leader.float_market_cap === null ? "缺失" : `${(leader.float_market_cap / 100_000_000).toFixed(1)} 亿元`],
              ["首次封板", leader.first_limit_time ?? "缺失"],
              ["炸板", leader.break_count === null ? "缺失" : `${leader.break_count} 次`],
              ["换手率", leader.turnover_rate === null ? "缺失" : `${leader.turnover_rate.toFixed(1)}%`],
            ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
            {leader.data_missing.length ? <p className="research-missing">待核对：{leader.data_missing.map(missingLabel).join("；")}</p> : null}
          </article>
        )) : <p>当前窗口暂无可展示的高标个案。</p>}
      </details>
    </section>
  );
}
