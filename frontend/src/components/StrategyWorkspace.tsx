import { AlertTriangle, BarChart3, CalendarDays, FlaskConical, ListFilter } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import {
  fetchStrategies,
  fetchStrategyHistory,
  fetchStrategyLatest,
  fetchStrategyStatistics,
  fetchStrategyStock,
} from "../api";
import type {
  StrategyCatalogResponse,
  StrategyDefinition,
  StrategyHistoryResponse,
  StrategyRunSnapshot,
  StrategyStatisticsResponse,
  StrategyStockResponse,
} from "../types";
import { canShowStrategyRanking, orderStrategies } from "../strategyPresentation";

type DetailTab = "candidates" | "stock" | "statistics" | "method";

const maturityLabel = {
  exploratory: "探索研究",
  forward_validation: "前向验证",
  validated: "已验证",
} as const;

const outputLabel = {
  ranked_research: "研究排名",
  observation_pool: "观察池",
} as const;

export function StrategyWorkspace() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [catalog, setCatalog] = useState<StrategyCatalogResponse | null>(null);
  const [selectedId, setSelectedId] = useState(searchParams.get("strategy") ?? "relay_one_to_two");
  const [latest, setLatest] = useState<StrategyRunSnapshot | null>(null);
  const [history, setHistory] = useState<StrategyHistoryResponse | null>(null);
  const [statistics, setStatistics] = useState<StrategyStatisticsResponse | null>(null);
  const [stock, setStock] = useState<StrategyStockResponse | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [tab, setTab] = useState<DetailTab>("candidates");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchStrategies()
      .then((response) => {
        if (!active) return;
        const ordered = { ...response, strategies: orderStrategies(response.strategies) };
        setCatalog(ordered);
        if (!ordered.strategies.some((item) => item.strategy_id === selectedId)) {
          setSelectedId(ordered.strategies[0]?.strategy_id ?? "relay_one_to_two");
        }
      })
      .catch((caught: unknown) => active && setError(errorMessage(caught)))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!catalog) return;
    let active = true;
    setLoading(true);
    setError(null);
    setLatest(null);
    setHistory(null);
    setStatistics(null);
    setStock(null);
    setSelectedSymbol(null);
    Promise.all([
      fetchStrategyLatest(selectedId),
      fetchStrategyHistory(selectedId),
      fetchStrategyStatistics(selectedId),
    ])
      .then(([latestResponse, historyResponse, statisticsResponse]) => {
        if (!active) return;
        setLatest(latestResponse);
        setHistory(historyResponse);
        setStatistics(statisticsResponse);
      })
      .catch((caught: unknown) => active && setError(errorMessage(caught)))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [catalog, selectedId]);

  const selected = useMemo(
    () => catalog?.strategies.find((item) => item.strategy_id === selectedId) ?? null,
    [catalog, selectedId],
  );

  const chooseStrategy = (strategyId: string) => {
    setSelectedId(strategyId);
    setSearchParams({ strategy: strategyId }, { replace: true });
    setTab("candidates");
  };

  const openStock = (symbol: string) => {
    setSelectedSymbol(symbol);
    setTab("stock");
    setStock(null);
    fetchStrategyStock(selectedId, symbol, latest?.data_as_of)
      .then(setStock)
      .catch((caught: unknown) => setError(errorMessage(caught)));
  };

  return (
    <main className="strategy-workspace">
      <section className="strategy-hero">
        <div>
          <span className="eyebrow">Post-limit strategy lab</span>
          <h1>涨停后策略列表</h1>
          <p>所有候选从可验证涨停事件出发。排名策略与探索观察池分开呈现，不建立跨策略总榜。</p>
        </div>
        <div className="strategy-hero-note">
          <FlaskConical size={18} />
          <span>收盘后研究 · 证据可追溯 · 不构成投资建议</span>
        </div>
      </section>

      {error ? <div className="strategy-error"><AlertTriangle size={17} />{error}</div> : null}

      <section className="strategy-layout">
        <aside className="strategy-catalog" aria-label="策略列表">
          {catalog?.strategies.map((strategy) => (
            <button
              type="button"
              key={strategy.strategy_id}
              className={`strategy-card${strategy.strategy_id === selectedId ? " is-active" : ""}`}
              onClick={() => chooseStrategy(strategy.strategy_id)}
            >
              <span className="strategy-card-title">{strategy.name}</span>
              <span className="strategy-card-badges">
                <em>{maturityLabel[strategy.maturity]}</em>
                <em>{outputLabel[strategy.output_type]}</em>
              </span>
              <span>{strategy.lifecycle_stage}</span>
              <small>数据日 {strategy.latest_data_date ?? "待生成"} · 样本 {strategy.latest_sample_size}</small>
            </button>
          ))}
          {loading && !catalog ? <div className="strategy-empty">正在读取策略目录…</div> : null}
        </aside>

        <section className="strategy-detail">
          {selected ? <StrategyHeader strategy={selected} latest={latest} /> : null}
          <nav className="strategy-tabs" aria-label="策略详情区域">
            <Tab active={tab === "candidates"} onClick={() => setTab("candidates")} icon={<ListFilter size={16} />} label="候选与观察" />
            <Tab active={tab === "stock"} onClick={() => setTab("stock")} icon={<CalendarDays size={16} />} label="单股路径" />
            <Tab active={tab === "statistics"} onClick={() => setTab("statistics")} icon={<BarChart3 size={16} />} label="历史统计" />
            <Tab active={tab === "method"} onClick={() => setTab("method")} icon={<FlaskConical size={16} />} label="方法说明" />
          </nav>

          {loading ? <section className="panel strategy-panel"><div className="strategy-empty">正在读取策略快照…</div></section> : null}
          {!loading && selected && tab === "candidates" ? (
            <CandidatePanel strategy={selected} run={latest} onStock={openStock} />
          ) : null}
          {!loading && selected && tab === "stock" ? (
            <StockPanel symbol={selectedSymbol} stock={stock} onBack={() => setTab("candidates")} />
          ) : null}
          {!loading && selected && tab === "statistics" ? (
            <StatisticsPanel history={history} statistics={statistics} />
          ) : null}
          {!loading && selected && tab === "method" ? <MethodPanel strategy={selected} /> : null}
        </section>
      </section>
    </main>
  );
}

function StrategyHeader({ strategy, latest }: { strategy: StrategyDefinition; latest: StrategyRunSnapshot | null }) {
  return (
    <header className="strategy-detail-header">
      <div>
        <span className="eyebrow">{strategy.strategy_id} · {strategy.version}</span>
        <h2>{strategy.name}</h2>
        <p>{strategy.description}</p>
      </div>
      <dl>
        <div><dt>成熟度</dt><dd>{maturityLabel[strategy.maturity]}</dd></div>
        <div><dt>输出</dt><dd>{outputLabel[strategy.output_type]}</dd></div>
        <div><dt>数据截止</dt><dd>{latest?.data_as_of ?? strategy.latest_data_date ?? "待生成"}</dd></div>
        <div><dt>样本</dt><dd>{latest?.candidate_count ?? strategy.latest_sample_size}</dd></div>
      </dl>
    </header>
  );
}

function Tab({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: ReactNode; label: string }) {
  return <button type="button" className={active ? "is-active" : ""} onClick={onClick}>{icon}{label}</button>;
}

function CandidatePanel({ strategy, run, onStock }: {
  strategy: StrategyDefinition;
  run: StrategyRunSnapshot | null;
  onStock: (symbol: string) => void;
}) {
  const candidates = run?.payload.candidates ?? [];
  return (
    <section className="panel strategy-panel">
      <div className="strategy-section-title">
        <div>
          <h3>{canShowStrategyRanking(strategy.output_type) ? "不可变研究排名" : "不可变观察池"}</h3>
          <p>{canShowStrategyRanking(strategy.output_type) ? "仅一进二具备当前排名资格。" : "探索策略不展示概率、胜率或跨策略名次。"}</p>
        </div>
        <span>{run?.signal_date ?? "—"}</span>
      </div>
      {candidates.length ? <div className="strategy-candidates">
        {candidates.map((candidate, index) => {
          const evidence = stringList(candidate.reasons ?? candidate.evidence ?? candidate.trigger_reasons);
          const missing = stringList(candidate.data_missing);
          return (
            <article className="strategy-candidate" key={`${candidate.symbol}-${index}`}>
              <div className="strategy-candidate-main">
                {canShowStrategyRanking(strategy.output_type) ? <strong className="strategy-rank">#{candidate.rank ?? index + 1}</strong> : null}
                <div><h4>{candidate.name || candidate.symbol} <small>{candidate.symbol}</small></h4><p>涨停锚点 {candidate.anchor_date}</p></div>
                {canShowStrategyRanking(strategy.output_type) && candidate.score != null ? <b>{String(candidate.score)} 分</b> : null}
              </div>
              {evidence.length ? <ul>{evidence.slice(0, 3).map((item) => <li key={item}>{item}</li>)}</ul> : null}
              {missing.length ? <p className="strategy-missing">缺失：{missing.join("、")}</p> : null}
              <button type="button" onClick={() => onStock(String(candidate.symbol))}>查看逐日路径与证据</button>
            </article>
          );
        })}
      </div> : <div className="strategy-empty">当前数据截止日没有符合规则的样本；空池不会由默认值补齐。</div>}
      {stringList(run?.payload.warnings).map((warning) => <p className="strategy-warning" key={warning}>{warning}</p>)}
    </section>
  );
}

function StockPanel({ symbol, stock, onBack }: { symbol: string | null; stock: StrategyStockResponse | null; onBack: () => void }) {
  if (!symbol) return <section className="panel strategy-panel"><div className="strategy-empty">先从“候选与观察”中选择一只股票。</div></section>;
  if (!stock) return <section className="panel strategy-panel"><div className="strategy-empty">正在读取 {symbol} 的涨停锚点与逐日路径…</div></section>;
  const rows = Array.isArray(stock.path.rows) ? stock.path.rows : Array.isArray(stock.path.path) ? stock.path.path : [];
  return (
    <section className="panel strategy-panel">
      <div className="strategy-section-title"><div><h3>{stock.candidate?.name || symbol} · {symbol}</h3><p>数据截止 {stock.data_as_of} · 涨停锚点 {stock.candidate?.anchor_date ?? "未入选当前快照"}</p></div><button type="button" onClick={onBack}>返回候选</button></div>
      {rows.length ? <div className="strategy-path-table"><table><thead><tr><th>交易日</th><th>阶段</th><th>收盘</th><th>涨跌幅</th><th>状态</th></tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.trade_date ?? row.date}-${index}`}><td>{String(row.trade_date ?? row.date ?? "—")}</td><td>{String(row.day_label ?? row.offset ?? "—")}</td><td>{display(row.close)}</td><td>{display(row.change_pct)}</td><td>{String(row.status ?? "已记录")}</td></tr>)}</tbody></table></div> : <pre className="strategy-json">{JSON.stringify(stock.path, null, 2)}</pre>}
      {stringList(stock.candidate?.data_missing).length ? <p className="strategy-missing">缺失：{stringList(stock.candidate?.data_missing).join("、")}</p> : null}
    </section>
  );
}

function StatisticsPanel({ history, statistics }: { history: StrategyHistoryResponse | null; statistics: StrategyStatisticsResponse | null }) {
  return <div className="strategy-stack"><section className="panel strategy-panel"><div className="strategy-section-title"><div><h3>描述性统计与完整度</h3><p>统计只描述已成熟样本；样本不可比时不输出策略优劣。</p></div></div><pre className="strategy-json">{JSON.stringify(statistics?.statistics ?? {}, null, 2)}</pre></section><section className="panel strategy-panel"><div className="strategy-section-title"><div><h3>历史运行覆盖</h3><p>每个策略版本与信号日的首次结果保持不可变。</p></div></div><div className="strategy-history">{history?.runs.map((run) => <div key={run.run_id}><span>{run.signal_date}</span><span>{run.strategy_version}</span><span>{run.status}</span><strong>{run.candidate_count} 样本</strong></div>)}</div></section></div>;
}

function MethodPanel({ strategy }: { strategy: StrategyDefinition }) {
  return <section className="panel strategy-panel"><div className="strategy-method"><section><h3>候选范围</h3><p>{strategy.universe}</p><p>{strategy.lifecycle_stage}</p></section><section><h3>数据截止契约</h3><p>{strategy.cutoff_contract}</p><p>{strategy.missing_data_policy}</p></section><section><h3>所需证据</h3><ul>{strategy.required_data.map((item) => <li key={item}>{item}</li>)}</ul></section><section><h3>Outcome 与晋级</h3><p>{strategy.outcome_metrics.join("、")}</p><p>{strategy.promotion_gate}</p></section></div></section>;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function display(value: unknown): string {
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  return value == null ? "—" : String(value);
}

function errorMessage(caught: unknown): string {
  return caught instanceof Error ? caught.message : "策略数据加载失败";
}
