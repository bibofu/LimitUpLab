export type ReviewSeverity = "info" | "low" | "medium" | "high";
export type ReviewStatus =
  | "new"
  | "confirmed"
  | "fixing"
  | "verifying"
  | "closed"
  | "dismissed"
  | "insufficient";
export type ReviewSource =
  | "user_feedback"
  | "deterministic"
  | "judge"
  | "sample"
  | "manual";
export type ReviewSignalKind =
  | "downvote"
  | "deterministic"
  | "judge"
  | "sample"
  | "run_error"
  | "grounding"
  | "manual";

export interface ReviewSignal {
  signal_id: string;
  run_id: string;
  case_id: string | null;
  kind: ReviewSignalKind;
  severity: ReviewSeverity;
  detector: string;
  detector_version: string;
  source_ref: string;
  summary: string;
  details: Record<string, unknown>;
  created_at: string;
  processed_at: string | null;
}

export interface ReviewCaseEvent {
  event_id: string;
  action: string;
  from_status: string | null;
  to_status: string | null;
  changes: Record<string, unknown>;
  note: string;
  actor: string;
  created_at: string;
}

export interface ReviewCaseSummary {
  case_id: string;
  run_id: string | null;
  session_id: string;
  status: ReviewStatus;
  source: ReviewSource;
  severity: ReviewSeverity;
  intent: string | null;
  task_status: string | null;
  question_preview: string;
  signal_count: number;
  has_new_signal: boolean;
  created_at: string;
  updated_at: string;
  reviewed_at: string | null;
  closed_at: string | null;
}

export interface ReviewCaseDetail extends ReviewCaseSummary {
  question: string;
  answer: string;
  evidence: Record<string, unknown>;
  trace_url: string | null;
  symptom: string;
  root_cause: string;
  fix_commit: string;
  fix_version: string;
  verification: string;
  revision: number;
  signals: ReviewSignal[];
  events: ReviewCaseEvent[];
}

export interface ReviewCasesResponse {
  cases: ReviewCaseSummary[];
  generated_by: string;
}

export interface ReviewCaseUpdate {
  status: ReviewStatus;
  severity: ReviewSeverity;
  symptom: string;
  root_cause: string;
  fix_commit: string;
  fix_version: string;
  verification: string;
  note: string;
  revision: number;
}

export interface ReviewFeedbackRow {
  run_id: string;
  session_id: string;
  vote: "up" | "down";
  reasons: string[];
  comment: string;
  question_preview: string;
  task_status: string | null;
  updated_at: string;
}

export interface ReviewFeedbackResponse {
  feedback: ReviewFeedbackRow[];
  generated_by: string;
}

export type ReviewJobKind = "deterministic" | "sample" | "judge";
export type ReviewJobStatus =
  | "pending"
  | "processing"
  | "succeeded"
  | "skipped"
  | "failed"
  | "deferred";

export interface ReviewJob {
  job_id: string;
  run_id: string;
  kind: ReviewJobKind;
  priority: number;
  status: ReviewJobStatus;
  attempts: number;
  available_at: string;
  detector_version: string;
  config_fingerprint: string;
  error_message: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface ReviewJobsResponse {
  jobs: ReviewJob[];
  generated_by: string;
}

export interface ReviewDrainResponse {
  sampled_runs: number;
  processed_jobs: number;
  aggregated_signals: number;
}

export interface ReviewSampleResponse {
  enqueued: number;
}
