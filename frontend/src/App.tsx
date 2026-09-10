import {
  BarChart3,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  Flame,
  Layers3,
  LineChart,
  LoaderCircle,
  MapPin,
  Minus,
  Newspaper,
  RefreshCcw,
  ShieldAlert,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";

import { AgentChatDock } from "./components/AgentChatDock";
import { ConsolidationPanel } from "./components/ConsolidationPanel";
import { Panel } from "./components/Panel";
import { ReviewDashboard } from "./components/ReviewDashboard";
import {
  formatAmount,
  formatNetAmount,
  formatOptionalPercent,
  formatPercent,
  formatSigned,
  stockDetailPath,
} from "./dashboardFormatters";
import {
  MarketKLineChart,
  type MarketCandleBar,
} from "./components/MarketKLineChart";
import {
  displayRelayPositionLabel,
  latestRelayCandidates,
  rankedRelayCandidates,
  sortFirstBoardByRelayRanking,
} from "./relayRanking";
import {
  fetchContinuedBoardEvents,
  fetchDailyBoardPromotion,
  fetchFirstBoardRatings,
  fetchFinanceNews,
  fetchRecommendationIntelligence,
  fetchFailedLimitUpEvents,
  fetchFirstBoardEvents,
  fetchMarketSummary,
  fetchRecentLimitUpEvents,
  fetchStockEvent,
  fetchStockIntradayHistory,
  fetchStockMarketData,
  fetchStockNews,
  fetchStockTradingDayKLine,
} from "./api";
import type {
  DailyBoardPromotionStat,
  FirstBoardRating,
  FirstBoardRatingsResponse,
  FinanceNewsPage,
  FinanceNewsItem,
  LimitUpEvent,
  MarketSummary,
  RecommendationIntelligenceItem,
  RecommendationIntelligenceResponse,
  StockCloseSnapshot,
  StockIntradayHistoryResponse,
  StockIntradayKLineBar,
  StockKLineBar,
  StockNewsFacts,
  StockPositionAssessment,
} from "./types";
import {
  toFiveDayIntradayCandleBars,
  toIntradayCandleBars,
} from "./intradayChart";
import {
  premarketStrategyFromParam,
  type PremarketStrategy,
} from "./consolidation";

type ViewKey = "overview" | "recommendation" | "review" | "pool" | "first" | "continued" | "failed" | "recent";
type StockListViewKey = "first" | "continued" | "failed";

interface DashboardData {
  summary: MarketSummary;
  firstBoard: LimitUpEvent[];
  continuedBoard: LimitUpEvent[];
  failed: LimitUpEvent[];
  recent: LimitUpEvent[];
  firstBoardRatings: FirstBoardRatingsResponse;
  dailyBoardPromotion: DailyBoardPromotionStat[];
}

const viewMeta: Record<ViewKey, { title: string; eyebrow: string }> = {
  overview: { title: "短线市场概况", eyebrow: "Overview" },
  recommendation: { title: "盘前推荐", eyebrow: "Pre-market Picks" },
  review: { title: "市场复盘", eyebrow: "Review" },
  pool: { title: "涨停池", eyebrow: "Limit-Up Pool" },
  first: { title: "首板票", eyebrow: "First Board" },
  continued: { title: "连板票", eyebrow: "Continued Board" },
  failed: { title: "炸板票", eyebrow: "Failed Limit-Up" },
  recent: { title: "近七个交易日涨停票复盘", eyebrow: "Recent Limit-Up" },
};

const routeToView: Record<string, ViewKey> = {
  "/": "overview",
  "/recommendations": "recommendation",
  "/review": "review",
  "/stocks/limit-up-pool": "pool",
  "/stocks/first-board": "first",
  "/stocks/continued-board": "continued",
  "/stocks/failed": "failed",
  "/stocks/recent-limit-up": "recent",
};

const stockListPaths = new Set([
  "/stocks/limit-up-pool",
  "/stocks/first-board",
  "/stocks/continued-board",
  "/stocks/failed",
  "/stocks/recent-limit-up",
]);

const primaryNavigation = [
  { to: "/", label: "首页", end: true },
  { to: "/recommendations", label: "盘前推荐", end: true },
  { to: "/review", label: "复盘", end: true },
  { to: "/stocks/limit-up-pool", label: "涨停池", end: true },
];

const agentWorkspaceHiddenPaths = new Set([
  "/stocks/first-board",
  "/stocks/continued-board",
  "/stocks/failed",
  "/stocks/recent-limit-up",
  "/stocks/limit-up-pool",
  "/recommendations",
  "/review",
]);

/**
 * Load the dashboard datasets and route between the market, review and stock-detail
 * workspaces. The initial Promise.all batch shares one loading/error state and must complete
 * before the combined data is displayed.
 */
export function App() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const location = useLocation();
  const activeView = routeToView[location.pathname] ?? "overview";

  /** Load the dashboard summary and the list data needed by every route. */
  async function loadDashboard() {
    setLoading(true);
    setError(null);

    try {
      const [
        summary,
        firstBoard,
        continuedBoard,
        failed,
        recent,
        firstBoardRatings,
        dailyBoardPromotion,
      ] = await Promise.all([
        fetchMarketSummary(),
        fetchFirstBoardEvents(),
        fetchContinuedBoardEvents(),
        fetchFailedLimitUpEvents(),
        fetchRecentLimitUpEvents(7),
        fetchFirstBoardRatings(),
        fetchDailyBoardPromotion(5),
      ]);

      setData({
        summary,
        firstBoard,
        continuedBoard,
        failed,
        recent,
        firstBoardRatings,
        dailyBoardPromotion,
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载数据失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(/* Synchronize App with its current dependencies; any returned callback releases this effect's resources or invalidates stale work. */ () => {
    void loadDashboard();
  }, []);

  if (loading) {
    return <ShellState label="正在加载 LimitUpLab 数据..." />;
  }

  if (error || !data) {
    return (
      <ShellState
        label="数据加载失败"
        detail={error ?? "请确认后端服务已经启动"}
        onRetry={loadDashboard}
      />
    );
  }

  const isStockDetail = location.pathname.startsWith("/stocks/")
    && !stockListPaths.has(location.pathname);
  const showAgentWorkspace = !isStockDetail
    && !agentWorkspaceHiddenPaths.has(location.pathname);
  const activeMeta = isStockDetail
    ? { title: "个股详情", eyebrow: "Stock Detail" }
    : viewMeta[activeView];

  return (
    <main className="app-shell">
      <header className="app-header">
        <Link aria-label="返回市场概况" className="app-brand" to="/">
          <span className="app-brand-mark"><Flame aria-hidden="true" size={19} /></span>
          <span>
            <strong>LimitUpLab</strong>
            <small>首板研究 Agent</small>
          </span>
        </Link>

        <nav aria-label="主导航" className="primary-navigation">
          {primaryNavigation.map(/* Transform each entry in primaryNavigation into the result used by App. */ (item) => (
            <NavLink
              className={/* Handle className for this control in App. */ ({ isActive }) => (
                isActive
                || (item.to === "/stocks/limit-up-pool" && stockListPaths.has(location.pathname))
                  ? "active"
                  : undefined
              )}
              end={item.end}
              key={item.to}
              to={item.to}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="app-header-actions">
          <div className="data-as-of">
            <i aria-hidden="true" />
            <span>
              <small>数据日期</small>
              <strong>{data.summary.trade_date}</strong>
            </span>
          </div>
          <button
            aria-label="刷新全部数据"
            className="icon-button"
            onClick={loadDashboard}
            title="刷新数据"
            type="button"
          >
            <RefreshCcw size={18} />
          </button>
        </div>
      </header>

      {activeView !== "overview" || isStockDetail ? (
        <section className="topbar">
          <div>
            <p className="eyebrow">{activeMeta.eyebrow}</p>
            <h1>{activeMeta.title}</h1>
          </div>
        </section>
      ) : null}

      {activeView === "overview" && !isStockDetail ? (
        <MarketSnapshot summary={data.summary} />
      ) : null}

      {showAgentWorkspace ? (
        <AgentChatDock
          tradeDate={data.summary.trade_date}
        />
      ) : null}

      <Routes>
        <Route path="/" element={null} />
        <Route
          path="/recommendations"
          element={<PremarketPage />}
        />
        <Route
          path="/review"
          element={
            <ReviewDashboard
              dailyBoardPromotion={data.dailyBoardPromotion}
              latestTradeDate={data.summary.trade_date}
            />
          }
        />
        <Route path="/stocks/limit-up-pool" element={<LimitUpPool data={data} />} />
        <Route path="/stocks/first-board" element={<DetailView view="first" data={data} />} />
        <Route
          path="/stocks/continued-board"
          element={<DetailView view="continued" data={data} />}
        />
        <Route path="/stocks/failed" element={<DetailView view="failed" data={data} />} />
        <Route path="/stocks/recent-limit-up" element={<RecentLimitUp events={data.recent} />} />
        <Route path="/stocks/:symbol" element={<StockDetail data={data} />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </main>
  );
}

/**
 * Render the current market summary and its compact counts from backend facts.
 */
function MarketSnapshot({ summary }: { summary: MarketSummary }) {
  return (
    <section className="market-snapshot">
      <div className="market-snapshot-indices">
        <time className="market-snapshot-date" dateTime={summary.trade_date}>
          {summary.trade_date}
        </time>
        {summary.indices.map(/* Transform each entry in summary.indices into the result used by MarketSnapshot. */ (index) => (
          <article key={index.symbol}>
            <span>{index.name}</span>
            <div>
              <strong>{index.close.toFixed(2)}</strong>
              <b className={index.change_pct >= 0 ? "positive" : "negative"}>
                {formatSigned(index.change_pct, 2)}%
              </b>
            </div>
          </article>
        ))}
        {summary.indices.length === 0 ? <span>指数数据暂不可用</span> : null}
      </div>

      <nav aria-label="今日涨停概览" className="market-snapshot-entries">
        <div className="market-snapshot-ceiling">
          <span>最高连板</span>
          <strong>{summary.max_board_height}<small>板</small></strong>
        </div>
        <Link to="/stocks/first-board">
          <span><Flame size={15} />首板</span>
          <strong>{summary.first_board_count}<small>只</small></strong>
        </Link>
        <Link to="/stocks/continued-board">
          <span><Layers3 size={15} />连板</span>
          <strong>{summary.continued_board_count}<small>只</small></strong>
        </Link>
      </nav>
    </section>
  );
}

/**
 * Read the requested strategy from the URL and open its research workspace.
 */
function PremarketPage() {
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
    setStrategyParams(/* Compute strategy params from the latest React state to avoid overwriting intervening updates. */ (previous) => { const next = new URLSearchParams(previous); next.set("strategy", value); return next; });
  };
  const {
    intelligence,
    loading: intelligenceLoading,
    error: intelligenceError,
  } = useRecommendationIntelligence();

  const strategyCandidates = latestRelayCandidates(
    intelligence?.items ?? [],
    intelligence?.relay_display_limit ?? 10,
  );
  const draftCandidates = strategyCandidates.map(/* Transform each entry in strategyCandidates into the result used by PremarketStrategyWorkspace. */ (item, index) => ({
    ...item,
    rank: index + 1,
  }));

  return (
    <section className="premarket-workspace">
      <div aria-label="盘前策略" className="strategy-switch" role="tablist">
        <button
          aria-selected={mode === "relay"}
          className={mode === "relay" ? "active" : undefined}
          onClick={/* Handle onClick for this control in PremarketStrategyWorkspace. */ () => setMode("relay")}
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
          onClick={/* Handle onClick for this control in PremarketStrategyWorkspace. */ () => setMode("consolidation")}
          role="tab"
          type="button"
        >
          <LineChart size={16} />缩量整理
        </button>
        <button
          aria-selected={mode === "drawdown"}
          aria-controls="drawdown-panel"
          className={mode === "drawdown" ? "active" : undefined}
          onClick={/* Handle onClick for this control in PremarketStrategyWorkspace. */ () => setMode("drawdown")}
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
      ) : !intelligence ? (
        <PremarketRankingStatePanel
          message={intelligenceError ?? "盘前动态榜暂不可用"}
          state="error"
        />
      ) : draftCandidates.length > 0 ? (
        <RecommendationDraftPanel
          candidates={draftCandidates}
          intelligence={intelligence}
        />
      ) : intelligence.stage === "missed_cutoff" ? (
        <PremarketCutoffMissedPanel intelligence={intelligence} />
      ) : (
        <PremarketRankingStatePanel
          message="当前目标交易日没有可展示的盘前候选"
          state="empty"
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
 * Explain why the publication cutoff was missed and expose the available research state.
 */
function PremarketCutoffMissedPanel({
  intelligence,
}: {
  intelligence: RecommendationIntelligenceResponse;
}) {
  const targetLabel = intelligence.target_trade_date ?? "今日";
  const lastSafeRefresh = intelligence.items.length > 0
    ? new Date(intelligence.refreshed_at).toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
      })
    : null;
  return (
    <Panel
      title="一进二接力"
      icon={<ShieldAlert size={18} />}
    >
      <div className="discovery-state discovery-state-error">
        <ShieldAlert size={20} />
        <div>
          <strong>{targetLabel} 盘前榜未在开盘前固化</strong>
          <span>
            已停止排名更新，不会使用开盘后数据补算盘前结果。
            {lastSafeRefresh ? ` 最后一版盘前草稿更新于 ${lastSafeRefresh}，未作为正式 Top10 发布。` : ""}
          </span>
        </div>
      </div>
    </Panel>
  );
}

/**
 * Load and refresh candidate intelligence while keeping loading, failure and current-result
 * state together.
 */
function useRecommendationIntelligence() {
  const [intelligence, setIntelligence] = useState<RecommendationIntelligenceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(/* Synchronize useRecommendationIntelligence with its current dependencies; any returned callback releases this effect's resources or invalidates stale work. */ () => {
    let active = true;
    const refresh = /* Reload this panel's source data and update its success/error state for the next render. */ () => {
      void fetchRecommendationIntelligence()
        .then(/* Apply the resolved asynchronous result to the current view state. */ (response) => {
          if (active) {
            setIntelligence(response);
            setError(null);
          }
        })
        .catch(/* Handle this asynchronous failure using the enclosing view's error/fallback state. */ (caught: unknown) => {
          if (active) {
            setError(caught instanceof Error ? caught.message : "盘前动态榜加载失败");
          }
        })
        .finally(/* Release request state after either success or failure. */ () => {
          if (active) setLoading(false);
        });
    };
    refresh();
    const timer = window.setInterval(refresh, 60 * 1000);
    return /* Release or invalidate the enclosing effect's work when dependencies change or the view unmounts. */ () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  return { intelligence, loading, error };
}

/**
 * Render candidate intelligence with its draft/final provenance, evidence adjustments and
 * missing-data explanations.
 */
function RecommendationDraftPanel({
  candidates,
  intelligence,
}: {
  candidates: RecommendationIntelligenceItem[];
  intelligence: RecommendationIntelligenceResponse;
}) {
  const targetLabel = intelligence.target_trade_date
    ? `${intelligence.target_trade_date} 目标日`
    : "下一交易日";
  const snapshotCopy = intelligence.stage === "missed_cutoff"
    ? {
        subtitle: "展示开盘前最后一次可用快照；未使用开盘后数据补算",
        timePrefix: "最新盘前快照 · ",
        disclaimer: "开盘前服务未完成固化，当前展示最近一次盘前快照；该快照不是正式 Top10，且未使用开盘后信息补算。",
      }
    : intelligence.stage === "final"
      ? {
          subtitle: "收盘综合分固化基线，盘后按公告、龙虎榜与人气变化做有界修正",
          timePrefix: "开盘前已固化 · ",
          disclaimer: "该排序已于目标交易日开盘前固化，供盘后复盘使用。",
        }
      : {
          subtitle: "展示最新可用盘前快照，按收盘后新增信息做有界修正",
          timePrefix: "快照更新 · ",
          disclaimer: "当前展示最新可用的盘前研究快照；开盘后停止更新，只有开盘前固化的 Top10 才进入复盘。",
        };
  return (
    <Panel
      title="一进二接力"
      icon={<BarChart3 size={18} />}
    >
      <div className="rating-summary-panel recommendation-draft-panel">
        <div className="recommendation-draft-header">
          <div>
            <strong>
              {intelligence.stage === "final" ? "盘前固化候选" : "最新候选快照"}
              {` Top${candidates.length} · ${targetLabel}`}
            </strong>
            <span>{snapshotCopy.subtitle}</span>
          </div>
          <span className="recommendation-draft-time">
            {snapshotCopy.timePrefix}
            {new Date(intelligence.finalized_at ?? intelligence.refreshed_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}
          </span>
        </div>
        <div className="rating-top-list">
          {candidates.map(/* Transform each entry in candidates into the result used by RecommendationDraftPanel. */ (candidate) => {
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
                  <ul>{candidate.update_reasons.slice(0, 3).map(/* Transform each entry in candidate.update_reasons.slice(0, 3) into the result used by RecommendationDraftPanel. */ (reason) => <li key={reason}>{reason}</li>)}</ul>
                </section>
              ) : null}
              {candidate.close_information_reasons.length > 0 ? (
                <section className="rating-top-reasons">
                  <strong>收盘综合分已纳入</strong>
                  <ul>{candidate.close_information_reasons.slice(0, 2).map(/* Transform each entry in candidate.close_information_reasons.slice(0, 2) into the result used by RecommendationDraftPanel. */ (reason) => <li key={reason}>{reason}</li>)}</ul>
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
          {snapshotCopy.disclaimer}
        </p>
      </div>
    </Panel>
  );
}

interface RecommendationNewsViewItem {
  key: string;
  title: string;
  summary: string;
  publishedAt: string;
  source: string;
  url: string;
  category: string;
}

/**
 * Show paginated research news with its fetch state and source links.
 */
function RecommendationNewsBoard() {
  /** Paginate the factual 24-hour market feed without involving the LLM. */

  const [page, setPage] = useState(1);
  const [news, setNews] = useState<FinanceNewsPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(/* Synchronize RecommendationNewsBoard with its current dependencies; any returned callback releases this effect's resources or invalidates stale work. */ () => {
    let active = true;
    const refresh = /* Reload this panel's source data and update its success/error state for the next render. */ (showLoading: boolean) => {
      if (showLoading) setLoading(true);
      void fetchFinanceNews(page)
        .then(/* Apply the resolved asynchronous result to the current view state. */ (response) => {
          if (!active) return;
          setNews(response);
          setFailed(false);
          setPage(response.page);
        })
        .catch(/* Handle this asynchronous failure using the enclosing view's error/fallback state. */ () => {
          if (active) setFailed(true);
        })
        .finally(/* Release request state after either success or failure. */ () => {
          if (active) setLoading(false);
        });
    };
    refresh(true);
    const timer = window.setInterval(/* Handle the callback from window.setInterval within RecommendationNewsBoard. */ () => refresh(false), 5 * 60 * 1000);
    return /* Release or invalidate the enclosing effect's work when dependencies change or the view unmounts. */ () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [page]);

  const visibleNews = useMemo(
    /* Derive visibleNews from the listed dependencies, reusing it until those dependencies change. */ () => (news?.items ?? []).map(marketNewsViewItem),
    [news],
  );
  const pageNumbers = news ? paginationWindow(news.page, news.total_pages) : [];

  return (
    <section className="recommendation-news-board" aria-label="盘前实时新闻">
      <header className="recommendation-news-header">
        <div>
          <Newspaper size={18} />
          <span>
            <strong>实时新闻</strong>
            <small>
              {news
                ? `近 24 小时 · ${news.sources.join(" · ")} · 共 ${news.total} 条`
                : "近 24 小时 · 5 分钟自动更新"}
            </small>
          </span>
        </div>
        <span className="recommendation-news-refresh">5 分钟自动更新</span>
      </header>
      {loading && !news ? (
        <div className="recommendation-news-state">
          <LoaderCircle className="state-spinner" size={18} />
          正在获取最新新闻...
        </div>
      ) : null}
      {failed ? (
        <div className="recommendation-news-state">财经快讯暂时没有加载成功。</div>
      ) : null}
      {!loading && !failed && visibleNews.length === 0 ? (
        <div className="recommendation-news-state">
          近 24 小时没有获取到市场快讯。
        </div>
      ) : null}
      {visibleNews.length > 0 ? (
        <div className="recommendation-news-list">
          {visibleNews.map(/* Transform each entry in visibleNews into the result used by RecommendationNewsBoard. */ (item) => (
            <article className="recommendation-news-item" key={item.key}>
              <time dateTime={item.publishedAt}>{formatRecommendationNewsTime(item.publishedAt)}</time>
              <div className="recommendation-news-body">
                <a href={item.url} target="_blank" rel="noreferrer">
                  <strong>{item.title}</strong>
                  <ExternalLink size={13} aria-hidden="true" />
                </a>
                <span>{item.source} · {item.category}</span>
              </div>
            </article>
          ))}
        </div>
      ) : null}
      {news && news.total_pages > 1 ? (
        <footer className="recommendation-news-pagination" aria-label="市场快讯分页">
          <span>第 {news.page} / {news.total_pages} 页</span>
          <div>
            <button
              aria-label="上一页"
              disabled={news.page <= 1}
              onClick={/* Handle onClick for this control in RecommendationNewsBoard. */ () => setPage(/* Compute page from the latest React state to avoid overwriting intervening updates. */ (value) => Math.max(1, value - 1))}
              title="上一页"
              type="button"
            >
              <ChevronLeft size={15} />
            </button>
            {pageNumbers.map(/* Transform each entry in pageNumbers into the result used by RecommendationNewsBoard. */ (pageNumber) => (
              <button
                aria-current={pageNumber === news.page ? "page" : undefined}
                className={pageNumber === news.page ? "active" : undefined}
                key={pageNumber}
                onClick={/* Handle onClick for this control in RecommendationNewsBoard. */ () => setPage(pageNumber)}
                type="button"
              >
                {pageNumber}
              </button>
            ))}
            <button
              aria-label="下一页"
              disabled={news.page >= news.total_pages}
              onClick={/* Handle onClick for this control in RecommendationNewsBoard. */ () => setPage(/* Compute page from the latest React state to avoid overwriting intervening updates. */ (value) => Math.min(news.total_pages, value + 1))}
              title="下一页"
              type="button"
            >
              <ChevronRight size={15} />
            </button>
          </div>
        </footer>
      ) : null}
    </section>
  );
}

/**
 * Convert a market-news record into the common news-board display shape.
 */
function marketNewsViewItem(item: FinanceNewsItem): RecommendationNewsViewItem {
  return {
    key: `${item.source}-${item.url}-${item.published_at}`,
    title: item.title,
    summary: item.summary,
    publishedAt: item.published_at,
    source: item.source,
    url: item.url,
    category: item.category,
  };
}

/**
 * Choose the bounded range of page numbers around the current page.
 */
function paginationWindow(current: number, total: number): number[] {
  const visible = Math.min(5, total);
  const start = Math.max(1, Math.min(current - 2, total - visible + 1));
  return Array.from({ length: visible }, /* Handle the callback from Array.from within paginationWindow. */ (_, index) => start + index);
}

/**
 * Render the publication time used by the intelligence news board.
 */
function formatRecommendationNewsTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value.slice(5, 16).replace("T", " ");
  const now = new Date();
  const sameDay = parsed.toDateString() === now.toDateString();
  return new Intl.DateTimeFormat("zh-CN", {
    month: sameDay ? undefined : "2-digit",
    day: sameDay ? undefined : "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

/**
 * Find the current intelligence item for the requested stock identity.
 */
function recommendationIntelligenceFor(
  response: RecommendationIntelligenceResponse | null,
  strategy: "relay",
  symbol: string,
) {
  return response?.items.find(
    /* Locate the entry matching the active identity/time used by recommendationIntelligenceFor. */ (item) => item.strategy === strategy && item.symbol === symbol,
  ) ?? null;
}

/**
 * Choose the dashboard detail panel corresponding to the active view.
 */
function DetailView({ view, data }: { view: StockListViewKey; data: DashboardData }) {
  /** Render one of the latest-day stock list views. */

  const eventsByView: Record<StockListViewKey, LimitUpEvent[]> = {
    first: data.firstBoard,
    continued: data.continuedBoard,
    failed: data.failed,
  };

  if (view === "first") {
    return (
      <FirstBoardPoolView
        events={data.firstBoard}
        initialRatings={data.firstBoardRatings}
      />
    );
  }

  return (
    <Panel title={viewMeta[view].title} icon={detailIcon(view)}>
      <StockTable events={eventsByView[view]} variant={view} />
    </Panel>
  );
}

/**
 * Present the first-board pool using the supplied ratings and research ranking.
 */
function FirstBoardPoolView({
  events,
  initialRatings,
}: {
  events: LimitUpEvent[];
  initialRatings: FirstBoardRatingsResponse;
}) {
  const [ratings, setRatings] = useState(initialRatings);
  const { intelligence } = useRecommendationIntelligence();
  const tradeDate = events[0]?.trade_date;
  const relayRanking = rankedRelayCandidates(
    intelligence?.items ?? [],
    tradeDate,
  );

  useEffect(/* Synchronize FirstBoardPoolView with its current dependencies; any returned callback releases this effect's resources or invalidates stale work. */ () => {
    let active = true;
    if (!tradeDate) return /* Release or invalidate the enclosing effect's work when dependencies change or the view unmounts. */ () => { active = false; };
    void fetchFirstBoardRatings(tradeDate, true)
      .then(/* Apply the resolved asynchronous result to the current view state. */ (response) => {
        if (active) setRatings(response);
      })
      .catch(/* Handle this asynchronous failure using the enclosing view's error/fallback state. */ () => {
        // Keep persisted prediction scores as a partial ordering fallback.
      });
    return /* Release or invalidate the enclosing effect's work when dependencies change or the view unmounts. */ () => { active = false; };
  }, [tradeDate]);

  return (
    <Panel title="首板票" icon={detailIcon("first")}>
      {relayRanking.length > 0 && intelligence ? (
        <div className="pool-ranking-status">
          <strong>动态一进二 Top10 已同步</strong>
          <span>
            {new Date(intelligence.refreshed_at).toLocaleTimeString("zh-CN", {
              hour: "2-digit",
              minute: "2-digit",
            })}
            更新；前 10 名与盘前推荐一致
          </span>
        </div>
      ) : null}
      <StockTable
        events={events}
        ratings={ratings}
        relayRanking={relayRanking}
        variant="first"
      />
    </Panel>
  );
}

/**
 * Show recent dated event groups and keep their expanded/collapsed state local to the view.
 */
function RecentLimitUp({ events }: { events: LimitUpEvent[] }) {
  /** Group recent events by persisted trading date for review. */

  const dateGroups = useMemo(/* Derive dateGroups from the listed dependencies, reusing it until those dependencies change. */ () => {
    const grouped = events.reduce<Record<string, LimitUpEvent[]>>(/* Accumulate the entries into the derived value used by RecentLimitUp. */ (groups, event) => {
      groups[event.trade_date] = groups[event.trade_date] ?? [];
      groups[event.trade_date].push(event);
      return groups;
    }, {});
    return Object.entries(grouped).sort(/* Compare two entries using the explicit tie-break order for RecentLimitUp. */ ([left], [right]) => right.localeCompare(left));
  }, [events]);
  const [expandedDates, setExpandedDates] = useState<string[]>(/* Handle the callback from useState within RecentLimitUp. */ () => (
    dateGroups[0] ? [dateGroups[0][0]] : []
  ));
  const allExpanded = expandedDates.length === dateGroups.length;

  /**
   * Toggle the expanded state of one date group without changing other groups.
   */
  function toggleDate(tradeDate: string) {
    setExpandedDates(/* Compute expanded dates from the latest React state to avoid overwriting intervening updates. */ (current) => (
      current.includes(tradeDate)
        ? current.filter(/* Keep only entries satisfying this predicate for toggleDate. */ (item) => item !== tradeDate)
        : [...current, tradeDate]
    ));
  }

  return (
    <div className="recent-groups">
      <div className="recent-groups-toolbar">
        <div>
          <strong>最近 {dateGroups.length} 个交易日</strong>
          <span>共 {events.length} 条封板记录，默认展开最新交易日</span>
        </div>
        <div className="recent-groups-actions">
          <button
            type="button"
            onClick={/* Handle onClick for this control in RecentLimitUp. */ () => setExpandedDates(dateGroups.map(/* Transform each entry in dateGroups into the result used by RecentLimitUp. */ ([date]) => date))}
            disabled={allExpanded}
          >
            <ChevronDown size={16} aria-hidden="true" />
            全部展开
          </button>
          <button type="button" onClick={/* Handle onClick for this control in RecentLimitUp. */ () => setExpandedDates([])} disabled={expandedDates.length === 0}>
            <Minus size={16} aria-hidden="true" />
            全部收起
          </button>
        </div>
      </div>

      {dateGroups.map(/* Transform each entry in dateGroups into the result used by RecentLimitUp. */ ([tradeDate, items], index) => {
        const expanded = expandedDates.includes(tradeDate);
        const firstBoardCount = items.filter(/* Keep only entries satisfying this predicate for RecentLimitUp. */ (item) => item.board_height === 1).length;
        const continuedBoardCount = items.length - firstBoardCount;
        const maxBoardHeight = Math.max(...items.map(/* Transform each entry in items into the result used by RecentLimitUp. */ (item) => item.board_height));
        const contentId = `recent-limit-up-${tradeDate}`;
        return (
          <section className={`recent-date-group ${expanded ? "expanded" : ""}`} key={tradeDate}>
            <button
              aria-controls={contentId}
              aria-expanded={expanded}
              className="recent-date-toggle"
              type="button"
              onClick={/* Handle onClick for this control in RecentLimitUp. */ () => toggleDate(tradeDate)}
            >
              <span className="recent-date-primary">
                {expanded
                  ? <ChevronDown size={19} aria-hidden="true" />
                  : <ChevronRight size={19} aria-hidden="true" />}
                <strong>{tradeDate}</strong>
                {index === 0 ? <em>最新</em> : null}
              </span>
              <span className="recent-date-summary">
                <b>{items.length} 只</b>
                <small>首板 {firstBoardCount}</small>
                <small>连板 {continuedBoardCount}</small>
                <small>最高 {maxBoardHeight} 板</small>
              </span>
            </button>
            {expanded ? (
              <div className="recent-date-content" id={contentId}>
                <StockTable events={items} variant="recent" />
              </div>
            ) : null}
          </section>
        );
      })}
    </div>
  );
}

/**
 * Render the selected event pool and the stock table for its available rows.
 */
function LimitUpPool({ data }: { data: DashboardData }) {
  /** Group the four limit-up datasets behind one focused navigation page. */

  const entries = [
    {
      to: "/stocks/first-board",
      label: "首板",
      count: `${data.firstBoard.length} 只`,
      description: "查看当日首次涨停股票与 Agent 评分",
      icon: <Flame size={18} />,
    },
    {
      to: "/stocks/continued-board",
      label: "连板",
      count: `${data.continuedBoard.length} 只`,
      description: "查看当日二板及以上连板梯队",
      icon: <Layers3 size={18} />,
    },
    {
      to: "/stocks/failed",
      label: "炸板",
      count: `${data.failed.length} 只`,
      description: "查看盘中触板但未能封住的股票",
      icon: <ShieldAlert size={18} />,
    },
    {
      to: "/stocks/recent-limit-up",
      label: "近七个交易日涨停票",
      count: `${data.recent.length} 条`,
      description: "按交易日回看最近七个交易日涨停记录",
      icon: <TrendingUp size={18} />,
    },
  ];

  return (
    <nav className="overview-grid" aria-label="涨停池分类">
      {entries.map(/* Transform each entry in entries into the result used by LimitUpPool. */ (entry) => (
        <Link className="entry-card" key={entry.to} to={entry.to}>
          <div className="metric-icon" aria-hidden="true">{entry.icon}</div>
          <span>{entry.label}</span>
          <strong>{entry.count}</strong>
          <p>{entry.description}</p>
        </Link>
      ))}
    </nav>
  );
}

/**
 * Render comparable stock facts and stock-detail navigation for the supplied event rows.
 */
function StockTable({
  events,
  ratings,
  relayRanking = [],
  variant,
}: {
  events: LimitUpEvent[];
  ratings?: FirstBoardRatingsResponse;
  relayRanking?: RecommendationIntelligenceItem[];
  variant: ViewKey;
}) {
  /** Shared clickable table for all stock-list routes. */

  const navigate = useNavigate();
  const ratingBySymbol = new Map(
    (ratings?.candidates ?? []).map(/* Transform each entry in (ratings?.candidates ?? []) into the result used by StockTable. */ (item) => [item.facts.symbol, item]),
  );
  const filteredBySymbol = new Map(
    (ratings?.filtered_out ?? []).map(/* Transform each entry in (ratings?.filtered_out ?? []) into the result used by StockTable. */ (item) => [item.symbol, item]),
  );
  const dynamicBySymbol = new Map(
    relayRanking.map(/* Transform each entry in relayRanking into the result used by StockTable. */ (item) => [item.symbol, item]),
  );
  const ratingScores = new Map(
    (ratings?.candidates ?? []).map(/* Transform each entry in (ratings?.candidates ?? []) into the result used by StockTable. */ (item) => [item.facts.symbol, item.score]),
  );
  const visibleEvents = variant === "first"
    ? sortFirstBoardByRelayRanking(events, relayRanking, ratingScores)
    : events;

  /**
   * Navigate to the selected stock's detail route with its encoded identity.
   */
  function openStock(symbol: string) {
    navigate(stockDetailPath(symbol));
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>股票</th>
            {variant === "first" ? <th>评分</th> : null}
            {variant === "first" ? <th>首板位置</th> : null}
            <th>日期</th>
            <th>高度</th>
            <th>首次封板</th>
            <th>最后封板</th>
            <th>{variant === "failed" ? "回封状态" : "封板次数"}</th>
            <th>炸板</th>
            <th>成交额</th>
            <th>换手</th>
            <th>题材</th>
          </tr>
        </thead>
        <tbody>
          {visibleEvents.map(/* Transform each entry in visibleEvents into the result used by StockTable. */ (event) => {
            const rating = ratingBySymbol.get(event.symbol);
            const filtered = filteredBySymbol.get(event.symbol);
            const dynamic = dynamicBySymbol.get(event.symbol);
            const positionLabel = dynamic?.position_label
              ?? rating?.facts.enrichment?.position?.primary.label
              ?? filtered?.position_label;
            return (
            <tr
              className="stock-row"
              key={`${event.trade_date}-${event.symbol}`}
              onClick={/* Handle onClick for this control in StockTable. */ () => openStock(event.symbol)}
              onKeyDown={/* Handle onKeyDown for this control in StockTable. */ (keyboardEvent) => {
                if (keyboardEvent.key === "Enter" || keyboardEvent.key === " ") {
                  keyboardEvent.preventDefault();
                  openStock(event.symbol);
                }
              }}
              tabIndex={0}
            >
              <td>
                <strong>{event.name}</strong>
                <span>{event.symbol}</span>
              </td>
              {variant === "first" ? (
                <td>
                  <strong>
                    {dynamic
                      ? dynamic.draft_score.toFixed(1)
                      : rating
                        ? rating.score.toFixed(1)
                        : "--"}
                  </strong>
                  <span>
                    {dynamic
                      ? `动态第 ${dynamic.rank}`
                      : rating
                        ? rating.rating
                        : filtered?.excluded_reasons[0] ?? "未评分"}
                  </span>
                </td>
              ) : null}
              {variant === "first" ? (
                <td className={`stock-position-cell${positionLabel ? "" : " is-missing"}`}>
                  <strong>{displayRelayPositionLabel(positionLabel)}</strong>
                </td>
              ) : null}
              <td>{event.trade_date}</td>
              <td>{event.closed_limit ? `${event.board_height} 板` : "未封板"}</td>
              <td>{event.first_limit_time.slice(0, 5)}</td>
              <td>{event.last_limit_time.slice(0, 5)}</td>
              <td>{variant === "failed" ? (event.closed_limit ? "回封" : "未回封") : event.seal_count}</td>
              <td>{event.break_count}</td>
              <td>{formatAmount(event.amount)}</td>
              <td>{event.turnover_rate.toFixed(1)}%</td>
              <td>
                <strong>{event.concept}</strong>
                <span>{event.industry}</span>
              </td>
            </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Load a stock's event, rating, news and market data and coordinate daily/intraday chart
 * views. Separate request state preserves useful evidence when one optional source fails.
 */
function StockDetail({ data }: { data: DashboardData }) {
  /** Render one stock's event facts together with daily and intraday K-lines. */

  const { symbol = "" } = useParams();
  const [searchParams] = useSearchParams();
  const { intelligence } = useRecommendationIntelligence();
  const linkedStockName = searchParams.get("name")?.trim() ?? "";
  const [stockEvent, setStockEvent] = useState<LimitUpEvent | null>(null);
  const [stockEventLoading, setStockEventLoading] = useState(true);
  const [stockEventError, setStockEventError] = useState<string | null>(null);
  const [firstBoardRating, setFirstBoardRating] = useState<FirstBoardRating | null>(null);
  const [kline, setKline] = useState<StockKLineBar[]>([]);
  const [tradingDayKline, setTradingDayKline] = useState<StockIntradayKLineBar[]>([]);
  const [fiveDayKline, setFiveDayKline] = useState<StockIntradayHistoryResponse | null>(null);
  const [chartMode, setChartMode] = useState<"daily" | "intraday" | "intraday5d">("daily");
  const [latestClose, setLatestClose] = useState<StockCloseSnapshot | null>(null);
  const [stockNews, setStockNews] = useState<StockNewsFacts | null>(null);
  const [position, setPosition] = useState<StockPositionAssessment | null>(null);
  const [klineLoading, setKlineLoading] = useState(true);
  const [klineError, setKlineError] = useState<string | null>(null);
  const [tradingDayLoading, setTradingDayLoading] = useState(false);
  const [tradingDayError, setTradingDayError] = useState<string | null>(null);
  const [fiveDayLoading, setFiveDayLoading] = useState(false);
  const [fiveDayError, setFiveDayError] = useState<string | null>(null);
  const [latestCloseLoading, setLatestCloseLoading] = useState(true);
  const [latestCloseError, setLatestCloseError] = useState<string | null>(null);
  const [stockNewsLoading, setStockNewsLoading] = useState(true);
  const [positionLoading, setPositionLoading] = useState(true);
  const [positionError, setPositionError] = useState<string | null>(null);
  const tradingDayCacheKeyRef = useRef("");
  const fiveDayCacheKeyRef = useRef("");
  const resolvedTradeDate = stockEvent?.trade_date
    ?? data.summary.trade_date;
  const marketTradeDate = data.summary.trade_date;
  const currentIntelligence = recommendationIntelligenceFor(
    intelligence,
    "relay",
    symbol,
  );

  useEffect(/* Synchronize StockDetail with its current dependencies; any returned callback releases this effect's resources or invalidates stale work. */ () => {
    let active = true;
    setStockEvent(null);
    setStockEventLoading(true);
    setStockEventError(null);
    fetchStockEvent(symbol)
      .then(/* Apply the resolved asynchronous result to the current view state. */ (event) => {
        if (active) {
          setStockEvent(event);
        }
      })
      .catch(/* Handle this asynchronous failure using the enclosing view's error/fallback state. */ (caught) => {
        if (active) {
          setStockEventError(caught instanceof Error ? caught.message : "加载涨停事件失败");
        }
      })
      .finally(/* Release request state after either success or failure. */ () => {
        if (active) {
          setStockEventLoading(false);
        }
      });
    return /* Release or invalidate the enclosing effect's work when dependencies change or the view unmounts. */ () => {
      active = false;
    };
  }, [symbol]);

  useEffect(/* Synchronize StockDetail with its current dependencies; any returned callback releases this effect's resources or invalidates stale work. */ () => {
    if (stockEventLoading) {
      return;
    }
    let active = true;
    setStockNews(null);
    setStockNewsLoading(true);
    fetchStockNews(symbol, stockEvent?.name || linkedStockName || undefined, 3)
      .then(/* Apply the resolved asynchronous result to the current view state. */ (news) => {
        if (active) {
          setStockNews(news);
        }
      })
      .catch(/* Handle this asynchronous failure using the enclosing view's error/fallback state. */ () => {
        if (active) {
          setStockNews(null);
        }
      })
      .finally(/* Release request state after either success or failure. */ () => {
        if (active) {
          setStockNewsLoading(false);
        }
      });
    return /* Release or invalidate the enclosing effect's work when dependencies change or the view unmounts. */ () => {
      active = false;
    };
  }, [linkedStockName, stockEvent?.name, stockEventLoading, symbol]);

  useEffect(/* Synchronize StockDetail with its current dependencies; any returned callback releases this effect's resources or invalidates stale work. */ () => {
    if (stockEventLoading) {
      return;
    }
    let active = true;

    setKline([]);
    setPosition(null);
    setLatestClose(null);
    setLatestCloseLoading(true);
    setLatestCloseError(null);
    setKlineLoading(true);
    setKlineError(null);
    setPositionLoading(Boolean(stockEvent));
    setPositionError(null);
    fetchStockMarketData(symbol, 60, stockEvent?.trade_date)
      .then(/* Apply the resolved asynchronous result to the current view state. */ (marketData) => {
        if (active) {
          setKline(marketData.kline);
          setLatestClose(marketData.latest_close);
          setPosition(marketData.position);
        }
      })
      .catch(/* Handle this asynchronous failure using the enclosing view's error/fallback state. */ (caught) => {
        if (active) {
          const message = caught instanceof Error ? caught.message : "加载个股行情失败";
          setKlineError(message);
          setLatestCloseError(message);
          if (stockEvent) {
            setPositionError(message);
          }
        }
      })
      .finally(/* Release request state after either success or failure. */ () => {
        if (active) {
          setKlineLoading(false);
          setLatestCloseLoading(false);
          setPositionLoading(false);
        }
      });

    return /* Release or invalidate the enclosing effect's work when dependencies change or the view unmounts. */ () => {
      active = false;
    };
  }, [marketTradeDate, stockEvent?.trade_date, stockEventLoading, symbol]);

  useEffect(/* Synchronize StockDetail with its current dependencies; any returned callback releases this effect's resources or invalidates stale work. */ () => {
    setTradingDayKline([]);
    setTradingDayError(null);
    setTradingDayLoading(false);
    tradingDayCacheKeyRef.current = "";
    setFiveDayKline(null);
    setFiveDayError(null);
    setFiveDayLoading(false);
    fiveDayCacheKeyRef.current = "";
  }, [marketTradeDate, symbol]);

  useEffect(/* Synchronize StockDetail with its current dependencies; any returned callback releases this effect's resources or invalidates stale work. */ () => {
    if (chartMode !== "intraday" || stockEventLoading) {
      return;
    }
    const tradeDate = marketTradeDate;
    const cacheKey = `${symbol}:${tradeDate}:1`;
    if (tradingDayCacheKeyRef.current === cacheKey) {
      return;
    }
    let active = true;
    setTradingDayLoading(true);
    setTradingDayError(null);
    fetchStockTradingDayKLine(symbol, 1, tradeDate)
      .then(/* Apply the resolved asynchronous result to the current view state. */ (bars) => {
        if (active) {
          setTradingDayKline(bars);
          tradingDayCacheKeyRef.current = cacheKey;
        }
      })
      .catch(/* Handle this asynchronous failure using the enclosing view's error/fallback state. */ (caught) => {
        if (active) {
          setTradingDayError(caught instanceof Error ? caught.message : "加载交易日走势失败");
        }
      })
      .finally(/* Release request state after either success or failure. */ () => {
        if (active) {
          setTradingDayLoading(false);
        }
      });
    return /* Release or invalidate the enclosing effect's work when dependencies change or the view unmounts. */ () => {
      active = false;
    };
  }, [chartMode, marketTradeDate, stockEventLoading, symbol]);

  useEffect(/* Synchronize StockDetail with its current dependencies; any returned callback releases this effect's resources or invalidates stale work. */ () => {
    if (chartMode !== "intraday5d" || stockEventLoading) {
      return;
    }
    const cacheKey = `${symbol}:${marketTradeDate}:5:1`;
    if (fiveDayCacheKeyRef.current === cacheKey) {
      return;
    }
    let active = true;
    setFiveDayLoading(true);
    setFiveDayError(null);
    fetchStockIntradayHistory(symbol, 5, 1, marketTradeDate)
      .then(/* Apply the resolved asynchronous result to the current view state. */ (history) => {
        if (active) {
          setFiveDayKline(history);
          fiveDayCacheKeyRef.current = cacheKey;
        }
      })
      .catch(/* Handle this asynchronous failure using the enclosing view's error/fallback state. */ (caught) => {
        if (active) {
          setFiveDayError(caught instanceof Error ? caught.message : "加载五日分时失败");
        }
      })
      .finally(/* Release request state after either success or failure. */ () => {
        if (active) {
          setFiveDayLoading(false);
        }
      });
    return /* Release or invalidate the enclosing effect's work when dependencies change or the view unmounts. */ () => {
      active = false;
    };
  }, [chartMode, marketTradeDate, stockEventLoading, symbol]);

  useEffect(/* Synchronize StockDetail with its current dependencies; any returned callback releases this effect's resources or invalidates stale work. */ () => {
    if (stockEventLoading) {
      return;
    }
    const tradeDate = resolvedTradeDate;
    const cachedRating = stockEvent && data.firstBoardRatings.trade_date === tradeDate
      ? data.firstBoardRatings.candidates.find(
          /* Locate the entry matching the active identity/time used by StockDetail. */ (rating) => rating.facts.symbol === symbol,
        ) ?? null
      : null;
    setFirstBoardRating(cachedRating);
    if (!stockEvent) {
      return;
    }
    let active = true;
    fetchFirstBoardRatings(tradeDate, true)
      .then(/* Apply the resolved asynchronous result to the current view state. */ (ratings) => {
        if (active) {
          setFirstBoardRating(
            ratings.candidates.find(/* Locate the entry matching the active identity/time used by StockDetail. */ (rating) => rating.facts.symbol === symbol) ?? null,
          );
        }
      })
      .catch(/* Handle this asynchronous failure using the enclosing view's error/fallback state. */ () => {
        if (active && !cachedRating) {
          setFirstBoardRating(null);
        }
      });

    return /* Release or invalidate the enclosing effect's work when dependencies change or the view unmounts. */ () => {
      active = false;
    };
  }, [
    data.firstBoardRatings,
    resolvedTradeDate,
    stockEvent?.trade_date,
    stockEventLoading,
    symbol,
  ]);

  const intradayReferencePrice = useMemo(/* Derive intradayReferencePrice from the listed dependencies, reusing it until those dependencies change. */ () => {
    const eventIndex = kline.findIndex(
      /* Locate the entry matching the active identity/time used by StockDetail. */ (bar) => bar.trade_date === marketTradeDate,
    );
    return eventIndex > 0 ? kline[eventIndex - 1].close : null;
  }, [kline, marketTradeDate]);
  const fiveDayChartBars = useMemo(
    /* Derive fiveDayChartBars from the listed dependencies, reusing it until those dependencies change. */ () => toFiveDayIntradayCandleBars(fiveDayKline),
    [fiveDayKline],
  );
  const fiveDayReferencePrice = useMemo(
    /* Derive fiveDayReferencePrice from the listed dependencies, reusing it until those dependencies change. */ () => fiveDayKline?.days.find(/* Locate the entry matching the active identity/time used by StockDetail. */ (day) => day.bars.length > 0)?.previous_close ?? null,
    [fiveDayKline],
  );

  if (stockEventLoading) {
    return <ShellState label="正在加载个股详情..." />;
  }

  return (
    <div className="stock-detail">
      <section className="stock-hero">
        <div>
          <p className="eyebrow">行情截至 {marketTradeDate}</p>
          <h2>{stockEvent?.name || linkedStockName || symbol}</h2>
          <span>{symbol}</span>
        </div>
        <div className="stock-status">
          <strong>
            {stockEvent
              ? stockEvent.closed_limit ? `${stockEvent.board_height} 板` : "炸板"
              : "行情详情"}
          </strong>
          <span>
            {stockEvent
              ? stockEvent.closed_limit ? "已封板" : "盘中触板未回封"
              : stockEventError ? "当前不在本地涨停事件库" : "基础行情"}
          </span>
        </div>
      </section>

      <LatestCloseStrip
        snapshot={latestClose}
        loading={latestCloseLoading}
        error={latestCloseError}
      />

      {stockEvent ? (
        <Panel title="封板信息" icon={<Flame size={18} />}>
          <div className="stock-facts">
            <Fact label="封板日期" value={stockEvent.trade_date} />
            <Fact label="首次封板" value={stockEvent.first_limit_time.slice(0, 5)} />
            <Fact label="最后封板" value={stockEvent.last_limit_time.slice(0, 5)} />
            <Fact label="封板次数" value={`${stockEvent.seal_count}`} />
            <Fact label="炸板次数" value={`${stockEvent.break_count}`} />
            <Fact label="成交额" value={formatAmount(stockEvent.amount)} />
            <Fact label="换手率" value={`${stockEvent.turnover_rate.toFixed(1)}%`} />
            <Fact label="行业" value={stockEvent.industry} />
            <Fact label="题材" value={stockEvent.concept || "暂无"} />
          </div>
        </Panel>
      ) : null}

      <section className="stock-market-chart">
        <Panel
          title="行情走势"
          icon={<LineChart size={18} />}
          actions={(
            <div className="chart-mode-switch" aria-label="行情周期">
              <button
                type="button"
                aria-pressed={chartMode === "daily"}
                onClick={/* Handle onClick for this control in StockDetail. */ () => setChartMode("daily")}
              >
                日 K · 60日
              </button>
              <button
                type="button"
                aria-pressed={chartMode === "intraday"}
                onClick={/* Handle onClick for this control in StockDetail. */ () => setChartMode("intraday")}
              >
                分时
              </button>
              <button
                type="button"
                aria-pressed={chartMode === "intraday5d"}
                onClick={/* Handle onClick for this control in StockDetail. */ () => setChartMode("intraday5d")}
              >
                五日
              </button>
            </div>
          )}
        >
          {chartMode === "daily" ? (
            klineLoading ? (
              <div className="chart-state">正在加载 60 日 K 线...</div>
            ) : klineError ? (
              <div className="chart-state">{klineError}</div>
            ) : (
              <MarketKLineChart
                bars={toDailyCandleBars(kline)}
                emptyLabel="暂无 60 日 K 线数据"
                mode="daily"
              />
            )
          ) : chartMode === "intraday" && tradingDayLoading ? (
            <div className="chart-state">正在加载交易日走势...</div>
          ) : chartMode === "intraday" && tradingDayError ? (
            <div className="chart-state">{tradingDayError}</div>
          ) : chartMode === "intraday" ? (
            <MarketKLineChart
              bars={toIntradayCandleBars(tradingDayKline)}
              emptyLabel="暂无交易日走势数据"
              mode="intraday"
              referencePrice={intradayReferencePrice}
            />
          ) : fiveDayLoading ? (
            <div className="chart-state">正在加载最近五日分时...</div>
          ) : fiveDayError ? (
            <div className="chart-state">{fiveDayError}</div>
          ) : (
            <>
              {fiveDayKline && !fiveDayKline.complete ? (
                <div className="chart-data-warning">
                  部分交易日分时缺失：{fiveDayKline.missing_trade_dates.join("、")}
                </div>
              ) : null}
              <MarketKLineChart
                bars={fiveDayChartBars}
                emptyLabel="暂无最近五日分时数据"
                mode="intraday5d"
                referencePrice={fiveDayReferencePrice}
              />
            </>
          )}
        </Panel>
      </section>

      <StockNewsPanel news={stockNews} loading={stockNewsLoading} />

      {stockEvent ? (
        <StockPositionPanel
          position={position}
          tradeDate={stockEvent.trade_date}
          loading={positionLoading}
          error={positionError}
        />
      ) : null}

      {firstBoardRating ? (
        <FirstBoardRatingDetail
          intelligence={
            currentIntelligence?.base_trade_date === resolvedTradeDate
              ? currentIntelligence
              : null
          }
          rating={firstBoardRating}
        />
      ) : null}
    </div>
  );
}

/**
 * Render stock-specific news with source links and explicit cache/data availability.
 */
function StockNewsPanel({
  news,
  loading,
}: {
  news: StockNewsFacts | null;
  loading: boolean;
}) {
  if (!loading && (!news || news.items.length === 0)) {
    return null;
  }

  return (
    <Panel
      title="个股资讯"
      icon={<Newspaper size={18} />}
      actions={<span className="stock-news-window">近 7 日</span>}
    >
      {loading ? (
        <div className="stock-news-loading">正在获取个股资讯...</div>
      ) : (
        <div className="stock-news-list">
          {news?.items.slice(0, 3).map(/* Transform each entry in news?.items.slice(0, 3) into the result used by StockNewsPanel. */ (item) => (
            <a
              className="stock-news-item"
              href={item.url}
              key={`${item.published_at}-${item.title}`}
              rel="noreferrer"
              target="_blank"
            >
              <time dateTime={item.published_at}>{formatStockNewsTime(item.published_at)}</time>
              <div>
                <strong>{item.title}</strong>
                <span>{item.source}</span>
              </div>
              <ExternalLink aria-hidden="true" size={16} />
            </a>
          ))}
        </div>
      )}
    </Panel>
  );
}

/**
 * Render the timestamp shown next to an individual stock-news item.
 */
function formatStockNewsTime(value: string) {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return "时间未知";
  }
  return timestamp.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}


/**
 * Show the backend's price-position assessment and supporting evidence.
 */
function StockPositionPanel({
  position,
  tradeDate,
  loading,
  error,
}: {
  position: StockPositionAssessment | null;
  tradeDate: string;
  loading: boolean;
  error: string | null;
}) {
  if (loading) {
    return (
      <Panel title="首板位置判断" icon={<MapPin size={18} />}>
        <div className="rating-detail-empty">正在分析价格位置...</div>
      </Panel>
    );
  }

  if (error || !position) {
    return (
      <Panel title="首板位置判断" icon={<MapPin size={18} />}>
        <div className="rating-detail-empty">{error ?? "暂无足够 K 线判断当前位置。"}</div>
      </Panel>
    );
  }

  return (
    <Panel title="首板位置判断" icon={<MapPin size={18} />}>
      <StockPositionDetail position={position} tradeDate={tradeDate} />
    </Panel>
  );
}


/**
 * Explain a first-board rating through its score breakdown, confidence, facts and risks.
 */
function FirstBoardRatingDetail({
  intelligence,
  rating,
}: {
  intelligence: RecommendationIntelligenceItem | null;
  rating: FirstBoardRating;
}) {
  /** Render explainable first-board score details for the selected stock. */

  const scoreBreakdown = rating.score_breakdown.map(/* Transform each entry in rating.score_breakdown into the result used by FirstBoardRatingDetail. */ (item) => {
    if (item.name === "龙虎榜资金" && intelligence) {
      return {
        ...item,
        evidence: [intelligence.dragon_tiger_on_list
          ? `当前龙虎榜已上榜，净买额 ${formatNetAmount(intelligence.dragon_tiger_net_buy_amount)}`
          : "当前龙虎榜数据未显示上榜"],
      };
    }
    if (item.name === "市场人气" && intelligence) {
      return {
        ...item,
        evidence: [intelligence.popularity_rank === null
          ? "当前接入榜单未覆盖该股，不推断榜外名次"
          : `当前人气排名第 ${intelligence.popularity_rank}`],
      };
    }
    return item;
  });
  const boardPatternScore = scoreBreakdown.find(/* Locate the entry matching the active identity/time used by FirstBoardRatingDetail. */ (item) => item.name === "上板形态");
  const marketCapScore = scoreBreakdown.find(/* Locate the entry matching the active identity/time used by FirstBoardRatingDetail. */ (item) => item.name === "市值偏好");
  const floatMarketCap = rating.facts.enrichment?.float_market_cap;
  const dragonTigerOnList = intelligence?.dragon_tiger_on_list
    ?? rating.facts.enrichment?.dragon_tiger_on_list
    ?? false;
  const popularityRank = intelligence?.popularity_rank
    ?? rating.facts.enrichment?.popularity_rank
    ?? null;
  const displayScore = intelligence?.draft_score ?? rating.score;
  const displayRating = displayScore >= 80
    ? "A"
    : displayScore >= 65
      ? "B"
      : displayScore >= 50
        ? "C"
        : "D";
  const capRule = floatMarketCap === null || floatMarketCap === undefined
    ? "流通市值数据缺失"
    : floatMarketCap <= 5_000_000_000
      ? "低市值档，因子加分"
      : floatMarketCap > 50_000_000_000
        ? "高市值档，因子降分"
        : "中间市值档，按区间评分";

  return (
    <Panel title="Agent 评分拆解" icon={<BarChart3 size={18} />}>
      <div className="rating-detail">
        <div className="rating-detail-head">
          <span className={`rating-badge rating-${displayRating.toLowerCase()}`}>
            {displayRating}
          </span>
          <div>
            <strong>{displayScore.toFixed(1)}</strong>
            <span>置信度 {formatPercent(rating.confidence)}</span>
          </div>
        </div>

        <div className="rating-rule-signals" aria-label="评分规则信号">
          <span>
            <small>上板形态</small>
            <strong>
              {rating.facts.is_one_word_board
                ? "一字板，形态评分降档"
                : "盘中上板，换手承接更充分"}
            </strong>
            {boardPatternScore ? (
              <em>{boardPatternScore.score.toFixed(1)} / {boardPatternScore.max_score.toFixed(1)}</em>
            ) : null}
          </span>
          <span>
            <small>市值偏好</small>
            <strong>{capRule}</strong>
            {marketCapScore ? (
              <em>{marketCapScore.score.toFixed(1)} / {marketCapScore.max_score.toFixed(1)}</em>
            ) : null}
          </span>
        </div>

        {rating.facts.enrichment ? (
          <div className="rating-enrichment-facts">
            <span>
              <small>近20日涨幅</small>
              <strong>{formatOptionalPercent(rating.facts.enrichment.return_20d_pct)}</strong>
            </span>
            <span>
              <small>距60日高点</small>
              <strong>{formatOptionalPercent(rating.facts.enrichment.distance_60d_high_pct)}</strong>
            </span>
            <span>
              <small>流通市值</small>
              <strong>
                {rating.facts.enrichment.float_market_cap === null
                  ? "暂无"
                  : formatAmount(rating.facts.enrichment.float_market_cap)}
              </strong>
            </span>
            <span>
              <small>上市日期</small>
              <strong>{rating.facts.enrichment.listing_date ?? "早期上市"}</strong>
            </span>
            <span>
              <small>龙虎榜</small>
              <strong>{dragonTigerOnList ? "上榜" : "当前数据未显示上榜"}</strong>
            </span>
            <span>
              <small>当前人气</small>
              <strong>
                {popularityRank === null
                  ? "当前榜单未覆盖"
                  : `第 ${popularityRank}`}
              </strong>
            </span>
          </div>
        ) : null}

        <div className="rating-detail-section">
          <h3>评分项</h3>
          <div className="score-breakdown-list">
            {scoreBreakdown.map(/* Transform each entry in scoreBreakdown into the result used by FirstBoardRatingDetail. */ (item) => (
              <div className="score-breakdown-item" key={item.name}>
                <div>
                  <strong>{item.name}</strong>
                  <span>{item.evidence.join("；")}</span>
                </div>
                <b>{item.score.toFixed(1)} / {item.max_score.toFixed(1)}</b>
              </div>
            ))}
          </div>
        </div>

        <TagSection title="主要理由" items={rating.reasons} tone="good" />
        <TagSection title="风险观察" items={rating.risks} tone="risk" />
        {rating.facts.data_missing.length > 0 ? (
          <TagSection title="缺失数据" items={rating.facts.data_missing} tone="muted" />
        ) : null}
      </div>
    </Panel>
  );
}

/**
 * Render the detailed regime matches and measurements behind a position assessment.
 */
function StockPositionDetail({
  position,
  tradeDate,
}: {
  position: StockPositionAssessment;
  tradeDate: string;
}) {
  return (
    <section className="stock-position-detail">
      <header>
        <div>
          <span>{tradeDate} 收盘 · {position.bar_count} 根日 K</span>
        </div>
        <strong>{position.primary.label}</strong>
        <b>匹配度 {position.primary.score.toFixed(0)}</b>
      </header>
      <div className="stock-position-tags">
        {position.tags.map(/* Transform each entry in position.tags into the result used by StockPositionDetail. */ (tag) => <span key={tag}>{tag}</span>)}
      </div>
      <ul>
        {position.evidence.map(/* Transform each entry in position.evidence into the result used by StockPositionDetail. */ (item) => <li key={item}>{item}</li>)}
      </ul>
      {position.alternatives.length > 0 ? (
        <small>
          次选：{position.alternatives.map(/* Transform each entry in position.alternatives into the result used by StockPositionDetail. */ (item) => `${item.label} ${item.score.toFixed(0)}`).join("；")}
        </small>
      ) : null}
    </section>
  );
}

/**
 * Render a titled group of evidence/risk tags from the supplied text items.
 */
function TagSection({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: "good" | "risk" | "muted";
}) {
  return (
    <div className="rating-detail-section">
      <h3>{title}</h3>
      <div className="tag-list">
        {items.map(/* Transform each entry in items into the result used by TagSection. */ (item) => (
          <span className={`detail-tag tag-${tone}`} key={item}>{item}</span>
        ))}
      </div>
    </div>
  );
}
/**
 * Show the latest known close and its change using the available reference price.
 */
function LatestCloseStrip({
  snapshot,
  loading,
  error,
}: {
  snapshot: StockCloseSnapshot | null;
  loading: boolean;
  error: string | null;
}) {
  /** Show latest available after-close price data without blocking charts. */

  if (loading) {
    return <div className="latest-close-strip">正在加载最新收盘数据...</div>;
  }

  if (error || !snapshot) {
    return <div className="latest-close-strip muted">暂无最新收盘数据</div>;
  }

  return (
    <section className="latest-close-strip">
      <div>
        <span>最新收盘</span>
        <strong>{snapshot.close.toFixed(2)}</strong>
      </div>
      <div>
        <span>涨跌幅</span>
        <strong className={snapshot.change_pct !== null && snapshot.change_pct >= 0 ? "positive" : "negative"}>
          {snapshot.change_pct === null ? "暂无" : `${formatSigned(snapshot.change_pct, 2)}%`}
        </strong>
      </div>
      <div>
        <span>涨跌额</span>
        <strong>
          {snapshot.change === null ? "暂无" : formatSigned(snapshot.change, 2)}
        </strong>
      </div>
      <div>
        <span>交易日</span>
        <strong>{snapshot.trade_date}</strong>
      </div>
    </section>
  );
}
/**
 * Render one labeled fact with consistent dashboard styling.
 */
function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="fact-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

/**
 * Adapt daily K-line records to the common chart-bar shape without recalculating market facts.
 */
function toDailyCandleBars(bars: StockKLineBar[]): MarketCandleBar[] {
  /** Convert API daily K-line bars into chart-friendly candle bars. */

  return bars.map(/* Transform each entry in bars into the result used by toDailyCandleBars. */ (bar) => ({
    time: bar.trade_date,
    label: bar.trade_date,
    open: bar.open,
    close: bar.close,
    high: bar.high,
    low: bar.low,
    volume: bar.volume,
  }));
}

/**
 * Render the workspace's loading or unavailable state around the supplied message.
 */
function ShellState({
  label,
  detail,
  onRetry,
}: {
  label: string;
  detail?: string;
  onRetry?: () => void;
}) {
  return (
    <main aria-live="polite" className="state-shell">
      <div>
        <p className="eyebrow">LimitUpLab</p>
        {!onRetry ? <LoaderCircle aria-hidden="true" className="state-spinner" size={24} /> : null}
        <h1>{label}</h1>
        {detail ? <p>{detail}</p> : null}
        {onRetry ? (
          <button className="primary-button" onClick={onRetry}>
            <RefreshCcw size={16} />
            重试
          </button>
        ) : null}
      </div>
    </main>
  );
}

/**
 * Select the icon associated with the current dashboard detail view.
 */
function detailIcon(view: ViewKey) {
  if (view === "first") {
    return <Flame size={18} />;
  }
  if (view === "continued") {
    return <Layers3 size={18} />;
  }
  if (view === "recent") {
    return <TrendingUp size={18} />;
  }
  return <ShieldAlert size={18} />;
}
