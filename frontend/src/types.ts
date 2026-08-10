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

export interface StockKLineBar {
  trade_date: string;
  open: number;
  close: number;
  high: number;
  low: number;
  volume: number;
}

export interface StockIntradayKLineBar {
  timestamp: string;
  open: number;
  close: number;
  high: number;
  low: number;
  volume: number;
  amount: number;
}

export interface StockCloseSnapshot {
  symbol: string;
  trade_date: string;
  close: number;
  previous_close: number | null;
  change: number | null;
  change_pct: number | null;
  volume: number;
  source: string;
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
export interface FirstBoardFilterResult {
  symbol: string;
  name: string;
  included: boolean;
  excluded_reasons: string[];
  data_missing: string[];
}

export interface FirstBoardCandidateFacts {
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
  same_industry_limit_up_count: number;
  same_concept_limit_up_count: number;
  market_limit_up_count: number;
  market_first_board_count: number;
  market_failed_limit_up_rate: number;
  market_max_board_height: number;
  market_sentiment: Sentiment;
  data_missing: string[];
}

export interface ScoreBreakdownItem {
  name: string;
  score: number;
  max_score: number;
  evidence: string[];
}

export interface FirstBoardRating {
  facts: FirstBoardCandidateFacts;
  score: number;
  rating: "A" | "B" | "C" | "D";
  confidence: number;
  score_breakdown: ScoreBreakdownItem[];
  reasons: string[];
  risks: string[];
}

export interface FirstBoardRatingsResponse {
  trade_date: string;
  candidates: FirstBoardRating[];
  filtered_out: FirstBoardFilterResult[];
  universe_count: number;
  generated_by: string;
}

export interface RatingBacktestBucket {
  rating: string;
  sample_size: number;
  outcome_ready_count: number;
  avg_next_open_pct: number | null;
  avg_next_high_pct: number | null;
  avg_next_close_pct: number | null;
  avg_three_day_high_pct: number | null;
  avg_three_day_close_pct: number | null;
  promoted_to_second_board_rate: number | null;
}

export interface RatingBacktestFailureSample {
  symbol: string;
  name: string;
  trade_date: string;
  rating: string;
  score: number;
  next_close_pct: number | null;
  three_day_close_pct: number | null;
  promoted_to_second_board: boolean;
  reasons: string[];
  risks: string[];
}

export interface RatingBacktestResponse {
  start_date: string;
  end_date: string;
  trade_dates: string[];
  sample_size: number;
  outcome_ready_count: number;
  buckets: RatingBacktestBucket[];
  failure_samples: RatingBacktestFailureSample[];
  observations: string[];
  warnings: string[];
  generated_by: string;
}

export interface FirstBoardCriticResponse {
  symbol: string;
  name: string;
  trade_date: string;
  rating: string;
  score: number;
  original_confidence: number;
  suggested_confidence: number;
  confidence_delta: number;
  verdict: "supportive" | "cautious" | "fragile";
  support_evidence: string[];
  counter_evidence: string[];
  missing_data: string[];
  critic_warnings: string[];
  review_questions: string[];
  similar_case_count: number;
  similar_case_outcome_ready_count: number;
  generated_by: string;
}

export interface SimilarCaseOutcome {
  next_trade_date: string | null;
  next_open_pct: number | null;
  next_high_pct: number | null;
  next_close_pct: number | null;
  three_day_high_pct: number | null;
  three_day_close_pct: number | null;
  max_drawdown_3d: number | null;
  promoted_to_second_board: boolean;
  outcome_ready: boolean;
}

export interface SimilarCaseDailyBar {
  trade_date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  amount: number;
}

export interface SimilarFirstBoardCase {
  symbol: string;
  name: string;
  trade_date: string;
  similarity: number;
  reasons: string[];
  differences: string[];
  outcome: SimilarCaseOutcome | null;
  post_bars: SimilarCaseDailyBar[];
}

export interface SimilarFirstBoardCasesResponse {
  target: {
    trade_date: string;
    symbol: string;
    name: string;
  };
  cases: SimilarFirstBoardCase[];
  window_days: number;
  recall_count: number;
  generated_by: string;
}

export interface AgentChatRequest {
  session_id: string;
  message: string;
  intent_hint?: string;
  trade_date?: string;
  symbol?: string;
  page_context?: Record<string, string>;
}

export interface AgentChatResponse {
  session_id: string;
  run_id: string | null;
  intent: string;
  answer: string;
  tool_calls: string[];
  tool_results: AgentToolTrace[];
  references: string[];
  warnings: string[];
  generated_by: string;
}

export interface AgentDataHealthTopCandidate {
  symbol: string;
  name: string;
  score: number;
  rating: string;
  feature_ready: boolean;
  similar_case_count: number;
  similar_cases_with_post_bars: number;
}

export interface AgentDataHealthResponse {
  trade_date: string | null;
  status: "healthy" | "partial" | "missing";
  raw_events_ready: boolean;
  raw_event_count: number;
  first_board_features_ready: boolean;
  first_board_feature_count: number;
  top_candidates_checked: number;
  similar_cases_ready: boolean;
  post_bars_ready: boolean;
  top_candidates: AgentDataHealthTopCandidate[];
  warnings: string[];
}

export interface AgentToolTrace {
  name: string;
  input: Record<string, unknown>;
  summary: string;
  status: "success" | "error" | "skipped";
  output: Record<string, unknown>;
  error: string | null;
}
