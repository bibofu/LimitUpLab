import { BarChart3, Layers3, LineChart, LoaderCircle, MapPin, ShieldAlert, TrendingDown } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { ConsolidationPanel } from "../components/ConsolidationPanel";
import { Panel } from "../components/Panel";
import { RecommendationNewsBoard } from "../components/RecommendationNewsBoard";
import { premarketStrategyFromParam, type PremarketStrategy } from "../consolidation";
import { stockDetailPath } from "../dashboardFormatters";
import { useRecommendationIntelligence } from "../hooks/useRecommendationIntelligence";
import { recommendationSnapshotView } from "../recommendationSnapshot";
import { displayRelayPositionLabel, latestRelayCandidates } from "../relayRanking";
import type { RecommendationIntelligenceItem } from "../types";


/**
 * Read the requested strategy from the URL and open its research workspace.
 */
export function PremarketPage() {
  return (
    <div className="premarket-page">
      <RecommendationNewsBoard />
      <PremarketStrategyWorkspace />
    </div>
  );
}


/**
 * Coordinate the strategy selector and the corresponding relay or observation-pool view.
 */
function PremarketStrategyWorkspace() {
  /** Keep each strategy independent while sharing a bookmarkable workspace. */

  const [strategyParams, setStrategyParams] = useSearchParams();
  const mode = premarketStrategyFromParam(strategyParams.get("strategy"));
  const setMode = /* Update the strategy in the URL so navigation and reloads preserve the selected research view. */ (value: PremarketStrategy) => {
    setStrategyParams((previous) => { const next = new URLSearchParams(previous); next.set("strategy", value); return next; });
  };
  const {
    intelligence,
    loading: intelligenceLoading,
    error: intelligenceError,
  } = useRecommendationIntelligence();
  const [snapshotNow, setSnapshotNow] = useState(() => new Date());
  useEffect(() => {
    // Advance cutoff labels even when a refresh fails or returns the same snapshot.
    const timer = window.setInterval(() => setSnapshotNow(new Date()), 15 * 1000);
    return () => window.clearInterval(timer);
  }, []);
  const snapshot = intelligence ? recommendationSnapshotView(intelligence, snapshotNow) : null;

  const strategyCandidates = latestRelayCandidates(
    intelligence?.items ?? [],
    intelligence?.relay_display_limit ?? 10,
  );
  const draftCandidates = strategyCandidates.map((item, index) => ({
    ...item,
    rank: index + 1,
  }));

  return (
    <section className="premarket-workspace">
      <div aria-label="盘前策略" className="strategy-switch" role="tablist">
        <button
          aria-selected={mode === "relay"}
          className={mode === "relay" ? "active" : undefined}
          onClick={() => setMode("relay")}
          role="tab"
          type="button"
        >
          <Layers3 size={16} />
          一进二接力
        </button>
        <button
          aria-selected={mode === "consolidation"}
          aria-controls="consolidation-panel"
          className={mode === "consolidation" ? "active" : undefined}
          onClick={() => setMode("consolidation")}
          role="tab"
          type="button"
        >
          <LineChart size={16} />缩量整理
        </button>
        <button
          aria-selected={mode === "drawdown"}
          aria-controls="drawdown-panel"
          className={mode === "drawdown" ? "active" : undefined}
          onClick={() => setMode("drawdown")}
          role="tab"
          type="button"
        >
          <TrendingDown size={16} />高位回撤
        </button>
      </div>
      {mode !== "relay" ? <ConsolidationPanel strategy={mode} /> : intelligenceLoading ? (
        <PremarketRankingStatePanel
          message="正在读取统一的盘前排名与证据"
          state="loading"
        />
      ) : !intelligence || !snapshot ? (
        <PremarketRankingStatePanel
          message={intelligenceError ?? "盘前动态榜暂不可用"}
          state="error"
        />
      ) : draftCandidates.length > 0 ? (
        <RecommendationDraftPanel
          candidates={draftCandidates}
          snapshot={snapshot}
          refreshError={intelligenceError}
        />
      ) : (
        <PremarketEmptySnapshotPanel
          snapshot={snapshot}
          refreshError={intelligenceError}
        />
      )}
    </section>
  );
}


/**
 * Explain the current ranking's data/publication state before presenting candidate rows.
 */
function PremarketRankingStatePanel({
  message,
  state,
}: {
  message: string;
  state: "loading" | "error" | "empty";
}) {
  const icon = state === "error"
    ? <ShieldAlert size={20} />
    : <LoaderCircle className={state === "loading" ? "spin" : undefined} size={20} />;
  return (
    <Panel
      title="一进二接力"
      icon={<BarChart3 size={18} />}
    >
      <div className={`discovery-state${state === "error" ? " discovery-state-error" : ""}`}>
        {icon}
        <span>{message}</span>
      </div>
    </Panel>
  );
}


/**
 * Keep empty results' target date, publication state and data warnings visible.
 */
function PremarketEmptySnapshotPanel({
  snapshot,
  refreshError,
}: {
  snapshot: ReturnType<typeof recommendationSnapshotView>;
  refreshError: string | null;
}) {
  return (
    <Panel
      title="一进二接力"
      icon={<ShieldAlert size={18} />}
    >
      <div className="rating-summary-panel recommendation-draft-panel">
        <strong>{snapshot.title} · {snapshot.targetLabel}</strong>
        <RecommendationSnapshotDetails snapshot={snapshot} refreshError={refreshError} />
        <p className="discovery-disclaimer">该快照没有可展示的盘前候选。</p>
      </div>
    </Panel>
  );
}


function RecommendationSnapshotDetails({
  snapshot,
  refreshError,
}: {
  snapshot: ReturnType<typeof recommendationSnapshotView>;
  refreshError: string | null;
}) {
  return (
    <div className="recommendation-snapshot-details">
      <div className="recommendation-snapshot-metadata">
        <span>候选基准日：{snapshot.baseLabel}</span>
        <span>快照更新：{snapshot.refreshedLabel}</span>
        {snapshot.finalizedLabel ? <span>固化时间：{snapshot.finalizedLabel}</span> : null}
      </div>
      {snapshot.notices.length > 0 ? (
        <div className="recommendation-snapshot-notice" role="status">
          {snapshot.notices.map((notice) => <p key={notice}>{notice}</p>)}
        </div>
      ) : null}
      {refreshError ? (
        <p className="recommendation-snapshot-notice" role="alert">刷新失败，保留上次快照：{refreshError}</p>
      ) : null}
      {snapshot.warnings.length > 0 ? (
        <details className="recommendation-snapshot-warnings" open>
          <summary>数据提示（{snapshot.warnings.length}）</summary>
          <ul>{snapshot.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
        </details>
      ) : null}
    </div>
  );
}


/**
 * Render candidate intelligence with its draft/final provenance, evidence adjustments and
 * missing-data explanations.
 */
function RecommendationDraftPanel({
  candidates,
  snapshot,
  refreshError,
}: {
  candidates: RecommendationIntelligenceItem[];
  snapshot: ReturnType<typeof recommendationSnapshotView>;
  refreshError: string | null;
}) {
  return (
    <Panel
      title="一进二接力"
      icon={<BarChart3 size={18} />}
    >
      <div className="rating-summary-panel recommendation-draft-panel">
        <div className="recommendation-draft-header">
          <div>
            <strong>
              {snapshot.title}
              {` Top${candidates.length} · ${snapshot.targetLabel}`}
            </strong>
            <span>基于收盘综合分与盘后新增信息的研究排序</span>
          </div>
        </div>
        <RecommendationSnapshotDetails snapshot={snapshot} refreshError={refreshError} />
        <div className="rating-top-list">
          {candidates.map((candidate) => {
            return (
            <Link
              className="rating-top-card"
              key={`${candidate.strategy}-${candidate.base_trade_date}-${candidate.symbol}`}
              to={stockDetailPath(candidate.symbol, candidate.name)}
            >
              <header>
                <div>
                  <span>Top {candidate.rank} · 收盘综合第 {candidate.base_rank}</span>
                  <strong>{candidate.name}</strong>
                  <small>{candidate.symbol}{candidate.sector ? ` / ${candidate.sector}` : ""}</small>
                </div>
                <div className="rating-top-score">
                  <b>{candidate.draft_score.toFixed(1)}</b>
                  <span className="rating-score-context">
                    收盘综合 {candidate.base_score.toFixed(1)} · 动态 {candidate.dynamic_adjustment >= 0 ? "+" : ""}{candidate.dynamic_adjustment.toFixed(1)}
                  </span>
                </div>
              </header>
              <div
                className={`rating-position-label${candidate.position_label ? "" : " is-missing"}`}
              >
                <MapPin aria-hidden="true" size={14} />
                <span>首板位置</span>
                <strong>{displayRelayPositionLabel(candidate.position_label)}</strong>
              </div>
              {candidate.update_reasons.length > 0 ? (
                <section className="rating-top-reasons">
                  <strong>收盘后新增信息</strong>
                  <ul>{candidate.update_reasons.slice(0, 3).map((reason) => <li key={reason}>{reason}</li>)}</ul>
                </section>
              ) : null}
              {candidate.close_information_reasons.length > 0 ? (
                <section className="rating-top-reasons">
                  <strong>收盘综合分已纳入</strong>
                  <ul>{candidate.close_information_reasons.slice(0, 2).map((reason) => <li key={reason}>{reason}</li>)}</ul>
                </section>
              ) : null}
              {candidate.latest_news[0] ? (
                <section className="discovery-catalyst">
                  <strong>相关资讯</strong>
                  <p>{candidate.latest_news[0].title}</p>
                </section>
              ) : null}
            </Link>
            );
          })}
        </div>
        <p className="discovery-disclaimer">
          {snapshot.disclaimer}
        </p>
      </div>
    </Panel>
  );
}
