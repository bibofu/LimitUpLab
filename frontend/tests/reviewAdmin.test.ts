import assert from "node:assert/strict";
import test from "node:test";

import { HttpResponseError } from "../src/utils/jsonRequest.ts";
import {
  buildReviewCaseUpdate,
  canExportReviewCase,
  formFromCase,
  reviewAuthFailure,
  reviewErrorMessage,
  reviewSeverityLabel,
  reviewSourceLabel,
  reviewStatusLabel,
} from "../src/utils/reviewAdmin.ts";
import type { ReviewCaseDetail } from "../src/reviewTypes.ts";

const detail: ReviewCaseDetail = {
  case_id: "case_1",
  run_id: "run_1",
  session_id: "chat_1",
  status: "confirmed",
  source: "user_feedback",
  severity: "high",
  intent: "market_summary",
  task_status: "complete",
  question_preview: "市场最高标是哪个",
  signal_count: 1,
  has_new_signal: true,
  created_at: "2026-10-28T00:00:00Z",
  updated_at: "2026-10-28T00:00:00Z",
  reviewed_at: "2026-10-28T00:00:00Z",
  closed_at: null,
  question: "市场最高标是哪个",
  answer: "研究回答",
  evidence: {},
  trace_url: null,
  symptom: "日期错误",
  root_cause: "",
  fix_commit: "",
  fix_version: "",
  verification: "",
  revision: 3,
  signals: [],
  events: [],
  reports: [],
};

test("review labels cover Chinese status, source and severity text", () => {
  assert.equal(reviewStatusLabel("new"), "待复核");
  assert.equal(reviewStatusLabel("verifying"), "待验证");
  assert.equal(reviewSourceLabel("user_feedback"), "用户反馈");
  assert.equal(reviewSourceLabel("deterministic"), "规则检查");
  assert.equal(reviewSeverityLabel("high"), "高");
  assert.equal(reviewStatusLabel("unknown"), "unknown");
});

test("only reviewed cases can be exported", () => {
  assert.equal(canExportReviewCase("new"), false);
  assert.equal(canExportReviewCase("dismissed"), false);
  assert.equal(canExportReviewCase("confirmed"), true);
  assert.equal(canExportReviewCase("closed"), true);
});

test("case updates trim conclusions and keep the server revision", () => {
  const form = formFromCase(detail);
  assert.deepEqual(form, {
    status: "confirmed",
    severity: "high",
    symptom: "日期错误",
    root_cause: "",
    fix_commit: "",
    fix_version: "",
    verification: "",
  });
  const update = buildReviewCaseUpdate(
    detail,
    { ...form, root_cause: "  未取最近交易日  ", fix_version: " v1.2 " },
    "  确认问题  ",
  );
  assert.equal(update.root_cause, "未取最近交易日");
  assert.equal(update.fix_version, "v1.2");
  assert.equal(update.note, "确认问题");
  assert.equal(update.revision, 3);
});

test("authentication failures are distinguished from queue conflicts", () => {
  assert.equal(reviewAuthFailure(new HttpResponseError(401, "no")), true);
  assert.equal(reviewAuthFailure(new HttpResponseError(409, "conflict")), false);
  assert.equal(reviewErrorMessage(new HttpResponseError(401, "no")), "管理员密钥无效或已过期");
  assert.equal(
    reviewErrorMessage(new HttpResponseError(409, "conflict")),
    "案例已被其他页面修改，请刷新后重试",
  );
});
