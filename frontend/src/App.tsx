import {
  ArrowLeft,
  BarChart3,
  Flame,
  Layers3,
  LineChart,
  RefreshCcw,
  ShieldAlert,
  TrendingUp,
  WalletCards,
} from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import {
  Link,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";

import {
  fetchContinuedBoardEvents,
  fetchFailedLimitUpEvents,
  fetchFirstBoardEvents,
  fetchMarketSummary,
  fetchRecentLimitUpEvents,
  fetchStockKLine,
  fetchStockTradingDayKLine,
} from "./api";
import type {
  LimitUpEvent,
  MarketSummary,
  StockIntradayKLineBar,
  StockKLineBar,
} from "./types";

type ViewKey = "overview" | "first" | "continued" | "failed" | "recent";

interface DashboardData {
  summary: MarketSummary;
  firstBoard: LimitUpEvent[];
  continuedBoard: LimitUpEvent[];
  failed: LimitUpEvent[];
  recent: LimitUpEvent[];
}

interface CandleBar {
  label: string;
  open: number;
  close: number;
  high: number;
  low: number;
}

const viewMeta: Record<ViewKey, { title: string; eyebrow: string }> = {
  overview: { title: "短线市场概况", eyebrow: "Overview" },
  first: { title: "首板票", eyebrow: "First Board" },
  continued: { title: "连板票", eyebrow: "Continued Board" },
  failed: { title: "炸板票", eyebrow: "Failed Limit-Up" },
  recent: { title: "近三日涨停票复盘", eyebrow: "Recent Limit-Up" },
};

const routeToView: Record<string, ViewKey> = {
  "/": "overview",
  "/stocks/first-board": "first",
  "/stocks/continued-board": "continued",
  "/stocks/failed": "failed",
  "/stocks/recent-limit-up": "recent",
};

const stockListPaths = new Set([
  "/stocks/first-board",
  "/stocks/continued-board",
  "/stocks/failed",
  "/stocks/recent-limit-up",
]);

const sentimentCopy = {
  heating: { label: "升温", detail: "连板梯队在抬升，风险偏好更积极" },
  diverging: { label: "分歧", detail: "涨停数量仍在，但封板稳定性需要观察" },
  cooling: { label: "退潮", detail: "炸板压力偏高，短线接力需要降速" },
};

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
      const [summary, firstBoard, continuedBoard, failed, recent] = await Promise.all([
        fetchMarketSummary(),
        fetchFirstBoardEvents(),
        fetchContinuedBoardEvents(),
        fetchFailedLimitUpEvents(),
        fetchRecentLimitUpEvents(3),
      ]);

      setData({ summary, firstBoard, continuedBoard, failed, recent });
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
  const activeMeta = isStockDetail
    ? { title: "个股详情", eyebrow: "Stock Detail" }
    : viewMeta[activeView];

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">{activeMeta.eyebrow}</p>
          <h1>{activeMeta.title}</h1>
        </div>
        <div className="topbar-actions">
          {activeView !== "overview" || isStockDetail ? (
            <Link className="text-button" to="/">
              <ArrowLeft size={17} />
              返回概况
            </Link>
          ) : null}
          <button className="icon-button" onClick={loadDashboard} title="刷新数据">
            <RefreshCcw size={18} />
          </button>
        </div>
      </header>

      <Routes>
        <Route path="/" element={<Overview data={data} />} />
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

function Overview({ data }: { data: DashboardData }) {
  /** Render the top-level after-close market review dashboard. */

  const sentiment = sentimentCopy[data.summary.sentiment];

  return (
    <>
      <section className="hero-summary">
        <div>
          <p className="eyebrow">{data.summary.trade_date}</p>
          <h2>{sentiment.label}</h2>
          <p>{sentiment.detail}</p>
        </div>
        <div className="hero-numbers">
          <span>最高连板</span>
          <strong>{data.summary.max_board_height} 板</strong>
        </div>
      </section>

      <section className="overview-grid">
        <EntryCard
          icon={<Flame size={20} />}
          label="首板票"
          value={`${data.summary.first_board_count} 只`}
          caption="查看今日首板个股和封板质量"
          to="/stocks/first-board"
        />
        <EntryCard
          icon={<Layers3 size={20} />}
          label="连板票"
          value={`${data.summary.continued_board_count} 只`}
          caption={`最高 ${data.summary.max_board_height} 板`}
          to="/stocks/continued-board"
        />
        <EntryCard
          icon={<ShieldAlert size={20} />}
          label="炸板票"
          value={`${data.summary.failed_count} 只`}
          caption={`炸板率 ${formatPercent(data.summary.failed_limit_up_rate)}`}
          to="/stocks/failed"
        />
        <EntryCard
          icon={<WalletCards size={20} />}
          label="涨停成交额"
          value={formatAmount(data.summary.total_amount)}
          caption="今日涨停池合计成交额"
        />
      </section>

      <section className="overview-content">
        <Panel title="大盘走势" icon={<LineChart size={18} />}>
          <div className="index-grid">
            {data.summary.indices.map((index) => (
              <article className="index-card" key={index.symbol}>
                <div>
                  <span>{index.name}</span>
                  <strong>{index.close.toFixed(2)}</strong>
                </div>
                <Sparkline values={index.trend} />
                <b className={index.change_pct >= 0 ? "positive" : "negative"}>
                  {formatSigned(index.change_pct, 2)}%
                </b>
              </article>
            ))}
          </div>
        </Panel>

        <Panel title="题材热度" icon={<BarChart3 size={18} />}>
          <div className="topic-list">
            {data.summary.hot_concepts.map((concept) => (
              <div className="topic-row" key={concept.name}>
                <strong>{concept.name}</strong>
                <span>
                  {concept.limit_up_count} 涨停 / {concept.failed_count} 炸板
                </span>
              </div>
            ))}
          </div>
        </Panel>
      </section>

      <Link className="recent-entry" to="/stocks/recent-limit-up">
        <div>
          <TrendingUp size={22} />
          <strong>查看最近三个交易日涨停过的股票</strong>
        </div>
        <span>{data.recent.length} 个涨停事件</span>
      </Link>
    </>
  );
}

function DetailView({ view, data }: { view: ViewKey; data: DashboardData }) {
  /** Render one of the latest-day stock list views. */

  const eventsByView = {
    first: data.firstBoard,
    continued: data.continuedBoard,
    failed: data.failed,
    overview: [],
    recent: [],
  };

  return (
    <Panel title={viewMeta[view].title} icon={detailIcon(view)}>
      <StockTable events={eventsByView[view]} variant={view} />
    </Panel>
  );
}

function RecentLimitUp({ events }: { events: LimitUpEvent[] }) {
  /** Group recent events by persisted trading date for review. */

  const grouped = useMemo(() => {
    return events.reduce<Record<string, LimitUpEvent[]>>((groups, event) => {
      groups[event.trade_date] = groups[event.trade_date] ?? [];
      groups[event.trade_date].push(event);
      return groups;
    }, {});
  }, [events]);

  return (
    <div className="recent-groups">
      {Object.entries(grouped).map(([tradeDate, items]) => (
        <Panel key={tradeDate} title={tradeDate} icon={<TrendingUp size={18} />}>
          <StockTable events={items} variant="recent" />
        </Panel>
      ))}
    </div>
  );
}

function StockTable({
  events,
  variant,
}: {
  events: LimitUpEvent[];
  variant: ViewKey;
}) {
  /** Shared clickable table for all stock-list routes. */

  const navigate = useNavigate();

  function openStock(symbol: string) {
    navigate(`/stocks/${symbol}`);
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>股票</th>
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
          {events.map((event) => (
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
              <td>{event.trade_date}</td>
              <td>{event.board_height} 板</td>
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
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StockDetail({ data }: { data: DashboardData }) {
  /** Render one stock's event facts together with daily and intraday K-lines. */

  const { symbol = "" } = useParams();
  const [kline, setKline] = useState<StockKLineBar[]>([]);
  const [tradingDayKline, setTradingDayKline] = useState<StockIntradayKLineBar[]>([]);
  const [klineLoading, setKlineLoading] = useState(true);
  const [klineError, setKlineError] = useState<string | null>(null);
  const [tradingDayLoading, setTradingDayLoading] = useState(true);
  const [tradingDayError, setTradingDayError] = useState<string | null>(null);
  const events = useMemo(
    () => [...data.firstBoard, ...data.continuedBoard, ...data.failed, ...data.recent],
    [data],
  );
  const stockEvent = useMemo(() => {
    return events
      .filter((event) => event.symbol === symbol)
      .sort((left, right) => right.trade_date.localeCompare(left.trade_date))[0];
  }, [events, symbol]);

  useEffect(() => {
    const tradeDate = stockEvent?.trade_date;
    if (!tradeDate) {
      setKlineLoading(false);
      setTradingDayLoading(false);
      return;
    }

    setKlineLoading(true);
    setKlineError(null);
    fetchStockKLine(symbol, 5)
      .then(setKline)
      .catch((caught) => {
        setKlineError(caught instanceof Error ? caught.message : "加载五日 K 线失败");
      })
      .finally(() => setKlineLoading(false));

    setTradingDayLoading(true);
    setTradingDayError(null);
    fetchStockTradingDayKLine(symbol, 5)
      .then(setTradingDayKline)
      .catch((caught) => {
        setTradingDayError(caught instanceof Error ? caught.message : "加载交易日走势失败");
      })
      .finally(() => setTradingDayLoading(false));
  }, [stockEvent?.trade_date, symbol]);

  if (!stockEvent) {
    return (
      <ShellState
        label="未找到这只股票"
        detail="请从涨停列表中选择一只股票进入详情"
      />
    );
  }

  return (
    <div className="stock-detail">
      <section className="stock-hero">
        <div>
          <p className="eyebrow">{stockEvent.trade_date}</p>
          <h2>{stockEvent.name}</h2>
          <span>{stockEvent.symbol}</span>
        </div>
        <div className="stock-status">
          <strong>{stockEvent.board_height} 板</strong>
          <span>{stockEvent.closed_limit ? "已封板" : "未回封"}</span>
        </div>
      </section>

      <section className="stock-detail-grid">
        <div className="chart-stack">
          <Panel title="五日 K 线" icon={<LineChart size={18} />}>
            {klineLoading ? (
              <div className="chart-state">正在加载 K 线...</div>
            ) : klineError ? (
              <div className="chart-state">{klineError}</div>
            ) : (
              <CandlestickChart bars={toDailyCandleBars(kline)} emptyLabel="暂无五日 K 线数据" />
            )}
          </Panel>

          <Panel title="交易日走势" icon={<LineChart size={18} />}>
            {tradingDayLoading ? (
              <div className="chart-state">正在加载交易日走势...</div>
            ) : tradingDayError ? (
              <div className="chart-state">{tradingDayError}</div>
            ) : (
              <CandlestickChart
                bars={toIntradayCandleBars(tradingDayKline)}
                emptyLabel="暂无交易日走势数据"
                dense
              />
            )}
          </Panel>
        </div>

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
      </section>
    </div>
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

function toDailyCandleBars(bars: StockKLineBar[]): CandleBar[] {
  /** Convert API daily K-line bars into chart-friendly candle bars. */

  return bars.map((bar) => ({
    label: bar.trade_date.slice(5),
    open: bar.open,
    close: bar.close,
    high: bar.high,
    low: bar.low,
  }));
}

function toIntradayCandleBars(bars: StockIntradayKLineBar[]): CandleBar[] {
  /** Convert API intraday bars into chart-friendly candle bars. */

  return bars.map((bar) => ({
    label: bar.timestamp.slice(11, 16),
    open: bar.open,
    close: bar.close,
    high: bar.high,
    low: bar.low,
  }));
}

function CandlestickChart({
  bars,
  emptyLabel,
  dense = false,
}: {
  bars: CandleBar[];
  emptyLabel: string;
  dense?: boolean;
}) {
  /** Draw a lightweight SVG candlestick chart without external chart libraries. */

  if (bars.length === 0) {
    return <div className="chart-state">{emptyLabel}</div>;
  }

  const width = 640;
  const height = 300;
  const padding = { top: 24, right: 28, bottom: 42, left: 56 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const prices = bars.flatMap((bar) => [bar.high, bar.low]);
  const minPrice = Math.min(...prices);
  const maxPrice = Math.max(...prices);
  const priceRange = maxPrice - minPrice || 1;
  const candleWidth = Math.max(
    dense ? 3 : 12,
    Math.min(dense ? 10 : 44, chartWidth / Math.max(bars.length, 1) * 0.45),
  );

  function xFor(index: number) {
    return padding.left + (index + 0.5) * (chartWidth / bars.length);
  }

  function yFor(price: number) {
    return padding.top + ((maxPrice - price) / priceRange) * chartHeight;
  }

  const gridPrices = [maxPrice, (maxPrice + minPrice) / 2, minPrice];

  return (
    <div className="kline-wrap">
      <svg className="kline-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="K 线图">
        {gridPrices.map((price) => {
          const y = yFor(price);
          return (
            <g key={price}>
              <line x1={padding.left} x2={width - padding.right} y1={y} y2={y} />
              <text x={padding.left - 10} y={y + 4} textAnchor="end">
                {price.toFixed(2)}
              </text>
            </g>
          );
        })}

        {bars.map((bar, index) => {
          const x = xFor(index);
          const openY = yFor(bar.open);
          const closeY = yFor(bar.close);
          const highY = yFor(bar.high);
          const lowY = yFor(bar.low);
          const rising = bar.close >= bar.open;
          const bodyY = Math.min(openY, closeY);
          const bodyHeight = Math.max(Math.abs(closeY - openY), 3);

          return (
            <g className={rising ? "kline-up" : "kline-down"} key={`${bar.label}-${index}`}>
              <line className="wick" x1={x} x2={x} y1={highY} y2={lowY} />
              <rect
                x={x - candleWidth / 2}
                y={bodyY}
                width={candleWidth}
                height={bodyHeight}
                rx={2}
              />
              {(!dense || index % 8 === 0 || index === bars.length - 1) ? (
                <text x={x} y={height - 16} textAnchor="middle">
                  {bar.label}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
      <div className="kline-caption">
        <span>开</span>
        <span>高</span>
        <span>低</span>
        <span>收</span>
      </div>
    </div>
  );
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
    <main className="state-shell">
      <div>
        <p className="eyebrow">LimitUpLab</p>
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

function EntryCard({
  icon,
  label,
  value,
  caption,
  to,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  caption: string;
  to?: string;
}) {
  const content = (
    <>
      <div className="metric-icon">{icon}</div>
      <span>{label}</span>
      <strong>{value}</strong>
      <p>{caption}</p>
    </>
  );

  if (to) {
    return (
      <Link className="entry-card" to={to}>
        {content}
      </Link>
    );
  }

  return <article className="entry-card">{content}</article>;
}

function Panel({
  title,
  icon,
  children,
}: {
  title: string;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      <header>
        <div>
          {icon}
          <h2>{title}</h2>
        </div>
      </header>
      {children}
    </section>
  );
}

function Sparkline({ values }: { values: number[] }) {
  /** Draw the compact index trend sparkline shown on overview cards. */

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const points = values
    .map((value, index) => {
      const x = (index / Math.max(values.length - 1, 1)) * 100;
      const y = 36 - ((value - min) / range) * 30;
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <svg className="sparkline" viewBox="0 0 100 40" role="img" aria-label="指数走势">
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="3" />
    </svg>
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

function formatPercent(value: number) {
  return `${Math.round(value * 100)}%`;
}

function formatAmount(value: number) {
  return `${(value / 100_000_000).toFixed(1)} 亿`;
}

function formatSigned(value: number, decimals = 1) {
  return value > 0 ? `+${value.toFixed(decimals)}` : value.toFixed(decimals);
}
