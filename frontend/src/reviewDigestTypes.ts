export interface DigestBucket {
  label: string;
  count: number;
  share: number | null;
  baseline_count: number | null;
  baseline_share: number | null;
}

export interface DigestDistribution {
  key: string;
  label: string;
  valid_count: number;
  total_count: number;
  baseline_valid_count: number | null;
  baseline_total_count: number | null;
  buckets: DigestBucket[];
  note: string;
}

export interface DigestObservation {
  id: string;
  dimension: string;
  text: string;
  support_count: number;
  sample_size: number;
}

export interface DigestStock {
  symbol: string;
  name: string;
  first_board_date: string | null;
  observed_days: number | null;
  return_pct: number | null;
  first_close: number | null;
  cutoff_close: number | null;
  next_trade_date?: string | null;
  next_open_pct?: number | null;
  first_dragon_tiger_on_list?: boolean | null;
  first_dragon_tiger_source?: string | null;
  first_dragon_tiger_reason?: string | null;
  position_label: string | null;
  industry: string | null;
  concepts: string[];
  float_market_cap: number | null;
  first_limit_time: string | null;
  break_count: number | null;
  turnover_rate: number | null;
  max_board_height: number | null;
  second_board_date: string | null;
  second_open_pct: number | null;
  second_limit_time: string | null;
  second_break_count: number | null;
  second_turnover_rate: number | null;
  second_board_shape: string | null;
  data_missing: string[];
}

export interface DigestGroup {
  scope: "excellent" | "weak" | "leaders";
  sample_size: number;
  summary: string;
  observations: DigestObservation[];
  selected_observation_ids: string[];
  distributions: DigestDistribution[];
  stocks: DigestStock[];
  notes: string[];
}

export interface DigestOverview {
  headline: string;
  candidate_count: number;
  excellent_count: number;
  weak_count: number;
  ordinary_count: number;
  unobserved_count: number;
  comparable_days: number;
  outperform_days: number;
  best_date: string | null;
  candidate_promoted: number;
  candidate_promotion_total: number;
  candidate_promotion_rate: number | null;
  market_promoted: number;
  market_promotion_total: number;
  market_promotion_rate: number | null;
  advantage_pp: number | null;
}

export interface ReviewDigest {
  version: string;
  as_of_date: string;
  candidate_dates: string[];
  market_dates: string[];
  overview: DigestOverview;
  excellent: DigestGroup;
  weak: DigestGroup;
  leaders: DigestGroup;
  notes: string[];
}
