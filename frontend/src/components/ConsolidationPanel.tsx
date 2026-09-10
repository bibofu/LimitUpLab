import { useEffect, useState } from "react";
import { Layers3, RefreshCcw } from "lucide-react";
import { Link } from "react-router-dom";
import { fetchConsolidationPool } from "../api";
import {
  consolidationEmptyMessage,
  consolidationReason,
  observationDisplayStocks,
  type ConsolidationPool,
  type ObservationStrategy,
} from "../consolidation";
import { stockDetailPath } from "../dashboardFormatters";
import { Panel } from "./Panel";

/**
 * Render the selected observation strategy's candidates, rejected examples, rules and data
 * gaps.
 */
export function ConsolidationPanel({ strategy }: { strategy: ObservationStrategy }) {
  const [pool, setPool] = useState<ConsolidationPool | null>(null);
  const isDrawdown = strategy === "drawdown";
  const [asOf, setAsOf] = useState("");
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const displayStocks = pool ? observationDisplayStocks(pool) : [];
  const showingNearMatches = Boolean(
    pool && pool.candidates.length === 0 && displayStocks.length > 0,
  );
  useEffect(/* Synchronize ConsolidationPanel with its current dependencies; any returned callback releases this effect's resources or invalidates stale work. */ () => {
    let active = true;
    setLoading(true);
    setError(null);
    void fetchConsolidationPool(asOf || undefined, strategy)
      .then(/* Apply the resolved asynchronous result to the current view state. */ (response) => { if (active) setPool(response); })
      .catch(/* Handle this asynchronous failure using the enclosing view's error/fallback state. */ (caught: unknown) => { if (active) setError(caught instanceof Error ? caught.message : "观察池加载失败"); })
      .finally(/* Release request state after either success or failure. */ () => { if (active) setLoading(false); });
    return /* Release or invalidate the enclosing effect's work when dependencies change or the view unmounts. */ () => { active = false; };
  }, [asOf, revision, strategy]);

  return (
    <div role="tabpanel" id={`${strategy}-panel`} aria-label={isDrawdown ? "高位回撤" : "缩量整理"}>
      <Panel title={isDrawdown ? "高位回撤" : "缩量整理"} icon={<Layers3 size={18} />} actions={
        <button type="button" className="consolidation-refresh" disabled={loading} onClick={/* Handle onClick for this control in ConsolidationPanel. */ () => setRevision(/* Compute revision from the latest React state to avoid overwriting intervening updates. */ (value) => value + 1)}>
          <RefreshCcw size={14} />刷新
        </button>
      }>
        <div className="consolidation-content">
          <div className="consolidation-heading">
            <div><strong>{isDrawdown ? "高位回撤观察池" : "缩量整理观察池"}</strong><p>{isDrawdown ? "观察近期涨停股从局部高点回落的幅度，尚未要求止跌确认。回撤为负表示收盘高于此前参考高点。" : "观察股票涨停后的价格与成交量变化。"}</p></div>
            <label>截至交易日
              <select aria-label={`${isDrawdown ? "高位回撤" : "缩量整理"}截至交易日`} value={asOf} onChange={/* Handle onChange for this control in ConsolidationPanel. */ (event) => setAsOf(event.target.value)}>
                <option value="">最新收盘</option>
                {(pool?.available_dates ?? []).map(/* Transform each entry in (pool?.available_dates ?? []) into the result used by ConsolidationPanel. */ (day) => <option value={day} key={day}>{day}</option>)}
              </select>
            </label>
          </div>
          {loading || (!error && pool && pool.strategy !== strategy) ? <p role="status">正在核对近期涨停与量价指标…</p>
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
                <ul>{pool.rules.map(/* Transform each entry in pool.rules into the result used by ConsolidationPanel. */ (rule) => <li key={rule}>{rule}</li>)}</ul>
              </details>
              {pool.data_missing.length > 0 && <p className="consolidation-warning">
                部分股票未能评价：{pool.data_missing.map(consolidationReason).join("；")}。
              </p>}
              {pool.candidates.length === 0 && <div className="discovery-state"><strong>暂无符合条件的候选</strong><p>{displayStocks.length > 0 ? `以下展示最接近条件的 ${displayStocks.length} 只股票，均未入选正式观察池。` : consolidationEmptyMessage(pool)}</p></div>}
              {displayStocks.length > 0 && <>
                <div><strong>{showingNearMatches ? "接近条件" : "符合条件股票"} · {displayStocks.length} 只</strong><p className="consolidation-note">{showingNearMatches ? "按未通过条件数量和超出阈值的距离排序；以下股票仍不符合全部条件。" : `仅展示同时满足全部${isDrawdown ? "高位回撤" : "缩量整理"}条件的股票。`}</p></div>
                <div className="consolidation-grid">
                  {displayStocks.map(/* Transform each entry in displayStocks into the result used by ConsolidationPanel. */ (candidate) => <article className="consolidation-card" key={candidate.symbol}>
                    <header><Link to={stockDetailPath(candidate.symbol, candidate.name)}>{candidate.name} <small>{candidate.symbol}</small></Link><span className={candidate.state === "rejected" ? "consolidation-badge-rejected" : "consolidation-badge-qualified"}>{showingNearMatches ? "接近条件" : candidate.state === "new" ? "首次符合" : "持续观察"}</span></header>
                    <p className="consolidation-note">涨停 {candidate.anchor_date}{candidate.confirmed_date ? ` · 首次确认 ${candidate.confirmed_date}` : " · 尚未同时满足形态条件"}</p>
                    <dl>
                      <div><dt>{isDrawdown ? "涨停后天数" : "整理天数"}</dt><dd>{candidate.consolidation_days} 日</dd></div>
                      {isDrawdown ? <>
                        <div><dt>高点回撤 · ≥10%</dt><dd>{candidate.drawdown_pct?.toFixed(2) ?? "—"}%</dd></div>
                        <div><dt>参考高点</dt><dd>{candidate.peak_price?.toFixed(2) ?? "—"} 元</dd></div>
                        <div><dt>高点日期</dt><dd>{candidate.peak_date ?? "—"}</dd></div>
                        <div><dt>期间量比 · 仅展示</dt><dd>{candidate.volume_ratio.toFixed(3)}</dd></div>
                      </> : <>
                        <div><dt>区间幅度 · ≤8%</dt><dd>{candidate.range_pct.toFixed(2)}%</dd></div>
                        <div><dt>整理期量比 · ≤0.75</dt><dd>{candidate.volume_ratio.toFixed(3)}</dd></div>
                        <div><dt>相对涨停 · −10%～+8%</dt><dd>{candidate.anchor_change_pct > 0 ? "+" : ""}{candidate.anchor_change_pct.toFixed(2)}%</dd></div>
                        <div><dt>整理区间</dt><dd>{candidate.range_low.toFixed(2)}–{candidate.range_high.toFixed(2)} 元</dd></div>
                      </>}
                      <div><dt>最新收盘</dt><dd>{candidate.close.toFixed(2)} 元</dd></div>
                    </dl>
                    <p>{candidate.reasons.join("；")}。</p>
                    {candidate.failed_conditions.length > 0 && <p className="consolidation-warning">未通过：{candidate.failed_conditions.map(consolidationReason).join("；")}。</p>}
                    <details><summary>数据来源与风险</summary><p>来源标签：{candidate.source}</p><ul>{candidate.risks.map(/* Transform each entry in candidate.risks into the result used by ConsolidationPanel. */ (risk) => <li key={risk}>{risk}</li>)}</ul></details>
                  </article>)}
                </div></>}
              {Object.keys(pool.exclusions).length > 0 && <details className="consolidation-rules"><summary>未入选及数据不足原因</summary><ul>{Object.entries(pool.exclusions).map(/* Transform each entry in Object.entries(pool.exclusions) into the result used by ConsolidationPanel. */ ([reason, count]) => <li key={reason}>{consolidationReason(reason)}：{count} 只</li>)}</ul><p>每只股票只记录首个未通过条件。</p></details>}
              <details className="consolidation-rules"><summary>研究口径与限制</summary><ul>{pool.warnings.map(/* Transform each entry in pool.warnings into the result used by ConsolidationPanel. */ (warning) => <li key={warning}>{warning}</li>)}</ul><p>规则版本 {pool.strategy_version} · 计算时间 {new Date(pool.generated_at).toLocaleString("zh-CN")}</p></details>
            </> : null}
        </div>
      </Panel>
    </div>
  );
}
