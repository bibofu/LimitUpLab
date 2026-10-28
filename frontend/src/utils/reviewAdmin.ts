import type {
  ReviewCaseDetail,
  ReviewCaseUpdate,
  ReviewSeverity,
  ReviewSource,
  ReviewStatus,
} from "../reviewTypes";

export interface ReviewCaseForm {
  status: ReviewStatus;
  severity: ReviewSeverity;
  symptom: string;
  root_cause: string;
  fix_commit: string;
  fix_version: string;
  verification: string;
}

export const REVIEW_STATUS_OPTIONS: { value: ReviewStatus; label: string }[] = [
  { value: "new", label: "待复核" },
  { value: "confirmed", label: "已确认" },
  { value: "fixing", label: "修复中" },
  { value: "verifying", label: "待验证" },
  { value: "closed", label: "已关闭" },
  { value: "dismissed", label: "已驳回" },
  { value: "insufficient", label: "证据不足" },
];

export const REVIEW_SEVERITY_OPTIONS: { value: ReviewSeverity; label: string }[] = [
  { value: "high", label: "高" },
  { value: "medium", label: "中" },
  { value: "low", label: "低" },
  { value: "info", label: "提示" },
];

const STATUS_LABELS = Object.fromEntries(
  REVIEW_STATUS_OPTIONS.map(item => [item.value, item.label]),
) as Record<ReviewStatus, string>;

const SEVERITY_LABELS = Object.fromEntries(
  REVIEW_SEVERITY_OPTIONS.map(item => [item.value, item.label]),
) as Record<ReviewSeverity, string>;

export const REVIEW_SOURCE_LABELS: Record<ReviewSource, string> = {
  user_feedback: "用户反馈",
  deterministic: "规则检查",
  judge: "模型评审",
  sample: "随机抽检",
  manual: "人工",
};

export const REVIEW_SEVERITY_RANK: Record<ReviewSeverity, number> = {
  info: 0,
  low: 1,
  medium: 2,
  high: 3,
};

export function reviewStatusLabel(value: string): string {
  return STATUS_LABELS[value as ReviewStatus] ?? value;
}

export function reviewSeverityLabel(value: string): string {
  return SEVERITY_LABELS[value as ReviewSeverity] ?? value;
}

export function reviewSourceLabel(value: string): string {
  return REVIEW_SOURCE_LABELS[value as ReviewSource] ?? value;
}

/** Only reviewed cases may be copied into badCase.md. */
export function canExportReviewCase(status: ReviewStatus): boolean {
  return ["confirmed", "fixing", "verifying", "closed"].includes(status);
}

export function formFromCase(detail: ReviewCaseDetail): ReviewCaseForm {
  return {
    status: detail.status,
    severity: detail.severity,
    symptom: detail.symptom,
    root_cause: detail.root_cause,
    fix_commit: detail.fix_commit,
    fix_version: detail.fix_version,
    verification: detail.verification,
  };
}

export function buildReviewCaseUpdate(
  detail: ReviewCaseDetail,
  form: ReviewCaseForm,
  note: string,
): ReviewCaseUpdate {
  return {
    status: form.status,
    severity: form.severity,
    symptom: form.symptom.trim(),
    root_cause: form.root_cause.trim(),
    fix_commit: form.fix_commit.trim(),
    fix_version: form.fix_version.trim(),
    verification: form.verification.trim(),
    note: note.trim(),
    revision: detail.revision,
  };
}

function httpStatus(error: unknown): number | null {
  if (typeof error !== "object" || error === null || !("status" in error)) return null;
  const status = (error as { status: unknown }).status;
  return typeof status === "number" ? status : null;
}

export function reviewErrorMessage(error: unknown): string {
  const status = httpStatus(error);
  if (status === 401) return "管理员密钥无效或已过期";
  if (status === 503) return "管理员接口未配置";
  if (status === 409) return "案例已被其他页面修改，请刷新后重试";
  return error instanceof Error ? error.message : "请求失败";
}

export function reviewAuthFailure(error: unknown): boolean {
  const status = httpStatus(error);
  return status === 401 || status === 503;
}

export function formatReviewTime(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("zh-CN", { hour12: false });
}
