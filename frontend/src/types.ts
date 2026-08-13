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
  enrichment: FirstBoardEnrichmentSnapshot | null;
  data_missing: string[];
}

export interface FirstBoardEnrichmentSnapshot {
  trade_date: string;
  symbol: string;
  kline_bar_count: number;
  return_5d_pct: number | null;
  return_20d_pct: number | null;
  return_60d_pct: number | null;
  distance_20d_high_pct: number | null;
  distance_60d_high_pct: number | null;
  volume_ratio_5d: number | null;
  volatility_20d: number | null;
  close_above_ma20: boolean | null;
  ma_alignment: string;
  listing_date: string | null;
  listing_age_days: number | null;
  float_market_cap: number | null;
  float_market_cap_source: string | null;
  recent_limit_up_count_20d: number;
  recent_limit_up_count_60d: number;
  industry_first_board_count: number;
  industry_continued_board_count: number;
  industry_failed_count: number;
  industry_max_board_height: number;
  industry_first_limit_rank: number | null;
  previous_first_board_promotion_rate: number | null;
  market_first_board_seal_rate: number | null;
  dragon_tiger_on_list: boolean;
  dragon_tiger_net_buy_amount: number | null;
  dragon_tiger_buy_amount: number | null;
  dragon_tiger_sell_amount: number | null;
  dragon_tiger_reason: string | null;
  popularity_rank: number | null;
  popularity_rank_change: number | null;
  popularity_snapshot_at: string | null;
  data_missing: string[];
  feature_version: string;
  created_at: string;
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

export interface AgentEvaluationItem {
  prediction_id: string;
  trade_date: string;
  symbol: string;
  name: string;
  score: number;
  rating: string;
  confidence: number;
  evaluation_label:
    | "success"
    | "partial"
    | "miss"
    | "avoid_success"
    | "false_negative"
    | "pending";
  outcome_ready: boolean;
  promoted_to_second_board: boolean;
  next_high_pct: number | null;
  next_close_pct: number | null;
  three_day_high_pct: number | null;
  three_day_close_pct: number | null;
  lesson: string;
  scoring_suggestion: string;
}

export interface AgentEvaluationResponse {
  start_date: string;
  end_date: string;
  prediction_count: number;
  outcome_ready_count: number;
  label_counts: Record<string, number>;
  evaluations: AgentEvaluationItem[];
  summary: string[];
  warnings: string[];
  generated_by: string;
}

export interface AgentEvalCaseReport {
  case_id: string;
  passed: boolean;
  failures: string[];
  intent: string;
  planner_tool_calls: string[];
  final_tool_calls: string[];
  backend_repaired_tools: string[];
  repair_reasons: string[];
  trace_names: string[];
  warnings: string[];
  answer_preview: string;
}

export interface AgentEvalReportResponse {
  mode: "offline";
  total: number;
  passed: number;
  failed: number;
  pass_rate: number;
  results: AgentEvalCaseReport[];
  generated_by: string;
}

export interface ReviewAgentPick {
  trade_date: string;
  symbol: string;
  name: string;
  score: number;
  rating: string;
  confidence: number;
  evaluation_label: string;
  outcome_ready: boolean;
  promoted_to_second_board: boolean;
  next_high_pct: number | null;
  next_close_pct: number | null;
  three_day_high_pct: number | null;
  three_day_close_pct: number | null;
  reasons: string[];
  risks: string[];
  post_bars: ReviewAgentPostBar[];
  expected_post_bar_count: number;
  post_bar_cache_complete: boolean;
}

export interface ReviewAgentPostBar {
  trade_date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  change_pct: number | null;
  return_from_base_pct: number | null;
}

export interface ReviewAgentReportResponse {
  start_date: string;
  end_date: string;
  sample_size: number;
  success_count: number;
  failed_count: number;
  pending_count: number;
  main_findings: string[];
  successful_patterns: string[];
  failed_patterns: string[];
  scoring_bias: string[];
  adjustment_suggestions: string[];
  confidence: number;
  reviewed_picks: ReviewAgentPick[];
  tool_results: AgentToolTrace[];
  warnings: string[];
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
  evidence_cards: AgentEvidenceCard[];
  tool_policy: AgentToolPolicyAudit;
  references: string[];
  warnings: string[];
  performance: AgentChatPerformance;
  generated_by: string;
}

export interface AgentChatPerformance {
  planner_duration_ms: number;
  tool_duration_ms: number;
  answer_duration_ms: number;
  total_duration_ms: number;
  planner_prompt_chars: number;
  answer_prompt_chars: number;
}

export type AgentChatStreamStage = "planning" | "tools" | "answering";

export type AgentChatStreamEvent =
  | {
      event: "progress";
      data: { stage: AgentChatStreamStage; message: string };
    }
  | {
      event: "answer_delta";
      data: { delta: string };
    }
  | {
      event: "completed";
      data: AgentChatResponse;
    }
  | {
      event: "error";
      data: { message: string; run_id?: string };
    };

export interface AgentRunSummary {
  run_id: string;
  session_id: string;
  status: "success" | "error";
  intent: string | null;
  message: string;
  answer_preview: string | null;
  tool_calls: string[];
  tool_results: AgentToolTrace[];
  warnings: string[];
  error_message: string | null;
  started_at: string;
  finished_at: string;
  duration_ms: number;
}

export interface AgentRunsResponse {
  runs: AgentRunSummary[];
  generated_by: string;
}

export interface AgentDataHealthTopCandidate {
  symbol: string;
  name: string;
  score: number;
  rating: string;
  feature_ready: boolean;
  enrichment_ready: boolean;
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
  enrichment_ready: boolean;
  enrichment_count: number;
  top_candidates_checked: number;
  similar_cases_ready: boolean;
  post_bars_ready: boolean;
  top_candidates: AgentDataHealthTopCandidate[];
  warnings: string[];
}

export interface AgentSystemHealthResponse {
  status: "healthy" | "partial" | "missing";
  current_date: string;
  current_time: string;
  latest_local_trade_date: string | null;
  expected_data_date: string | null;
  data_fresh: boolean;
  data_update_recommended: boolean;
  data_update_reason: string;
  llm_enabled: boolean;
  llm_provider_configured: boolean;
  llm_model: string | null;
  proxy_configured: boolean;
  proxy_warning: string | null;
  offline_eval_passed: boolean | null;
  offline_eval_total: number | null;
  offline_eval_failed: number | null;
  data_health: AgentDataHealthResponse;
  warnings: string[];
  generated_by: string;
}

export interface AgentToolTrace {
  name: string;
  input: Record<string, unknown>;
  summary: string;
  status: "success" | "error" | "skipped";
  output: Record<string, unknown>;
  error: string | null;
  duration_ms: number | null;
}

export interface AgentToolPolicyAudit {
  planner_tool_calls: string[];
  final_tool_calls: string[];
  backend_repaired_tools: string[];
  repair_reasons: string[];
  safety_fallback_used: boolean;
}

export interface AgentEvidenceCard {
  title: string;
  kind:
    | "execution"
    | "market"
    | "candidate_pool"
    | "limit_up_events"
    | "rating"
    | "critic"
    | "similar_cases"
    | "evaluation"
    | "data_availability"
    | "tool";
  status: "success" | "error" | "skipped";
  summary: string;
  facts: string[];
  metrics: Record<string, string | number | boolean | null>;
  source_tools: string[];
}
