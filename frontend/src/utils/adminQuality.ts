import type { AnswerFeedbackValue, QualityRequest } from "./agentQuality";

export const caseStatuses = {
  pending: "待复核", confirmed: "已确认", in_progress: "修复中", verifying: "待验证", closed: "已关闭",
  duplicate: "重复案例", not_a_bug: "非缺陷", insufficient_evidence: "证据不足",
} as const;
export type CaseStatus = keyof typeof caseStatuses;
export const severities = { low: "低", medium: "中", high: "高", critical: "严重" } as const;
export type Severity = keyof typeof severities;
const transitions: Record<CaseStatus, CaseStatus[]> = {
  pending: ["confirmed", "duplicate", "not_a_bug", "insufficient_evidence"],
  confirmed: ["pending", "in_progress", "verifying", "duplicate", "not_a_bug", "insufficient_evidence"],
  in_progress: ["pending", "confirmed", "verifying", "duplicate", "not_a_bug", "insufficient_evidence"],
  verifying: ["in_progress", "confirmed", "closed"], closed: ["pending", "confirmed"],
  duplicate: ["pending"], not_a_bug: ["pending"], insufficient_evidence: ["pending"],
};
export const reviewTransitions = (status: CaseStatus) => [status, ...transitions[status]];
export interface QualityCase {
  case_id: string; run_id: string; status: CaseStatus; severity: Severity; symptom: string; root_cause: string;
  assignee: string; fix_version: string; duplicate_of: string | null; version: string; revision: number;
  question?: string; sources?: string[]; signal_count?: number; updated_at: string;
}
export interface CaseDetail {
  case: QualityCase;
  evaluations?: { items: EvaluationRecord[]; total: number };
  has_new_signals_since_review?: boolean;
  signals: { signal_id: string; source: string; code: string; severity: Severity; summary: string; evidence: unknown; detector_version: string; created_at: string }[];
  reviews: { actor_id: string; from_status: CaseStatus; to_status: CaseStatus; revision: number; note: string; changes: unknown; created_at: string }[];
  run: {
    request: Record<string, unknown>; response: Record<string, unknown> | null; checkpoint: unknown; calls: unknown;
    execution_status?: string; delivery_status: string; delivery_events: unknown; context: unknown; feedback: AnswerFeedbackValue | null;
  };
  trace_snapshots: { id: number; stage: string; captured_at: string; payload: unknown }[];
}

export interface EvaluationRecord {
  evaluation_id: string; bundle_id: string | null; evaluator_version: string;
  execution_status: "running" | "succeeded" | "error" | "skipped"; verdict: "pass" | "fail" | "unknown" | null;
  report: { checks?: { check_id: string; verdict: "pass" | "fail" | "unknown"; summary: string; unknown_reason?: string | null; evidence: unknown }[]; metadata?: unknown } | null;
}
export type CaseFilters = { status: string; source: string; severity: string; symptom: string; version: string };
export const emptyCaseFilters = (): CaseFilters => ({ status: "", source: "", severity: "", symptom: "", version: "" });
export interface CaseReview {
  status: CaseStatus; expected_revision: number; note: string; symptom: string; root_cause: string;
  severity: Severity; assignee: string; fix_version: string; duplicate_of: string | null;
}
export type JobHealth = { counts: Record<string, number>; failed: { id: string; run_id: string; attempts: number; last_error: string }[] };

/** An explicit in-memory key is required on every admin operation, including reads. */
export function createAdminQualityClient(request: QualityRequest, adminKey: string) {
  const call = <T>(path: string, init: RequestInit = {}) => request<T>(`/api/admin/quality${path}`, {
    ...init, headers: { ...init.headers, "X-LimitUpLab-Admin-Key": adminKey },
  });
  const path = (id: string) => `/cases/${encodeURIComponent(id)}`;
  return {
    list(filters = emptyCaseFilters(), offset = 0, signal?: AbortSignal) {
      const query = new URLSearchParams({ limit: "20", offset: String(offset) });
      Object.entries(filters).forEach(([key, value]) => { if (value.trim()) query.set(key, value.trim()); });
      return call<{ items: QualityCase[]; total: number }>(`/cases?${query}`, { signal });
    },
    detail: (id: string, signal?: AbortSignal) => call<CaseDetail>(path(id), { signal }),
    review: (id: string, value: CaseReview) => call<CaseDetail>(path(id), {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value),
    }),
    jobs: (signal?: AbortSignal) => call<JobHealth>("/jobs", { signal }),
    process: () => call<{ processed: number; succeeded: number; failed: number }>("/process?limit=20", { method: "POST" }),
  };
}

export function reviewDraft(item: QualityCase): CaseReview {
  return { status: item.status, expected_revision: item.revision, note: "", symptom: item.symptom,
    root_cause: item.root_cause, severity: item.severity, assignee: item.assignee,
    fix_version: item.fix_version, duplicate_of: item.duplicate_of };
}

export function validateReview(draft: CaseReview): string | null {
  if (!draft.note.trim()) return "请填写本次审核说明。";
  if (["confirmed", "in_progress", "verifying", "closed"].includes(draft.status) && !draft.symptom.trim()) return "确认问题时需要填写症状。";
  if (["verifying", "closed"].includes(draft.status) && (!draft.root_cause.trim() || !draft.fix_version.trim())) return "验证和关闭前需要填写根因及修复版本。";
  if (draft.status === "duplicate" && !draft.duplicate_of?.trim()) return "请填写关联的非重复案例 ID。";
  return null;
}
