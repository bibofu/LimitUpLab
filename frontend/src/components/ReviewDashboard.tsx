import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  GitBranch,
  Landmark,
  LoaderCircle,
  RefreshCcw,
  ShieldAlert,
  TrendingUp,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import {
  fetchDailyReviewSnapshots,
  fetchDragonTigerReview,
  fetchReviewAgentReport,
  fetchScoringErrorDiagnostic,
} from "../api";
import type {
  DailyBoardPromotionStat,
  DailyReviewSnapshotSummary,
  DragonTigerReviewResponse,
  ReviewAgentPick,
  ReviewAgentReportResponse,
  ReviewPromotionComparison,
  ScoringErrorDiagnosticResponse,
} from "../types";
import {
  formatEmpiricalRate,
  formatNetAmount,
  formatOptionalPercent,
  formatPercent,
  formatSigned,
  numberTone,
  stockDetailPath,
} from "../dashboardFormatters";
import { Panel } from "./Panel";

interface ReviewDashboardProps {
  dailyBoardPromotion: DailyBoardPromotionStat[];
  latestTradeDate: string;
}

export function ReviewDashboard({
  dailyBoardPromotion,
  latestTradeDate,
}: ReviewDashboardProps) {
  return (
    <>
      <HighScoreReviewPanel latestTradeDate={latestTradeDate} />
      <DailyBoardPromotionPanel stats={dailyBoardPromotion} />
      <DragonTigerReviewPanel tradeDate={latestTradeDate} />
    </>
  );
}

type DragonTigerFilter = "all" | "organization" | "hot_money";

function DragonTigerReviewPanel({ tradeDate }: { tradeDate: string }) {
  /** Load post-close Dragon-Tiger facts without delaying the rest of the dashboard. */

  const [data, setData] = useState<DragonTigerReviewResponse | null>(null);
  const [filter, setFilter] = useState<DragonTigerFilter>("all");
  const [expanded, setExpanded] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setData(null);
    setError(null);
    void fetchDragonTigerReview(tradeDate)
      .then((response) => {
        if (active) {
          setData(response);
        }
      })
      .catch(() => {
        if (active) {
          setError("龙虎榜数据暂时没有加载成功");
        }
      });
    return () => {
      active = false;
    };
  }, [reloadToken, tradeDate]);

  const filteredItems = useMemo(() => {
    const items = data?.items ?? [];
    if (filter === "organization") {
      return items.filter((item) => item.organization_net_buy_amount !== null);
    }
    if (filter === "hot_money") {
      return items.filter((item) => item.hot_money_net_buy_amount !== null);
    }
    return items;
  }, [data, filter]);
  const visibleItems = expanded ? filteredItems : filteredItems.slice(0, 20);

  function selectFilter(nextFilter: DragonTigerFilter) {
    setFilter(nextFilter);
    setExpanded(false);
  }

  return (
    <section className="dragon-tiger-section">
      <Panel
        title="龙虎榜"
        icon={<Landmark size={18} />}
        actions={(
          <div className="dragon-tiger-filters" role="tablist" aria-label="龙虎榜类型">
            {([
              ["all", "全部"],
              ["organization", "机构"],
              ["hot_money", "游资"],
            ] as const).map(([value, label]) => (
              <button
                type="button"
                role="tab"
                aria-selected={filter === value}
                className={filter === value ? "active" : ""}
                key={value}
                onClick={() => selectFilter(value)}
              >
                {label}
              </button>
            ))}
          </div>
        )}
      >
        {!data && !error ? (
          <div className="dragon-tiger-state">
            <LoaderCircle className="state-spinner" size={22} />
            <span>正在获取 {tradeDate} 龙虎榜...</span>
          </div>
        ) : null}
        {error ? (
          <div className="dragon-tiger-state">
            <span>{error}</span>
            <button type="button" onClick={() => setReloadToken((value) => value + 1)}>
              <RefreshCcw size={15} />
              重试
            </button>
          </div>
        ) : null}
        {data ? (
          <div className="dragon-tiger-content">
            <div className="dragon-tiger-metrics">
              <span><small>交易日</small><strong>{data.trade_date}</strong></span>
              <span><small>上榜股票</small><strong>{data.stock_count}<em>只</em></strong></span>
              <span><small>净流入股票</small><strong className="positive">{data.net_inflow_count}<em>只</em></strong></span>
              <span><small>机构席位</small><strong>{data.organization_count}<em>只</em></strong></span>
              <span><small>游资席位</small><strong>{data.hot_money_count}<em>只</em></strong></span>
            </div>

            {filteredItems.length > 0 ? (
              <>
                <div className="table-wrap dragon-tiger-table-wrap">
                  <table className="dragon-tiger-table">
                    <thead>
                      <tr>
                        <th>股票</th>
                        <th>涨跌幅</th>
                        <th>净买额</th>
                        <th>机构净额</th>
                        <th>游资净额</th>
                        <th>上榜信息</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleItems.map((item) => {
                        const stockIdentity = (
                          <>
                            <strong>{item.name}</strong>
                            <small>{item.symbol}{item.hot_rank ? ` · 人气 ${item.hot_rank}` : ""}</small>
                          </>
                        );
                        return (
                          <tr key={item.symbol}>
                            <td className="dragon-tiger-stock">
                              {item.detail_trade_date ? (
                                <Link to={stockDetailPath(item.symbol, item.detail_trade_date)}>
                                  {stockIdentity}
                                  <ChevronRight size={15} aria-hidden="true" />
                                </Link>
                              ) : (
                                <div>{stockIdentity}</div>
                              )}
                            </td>
                            <td className={numberTone(item.change_pct)}>
                              {formatOptionalPercent(item.change_pct)}
                            </td>
                            <td className={numberTone(item.net_buy_amount)}>
                              {formatNetAmount(item.net_buy_amount)}
                            </td>
                            <td className={numberTone(item.organization_net_buy_amount)}>
                              {formatNetAmount(item.organization_net_buy_amount)}
                            </td>
                            <td className={numberTone(item.hot_money_net_buy_amount)}>
                              {formatNetAmount(item.hot_money_net_buy_amount)}
                            </td>
                            <td className="dragon-tiger-reason">
                              <strong>{item.limit_reason || "上榜原因待补充"}</strong>
                              <small>
                                {item.concepts.slice(0, 3).join(" · ") || "题材待补充"}
                                {item.range_days && item.range_days > 1 ? ` · ${item.range_days}日统计` : ""}
                              </small>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                {filteredItems.length > 20 ? (
                  <div className="dragon-tiger-expand">
                    <button type="button" onClick={() => setExpanded((value) => !value)}>
                      {expanded ? "收起榜单" : `查看全部 ${filteredItems.length} 只`}
                    </button>
                  </div>
                ) : null}
              </>
            ) : (
              <div className="dragon-tiger-state">当前分类暂无龙虎榜股票。</div>
            )}
          </div>
        ) : null}
      </Panel>
    </section>
  );
}

function DailyBoardPromotionPanel({ stats }: { stats: DailyBoardPromotionStat[] }) {
  /** Let users inspect five daily promotion cohorts and their successful stocks. */

  const recentStats = useMemo(() => stats.slice(-5), [stats]);
  const displayStats = useMemo(() => [...recentStats].reverse(), [recentStats]);
  const latestDate = recentStats[recentStats.length - 1]?.trade_date ?? "";
  const [selectedDate, setSelectedDate] = useState(latestDate);

  useEffect(() => {
    if (!recentStats.some((item) => item.trade_date === selectedDate)) {
      setSelectedDate(latestDate);
    }
  }, [latestDate, recentStats, selectedDate]);

  const selectedIndex = recentStats.findIndex((item) => item.trade_date === selectedDate);
  const selected = recentStats[selectedIndex] ?? recentStats[recentStats.length - 1];
  const previous = selectedIndex > 0 ? recentStats[selectedIndex - 1] : undefined;
  if (!selected) {
    return (
      <section className="promotion-section">
        <Panel title="每日连板晋级率" icon={<GitBranch size={18} />}>
          <div className="empty-state">相邻交易日收盘数据不足，暂时无法计算晋级率。</div>
        </Panel>
      </section>
    );
  }
  const change = previous ? selected.probability - previous.probability : null;

  return (
    <section className="promotion-section">
      <Panel title="每日连板晋级率" icon={<GitBranch size={18} />}>
        <div className="promotion-panel">
          <div className="promotion-latest">
            <div>
              <span>{selected.trade_date} 晋级观察</span>
              <strong>{formatEmpiricalRate(selected.probability)}</strong>
              <small>
                {selected.previous_trade_date} 封板 {selected.sample_size} 只，
                当日收盘晋级 {selected.promoted_count} 只
              </small>
            </div>
            <div className="promotion-cohort-summary">
              <span>
                <small>首板→二板</small>
                <b>{formatEmpiricalRate(selected.first_board_probability)}</b>
                <em>{selected.first_board_promoted_count}/{selected.first_board_sample_size}</em>
              </span>
              <span>
                <small>连板梯队晋级</small>
                <b>{formatEmpiricalRate(selected.continued_board_probability)}</b>
                <em>{selected.continued_board_promoted_count}/{selected.continued_board_sample_size}</em>
              </span>
              <span>
                <small>较前一日</small>
                <b className={(change ?? 0) >= 0 ? "positive" : "negative"}>
                  {change === null ? "暂无" : `${formatSigned(change * 100, 1)} 个百分点`}
                </b>
                <em>总晋级率变化</em>
              </span>
            </div>
          </div>

          <div className="promotion-history" aria-label="近5个交易日晋级率">
            {displayStats.map((item) => (
              <button
                type="button"
                className={`promotion-day ${item.trade_date === selected.trade_date ? "active" : ""}`}
                key={item.trade_date}
                aria-pressed={item.trade_date === selected.trade_date}
                onClick={() => setSelectedDate(item.trade_date)}
              >
                <div>
                  <time dateTime={item.trade_date}>{item.trade_date.slice(5)}</time>
                  <strong>{formatEmpiricalRate(item.probability)}</strong>
                </div>
                <div className="promotion-rate-track" aria-hidden="true">
                  <span style={{ width: `${Math.max(item.probability * 100, 2)}%` }} />
                </div>
                <small>
                  晋级 {item.promoted_count}/{item.sample_size} · 首→二 {item.first_board_promoted_count}/{item.first_board_sample_size}
                </small>
              </button>
            ))}
          </div>

          <div className="promotion-success-section">
            <div className="promotion-success-heading">
              <div>
                <strong>{selected.trade_date} 晋级成功</strong>
                <small>点击股票查看 K 线、封板信息和首板位置判断</small>
              </div>
              <b>{selected.promoted_stocks.length} 只</b>
            </div>
            {selected.promoted_stocks.length > 0 ? (
              <div className="promotion-stock-list">
                {selected.promoted_stocks.map((stock) => (
                  <Link
                    className="promotion-stock-row"
                    to={stockDetailPath(stock.symbol, selected.trade_date)}
                    key={stock.symbol}
                  >
                    <div>
                      <strong>{stock.name}</strong>
                      <small>{stock.symbol} · {stock.industry || "行业待补充"}</small>
                    </div>
                    <span>{stock.from_board_height}→{stock.to_board_height}板</span>
                    <small>{stock.concept || "题材待补充"}</small>
                    <em>首封 {stock.first_limit_time.slice(0, 5)} · 炸板 {stock.break_count}</em>
                    <ChevronRight size={17} aria-hidden="true" />
                  </Link>
                ))}
              </div>
            ) : (
              <div className="promotion-success-empty">当日没有识别到收盘晋级成功的股票。</div>
            )}
          </div>
        </div>
      </Panel>
    </section>
  );
}

function HighScoreReviewPanel({ latestTradeDate }: { latestTradeDate: string }) {
  const [report, setReport] = useState<ReviewAgentReportResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeReviewSelection, setActiveReviewSelection] = useState<string | null>(null);
  const [snapshotDates, setSnapshotDates] = useState<DailyReviewSnapshotSummary[]>([]);
  const [selectedAsOfDate, setSelectedAsOfDate] = useState(latestTradeDate);
  const [scoringDiagnostic, setScoringDiagnostic] = useState<ScoringErrorDiagnosticResponse | null>(null);

  useEffect(() => {
    let active = true;
    void fetchDailyReviewSnapshots()
      .then((response) => {
        if (!active) return;
        setSnapshotDates(response.snapshots);
        setSelectedAsOfDate((current) => (
          response.snapshots.some((item) => item.as_of_date === current)
            ? current
            : response.snapshots[0]?.as_of_date ?? latestTradeDate
        ));
      })
      .catch(() => {
        if (active) setSnapshotDates([]);
      });
    return () => {
      active = false;
    };
  }, [latestTradeDate]);

  useEffect(() => {
    void loadReview(selectedAsOfDate);
  }, [selectedAsOfDate]);

  async function loadReview(endDate: string) {
    setRunning(true);
    setError(null);
    setScoringDiagnostic(null);
    try {
      const [response, diagnostic] = await Promise.all([
        fetchReviewAgentReport({
          end_date: endDate,
          top_per_day: 10,
          follow_days: 5,
          use_llm: false,
        }),
        fetchScoringErrorDiagnostic(endDate).catch(() => null),
      ]);
      setReport(response);
      setScoringDiagnostic(diagnostic);
      const grouped = groupReviewPicksByDate(response.reviewed_picks);
      const trackDates = buildReviewTrackDates(grouped, response.end_date);
      setActiveReviewSelection((current) => (
        current && (
          trackDates.includes(current)
          || current === REVIEW_SUCCESS_SELECTION
          || current === REVIEW_MISS_SELECTION
        )
          ? current
          : trackDates[0] ?? REVIEW_SUCCESS_SELECTION
      ));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Review Agent 复盘失败");
    } finally {
      setRunning(false);
    }
  }

  const readyCount = report
    ? report.sample_size - report.pending_count
    : 0;
  const successRate = readyCount > 0 && report
    ? report.success_count / readyCount
    : null;
  const reviewedPicks = report?.reviewed_picks ?? [];
  const reviewDates = groupReviewPicksByDate(reviewedPicks);
  const trackDates = report ? buildReviewTrackDates(reviewDates, report.end_date) : [];
  const trackedSampleSize = trackDates.reduce(
    (total, tradeDate) => total + (reviewDates[tradeDate]?.length ?? 0),
    0,
  );
  const trackedPendingCount = trackDates.reduce(
    (total, tradeDate) => total + (reviewDates[tradeDate] ?? []).filter((item) => !item.outcome_ready).length,
    0,
  );
  const trackedReadyCount = trackedSampleSize - trackedPendingCount;
  const trackedSuccessCount = trackDates.reduce(
    (total, tradeDate) => total + (reviewDates[tradeDate] ?? []).filter(
      (item) => (latestTrackedReturn(item) ?? 0) > 0,
    ).length,
    0,
  );
  const trackedSuccessRate = trackedReadyCount > 0 ? trackedSuccessCount / trackedReadyCount : null;
  const promotionComparisons = (report?.promotion_comparisons ?? []).reduce<
    Record<string, ReviewPromotionComparison>
  >((items, item) => {
    items[item.trade_date] = item;
    return items;
  }, {});
  const successfulPicks = sortReviewPicksForSummary(
    reviewedPicks.filter((item) => (latestTrackedReturn(item) ?? 0) > 0),
    "desc",
  );
  const failedPicks = sortReviewPicksForSummary(
    reviewedPicks.filter((item) => (latestTrackedReturn(item) ?? 0) < 0),
    "asc",
  );
  const selectedReviewSelection = activeReviewSelection && (
    trackDates.includes(activeReviewSelection)
    || activeReviewSelection === REVIEW_SUCCESS_SELECTION
    || activeReviewSelection === REVIEW_MISS_SELECTION
  )
    ? activeReviewSelection
    : trackDates[0] ?? REVIEW_SUCCESS_SELECTION;
  const selectedPicks = selectedReviewSelection === REVIEW_SUCCESS_SELECTION
    ? successfulPicks
    : selectedReviewSelection === REVIEW_MISS_SELECTION
      ? failedPicks
      : reviewDates[selectedReviewSelection] ?? [];

  return (
    <Panel title="高分票追踪复盘" icon={<TrendingUp size={18} />}>
      <div className="review-agent-panel">
        <div className="review-agent-hero">
          <div>
            <span>High Score Review</span>
            <strong>
              {report
                ? `${report.start_date} 至 ${report.end_date}`
                : `截至 ${latestTradeDate}`}
            </strong>
            <p>
              {report
                ? `过去 5 个交易日，每天一个卡片；点进去看当日 Top10 到 ${report.end_date} 收盘的走势。`
                : "自动加载过去 5 个交易日的每日 Top10 评分票，点击日期卡片查看到最新收盘的兑现情况。"}
            </p>
          </div>
        </div>

        {error ? <p className="review-agent-error">{error}</p> : null}

        <div className="review-date-selector">
          <label htmlFor="review-as-of-date">复盘截止日</label>
          <select
            id="review-as-of-date"
            onChange={(event) => setSelectedAsOfDate(event.target.value)}
            value={selectedAsOfDate}
          >
            {(snapshotDates.length > 0
              ? snapshotDates
              : [{ as_of_date: latestTradeDate } as DailyReviewSnapshotSummary]
            ).map((item) => (
              <option key={item.as_of_date} value={item.as_of_date}>
                {item.as_of_date}
              </option>
            ))}
          </select>
          <span>{snapshotDates.length > 0 ? "已固化复盘" : "当前动态复盘"}</span>
        </div>

        {running && !report ? (
          <div className="review-agent-empty">
            <strong>正在加载过去 5 个交易日 Top10 追踪</strong>
            <p>加载完成后可以按日期查看当天预测 Top10 到最新收盘的走势。</p>
          </div>
        ) : report ? (
          <>
            <ReviewConclusion report={report} trackDates={trackDates} />
            {scoringDiagnostic ? (
              <ScoringErrorDiagnosticPanel diagnostic={scoringDiagnostic} />
            ) : null}
            <div className="review-agent-metrics">
              <span>
                <small>样本</small>
                <strong>{trackedSampleSize}</strong>
              </span>
              <span>
                <small>日期卡片</small>
                <strong>{trackDates.length}</strong>
              </span>
              <span>
                <small>Top10 1进2</small>
                <strong>{formatEmpiricalRate(report.top_pick_promotion_rate)}</strong>
                <em>{report.top_pick_promoted_count}/{report.top_pick_promotion_sample_size}</em>
              </span>
              <span>
                <small>同期全部首板</small>
                <strong>{formatEmpiricalRate(report.market_promotion_rate)}</strong>
                <em>{report.market_promoted_count}/{report.market_promotion_sample_size}</em>
              </span>
              <span>
                <small>相对全市场</small>
                <strong className={(report.promotion_rate_delta ?? 0) >= 0 ? "positive" : "negative"}>
                  {report.promotion_rate_delta === null
                    ? "暂无"
                    : `${formatSigned(report.promotion_rate_delta * 100, 1)} 个百分点`}
                </strong>
              </span>
              <span>
                <small>收益兑现率</small>
                <strong>{trackedSuccessRate === null ? "暂无" : formatPercent(trackedSuccessRate)}</strong>
              </span>
            </div>

            <DailyTopReview
              activeSelection={selectedReviewSelection}
              adjustmentSuggestions={report.adjustment_suggestions}
              failedPatterns={report.failed_patterns}
              failedPicks={failedPicks}
              groupedPicks={reviewDates}
              onSelect={setActiveReviewSelection}
              picks={selectedPicks}
              promotionComparisons={promotionComparisons}
              successfulPatterns={report.successful_patterns}
              successfulPicks={successfulPicks}
              trackDates={trackDates}
            />
          </>
        ) : (
          <div className="review-agent-empty">
            <strong>暂无 Top10 追踪明细</strong>
            <p>请先完成收盘数据同步与每日预测固化，再查看近期复盘。</p>
          </div>
        )}
      </div>
    </Panel>
  );
}

function ReviewConclusion({
  report,
  trackDates,
}: {
  report: ReviewAgentReportResponse;
  trackDates: string[];
}) {
  /** Turn the persisted review facts into one scannable daily conclusion. */

  const trackedPicks = report.reviewed_picks.filter((item) => (
    trackDates.includes(item.trade_date)
  ));
  const trackedReturns = trackedPicks
    .map(latestTrackedReturn)
    .filter((value): value is number => value !== null);
  const averageReturn = trackedReturns.length > 0
    ? trackedReturns.reduce((total, value) => total + value, 0) / trackedReturns.length
    : null;
  const positiveRate = trackedReturns.length > 0
    ? trackedReturns.filter((value) => value > 0).length / trackedReturns.length
    : null;
  const drawdowns = trackedPicks
    .map((item) => item.max_drawdown_from_next_open_3d)
    .filter((value): value is number => value !== null);
  const averageDrawdown = drawdowns.length > 0
    ? drawdowns.reduce((total, value) => total + value, 0) / drawdowns.length
    : null;
  const cacheReadyCount = trackedPicks.filter((item) => item.post_bar_cache_complete).length;
  const promotionDelta = report.promotion_rate_delta;
  const verdict = promotionDelta === null
    ? { tone: "neutral", text: "1进2对照仍在等待成熟样本" }
    : promotionDelta >= 0.03
      ? { tone: "positive", text: "Top10 的1进2表现优于同期全部首板" }
      : promotionDelta <= -0.03
        ? { tone: "negative", text: "Top10 的1进2表现弱于同期全部首板" }
        : { tone: "neutral", text: "Top10 与同期全部首板接近，暂未形成明显优势" };

  return (
    <section className="review-conclusion" aria-label="本次复盘结论">
      <header>
        <div>
          <span>本次复盘结论</span>
          <strong>{verdict.text}</strong>
        </div>
        <b className={verdict.tone}>{report.end_date}</b>
      </header>
      <div className="review-conclusion-metrics">
        <span>
          <small>各自最新收盘收益均值</small>
          <strong className={numberTone(averageReturn)}>{formatOptionalPercent(averageReturn)}</strong>
        </span>
        <span>
          <small>上涨样本占比</small>
          <strong>{positiveRate === null ? "暂无" : formatPercent(positiveRate)}</strong>
        </span>
        <span>
          <small>Top10 相对全市场</small>
          <strong className={numberTone(promotionDelta)}>
            {promotionDelta === null
              ? "待确认"
              : `${formatSigned(promotionDelta * 100, 1)} 个百分点`}
          </strong>
        </span>
        <span>
          <small>三日平均最大回撤</small>
          <strong className={numberTone(averageDrawdown)}>{formatOptionalPercent(averageDrawdown)}</strong>
        </span>
        <span>
          <small>五日走势缓存</small>
          <strong>{cacheReadyCount}/{trackedPicks.length}</strong>
        </span>
      </div>
      <div className="review-conclusion-evidence">
        <article>
          <small>表现较好特征</small>
          <p>{report.successful_patterns[0] ?? "成熟样本不足，暂不归纳共同特征。"}</p>
        </article>
        <article>
          <small>表现较差特征</small>
          <p>{report.failed_patterns[0] ?? "成熟样本不足，暂不归纳风险特征。"}</p>
        </article>
        <article>
          <small>下一轮观察</small>
          <p>{report.adjustment_suggestions[0] ?? report.main_findings[0] ?? "继续积累样本并保持当前评分口径。"}</p>
        </article>
      </div>
    </section>
  );
}

function ScoringErrorDiagnosticPanel({
  diagnostic,
}: {
  diagnostic: ScoringErrorDiagnosticResponse;
}) {
  /** Expose promotion errors and ablation evidence without changing Champion. */

  const actionableFactors = diagnostic.factors
    .filter((item) => item.recommendation !== "neutral")
    .slice(0, 4);
  const factors = actionableFactors.length > 0
    ? actionableFactors
    : diagnostic.factors.slice(0, 4);
  const sampleWarning = diagnostic.trade_date_count < 20
    ? `当前只有 ${diagnostic.trade_date_count} 个结果完整交易日，所有方向仅进入影子验证。`
    : "调整方向仍需通过后续交易日的样本外验证。";

  return (
    <section className="scoring-error-diagnostic" aria-label="评分改进诊断">
      <header>
        <div>
          <span>评分改进诊断</span>
          <strong>{diagnostic.findings[0] ?? "等待结果完整样本"}</strong>
        </div>
        <b>Champion 保持不变</b>
      </header>

      <div className="scoring-error-summary">
        <span>
          <small>高分误选</small>
          <strong>{diagnostic.false_positive_count}</strong>
          <em>Top10 未晋级</em>
        </span>
        <span>
          <small>晋级漏选</small>
          <strong>{diagnostic.false_negative_count}</strong>
          <em>Top10 之外晋级</em>
        </span>
        <span>
          <small>有效交易日</small>
          <strong>{diagnostic.trade_date_count}</strong>
          <em>{diagnostic.pool_sample_size} 个首板样本</em>
        </span>
        <span>
          <small>相对全市场</small>
          <strong className={numberTone(diagnostic.promotion_rate_delta)}>
            {diagnostic.promotion_rate_delta === null
              ? "暂无"
              : `${formatSigned(diagnostic.promotion_rate_delta * 100, 1)} 个百分点`}
          </strong>
          <em>Top10 减全市场</em>
        </span>
      </div>

      <div className="scoring-error-body">
        <div className="scoring-factor-diagnosis">
          <strong>值得继续验证的因子方向</strong>
          {factors.map((item) => (
            <article key={item.factor_key}>
              <div>
                <span>{item.factor_name}</span>
                <b className={`factor-action ${item.recommendation}`}>
                  {item.recommendation === "increase"
                    ? "观察上调"
                    : item.recommendation === "decrease"
                      ? "观察下调"
                      : "暂不调整"}
                </b>
              </div>
              <p>{item.evidence}</p>
            </article>
          ))}
        </div>

        <div className="scoring-error-cases">
          <ErrorCaseList
            title="典型高分误选"
            items={diagnostic.false_positive_samples.slice(0, 3)}
          />
          <ErrorCaseList
            title="典型晋级漏选"
            items={diagnostic.false_negative_samples.slice(0, 3)}
          />
        </div>
      </div>

      <p className="scoring-shadow-note">{sampleWarning}</p>
    </section>
  );
}

function ErrorCaseList({
  title,
  items,
}: {
  title: string;
  items: ScoringErrorDiagnosticResponse["false_positive_samples"];
}) {
  return (
    <section>
      <strong>{title}</strong>
      {items.length > 0 ? items.map((item) => (
        <Link
          key={`${item.trade_date}-${item.symbol}`}
          to={stockDetailPath(item.symbol, item.trade_date)}
        >
          <span>{item.name}<small>{item.symbol}</small></span>
          <b>第 {item.rank} 名</b>
          <em>{item.leading_factors.slice(0, 2).join(" · ")}</em>
        </Link>
      )) : <p>当前没有可展示的成熟样本。</p>}
    </section>
  );
}

const REVIEW_SUCCESS_SELECTION = "review-success";
const REVIEW_MISS_SELECTION = "review-miss";

function DailyTopReview({
  activeSelection,
  adjustmentSuggestions,
  failedPatterns,
  failedPicks,
  groupedPicks,
  onSelect,
  picks,
  promotionComparisons,
  successfulPatterns,
  successfulPicks,
  trackDates,
}: {
  activeSelection: string;
  adjustmentSuggestions: string[];
  failedPatterns: string[];
  failedPicks: ReviewAgentPick[];
  groupedPicks: Record<string, ReviewAgentPick[]>;
  onSelect: (selection: string) => void;
  picks: ReviewAgentPick[];
  promotionComparisons: Record<string, ReviewPromotionComparison>;
  successfulPatterns: string[];
  successfulPicks: ReviewAgentPick[];
  trackDates: string[];
}) {
  const isSuccessView = activeSelection === REVIEW_SUCCESS_SELECTION;
  const isMissView = activeSelection === REVIEW_MISS_SELECTION;
  const isPerformanceView = isSuccessView || isMissView;
  const activePatterns = isSuccessView ? successfulPatterns : isMissView ? failedPatterns : [];
  const title = isSuccessView ? "表现较好" : isMissView ? "表现较差" : activeSelection;
  const description = isSuccessView
    ? `共 ${successfulPicks.length} 只，按首板至最新收盘收益率从高到低展示前 10 只`
    : `共 ${failedPicks.length} 只，按首板至最新收盘收益率从低到高展示前 10 只`;
  const visiblePicks = picks.slice(0, 10);
  const promotionComparison = isPerformanceView
    ? undefined
    : promotionComparisons[activeSelection];

  return (
    <div className="daily-top-review">
      <div className="daily-top-cards">
        {trackDates.map((tradeDate) => {
          const dailyPicks = groupedPicks[tradeDate] ?? [];
          const readyCount = dailyPicks.filter((item) => item.post_bar_cache_complete).length;
          const promotion = promotionComparisons[tradeDate];
          return (
          <button
            className={tradeDate === activeSelection ? "active" : ""}
            key={tradeDate}
            type="button"
            onClick={() => onSelect(tradeDate)}
          >
            <span>{tradeDate}</span>
            <strong>Top10 追踪</strong>
            <small>{readyCount} / {dailyPicks.length} 缓存已同步</small>
            <b>
              {promotion?.outcome_ready
                ? `1进2 ${promotion.top_pick_promoted_count}/${promotion.top_pick_sample_size} · 全部 ${promotion.market_promoted_count}/${promotion.market_first_board_sample_size}`
                : "1进2等待下一交易日"}
            </b>
          </button>
          );
        })}
        <button
          className={`review-outcome-option outcome-success ${isSuccessView ? "active" : ""}`}
          type="button"
          onClick={() => onSelect(REVIEW_SUCCESS_SELECTION)}
        >
          <span>跨日期复盘</span>
          <strong className="review-outcome-label"><TrendingUp size={16} />表现较好</strong>
          <small>{successfulPicks.length} 只已兑现样本</small>
          <b>收益最高 Top10</b>
        </button>
        <button
          className={`review-outcome-option outcome-miss ${isMissView ? "active" : ""}`}
          type="button"
          onClick={() => onSelect(REVIEW_MISS_SELECTION)}
        >
          <span>跨日期复盘</span>
          <strong className="review-outcome-label"><ShieldAlert size={16} />表现较差</strong>
          <small>{failedPicks.length} 只误判样本</small>
          <b>收益最低 Top10</b>
        </button>
      </div>

      <section className="daily-top-body">
        {isPerformanceView ? (
          <div className="daily-top-title">
            <div>
              <strong>{title}</strong>
              <span>{description}</span>
            </div>
            <span>
              {visiblePicks.filter((item) => item.post_bar_cache_complete).length} / {visiblePicks.length} 缓存已同步
            </span>
          </div>
        ) : null}

        {promotionComparison ? (
          <div className="review-promotion-comparison">
            <span>
              <small>预测 Top10 1进2</small>
              <strong>{formatEmpiricalRate(promotionComparison.top_pick_promotion_rate)}</strong>
              <em>{promotionComparison.top_pick_promoted_count}/{promotionComparison.top_pick_sample_size}</em>
            </span>
            <span>
              <small>当天全部首板 1进2</small>
              <strong>{formatEmpiricalRate(promotionComparison.market_promotion_rate)}</strong>
              <em>{promotionComparison.market_promoted_count}/{promotionComparison.market_first_board_sample_size}</em>
            </span>
            <span>
              <small>Top10 相对全市场</small>
              <strong className={(promotionComparison.promotion_rate_delta ?? 0) >= 0 ? "positive" : "negative"}>
                {promotionComparison.promotion_rate_delta === null
                  ? "等待次日收盘"
                  : `${formatSigned(promotionComparison.promotion_rate_delta * 100, 1)} 个百分点`}
              </strong>
              <em>{promotionComparison.next_trade_date ?? "下一交易日待确认"}</em>
            </span>
          </div>
        ) : null}

        {isPerformanceView && activePatterns.length > 0 ? (
          <div className={`review-pattern-summary ${isSuccessView ? "summary-success" : "summary-miss"}`}>
            <strong>{isSuccessView ? "表现较好股票的共同特征" : "表现较差股票的共同特征"}</strong>
            <ul>
              {activePatterns.slice(0, 3).map((pattern) => <li key={pattern}>{pattern}</li>)}
            </ul>
            {isMissView && adjustmentSuggestions.length > 0 ? (
              <p><b>评分改进：</b>{adjustmentSuggestions[0]}</p>
            ) : null}
          </div>
        ) : null}

        <ReviewPickTable
          picks={visiblePicks}
          limit={10}
          showLatestReturn={isPerformanceView}
          showTradeDate={isPerformanceView}
        />
      </section>
    </div>
  );
}

function groupReviewPicksByDate(picks: ReviewAgentPick[]) {
  return picks.reduce<Record<string, ReviewAgentPick[]>>((groups, pick) => {
    groups[pick.trade_date] = groups[pick.trade_date] ?? [];
    groups[pick.trade_date].push(pick);
    groups[pick.trade_date].sort((left, right) => right.score - left.score);
    return groups;
  }, {});
}

function latestTrackedReturn(pick: ReviewAgentPick) {
  const latestBar = pick.post_bars.reduce<ReviewAgentPick["post_bars"][number] | null>(
    (latest, bar) => (
      bar.return_from_base_pct !== null && (!latest || bar.trade_date > latest.trade_date)
        ? bar
        : latest
    ),
    null,
  );
  return latestBar?.return_from_base_pct ?? null;
}

function sortReviewPicksForSummary(
  picks: ReviewAgentPick[],
  direction: "asc" | "desc",
) {
  return [...picks].sort((left, right) => {
    const leftReturn = latestTrackedReturn(left);
    const rightReturn = latestTrackedReturn(right);
    if (leftReturn === null && rightReturn !== null) return 1;
    if (leftReturn !== null && rightReturn === null) return -1;
    if (leftReturn !== null && rightReturn !== null && leftReturn !== rightReturn) {
      return direction === "desc" ? rightReturn - leftReturn : leftReturn - rightReturn;
    }
    return (
      right.trade_date.localeCompare(left.trade_date)
      || right.score - left.score
      || left.symbol.localeCompare(right.symbol)
    );
  });
}

function buildReviewTrackDates(
  groupedPicks: Record<string, ReviewAgentPick[]>,
  latestTradeDate: string,
) {
  return Object.keys(groupedPicks)
    .filter((tradeDate) => tradeDate < latestTradeDate)
    .sort()
    .reverse()
    .slice(0, 5);
}

function ReviewInsightCard({
  title,
  items,
  tone = "neutral",
}: {
  title: string;
  items: string[];
  tone?: "neutral" | "good" | "risk" | "warn";
}) {
  return (
    <article className={`review-insight-card review-${tone}`}>
      <strong>{title}</strong>
      {items.length > 0 ? (
        <ul>
          {items.slice(0, 5).map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : (
        <p>暂无足够样本。</p>
      )}
    </article>
  );
}

function ReviewToolTraceCard({ traces }: { traces: ReviewAgentReportResponse["tool_results"] }) {
  return (
    <article className="review-insight-card review-tools">
      <strong>工具链</strong>
      <div className="review-tool-chain">
        {traces.length > 0 ? (
          traces.map((trace) => (
            <span key={trace.name}>{trace.name}</span>
          ))
        ) : (
          <span>暂无工具 trace</span>
        )}
      </div>
    </article>
  );
}

function ReviewPickTable({
  picks,
  limit = 10,
  showLatestReturn = false,
  showTradeDate = false,
}: {
  picks: ReviewAgentPick[];
  limit?: number;
  showLatestReturn?: boolean;
  showTradeDate?: boolean;
}) {
  const visible = picks.slice(0, limit);

  if (visible.length === 0) {
    return <div className="review-agent-empty">暂无高分票追踪样本。</div>;
  }

  return (
    <div className="review-pick-table">
      <div className="review-pick-table-head">
        <span>股票</span>
        <span>评分</span>
        <span>结论</span>
        <span>{showLatestReturn ? "首板至今" : "次日开收"}</span>
        <span>走势追踪</span>
      </div>
      {visible.map((pick) => (
        <Link
          className={`review-pick-row pick-${pick.evaluation_label}`}
          key={`${pick.trade_date}-${pick.symbol}`}
          to={stockDetailPath(pick.symbol, pick.trade_date)}
        >
          <strong>
            {pick.name}
            <small>
              {showTradeDate ? `${pick.trade_date} / ` : ""}
              {pick.symbol} / {pick.prediction_source === "live" ? "实时预测" : "历史回测"}
            </small>
          </strong>
          <span>{pick.score.toFixed(1)} / {pick.rating}</span>
          <span className="review-pick-verdict">
            {showLatestReturn
              ? trackedReturnLabel(latestTrackedReturn(pick))
              : reviewLabelCopy(pick.evaluation_label)}
            <small className={pick.promoted_to_second_board ? "promoted" : ""}>
              {!pick.outcome_ready
                ? "1进2待确认"
                : pick.promoted_to_second_board
                  ? "已晋级二板"
                  : "未晋级二板"}
            </small>
          </span>
          <span>
            {formatOptionalPercent(
              showLatestReturn ? latestTrackedReturn(pick) : pick.next_open_to_close_pct,
            )}
          </span>
          <ReviewPostBars
            bars={pick.post_bars}
            expectedCount={pick.expected_post_bar_count}
          />
        </Link>
      ))}
    </div>
  );
}

function ReviewPostBars({
  bars,
  expectedCount,
}: {
  bars: ReviewAgentPick["post_bars"];
  expectedCount: number;
}) {
  return (
    <div className="review-post-bars">
      {Array.from({ length: 6 }, (_, index) => {
        const bar = bars[index];
        const label = index === 0 ? "首板" : `D+${index}`;
        if (!bar) {
          const cacheMissing = index < expectedCount;
          return (
            <span
              className={cacheMissing ? "cache-missing" : "awaiting-close"}
              key={`${label}-empty`}
              title={cacheMissing ? "该交易日行情缓存缺失" : "等待后续交易日收盘"}
            >
              <small>{label}</small>
              <strong>{cacheMissing ? "缺缓存" : "待收盘"}</strong>
            </span>
          );
        }
        return (
          <span
            className={
              bar.return_from_base_pct === null
                ? ""
                : bar.return_from_base_pct >= 0
                  ? "positive"
                  : "negative"
            }
            key={bar.trade_date}
            title={`${bar.trade_date} 收盘 ${bar.close.toFixed(2)}`}
          >
            <small>{label}</small>
            <strong>{formatOptionalPercent(bar.return_from_base_pct)}</strong>
          </span>
        );
      })}
    </div>
  );
}

function reviewLabelCopy(label: string) {
  const labels: Record<string, string> = {
    success: "兑现",
    partial: "部分兑现",
    miss: "失败",
    avoid_success: "规避有效",
    false_negative: "漏判",
    pending: "待观察",
  };
  return labels[label] ?? label;
}

function trackedReturnLabel(value: number | null) {
  if (value === null) return "待观察";
  if (value > 0) return "累计上涨";
  if (value < 0) return "累计下跌";
  return "累计持平";
}
