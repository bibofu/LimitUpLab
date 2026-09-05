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
  rankedRelayCandidates,
  sortFirstBoardByRelayRanking,
} from "./relayRanking";
import {
  fetchContinuedBoardEvents,
  fetchDailyBoardPromotion,
  fetchFirstBoardCritic,
  fetchFirstBoardDiscovery,
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
  FirstBoardCriticResponse,
  FirstBoardDiscoveryPattern,
  FirstBoardDiscoveryResponse,
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
  recent: { title: "近五日涨停票复盘", eyebrow: "Recent Limit-Up" },
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
        fetchRecentLimitUpEvents(5),
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

  useEffect(() => {
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
          {primaryNavigation.map((item) => (
            <NavLink
              className={({ isActive }) => (
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
          element={<PremarketPage ratings={data.firstBoardRatings} />}
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

function MarketSnapshot({ summary }: { summary: MarketSummary }) {
  return (
    <section className="market-snapshot">
      <div className="market-snapshot-indices">
        <time className="market-snapshot-date" dateTime={summary.trade_date}>
          {summary.trade_date}
        </time>
        {summary.indices.map((index) => (
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

function PremarketPage({ ratings }: { ratings: FirstBoardRatingsResponse }) {
  return (
    <div className="premarket-page">
      <RecommendationNewsBoard />
      <PremarketStrategyWorkspace ratings={ratings} />
    </div>
  );
}

function PremarketStrategyWorkspace({ ratings }: { ratings: FirstBoardRatingsResponse }) {
  /** Keep the two pre-market strategies distinct while sharing one workspace. */

  const [mode, setMode] = useState<"discovery" | "relay">("discovery");
  const [discovery, setDiscovery] = useState<FirstBoardDiscoveryResponse | null>(null);
  const [discoveryError, setDiscoveryError] = useState<string | null>(null);
  const [discoveryLoading, setDiscoveryLoading] = useState(true);
  const {
    intelligence,
    loading: intelligenceLoading,
    error: intelligenceError,
  } = useRecommendationIntelligence();

  useEffect(() => {
    let active = true;
    void fetchFirstBoardDiscovery()
      .then((response) => {
        if (active) setDiscovery(response);
      })
      .catch((caught: unknown) => {
        if (active) {
          setDiscoveryError(caught instanceof Error ? caught.message : "低位挖掘数据加载失败");
        }
      })
      .finally(() => {
        if (active) setDiscoveryLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const draftBaseDate = mode === "discovery" ? discovery?.data_as_of : ratings.trade_date;
  const strategyCandidates = mode === "relay"
    ? rankedRelayCandidates(
        intelligence?.items ?? [],
        draftBaseDate,
        intelligence?.relay_display_limit ?? 10,
      )
    : (intelligence?.items ?? [])
      .filter(
        (item) => item.strategy === mode && item.base_trade_date === draftBaseDate,
      )
      .sort((left, right) => left.rank - right.rank)
      .slice(0, intelligence?.discovery_display_limit ?? 15);
  const draftCandidates = strategyCandidates
    .map((item, index) => ({
      ...item,
      rank: index + 1,
      sector: item.sector ?? "",
      position_label: item.position_label ?? null,
      rule_rank: item.rule_rank ?? item.base_rank ?? item.rank,
      rule_score: item.rule_score ?? item.base_score,
      base_rank: item.base_rank ?? item.rank,
      draft_score: item.draft_score ?? item.base_score,
      facts_cutoff_at: item.facts_cutoff_at ?? null,
      close_information_adjustment: item.close_information_adjustment ?? 0,
      close_information_reasons: item.close_information_reasons ?? [],
      news_adjustment: item.news_adjustment ?? 0,
      financial_adjustment: item.financial_adjustment ?? 0,
      dragon_tiger_adjustment: item.dragon_tiger_adjustment ?? 0,
      popularity_adjustment: item.popularity_adjustment ?? 0,
      dynamic_adjustment:
        item.dynamic_adjustment ?? item.draft_score - item.base_score,
      dragon_tiger_on_list: item.dragon_tiger_on_list ?? false,
      dragon_tiger_is_new: item.dragon_tiger_is_new ?? false,
      dragon_tiger_net_buy_amount: item.dragon_tiger_net_buy_amount ?? null,
      dragon_tiger_source: item.dragon_tiger_source ?? null,
      popularity_base_rank: item.popularity_base_rank ?? null,
      popularity_rank: item.popularity_rank ?? null,
      popularity_rank_change: item.popularity_rank_change ?? null,
      popularity_snapshot_at: item.popularity_snapshot_at ?? null,
      popularity_source: item.popularity_source ?? null,
      update_reasons: item.update_reasons ?? [],
    })) ?? [];

  return (
    <section className="premarket-workspace">
      <div aria-label="盘前策略" className="strategy-switch" role="tablist">
        <button
          aria-selected={mode === "discovery"}
          className={mode === "discovery" ? "active" : undefined}
          onClick={() => setMode("discovery")}
          role="tab"
          type="button"
        >
          <TrendingUp size={16} />
          低位挖掘
        </button>
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
      </div>
      {intelligence?.stage === "missed_cutoff" ? (
        <PremarketCutoffMissedPanel intelligence={intelligence} strategy={mode} />
      ) : intelligenceLoading ? (
        <PremarketRankingStatePanel
          message="正在读取统一的盘前排名与证据"
          state="loading"
          strategy={mode}
        />
      ) : !intelligence ? (
        <PremarketRankingStatePanel
          message={intelligenceError ?? "盘前动态榜暂不可用"}
          state="error"
          strategy={mode}
        />
      ) : draftCandidates.length > 0 && intelligence ? (
        <RecommendationDraftPanel
          candidates={draftCandidates}
          discovery={discovery}
          intelligence={intelligence}
          strategy={mode}
        />
      ) : (
        <PremarketRankingStatePanel
          message={mode === "discovery" && discoveryLoading
            ? "正在读取低位挖掘证据"
            : mode === "discovery" && discoveryError
              ? discoveryError
              : "当前目标交易日没有可展示的盘前候选"}
          state={mode === "discovery" && discoveryLoading
            ? "loading"
            : mode === "discovery" && discoveryError
              ? "error"
              : "empty"}
          strategy={mode}
        />
      )}
    </section>
  );
}

function PremarketRankingStatePanel({
  message,
  state,
  strategy,
}: {
  message: string;
  state: "loading" | "error" | "empty";
  strategy: "discovery" | "relay";
}) {
  const icon = state === "error"
    ? <ShieldAlert size={20} />
    : <LoaderCircle className={state === "loading" ? "spin" : undefined} size={20} />;
  return (
    <Panel
      title={strategy === "discovery" ? "低位挖掘" : "一进二接力"}
      icon={strategy === "discovery" ? <TrendingUp size={18} /> : <BarChart3 size={18} />}
    >
      <div className={`discovery-state${state === "error" ? " discovery-state-error" : ""}`}>
        {icon}
        <span>{message}</span>
      </div>
    </Panel>
  );
}

function PremarketCutoffMissedPanel({
  intelligence,
  strategy,
}: {
  intelligence: RecommendationIntelligenceResponse;
  strategy: "discovery" | "relay";
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
      title={strategy === "discovery" ? "低位挖掘" : "一进二接力"}
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

function useRecommendationIntelligence() {
  const [intelligence, setIntelligence] = useState<RecommendationIntelligenceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const refresh = () => {
      void fetchRecommendationIntelligence()
        .then((response) => {
          if (active) {
            setIntelligence(response);
            setError(null);
          }
        })
        .catch((caught: unknown) => {
          if (active) {
            setError(caught instanceof Error ? caught.message : "盘前动态榜加载失败");
          }
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    };
    refresh();
    const timer = window.setInterval(refresh, 60 * 1000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  return { intelligence, loading, error };
}

function RecommendationDraftPanel({
  candidates,
  discovery,
  intelligence,
  strategy,
}: {
  candidates: RecommendationIntelligenceItem[];
  discovery: FirstBoardDiscoveryResponse | null;
  intelligence: RecommendationIntelligenceResponse;
  strategy: "discovery" | "relay";
}) {
  const title = strategy === "discovery" ? "低位挖掘" : "一进二接力";
  const targetLabel = intelligence.target_trade_date
    ? `${intelligence.target_trade_date} 目标日`
    : "下一交易日";
  return (
    <Panel
      title={title}
      icon={strategy === "discovery" ? <TrendingUp size={18} /> : <BarChart3 size={18} />}
    >
      <div className="rating-summary-panel recommendation-draft-panel">
        <div className="recommendation-draft-header">
          <div>
            <strong>{strategy === "discovery" ? `低位启动观察池 · ${candidates.length} 只` : `盘前动态候选 Top${candidates.length}`} · {targetLabel}</strong>
            <span>{strategy === "discovery" ? "热门题材与最新催化召回，财报和 K 线位置共同验证" : "收盘综合分固化基线，盘后按公告、龙虎榜与人气变化做有界修正"}</span>
          </div>
          <span className="recommendation-draft-time">
            {intelligence.stage === "final" ? "开盘前已固化 · " : "盘前动态更新 · "}
            {new Date(intelligence.finalized_at ?? intelligence.refreshed_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}
          </span>
        </div>
        <div className="rating-top-list">
          {candidates.map((candidate) => {
            const lowPosition = discovery?.candidates.find(
              (item) => item.facts.symbol === candidate.symbol,
            );
            return (
            <Link
              className="rating-top-card"
              key={`${candidate.strategy}-${candidate.base_trade_date}-${candidate.symbol}`}
              to={stockDetailPath(candidate.symbol, candidate.name)}
            >
              <header>
                <div>
                  <span>{strategy === "discovery" ? `低位候选 · ${lowPosition ? discoveryPatternLabel(lowPosition.facts.pattern) : "结构待验证"}` : `Top ${candidate.rank} · 收盘综合第 ${candidate.base_rank}`}</span>
                  <strong>{candidate.name}</strong>
                  <small>{candidate.symbol}{candidate.sector ? ` / ${candidate.sector}` : ""}</small>
                </div>
                <div className="rating-top-score">
                  <b>{candidate.draft_score.toFixed(1)}</b>
                  <span className="rating-score-context">
                    {strategy === "discovery"
                      ? "研究分"
                      : `收盘综合 ${candidate.base_score.toFixed(1)} · 动态 ${candidate.dynamic_adjustment >= 0 ? "+" : ""}${candidate.dynamic_adjustment.toFixed(1)}`}
                  </span>
                </div>
              </header>
              {strategy === "discovery" ? (
                <LowPositionEvidence candidate={lowPosition} intelligence={candidate} />
              ) : candidate.update_reasons.length > 0 ? (
                <section className="rating-top-reasons">
                  <strong>收盘后新增信息</strong>
                  <ul>{candidate.update_reasons.slice(0, 3).map((reason) => <li key={reason}>{reason}</li>)}</ul>
                </section>
              ) : null}
              {strategy !== "discovery" && candidate.close_information_reasons.length > 0 ? (
                <section className="rating-top-reasons">
                  <strong>收盘综合分已纳入</strong>
                  <ul>{candidate.close_information_reasons.slice(0, 2).map((reason) => <li key={reason}>{reason}</li>)}</ul>
                </section>
              ) : null}
              {strategy !== "discovery" && candidate.latest_news[0] ? (
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
          {strategy === "discovery"
            ? "低位挖掘用于研究可能进入趋势启动阶段的标的，不代表主升浪或收益概率。"
            : intelligence.stage === "final"
              ? "该排序已于目标交易日开盘前固化，供盘后复盘使用。"
              : "当前为盘前动态研究排序；开盘后停止更新，只有开盘前固化的 Top10 才进入复盘。"}
        </p>
      </div>
    </Panel>
  );
}

function LowPositionEvidence({
  candidate,
  intelligence,
}: {
  candidate: FirstBoardDiscoveryResponse["candidates"][number] | undefined;
  intelligence: RecommendationIntelligenceItem | null;
}) {
  const facts = candidate?.facts;
  const themes = facts?.themes.slice(0, 2) ?? [];
  const latestNews = intelligence?.latest_news[0]?.title
    ?? facts?.news_catalysts[0]
    ?? "暂未匹配到明确的近期催化";
  const report = intelligence?.financial_report;
  const financial = report
    ? `${report.fiscal_year} ${report.fiscal_period}，营收同比 ${formatNullableSigned(report.operating_income_yoy_pct)}，归母净利同比 ${formatNullableSigned(report.net_profit_yoy_pct)}`
    : "暂未获取到可比较的最新季度财报";
  return (
    <div className="low-position-evidence">
      <section>
        <strong><b>1</b>题材</strong>
        <p>{themes.length > 0
          ? themes.map((theme) => `${theme.name} ${formatSigned(theme.change_pct, 1)}%`).join("；")
          : intelligence?.sector || "暂未匹配到明确热门题材"}</p>
      </section>
      <section>
        <strong><b>2</b>新闻和财报</strong>
        <p>{latestNews}</p>
        <small>{financial}</small>
      </section>
      <section>
        <strong><b>3</b>走势</strong>
        <p>{facts
          ? `${discoveryPatternLabel(facts.pattern)}；近5日 ${formatNullableSigned(facts.return_5d_pct)}，近20日 ${formatNullableSigned(facts.return_20d_pct)}，近60日 ${formatNullableSigned(facts.return_60d_pct)}；量比 ${facts.volume_ratio_5d?.toFixed(2) ?? "暂无"}，60日区间位置 ${facts.position_60d_pct?.toFixed(0) ?? "暂无"}%`
          : intelligence?.position_label || "K 线位置事实暂缺"}</p>
      </section>
    </div>
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

function RecommendationNewsBoard() {
  /** Paginate the factual 24-hour market feed without involving the LLM. */

  const [page, setPage] = useState(1);
  const [news, setNews] = useState<FinanceNewsPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    const refresh = (showLoading: boolean) => {
      if (showLoading) setLoading(true);
      void fetchFinanceNews(page)
        .then((response) => {
          if (!active) return;
          setNews(response);
          setFailed(false);
          setPage(response.page);
        })
        .catch(() => {
          if (active) setFailed(true);
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    };
    refresh(true);
    const timer = window.setInterval(() => refresh(false), 5 * 60 * 1000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [page]);

  const visibleNews = useMemo(
    () => (news?.items ?? []).map(marketNewsViewItem),
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
          {visibleNews.map((item) => (
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
              onClick={() => setPage((value) => Math.max(1, value - 1))}
              title="上一页"
              type="button"
            >
              <ChevronLeft size={15} />
            </button>
            {pageNumbers.map((pageNumber) => (
              <button
                aria-current={pageNumber === news.page ? "page" : undefined}
                className={pageNumber === news.page ? "active" : undefined}
                key={pageNumber}
                onClick={() => setPage(pageNumber)}
                type="button"
              >
                {pageNumber}
              </button>
            ))}
            <button
              aria-label="下一页"
              disabled={news.page >= news.total_pages}
              onClick={() => setPage((value) => Math.min(news.total_pages, value + 1))}
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

function paginationWindow(current: number, total: number): number[] {
  const visible = Math.min(5, total);
  const start = Math.max(1, Math.min(current - 2, total - visible + 1));
  return Array.from({ length: visible }, (_, index) => start + index);
}

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

function discoveryPatternLabel(pattern: FirstBoardDiscoveryPattern) {
  const labels: Record<FirstBoardDiscoveryPattern, string> = {
    low_base_breakout: "低位突破",
    trend_acceleration: "趋势加速",
    oversold_rebound: "超跌反弹",
    second_wave: "二波观察",
    range_breakout: "区间突破",
    unclassified: "结构观察",
  };
  return labels[pattern];
}

function recommendationIntelligenceFor(
  response: RecommendationIntelligenceResponse | null,
  strategy: "discovery" | "relay",
  symbol: string,
) {
  return response?.items.find(
    (item) => item.strategy === strategy && item.symbol === symbol,
  ) ?? null;
}

function formatNullableSigned(value: number | null) {
  return value === null ? "暂无" : `${formatSigned(value, 1)}%`;
}

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

  useEffect(() => {
    let active = true;
    if (!tradeDate) return () => { active = false; };
    void fetchFirstBoardRatings(tradeDate, true)
      .then((response) => {
        if (active) setRatings(response);
      })
      .catch(() => {
        // Keep persisted prediction scores as a partial ordering fallback.
      });
    return () => { active = false; };
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

function RecentLimitUp({ events }: { events: LimitUpEvent[] }) {
  /** Group recent events by persisted trading date for review. */

  const dateGroups = useMemo(() => {
    const grouped = events.reduce<Record<string, LimitUpEvent[]>>((groups, event) => {
      groups[event.trade_date] = groups[event.trade_date] ?? [];
      groups[event.trade_date].push(event);
      return groups;
    }, {});
    return Object.entries(grouped).sort(([left], [right]) => right.localeCompare(left));
  }, [events]);
  const [expandedDates, setExpandedDates] = useState<string[]>(() => (
    dateGroups[0] ? [dateGroups[0][0]] : []
  ));
  const allExpanded = expandedDates.length === dateGroups.length;

  function toggleDate(tradeDate: string) {
    setExpandedDates((current) => (
      current.includes(tradeDate)
        ? current.filter((item) => item !== tradeDate)
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
            onClick={() => setExpandedDates(dateGroups.map(([date]) => date))}
            disabled={allExpanded}
          >
            <ChevronDown size={16} aria-hidden="true" />
            全部展开
          </button>
          <button type="button" onClick={() => setExpandedDates([])} disabled={expandedDates.length === 0}>
            <Minus size={16} aria-hidden="true" />
            全部收起
          </button>
        </div>
      </div>

      {dateGroups.map(([tradeDate, items], index) => {
        const expanded = expandedDates.includes(tradeDate);
        const firstBoardCount = items.filter((item) => item.board_height === 1).length;
        const continuedBoardCount = items.length - firstBoardCount;
        const maxBoardHeight = Math.max(...items.map((item) => item.board_height));
        const contentId = `recent-limit-up-${tradeDate}`;
        return (
          <section className={`recent-date-group ${expanded ? "expanded" : ""}`} key={tradeDate}>
            <button
              aria-controls={contentId}
              aria-expanded={expanded}
              className="recent-date-toggle"
              type="button"
              onClick={() => toggleDate(tradeDate)}
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
      label: "近五日涨停票",
      count: `${data.recent.length} 条`,
      description: "按交易日回看最近五日涨停记录",
      icon: <TrendingUp size={18} />,
    },
  ];

  return (
    <nav className="overview-grid" aria-label="涨停池分类">
      {entries.map((entry) => (
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
    (ratings?.candidates ?? []).map((item) => [item.facts.symbol, item]),
  );
  const filteredBySymbol = new Map(
    (ratings?.filtered_out ?? []).map((item) => [item.symbol, item]),
  );
  const dynamicBySymbol = new Map(
    relayRanking.map((item) => [item.symbol, item]),
  );
  const ratingScores = new Map(
    (ratings?.candidates ?? []).map((item) => [item.facts.symbol, item.score]),
  );
  const visibleEvents = variant === "first"
    ? sortFirstBoardByRelayRanking(events, relayRanking, ratingScores)
    : events;

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
          {visibleEvents.map((event) => {
            const rating = ratingBySymbol.get(event.symbol);
            const filtered = filteredBySymbol.get(event.symbol);
            const dynamic = dynamicBySymbol.get(event.symbol);
            return (
            <tr
              className="stock-row"
              key={`${event.trade_date}-${event.symbol}`}
              onClick={() => openStock(event.symbol)}
              onKeyDown={(keyboardEvent) => {
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
  const [critic, setCritic] = useState<FirstBoardCriticResponse | null>(null);
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
  const [criticLoading, setCriticLoading] = useState(false);
  const tradingDayCacheKeyRef = useRef("");
  const fiveDayCacheKeyRef = useRef("");
  const resolvedTradeDate = stockEvent?.trade_date
    ?? data.summary.trade_date;
  const currentIntelligence = recommendationIntelligenceFor(
    intelligence,
    "relay",
    symbol,
  );

  useEffect(() => {
    let active = true;
    setStockEvent(null);
    setStockEventLoading(true);
    setStockEventError(null);
    fetchStockEvent(symbol)
      .then((event) => {
        if (active) {
          setStockEvent(event);
        }
      })
      .catch((caught) => {
        if (active) {
          setStockEventError(caught instanceof Error ? caught.message : "加载涨停事件失败");
        }
      })
      .finally(() => {
        if (active) {
          setStockEventLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [symbol]);

  useEffect(() => {
    if (stockEventLoading) {
      return;
    }
    let active = true;
    setStockNews(null);
    setStockNewsLoading(true);
    fetchStockNews(symbol, stockEvent?.name || linkedStockName || undefined, 3)
      .then((news) => {
        if (active) {
          setStockNews(news);
        }
      })
      .catch(() => {
        if (active) {
          setStockNews(null);
        }
      })
      .finally(() => {
        if (active) {
          setStockNewsLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [linkedStockName, stockEvent?.name, stockEventLoading, symbol]);

  useEffect(() => {
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
      .then((marketData) => {
        if (active) {
          setKline(marketData.kline);
          setLatestClose(marketData.latest_close);
          setPosition(marketData.position);
        }
      })
      .catch((caught) => {
        if (active) {
          const message = caught instanceof Error ? caught.message : "加载个股行情失败";
          setKlineError(message);
          setLatestCloseError(message);
          if (stockEvent) {
            setPositionError(message);
          }
        }
      })
      .finally(() => {
        if (active) {
          setKlineLoading(false);
          setLatestCloseLoading(false);
          setPositionLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [resolvedTradeDate, stockEvent?.trade_date, stockEventLoading, symbol]);

  useEffect(() => {
    setTradingDayKline([]);
    setTradingDayError(null);
    setTradingDayLoading(false);
    tradingDayCacheKeyRef.current = "";
    setFiveDayKline(null);
    setFiveDayError(null);
    setFiveDayLoading(false);
    fiveDayCacheKeyRef.current = "";
  }, [resolvedTradeDate, symbol]);

  useEffect(() => {
    if (chartMode !== "intraday" || stockEventLoading) {
      return;
    }
    const tradeDate = resolvedTradeDate;
    const cacheKey = `${symbol}:${tradeDate}:1`;
    if (tradingDayCacheKeyRef.current === cacheKey) {
      return;
    }
    let active = true;
    setTradingDayLoading(true);
    setTradingDayError(null);
    fetchStockTradingDayKLine(symbol, 1, tradeDate)
      .then((bars) => {
        if (active) {
          setTradingDayKline(bars);
          tradingDayCacheKeyRef.current = cacheKey;
        }
      })
      .catch((caught) => {
        if (active) {
          setTradingDayError(caught instanceof Error ? caught.message : "加载交易日走势失败");
        }
      })
      .finally(() => {
        if (active) {
          setTradingDayLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [chartMode, resolvedTradeDate, stockEventLoading, symbol]);

  useEffect(() => {
    if (chartMode !== "intraday5d" || stockEventLoading) {
      return;
    }
    const cacheKey = `${symbol}:${resolvedTradeDate}:5:1`;
    if (fiveDayCacheKeyRef.current === cacheKey) {
      return;
    }
    let active = true;
    setFiveDayLoading(true);
    setFiveDayError(null);
    fetchStockIntradayHistory(symbol, 5, 1, resolvedTradeDate)
      .then((history) => {
        if (active) {
          setFiveDayKline(history);
          fiveDayCacheKeyRef.current = cacheKey;
        }
      })
      .catch((caught) => {
        if (active) {
          setFiveDayError(caught instanceof Error ? caught.message : "加载五日分时失败");
        }
      })
      .finally(() => {
        if (active) {
          setFiveDayLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [chartMode, resolvedTradeDate, stockEventLoading, symbol]);

  useEffect(() => {
    if (stockEventLoading) {
      return;
    }
    const tradeDate = resolvedTradeDate;
    const cachedRating = stockEvent && data.firstBoardRatings.trade_date === tradeDate
      ? data.firstBoardRatings.candidates.find(
          (rating) => rating.facts.symbol === symbol,
        ) ?? null
      : null;
    setFirstBoardRating(cachedRating);
    if (!stockEvent) {
      return;
    }
    let active = true;
    fetchFirstBoardRatings(tradeDate, true)
      .then((ratings) => {
        if (active) {
          setFirstBoardRating(
            ratings.candidates.find((rating) => rating.facts.symbol === symbol) ?? null,
          );
        }
      })
      .catch(() => {
        if (active && !cachedRating) {
          setFirstBoardRating(null);
        }
      });

    return () => {
      active = false;
    };
  }, [
    data.firstBoardRatings,
    resolvedTradeDate,
    stockEvent?.trade_date,
    stockEventLoading,
    symbol,
  ]);

  useEffect(() => {
    if (!firstBoardRating) {
      setCritic(null);
      setCriticLoading(false);
      return;
    }

    setCritic(null);
    setCriticLoading(true);
    fetchFirstBoardCritic(symbol, firstBoardRating.facts.trade_date)
      .then(setCritic)
      .catch(() => setCritic(null))
      .finally(() => setCriticLoading(false));
  }, [firstBoardRating, symbol]);

  const intradayReferencePrice = useMemo(() => {
    const eventIndex = kline.findIndex(
      (bar) => bar.trade_date === resolvedTradeDate,
    );
    return eventIndex > 0 ? kline[eventIndex - 1].close : null;
  }, [kline, resolvedTradeDate]);
  const fiveDayChartBars = useMemo(
    () => toFiveDayIntradayCandleBars(fiveDayKline),
    [fiveDayKline],
  );
  const fiveDayReferencePrice = useMemo(
    () => fiveDayKline?.days.find((day) => day.bars.length > 0)?.previous_close ?? null,
    [fiveDayKline],
  );

  if (stockEventLoading) {
    return <ShellState label="正在加载个股详情..." />;
  }

  return (
    <div className="stock-detail">
      <section className="stock-hero">
        <div>
          <p className="eyebrow">{resolvedTradeDate}</p>
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
                onClick={() => setChartMode("daily")}
              >
                日 K · 60日
              </button>
              <button
                type="button"
                aria-pressed={chartMode === "intraday"}
                onClick={() => setChartMode("intraday")}
              >
                分时
              </button>
              <button
                type="button"
                aria-pressed={chartMode === "intraday5d"}
                onClick={() => setChartMode("intraday5d")}
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

      {firstBoardRating || criticLoading || critic ? (
        <section className="stock-agent-grid">
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
          {criticLoading || critic ? (
            <FirstBoardCriticPanel
              data={critic}
              loading={criticLoading}
            />
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

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
          {news?.items.slice(0, 3).map((item) => (
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


function FirstBoardCriticPanel({
  data,
  loading,
}: {
  data: FirstBoardCriticResponse | null;
  loading: boolean;
}) {
  /** Render critic-side review that challenges the original rating. */

  const verdictCopy = {
    supportive: "证据较稳",
    cautious: "需要谨慎",
    fragile: "结论偏脆弱",
  };

  if (loading) {
    return (
      <Panel title="Critic 复核" icon={<ShieldAlert size={18} />}>
        <div className="rating-detail-empty">正在复核评分可靠性...</div>
      </Panel>
    );
  }

  if (!data) {
    return null;
  }

  return (
    <Panel title="Critic 复核" icon={<ShieldAlert size={18} />}>
      <div className="critic-detail">
        <div className={`critic-verdict critic-${data.verdict}`}>
          <div>
            <span>复核结论</span>
            <strong>{verdictCopy[data.verdict]}</strong>
          </div>
          <div>
            <span>置信度建议</span>
            <strong>
              {formatPercent(data.original_confidence)} {"->"} {formatPercent(data.suggested_confidence)}
            </strong>
          </div>
        </div>

        <TagSection title="支持证据" items={data.support_evidence} tone="good" />
        <TagSection title="反向证据" items={data.counter_evidence} tone="risk" />
        {data.missing_data.length > 0 ? (
          <TagSection title="缺失数据" items={data.missing_data} tone="muted" />
        ) : null}
        <TagSection title="复盘问题" items={data.review_questions} tone="muted" />

        {data.critic_warnings.length > 0 ? (
          <div className="critic-warnings">
            {data.critic_warnings.map((warning) => (
              <span key={warning}>{warning}</span>
            ))}
          </div>
        ) : null}
      </div>
    </Panel>
  );
}

function FirstBoardRatingDetail({
  intelligence,
  rating,
}: {
  intelligence: RecommendationIntelligenceItem | null;
  rating: FirstBoardRating;
}) {
  /** Render explainable first-board score details for the selected stock. */

  const scoreBreakdown = rating.score_breakdown.map((item) => {
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
  const boardPatternScore = scoreBreakdown.find((item) => item.name === "上板形态");
  const marketCapScore = scoreBreakdown.find((item) => item.name === "市值偏好");
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
            {scoreBreakdown.map((item) => (
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
        {position.tags.map((tag) => <span key={tag}>{tag}</span>)}
      </div>
      <ul>
        {position.evidence.map((item) => <li key={item}>{item}</li>)}
      </ul>
      {position.alternatives.length > 0 ? (
        <small>
          次选：{position.alternatives.map((item) => `${item.label} ${item.score.toFixed(0)}`).join("；")}
        </small>
      ) : null}
    </section>
  );
}

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
        {items.map((item) => (
          <span className={`detail-tag tag-${tone}`} key={item}>{item}</span>
        ))}
      </div>
    </div>
  );
}
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
function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="fact-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function toDailyCandleBars(bars: StockKLineBar[]): MarketCandleBar[] {
  /** Convert API daily K-line bars into chart-friendly candle bars. */

  return bars.map((bar) => ({
    time: bar.trade_date,
    label: bar.trade_date,
    open: bar.open,
    close: bar.close,
    high: bar.high,
    low: bar.low,
    volume: bar.volume,
  }));
}

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
