export interface MarketIndexSnapshot {
  name: string;
  symbol: string;
  trade_date: string;
  close: number;
  change_pct: number;
  trend: number[];
  source: string;
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
  limit_down_count: number | null;
  failed_limit_up_rate: number;
  max_board_height: number;
  total_amount: number;
  hot_industries: string[];
  hot_concepts: ConceptHeat[];
  indices: MarketIndexSnapshot[];
}

export interface DragonTigerReviewItem {
  symbol: string;
  name: string;
  change_pct: number | null;
  buy_amount: number | null;
  sell_amount: number | null;
  net_buy_amount: number | null;
  net_rate: number | null;
  organization_net_buy_amount: number | null;
  hot_money_net_buy_amount: number | null;
  hot_rank: number | null;
  range_days: number | null;
  limit_reason: string | null;
  concepts: string[];
  detail_trade_date: string | null;
}

export interface DragonTigerReviewResponse {
  trade_date: string;
  source: string;
  stock_count: number;
  net_inflow_count: number;
  net_outflow_count: number;
  organization_count: number;
  hot_money_count: number;
  items: DragonTigerReviewItem[];
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

export interface StockDetailMarketData {
  symbol: string;
  requested_days: number;
  data_as_of: string;
  kline: StockKLineBar[];
  latest_close: StockCloseSnapshot;
  position_trade_date: string | null;
  position: StockPositionAssessment | null;
}

export interface StockNewsItem {
  symbol: string;
  name: string;
  title: string;
  summary: string;
  published_at: string;
  source: string;
  url: string;
  item_type: string;
  relevance_score: number;
  fetched_at: string;
}

export interface StockNewsFacts {
  symbol: string;
  name: string;
  fetched_at: string;
  window_days: number;
  cache_status: string;
  sources: string[];
  items: StockNewsItem[];
  data_missing: string[];
}

export interface FinanceNewsItem {
  title: string;
  summary: string;
  published_at: string;
  source: string;
  url: string;
  category: string;
  relevance_score: number;
}

export interface FinanceNewsPage {
  fetched_at: string;
  window_hours: number;
  sources: string[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  items: FinanceNewsItem[];
}

export interface ContinuationStat {
  board_height: number;
  sample_size: number;
  continued_count: number;
  probability: number;
}

export interface BoardPromotionBucket {
  from_board_height: number;
  to_board_height: number;
  sample_size: number;
  promoted_count: number;
  probability: number;
}

export interface BoardPromotionStock {
  symbol: string;
  name: string;
  industry: string;
  concept: string;
  from_board_height: number;
  to_board_height: number;
  first_limit_time: string;
  break_count: number;
}

export interface DailyBoardPromotionStat {
  trade_date: string;
  previous_trade_date: string;
  sample_size: number;
  promoted_count: number;
  probability: number;
  first_board_sample_size: number;
  first_board_promoted_count: number;
  first_board_probability: number | null;
  continued_board_sample_size: number;
  continued_board_promoted_count: number;
  continued_board_probability: number | null;
  buckets: BoardPromotionBucket[];
  promoted_stocks: BoardPromotionStock[];
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
  is_one_word_board: boolean;
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
  position: StockPositionAssessment | null;
  data_missing: string[];
  feature_version: string;
  created_at: string;
}

export type StockPositionRegime =
  | "oversold_rebound"
  | "v_reversal"
  | "low_base_breakout"
  | "mid_base_breakout"
  | "trend_acceleration"
  | "high_consolidation"
  | "high_breakout"
  | "second_wave"
  | "unclassified";

export interface StockPositionMatch {
  regime: StockPositionRegime;
  label: string;
  score: number;
}

export interface StockPositionAssessment {
  primary: StockPositionMatch;
  alternatives: StockPositionMatch[];
  confidence: number;
  tags: string[];
  evidence: string[];
  metrics: Record<string, number | null>;
  bar_count: number;
  classifier_version: string;
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
  snapshot_source: "live" | "historical_backtest" | "calculated";
  data_as_of: string | null;
  snapshot_created_at: string | null;
}

export type FirstBoardDiscoveryPattern =
  | "low_base_breakout"
  | "trend_acceleration"
  | "oversold_rebound"
  | "second_wave"
  | "range_breakout"
  | "unclassified";

export interface FirstBoardDiscoveryTheme {
  name: string;
  category: "industry" | "concept";
  change_pct: number;
  rank: number;
  member_count: number;
  news_headlines: string[];
  source: string;
}

export interface FirstBoardDiscoveryFacts {
  symbol: string;
  name: string;
  data_as_of: string;
  target_trade_date: string | null;
  close: number;
  change_pct: number;
  amount: number;
  volume: number;
  intraday_range_pct: number;
  close_location: number;
  open_to_close_pct: number;
  kline_bar_count: number;
  return_5d_pct: number | null;
  return_20d_pct: number | null;
  return_60d_pct: number | null;
  distance_20d_high_pct: number | null;
  distance_60d_high_pct: number | null;
  position_60d_pct: number | null;
  volume_ratio_5d: number | null;
  volatility_20d: number | null;
  ma_alignment: string;
  pattern: FirstBoardDiscoveryPattern;
  themes: FirstBoardDiscoveryTheme[];
  popularity_rank: number | null;
  news_catalysts: string[];
  data_missing: string[];
}

export interface FirstBoardDiscoveryCandidate {
  facts: FirstBoardDiscoveryFacts;
  score: number;
  rating: "A" | "B" | "C" | "D";
  confidence: number;
  score_breakdown: ScoreBreakdownItem[];
  reasons: string[];
  risks: string[];
}

export interface FirstBoardDiscoveryResponse {
  data_as_of: string;
  target_trade_date: string | null;
  universe_count: number;
  eligible_count: number;
  recalled_count: number;
  themes: FirstBoardDiscoveryTheme[];
  candidates: FirstBoardDiscoveryCandidate[];
  generated_by: string;
  source: string;
  snapshot_created_at: string;
  warnings: string[];
  disclaimer: string;
}

export interface RecommendationFinancialReport {
  fiscal_year: number;
  fiscal_period: string;
  report_date: string;
  period_end: string;
  operating_income: number | null;
  net_profit: number | null;
  parent_holder_net_profit: number | null;
  basic_eps: number | null;
  operating_income_yoy_pct: number | null;
  net_profit_yoy_pct: number | null;
  fetched_at: string;
  source: string;
}

export interface RecommendationIntelligenceItem {
  strategy: "discovery" | "relay";
  base_trade_date: string;
  symbol: string;
  name: string;
  sector: string;
  position_label: string | null;
  rule_rank: number;
  rule_score: number;
  base_rank: number;
  rank: number;
  base_score: number;
  draft_score: number;
  facts_cutoff_at: string | null;
  close_information_adjustment: number;
  close_information_reasons: string[];
  news_adjustment: number;
  financial_adjustment: number;
  dragon_tiger_adjustment: number;
  popularity_adjustment: number;
  dynamic_adjustment: number;
  dragon_tiger_on_list: boolean;
  dragon_tiger_is_new: boolean;
  dragon_tiger_net_buy_amount: number | null;
  dragon_tiger_source: string | null;
  popularity_base_rank: number | null;
  popularity_rank: number | null;
  popularity_rank_change: number | null;
  popularity_snapshot_at: string | null;
  popularity_source: string | null;
  update_reasons: string[];
  current_price: number | null;
  change_pct: number | null;
  turnover: number | null;
  quote_captured_at: string | null;
  latest_news: StockNewsItem[];
  financial_report: RecommendationFinancialReport | null;
  refreshed_at: string;
  data_missing: string[];
}

export interface RecommendationIntelligenceResponse {
  refresh_id: string;
  refreshed_at: string;
  interval_minutes: number;
  stage: "draft";
  status: "complete" | "partial";
  discovery_base_date: string | null;
  relay_base_date: string | null;
  items: RecommendationIntelligenceItem[];
  warnings: string[];
}

export interface PredictionQualityCohort {
  dimension: "prediction_source" | "scoring_version";
  value: string;
  row_count: number;
  unique_stock_date_count: number;
  trade_date_count: number;
  next_day_ready_count: number;
  next_day_coverage_rate: number;
}

export interface PredictionDateCoverage {
  trade_date: string;
  candidate_count: number;
  top_count: number;
  next_day_ready_count: number;
  three_day_ready_count: number;
  next_day_coverage_rate: number;
  three_day_coverage_rate: number;
  next_day_mature: boolean;
  three_day_mature: boolean;
  status: "complete" | "partial" | "pending" | "not_mature";
}

export interface PredictionBenchmarkMetrics {
  benchmark: string;
  label: string;
  trade_date_count: number;
  sample_size: number;
  avg_next_open_to_close_pct: number | null;
  positive_rate: number | null;
  promoted_to_second_board_rate: number | null;
  large_loss_rate: number | null;
  avg_three_day_open_to_close_pct: number | null;
  avg_max_drawdown_from_next_open_3d: number | null;
  excess_vs_ready_pool_pct: number | null;
}

export interface PredictionQualityPolicyStatus {
  champion_version: string;
  latest_challenger_version: string | null;
  latest_optimizer_version: string | null;
  promotion_eligible: boolean | null;
  outcome_ready_trade_dates: number;
  required_trade_dates: number;
  readiness_rate: number;
  gate_reasons: string[];
}

export interface PredictionQualityAuditResponse {
  start_date: string;
  end_date: string;
  latest_trade_date: string;
  audited_scoring_version: string;
  top_k: number;
  raw_prediction_rows: number;
  audited_prediction_rows: number;
  canonical_prediction_count: number;
  cross_cohort_duplicate_rows: number;
  data_as_of_violation_count: number;
  prediction_trade_date_count: number;
  next_day_mature_trade_date_count: number;
  complete_next_day_trade_date_count: number;
  next_day_outcome_coverage_rate: number;
  three_day_outcome_coverage_rate: number;
  cohorts: PredictionQualityCohort[];
  date_coverage: PredictionDateCoverage[];
  benchmarks: PredictionBenchmarkMetrics[];
  policy_status: PredictionQualityPolicyStatus;
  findings: string[];
  recommendations: string[];
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
  prediction_source: "live" | "historical_backtest";
  data_as_of: string;
  evaluation_label: string;
  outcome_ready: boolean;
  promoted_to_second_board: boolean;
  next_high_pct: number | null;
  next_close_pct: number | null;
  next_open_to_high_pct: number | null;
  next_open_to_low_pct: number | null;
  next_open_to_close_pct: number | null;
  three_day_high_pct: number | null;
  three_day_close_pct: number | null;
  three_day_open_to_close_pct: number | null;
  max_drawdown_from_next_open_3d: number | null;
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

export interface ReviewPromotionComparison {
  trade_date: string;
  next_trade_date: string | null;
  outcome_ready: boolean;
  top_pick_sample_size: number;
  top_pick_promoted_count: number;
  top_pick_promotion_rate: number | null;
  market_first_board_sample_size: number;
  market_promoted_count: number;
  market_promotion_rate: number | null;
  promotion_rate_delta: number | null;
}

export interface ReviewAgentReportResponse {
  start_date: string;
  end_date: string;
  sample_size: number;
  success_count: number;
  failed_count: number;
  pending_count: number;
  promotion_ready_date_count: number;
  top_pick_promotion_sample_size: number;
  top_pick_promoted_count: number;
  top_pick_promotion_rate: number | null;
  market_promotion_sample_size: number;
  market_promoted_count: number;
  market_promotion_rate: number | null;
  promotion_rate_delta: number | null;
  promotion_comparisons: ReviewPromotionComparison[];
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

export interface DailyReviewSnapshotSummary {
  as_of_date: string;
  start_date: string;
  sample_size: number;
  outcome_ready_count: number;
  top_pick_promotion_rate: number | null;
  market_promotion_rate: number | null;
  generated_by: string;
  generated_at: string;
}

export interface DailyReviewSnapshotsResponse {
  snapshots: DailyReviewSnapshotSummary[];
  generated_by: string;
}

export interface ScoringErrorCase {
  trade_date: string;
  symbol: string;
  name: string;
  rank: number;
  score: number;
  promoted_to_second_board: boolean;
  next_open_to_close_pct: number | null;
  leading_factors: string[];
}

export interface ScoringFactorErrorDiagnostic {
  factor_key: string;
  factor_name: string;
  false_positive_mean_score: number | null;
  false_negative_mean_score: number | null;
  false_negative_minus_false_positive: number | null;
  ablation_top_promotion_rate: number | null;
  ablation_delta: number | null;
  recommendation: "increase" | "decrease" | "neutral";
  evidence: string;
}

export interface ScoringErrorDiagnosticResponse {
  start_date: string;
  end_date: string;
  scoring_version: string;
  top_k: number;
  trade_date_count: number;
  pool_sample_size: number;
  top_sample_size: number;
  top_promoted_count: number;
  top_promotion_rate: number | null;
  market_promoted_count: number;
  market_promotion_rate: number | null;
  promotion_rate_delta: number | null;
  false_positive_count: number;
  false_negative_count: number;
  false_positive_samples: ScoringErrorCase[];
  false_negative_samples: ScoringErrorCase[];
  factors: ScoringFactorErrorDiagnostic[];
  findings: string[];
  warnings: string[];
  generated_by: string;
}

export interface AgentChatRequest {
  session_id: string;
  message_id?: string;
  message: string;
  intent_hint?: string;
  trade_date?: string;
  symbol?: string;
  page_context?: Record<string, string>;
}

export interface AgentStockMention {
  name: string;
  symbol: string;
  trade_date: string | null;
}

export interface ChatSessionMessage {
  message_id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  status: "success" | "error";
  run_id: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface ChatSessionSummary {
  session_id: string;
  owner_id: string;
  title: string;
  message_count: number;
  last_message_preview: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatSessionDetail extends ChatSessionSummary {
  messages: ChatSessionMessage[];
}

export interface ChatSessionsResponse {
  sessions: ChatSessionSummary[];
  generated_by: string;
}

export interface AgentChatResponse {
  session_id: string;
  run_id: string | null;
  intent: string;
  answer: string;
  stock_mentions: AgentStockMention[];
  tool_calls: string[];
  tool_results: AgentToolTrace[];
  evidence_cards: AgentEvidenceCard[];
  tool_policy: AgentToolPolicyAudit;
  references: string[];
  warnings: string[];
  performance: AgentChatPerformance;
  suggested_questions: string[];
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
}

export interface OutcomeCompletenessDate {
  trade_date: string;
  prediction_source: "live" | "historical_backtest";
  candidate_count: number;
  elapsed_post_trade_days: number;
  d1_mature: boolean;
  d1_expected_count: number;
  d1_ready_count: number;
  d1_missing_symbols: string[];
  d3_mature: boolean;
  d3_expected_count: number;
  d3_ready_count: number;
  d3_missing_symbols: string[];
  d5_mature: boolean;
  d5_expected_count: number;
  d5_ready_count: number;
  d5_missing_symbols: string[];
  status: "complete" | "partial" | "pending";
}

export interface OutcomeCompletenessReport {
  as_of_date: string | null;
  status: "healthy" | "partial" | "missing" | "pending";
  prediction_trade_date_count: number;
  tracked_prediction_count: number;
  d1_expected_count: number;
  d1_ready_count: number;
  d3_expected_count: number;
  d3_ready_count: number;
  d5_expected_count: number;
  d5_ready_count: number;
  missing_case_count: number;
  dates: OutcomeCompletenessDate[];
  warnings: string[];
  generated_by: string;
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
  top_candidates: AgentDataHealthTopCandidate[];
  outcome_completeness: OutcomeCompletenessReport | null;
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

export interface DailyPipelineRun {
  run_id: string;
  trade_date: string;
  trigger: "scheduled" | "manual" | "startup";
  status: "running" | "success" | "partial" | "error" | "skipped";
  attempt_count: number;
  report: Record<string, unknown> | null;
  error_message: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface DailyPipelineStatusResponse {
  latest: DailyPipelineRun | null;
  recent: DailyPipelineRun[];
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
    | "evaluation"
    | "data_availability"
    | "tool";
  status: "success" | "error" | "skipped";
  summary: string;
  facts: string[];
  metrics: Record<string, string | number | boolean | null>;
  source_tools: string[];
}
