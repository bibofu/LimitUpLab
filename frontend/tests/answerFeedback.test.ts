import assert from "node:assert/strict";
import test from "node:test";

import {
  buildFeedbackPayload,
  normalizeFeedbackComment,
  toggleFeedbackReason,
  type FeedbackReason,
} from "../src/utils/answerFeedback.ts";

test("reason toggles stay bounded and order preserving", () => {
  assert.deepEqual(toggleFeedbackReason([], "data_error"), ["data_error"]);
  assert.deepEqual(
    toggleFeedbackReason(["data_error"], "data_error"),
    [],
  );
  const five: FeedbackReason[] = [
    "data_error", "wrong_scope", "missing_source", "incomplete", "display",
  ];
  assert.deepEqual(toggleFeedbackReason(five, "other"), five);
  assert.deepEqual(toggleFeedbackReason(five, "data_error"), [
    "wrong_scope", "missing_source", "incomplete", "display",
  ]);
});

test("feedback comments drop control characters and respect the limit", () => {
  assert.equal(normalizeFeedbackComment("  日期\u0000不对  "), "日期不对");
  assert.equal(normalizeFeedbackComment("x".repeat(1200)).length, 1000);
});

test("positive votes never carry stale reasons or comments", () => {
  assert.deepEqual(buildFeedbackPayload("up", ["data_error"], "旧说明"), {
    vote: "up", reasons: [], comment: "",
  });
  assert.deepEqual(
    buildFeedbackPayload("down", ["data_error", "data_error", "display"], " 说明 "),
    { vote: "down", reasons: ["data_error", "display"], comment: "说明" },
  );
});
