export type ObservationStrategy = "consolidation" | "drawdown";
export type PremarketStrategy = "relay" | ObservationStrategy;

export function premarketStrategyFromParam(value: string | null): PremarketStrategy {
  return value === "consolidation" || value === "drawdown" ? value : "relay";
}

export interface ConsolidationEvaluation {
  symbol: string;
  name: string;
  anchor_date: string;
  confirmed_date: string | null;
  state: "new" | "watching" | "rejected";
  failed_conditions: string[];
  consolidation_days: number;
  anchor_close: number;
  close: number;
  range_low: number;
  range_high: number;
  range_pct: number;
  anchor_change_pct: number;
  volume_ratio: number;
  peak_date: string | null;
  peak_price: number | null;
  drawdown_pct: number | null;
  source: string;
  reasons: string[];
  risks: string[];
}

export interface ConsolidationCandidate extends ConsolidationEvaluation {
  confirmed_date: string;
  state: "new" | "watching";
}

export interface ConsolidationPool {
  strategy: ObservationStrategy;
  strategy_version: string;
  generated_at: string;
  data_as_of: string | null;
  latest_data_date: string | null;
  available_dates: string[];
  status: "ready" | "empty" | "data_missing";
  snapshot_kind: "recomputed_observation";
  calendar_source: string;
  pool_count: number;
  evaluated_count: number;
  candidates: ConsolidationCandidate[];
  evaluated_stocks: ConsolidationEvaluation[];
  exclusions: Record<string, number>;
  data_missing: string[];
  warnings: string[];
  rules: string[];
}

const REASONS: Record<string, string> = {
  unsupported_security: "不在主板研究范围或含 ST、退市标记",
  age_outside_2_4: "涨停后尚未整理满 2 日",
  age_outside_1_4: "涨停后尚未经过 1 个交易日",
  drawdown_below_10pct: "较参考高点回撤不足 10%",
  missing_history20: "连续 20 日行情不完整",
  price_discontinuity: "存在明显价格断点",
  anchor_price_mismatch: "涨停事件与价格不一致",
  invalid_volume: "成交量缺失或无效",
  mixed_or_missing_source: "量价来源混用或缺失",
  range_above_8pct: "整理区间超过 8%",
  close_outside_band: "收盘偏离涨停日超出范围",
  volume_above_075: "整理期量比高于 0.75",
  local_database: "本地行情库尚未就绪",
  daily_bars: "暂无已结束交易日的日K",
  market_history20: "市场交易日历史不足 20 日",
  recent_event_dates: "近 7 个交易日的涨停事件记录不完整",
};

export function consolidationReason(key: string): string {
  return REASONS[key] ?? key;
}

export function consolidationEmptyMessage(pool: ConsolidationPool): string {
  return pool.status === "data_missing"
    ? "现有可核验样本中暂无符合项，部分数据不足，不能据此判断全市场没有符合形态的股票。"
    : "该交易日现有样本中没有同时满足全部条件的股票，保持空池。";
}

export function observationDisplayStocks(
  pool: ConsolidationPool,
  fallbackLimit = 6,
): ConsolidationEvaluation[] {
  if (pool.candidates.length > 0) return pool.candidates;
  return [...pool.evaluated_stocks]
    .filter((stock) => stock.state === "rejected")
    .sort((left, right) => (
      left.failed_conditions.length - right.failed_conditions.length
      || observationThresholdDistance(left, pool.strategy)
        - observationThresholdDistance(right, pool.strategy)
      || left.symbol.localeCompare(right.symbol)
    ))
    .slice(0, Math.max(0, fallbackLimit));
}

function observationThresholdDistance(
  stock: ConsolidationEvaluation,
  strategy: ObservationStrategy,
): number {
  if (strategy === "drawdown") {
    return stock.drawdown_pct === null
      ? Number.POSITIVE_INFINITY
      : Math.max(0, 10 - stock.drawdown_pct) / 10;
  }
  const rangeGap = Math.max(0, stock.range_pct - 8) / 8;
  const closeGap = stock.anchor_change_pct < -10
    ? (-10 - stock.anchor_change_pct) / 10
    : Math.max(0, stock.anchor_change_pct - 8) / 8;
  const volumeGap = Math.max(0, stock.volume_ratio - 0.75) / 0.75;
  return rangeGap + closeGap + volumeGap;
}
