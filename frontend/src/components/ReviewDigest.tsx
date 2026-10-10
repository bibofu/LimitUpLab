import { Link } from "react-router-dom";
import type { DigestDistribution, DigestGroup, DigestStock, ReviewDigest } from "../reviewDigestTypes";

const percent = (value: number | null) => value === null ? "比例不足" : `${(value * 100).toFixed(1)}%`;
const signed = (value: number | null) => value === null ? "缺失" : `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
const countValue = (value: number | null, unit = "") => value === null ? "缺失" : `${value}${unit}`;
const dateRange = (dates: string[]) => dates.length ? `${dates[0]} — ${dates[dates.length - 1]} · ${dates.length} 个交易日` : "交易日窗口待补齐";
const openingBucket = (count: number | null, total: number | null, share: number | null) => count === null || total === null || total === 0
  ? "待核验" : `${count}/${total}${share === null ? "" : ` · ${percent(share)}`}`;

export function ReviewDigestContent({ digest, summarySource }: { digest: ReviewDigest; summarySource: string }) {
  const overview = digest.overview;
  return <div className="review-digest">
    <section className="digest-overview" aria-label="整体表现">
      <h3><span>01</span> 整体表现 <small>截至 {digest.as_of_date}</small></h3>
      <p className="digest-headline">{overview.headline}</p>
      <div className="digest-counts" aria-label="候选分组数量">
        <span>候选 <b>{overview.candidate_count}</b></span>
        <span className="digest-up">优秀 <b>{overview.excellent_count}</b></span>
        <span className="digest-down">较差 <b>{overview.weak_count}</b></span>
        <span>普通 <b>{overview.ordinary_count}</b></span>
        <span>缺失 / 未观察 <b>{overview.unobserved_count}</b></span>
      </div>
      <details className="digest-overview-evidence"><summary>核对整体晋级数据</summary>
        <p>候选 {overview.candidate_promoted}/{overview.candidate_promotion_total} · {percent(overview.candidate_promotion_rate)}；全市场 {overview.market_promoted}/{overview.market_promotion_total} · {percent(overview.market_promotion_rate)}。</p>
        <p>可比 {overview.comparable_days} 日，其中领先 {overview.outperform_days} 日；最佳日期：{overview.best_date ?? "暂无可比日期"}。</p>
      </details>
    </section>
    <DigestPortrait group={digest.excellent} number="02" title="优秀候选" threshold="上涨 ≥9.8%" dates={digest.candidate_dates} summarySource={summarySource} />
    <DigestPortrait group={digest.weak} number="03" title="较差候选" threshold="下跌超过 5%" dates={digest.candidate_dates} summarySource={summarySource} />
    <DigestPortrait group={digest.leaders} number="04" title="市场三板及以上" threshold="首板 + 二板画像" dates={digest.market_dates} summarySource={summarySource} />
  </div>;
}

function DigestPortrait({ group, number, title, threshold, dates, summarySource }: {
  group: DigestGroup; number: string; title: string; threshold: string; dates: string[]; summarySource: string;
}) {
  const leaders = group.scope === "leaders";
  const byId = new Map(group.observations.map(item => [item.id, item]));
  const selected = [...new Set(group.selected_observation_ids)].flatMap(id => byId.has(id) ? [byId.get(id)!] : []).slice(0, leaders ? 5 : 3);
  const summary = group.sample_size > 0 ? group.summary.trim() : "";
  const source = selected.length ? summarySource : "本地说明";
  return <section className={`digest-portrait digest-${group.scope}`} aria-label={title}>
    <div className="digest-portrait-heading">
      <h3><span>{number}</span> {title}</h3><strong>{group.sample_size} 个样本</strong><span className="digest-threshold">{threshold}</span>
    </div>
    <p className="digest-window">{leaders ? "含截至日近 5 个交易日" : "截至日前 5 个交易日的候选"} · {dateRange(dates)}</p>
    {summary ? <p className="digest-interpretation"><span>{source}</span>{summary}</p> : null}
    {group.sample_size === 0 ? <p className="digest-empty">当前窗口没有符合条件的可观察样本。</p>
      : selected.length ? <ul className="digest-observations">{selected.map(item => <li key={item.id}>
        {leaders || item.dimension.startsWith("next_") ? <span className="digest-stage">{item.dimension.startsWith("next_") ? "次日" : item.dimension.startsWith("second_") ? "二板" : "首板"}</span> : null}
        <span>{item.text}</span>
      </li>)}</ul> : <p className="digest-empty">特征证据尚不足，暂不概括画像。</p>}
    {group.sample_size > 0 ? <CompactDistributions group={group} /> : null}
    {group.notes.length ? <details className="digest-more-notes"><summary>数据说明 · {group.notes.length} 条</summary><ul>{group.notes.map((note, index) => <li key={index}>{note}</li>)}</ul></details> : null}
    <DistributionEvidence distributions={group.distributions} leaders={leaders} />
    <StockEvidence stocks={group.stocks} leaders={leaders} />
  </section>;
}

function NextOpenDistribution({ group }: { group: DigestGroup }) {
  const opening = group.distributions.find(item => item.key === "next_open_pct");
  return <section className="digest-feature-strip digest-next-open" aria-label="次日开盘">
    {opening ? <LeadingBucket distribution={opening} compare={group.scope !== "leaders"} />
      : <div className="digest-strip-metric"><strong>次日开盘</strong><span className="digest-empty">有效样本待核验</span></div>}
  </section>;
}

function CompactDistributions({ group }: { group: DigestGroup }) {
  const preferred = [["position_label"], ["concepts", "industry"], ["float_market_cap"], ["first_limit_time"]];
  const chosen = preferred.flatMap(keys => {
    const metrics = keys.flatMap(key => group.distributions.find(item => item.key === key) ?? []);
    return metrics.length ? [metrics] : [];
  });
  return <div className="digest-feature-strips" aria-label="首板特征与次日开盘">{chosen.map(metrics =>
    <div className="digest-feature-strip" key={metrics[0].key}>{metrics.map(distribution => <LeadingBucket key={distribution.key} distribution={distribution} />)}</div>
  )}<NextOpenDistribution group={group} /></div>;
}

function LeadingBucket({ distribution, compare = false }: { distribution: DigestDistribution; compare?: boolean }) {
  const buckets = distribution.valid_count > 0
    ? [...distribution.buckets].filter(item => item.count > 0).sort((left, right) => right.count - left.count) : [];
  const bucket = buckets[0];
  const inline = distribution.key === "industry";
  const opening = distribution.key === "next_open_pct";
  const tied = bucket ? buckets.filter(item => item.count === bucket.count) : [];
  const openingTies = opening && tied.length > 1;
  const rank = tied.length > 1 ? opening ? `并列最多（${tied.length}档）` : "并列最多" : bucket ? "占比最多" : "";
  const share = bucket && (!openingTies || tied.every(item => item.share === bucket.share)) ? bucket.share : null;
  return <div className={`digest-strip-metric${inline ? " digest-strip-inline" : ""}`}>
    <strong>{opening ? "次日开盘" : distribution.label}{rank ? <em>{rank}</em> : null}</strong>
    {bucket ? <div className={`digest-strip-value${openingTies ? " digest-opening-ties" : ""}`}>
      <span>{openingTies ? tied.map(item => item.label).join(" / ") : bucket.label}</span><b>{openingTies ? "各 " : ""}{bucket.count}/{distribution.valid_count}{share === null ? "" : ` · ${percent(share)}`}</b>
      {share === null || inline ? null : <i aria-hidden="true"><i style={{ width: `${Math.max(0, Math.min(1, share)) * 100}%` }} /></i>}
    </div> : <span className="digest-empty">暂无有效数据</span>}
    {opening && compare && bucket ? <small className="digest-opening-baseline">{openingTies ? "候选对照见完整分布"
      : <>该档全部候选 {openingBucket(bucket.baseline_count, distribution.baseline_valid_count, bucket.baseline_share)}</>}</small> : null}
    <small>有效 {distribution.valid_count}/{distribution.total_count}{opening ? " · 相对首板收盘" : ""}</small>
  </div>;
}

function DistributionEvidence({ distributions, leaders }: { distributions: DigestDistribution[]; leaders: boolean }) {
  return <details className="digest-evidence"><summary>{leaders ? "完整特征分布" : "完整特征分布 · 与全部候选对照"}</summary>
    {distributions.length ? distributions.map(distribution => {
      const baseline = !leaders && distribution.baseline_valid_count !== null;
      return <section className="digest-distribution" key={distribution.key}>
      <h4>{distribution.label}</h4>
      <p>{distribution.total_count ? `本组有效 ${distribution.valid_count}/${distribution.total_count}` : "本组暂无样本"}{baseline ? distribution.baseline_total_count === 0 ? "；全部候选暂无样本" : `；全部候选有效 ${distribution.baseline_valid_count}/${distribution.baseline_total_count ?? "未提供"}` : ""}。</p>
      <table><thead><tr><th>特征</th><th>本组</th>{baseline ? <th>全部候选</th> : null}</tr></thead><tbody>
        {distribution.buckets.map(bucket => <tr key={bucket.label}><th scope="row">{bucket.label}</th>
          <td>{distribution.key === "next_open_pct" ? openingBucket(bucket.count, distribution.valid_count, bucket.share) : <>{bucket.count}/{distribution.valid_count} · {percent(bucket.share)}</>}</td>
          {baseline ? <td>{distribution.key === "next_open_pct" ? openingBucket(bucket.baseline_count, distribution.baseline_valid_count, bucket.baseline_share) : <>{bucket.baseline_count === null ? "未提供" : `${bucket.baseline_count}/${distribution.baseline_valid_count}`}{bucket.baseline_share === null ? "" : ` · ${percent(bucket.baseline_share)}`}</>}</td> : null}
        </tr>)}
      </tbody></table>
      {distribution.note ? <p>{distribution.note}</p> : null}
    </section>;
    }) : <p>暂无可用特征分布。</p>}
  </details>;
}

function StockEvidence({ stocks, leaders }: { stocks: DigestStock[]; leaders: boolean }) {
  return <details className="digest-evidence digest-stocks"><summary>核对个股证据 · {stocks.length} 条</summary>
    {stocks.length ? stocks.map((stock, index) => <article key={`${stock.symbol}-${stock.first_board_date}-${index}`}>
      <div className="digest-stock-heading"><Link to={`/stocks/${encodeURIComponent(stock.symbol)}`}>{stock.name} <small>{stock.symbol}</small></Link>
        {leaders ? <strong>{stock.max_board_height === null ? "板高待核验" : `该轮最高 ${stock.max_board_height} 板`}</strong> : <strong className={stock.return_pct === null ? "" : stock.return_pct >= 0 ? "digest-up" : "digest-down"}>{stock.return_pct === null ? "涨跌待观察" : `累计 ${signed(stock.return_pct)}`}</strong>}
      </div>
      <p>首板 {stock.first_board_date ?? "待核验"}{!leaders ? ` · 已观察 ${countValue(stock.observed_days, " 个交易日")}` : ""}</p>
      <dl className="digest-stock-fields">
        <StockField label="首板位置" value={stock.position_label ?? "缺失"} />
        <StockField label="题材" value={stock.concepts.length ? stock.concepts.join("、") : "缺失"} />
        <StockField label="行业" value={stock.industry ?? "缺失"} />
        <StockField label="首板市值" value={stock.float_market_cap === null ? "缺失" : `${(stock.float_market_cap / 100_000_000).toFixed(1)} 亿元`} />
        <StockField label="首板封板" value={stock.first_limit_time ?? "缺失"} />
        <StockField label="首板炸板 / 换手" value={`${countValue(stock.break_count, " 次")} / ${stock.turnover_rate === null ? "缺失" : `${stock.turnover_rate.toFixed(1)}%`}`} />
        {!leaders ? <StockField label="首板 / 截止收盘" value={`${stock.first_close === null ? "缺失" : stock.first_close.toFixed(2)} / ${stock.cutoff_close === null ? "缺失" : stock.cutoff_close.toFixed(2)}`} /> : null}
      </dl>
      <div className="digest-stock-followup">
        <p><b>次日开盘</b> {stock.next_trade_date ?? "日期待核验"} · {stock.next_open_pct == null ? "涨幅缺失" : signed(stock.next_open_pct)} <small>相对首板收盘</small></p>
      </div>
      {leaders ? <div className="digest-second-board"><h4>二板 {stock.second_board_date ?? "待核验"}</h4><dl className="digest-stock-fields">
        {stock.next_trade_date === stock.second_board_date && stock.next_open_pct != null && stock.next_open_pct === stock.second_open_pct ? null : <StockField label="开盘涨幅" value={signed(stock.second_open_pct)} />}
        <StockField label="首次封板" value={stock.second_limit_time ?? "缺失"} />
        <StockField label="炸板次数" value={countValue(stock.second_break_count, " 次")} />
        <StockField label="换手率" value={stock.second_turnover_rate === null ? "缺失" : `${stock.second_turnover_rate.toFixed(1)}%`} />
        <StockField label="形态" value={stock.second_board_shape ?? "缺失"} />
      </dl></div> : null}
      {stock.data_missing.length ? <p className="digest-note">缺失说明：{stock.data_missing.join("；")}</p> : null}
    </article>) : <p>暂无可核对的个股证据。</p>}
  </details>;
}

function StockField({ label, value }: { label: string; value: string }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}
