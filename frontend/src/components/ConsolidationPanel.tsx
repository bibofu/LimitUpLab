import { useEffect, useState } from "react";
import { Layers3, RefreshCcw } from "lucide-react";
import { Link } from "react-router-dom";
import { fetchConsolidationPool } from "../api";
import { consolidationEmptyMessage, consolidationReason, type ConsolidationPool } from "../consolidation";
import { stockDetailPath } from "../dashboardFormatters";
import { Panel } from "./Panel";

export function ConsolidationPanel() {
  const [pool, setPool] = useState<ConsolidationPool | null>(null);
  const [asOf, setAsOf] = useState("");
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    void fetchConsolidationPool(asOf || undefined)
      .then((response) => { if (active) setPool(response); })
      .catch((caught: unknown) => { if (active) setError(caught instanceof Error ? caught.message : "观察池加载失败"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [asOf, revision]);

  return (
    <div role="tabpanel" id="consolidation-panel" aria-label="涨停后缩量整理">
      <Panel title="涨停后缩量整理" icon={<Layers3 size={18} />} actions={
        <button type="button" className="consolidation-refresh" disabled={loading} onClick={() => setRevision((value) => value + 1)}>
          <RefreshCcw size={14} />刷新
        </button>
      }>
        <div className="consolidation-content">
          <div className="consolidation-heading">
            <div><strong>涨停后的整理观察池</strong><p>观察强势股在涨停后的价格收敛与成交量变化。</p></div>
            <label>截至交易日
              <select aria-label="整理策略截至交易日" value={asOf} onChange={(event) => setAsOf(event.target.value)}>
                <option value="">最新收盘</option>
                {(pool?.available_dates ?? []).map((day) => <option value={day} key={day}>{day}</option>)}
              </select>
            </label>
          </div>
          {loading ? <p role="status">正在核对近期涨停、整理区间与成交量…</p>
            : error ? <p role="alert" className="consolidation-warning">{error}，可点击刷新重试。</p>
            : pool ? <>
              <div className="consolidation-meta">
                <span>数据截至 <strong>{pool.data_as_of ?? "暂无"}</strong></span>
                <span>近期涨停池 {pool.pool_count} 只</span>
                <span>可评价 {pool.evaluated_count} 只</span>
                <span>符合 {pool.candidates.length} 只</span>
              </div>
              <p className="consolidation-note">
                {pool.data_as_of !== pool.latest_data_date ? "正在查看历史日期重算结果。" : "基于最新本地收盘数据重算，供下一交易日盘前观察。"}
                首次确认按历史行情推算；“持续观察”不计作新的首次信号。当前为研究版观察池。
              </p>
              <details className="consolidation-rules" open>
                <summary>筛选条件</summary>
                <ul>{pool.rules.map((rule) => <li key={rule}>{rule}</li>)}</ul>
              </details>
              {pool.data_missing.length > 0 && <p className="consolidation-warning">
                部分股票未能评价：{pool.data_missing.map(consolidationReason).join("；")}。
              </p>}
              {pool.candidates.length === 0 && <div className="discovery-state"><strong>暂无符合条件的候选</strong><p>{consolidationEmptyMessage(pool)}</p></div>}
              {pool.evaluated_stocks.length > 0 && <>
                <div><strong>可评价股票 · {pool.evaluated_stocks.length} 只</strong><p className="consolidation-note">已通过研究范围、整理天数与数据质量检查；符合项优先展示，未符合项列出全部形态条件差距。</p></div>
                <div className="consolidation-grid">
                  {pool.evaluated_stocks.map((candidate) => <article className="consolidation-card" key={candidate.symbol}>
                    <header><Link to={stockDetailPath(candidate.symbol, candidate.name)}>{candidate.name} <small>{candidate.symbol}</small></Link><span className={candidate.state === "rejected" ? "consolidation-badge-rejected" : "consolidation-badge-qualified"}>{candidate.state === "rejected" ? "未符合" : candidate.state === "new" ? "首次符合" : "持续观察"}</span></header>
                    <p className="consolidation-note">涨停 {candidate.anchor_date}{candidate.confirmed_date ? ` · 首次确认 ${candidate.confirmed_date}` : " · 尚未同时满足形态条件"}</p>
                    <dl>
                      <div><dt>整理天数</dt><dd>{candidate.consolidation_days} 日</dd></div>
                      <div><dt>区间幅度 · ≤8%</dt><dd>{candidate.range_pct.toFixed(2)}%</dd></div>
                      <div><dt>整理期量比 · ≤0.75</dt><dd>{candidate.volume_ratio.toFixed(3)}</dd></div>
                      <div><dt>相对涨停 · −10%～+8%</dt><dd>{candidate.anchor_change_pct > 0 ? "+" : ""}{candidate.anchor_change_pct.toFixed(2)}%</dd></div>
                      <div><dt>整理区间</dt><dd>{candidate.range_low.toFixed(2)}–{candidate.range_high.toFixed(2)} 元</dd></div>
                      <div><dt>最新收盘</dt><dd>{candidate.close.toFixed(2)} 元</dd></div>
                    </dl>
                    <p>{candidate.reasons.join("；")}。</p>
                    {candidate.failed_conditions.length > 0 && <p className="consolidation-warning">未通过：{candidate.failed_conditions.map(consolidationReason).join("；")}。</p>}
                    <details><summary>数据来源与风险</summary><p>来源标签：{candidate.source}</p><ul>{candidate.risks.map((risk) => <li key={risk}>{risk}</li>)}</ul></details>
                  </article>)}
                </div></>}
              {Object.keys(pool.exclusions).length > 0 && <details className="consolidation-rules"><summary>未入选及数据不足原因</summary><ul>{Object.entries(pool.exclusions).map(([reason, count]) => <li key={reason}>{consolidationReason(reason)}：{count} 只</li>)}</ul><p>每只股票只记录首个未通过条件。</p></details>}
              <details className="consolidation-rules"><summary>研究口径与限制</summary><ul>{pool.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul><p>规则版本 {pool.strategy_version} · 计算时间 {new Date(pool.generated_at).toLocaleString("zh-CN")}</p></details>
            </> : null}
        </div>
      </Panel>
    </div>
  );
}
