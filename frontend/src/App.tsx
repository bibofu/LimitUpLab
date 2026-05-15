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
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";

import {
  fetchContinuedBoardEvents,
  fetchFailedLimitUpEvents,
  fetchFirstBoardEvents,
  fetchMarketSummary,
  fetchRecentLimitUpEvents,
} from "./api";
import type { LimitUpEvent, MarketSummary } from "./types";

type ViewKey = "overview" | "first" | "continued" | "failed" | "recent";

interface DashboardData {
  summary: MarketSummary;
  firstBoard: LimitUpEvent[];
  continuedBoard: LimitUpEvent[];
  failed: LimitUpEvent[];
  recent: LimitUpEvent[];
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

  const activeMeta = viewMeta[activeView];

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">{activeMeta.eyebrow}</p>
          <h1>{activeMeta.title}</h1>
        </div>
        <div className="topbar-actions">
          {activeView !== "overview" ? (
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
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </main>
  );
}

function Overview({ data }: { data: DashboardData }) {
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
                  {formatSigned(index.change_pct)}%
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
            <tr key={`${event.trade_date}-${event.symbol}`}>
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

function formatSigned(value: number) {
  return value > 0 ? `+${value.toFixed(1)}` : value.toFixed(1);
}
