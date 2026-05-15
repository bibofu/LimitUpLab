export type Sentiment = "heating" | "diverging" | "cooling";

export interface MarketIndexSnapshot {
  name: string;
  symbol: string;
  close: number;
  change_pct: number;
  trend: number[];
}

export interface ConceptHeat {
  name: string;
  limit_up_count: number;
  failed_count: number;
}

export interface MarketSummary {
  trade_date: string;
  limit_up_count: number;
  first_board_count: number;
  continued_board_count: number;
  failed_count: number;
  limit_down_count: number;
  failed_limit_up_rate: number;
  max_board_height: number;
  total_amount: number;
  hot_industries: string[];
  hot_concepts: ConceptHeat[];
  indices: MarketIndexSnapshot[];
  sentiment: Sentiment;
}

export interface LimitUpEvent {
  symbol: string;
  name: string;
  trade_date: string;
  first_limit_time: string;
  last_limit_time: string;
  seal_count: number;
  break_count: number;
  closed_limit: boolean;
  board_height: number;
  amount: number;
  turnover_rate: number;
  industry: string;
  concept: string;
  next_open_pct: number;
  next_high_pct: number;
  next_close_pct: number;
  three_day_return_pct: number;
  five_day_return_pct: number;
  continued_next_day: boolean;
}

export interface ContinuationStat {
  board_height: number;
  sample_size: number;
  continued_count: number;
  probability: number;
}

export interface FailedRateStat {
  board_height: number;
  sample_size: number;
  failed_count: number;
  failed_rate: number;
}

export interface PostPerformanceStat {
  board_height: number;
  sample_size: number;
  avg_next_open_pct: number;
  avg_next_high_pct: number;
  avg_next_close_pct: number;
  avg_five_day_return_pct: number;
}
