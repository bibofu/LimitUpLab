export interface ObservabilityBucket {
  date: string;
  request_count: number;
  success_count: number;
  error_count: number;
  rejected_count: number;
  distinct_owners: number;
  answered_count: number;
  partial_count: number;
  answer_error_count: number;
  clarify_refuse_count: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  measured_request_count: number;
  incomplete_token_count: number;
  estimated_cost_usd: number;
  up_count: number;
  down_count: number;
  case_count: number;
}

export interface ObservabilityTotals {
  request_count: number;
  success_count: number;
  error_count: number;
  rejected_count: number;
  running_count: number;
  distinct_owners: number;
  distinct_sessions: number;
  answered_count: number;
  partial_count: number;
  answer_error_count: number;
  clarify_refuse_count: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  measured_request_count: number;
  incomplete_token_count: number;
  estimated_cost_usd: number;
  up_count: number;
  down_count: number;
  vote_count: number;
  like_ratio: number | null;
  feedback_coverage: number | null;
  down_per_100_answers: number | null;
  case_count: number;
  open_case_count: number;
  confirmed_case_count: number;
  closed_case_count: number;
  mtta_seconds: number | null;
  mttr_seconds: number | null;
  p50_duration_ms: number | null;
  p95_duration_ms: number | null;
  judge_call_count: number;
  judge_succeeded_count: number;
  judge_error_count: number;
  judge_prompt_tokens: number;
  judge_completion_tokens: number;
  judge_total_tokens: number;
  judge_estimated_cost_usd: number;
  judge_priced_call_count: number;
  judge_incomplete_token_count: number;
}

export interface ObservabilityReasonCount {
  reason: string;
  count: number;
}

export interface ObservabilitySummary {
  days: number;
  period_started_at: string;
  generated_at: string;
  timezone: string;
  totals: ObservabilityTotals;
  buckets: ObservabilityBucket[];
  feedback_reasons: ObservabilityReasonCount[];
  case_status_counts: Record<string, number>;
  case_source_counts: Record<string, number>;
  job_status_counts: Record<string, number>;
  job_kind_counts: Record<string, number>;
  notes: string[];
  generated_by: string;
}
