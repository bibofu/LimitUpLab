import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import { createReviewReportResource } from "../src/utils/reviewReportResource.ts";
import type { ReviewAgentReportResponse } from "../src/types.ts";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const flush = () => new Promise<void>(resolve => setImmediate(resolve));
const day = "2026-10-09";
function report(patch: Partial<ReviewAgentReportResponse> = {}): ReviewAgentReportResponse {
  return {
    start_date: "2026-09-28", end_date: day, sample_size: 10, success_count: 2,
    failed_count: 3, pending_count: 5, promotion_ready_date_count: 1,
    top_pick_promotion_sample_size: 10, top_pick_promoted_count: 2, top_pick_promotion_rate: 0.2,
    market_promotion_sample_size: 20, market_promoted_count: 3, market_promotion_rate: 0.15,
    promotion_rate_delta: 0.05, promotion_comparisons: [], main_findings: ["样本中晋级较集中"],
    successful_patterns: ["成功特征"], failed_patterns: ["失败特征"], scoring_bias: ["高分样本有偏差"],
    adjustment_suggestions: ["扩大样本后验证"], confidence: 0.4, reviewed_picks: [],
    tool_results: [], warnings: [], generated_by: "review_agent", generation_mode: "deterministic",
    ...patch,
  };
}

test("facts publish before the model; duplicate mounts share both requests and adopt refreshed facts together", async () => {
  const base = deferred<ReviewAgentReportResponse>();
  const model = deferred<ReviewAgentReportResponse>();
  const calls: unknown[] = [];
  const resource = createReviewReportResource(params => {
    calls.push(params);
    return params.use_llm ? model.promise : base.promise;
  });
  const stop = resource.subscribe(day, () => {});
  const first = resource.load(day);
  stop();
  resource.subscribe(day, () => {});
  await resource.load(day);
  assert.equal(calls.length, 1);
  const facts = report();
  base.resolve(facts);
  await flush();
  assert.equal(resource.getState(day).report, facts);
  assert.equal(resource.getState(day).loading, false);
  assert.equal(resource.getState(day).generating, true);
  await resource.load(day);
  await resource.regenerate(day);
  assert.deepEqual(calls, [false, true].map(use_llm => ({ end_date: day, top_per_day: 10, follow_days: 5, use_llm })));
  const refreshed = report({ sample_size: 12, generation_mode: "llm", main_findings: ["回填后的总结"] });
  model.resolve(refreshed);
  await first;
  assert.equal(resource.getState(day).report, refreshed);
  await resource.load(day);
  assert.equal(calls.length, 2);
});

test("generation failure preserves the report and is retried only by an explicit single-flight action", async () => {
  let modelCalls = 0;
  const retry = deferred<ReviewAgentReportResponse>();
  const resource = createReviewReportResource(async params => {
    if (!params.use_llm) return report();
    if (++modelCalls === 1) throw new Error("模型请求超时");
    return retry.promise;
  });
  resource.subscribe(day, () => {});
  await resource.load(day);
  assert.equal(resource.getState(day).summaryError, "模型请求超时");
  assert.equal(resource.getState(day).report?.sample_size, 10);
  await resource.load(day);
  assert.equal(modelCalls, 1);
  const pending = resource.regenerate(day);
  await resource.regenerate(day);
  assert.equal(modelCalls, 2);
  assert.equal(resource.getState(day).report?.sample_size, 10);
  retry.resolve(report({ generation_note: "模型不可用，使用规则总结" }));
  await pending;
  assert.equal(resource.getState(day).summaryError, null);
  assert.equal(resource.getState(day).report?.generation_mode, "deterministic");
});

test("late responses or errors after a date switch never publish into the new date", async () => {
  for (const fails of [false, true]) {
    const oldModel = deferred<ReviewAgentReportResponse>();
    const resource = createReviewReportResource(async params => {
      if (params.end_date === day && params.use_llm) return oldModel.promise;
      return report({ end_date: params.end_date, generation_mode: params.use_llm ? "llm" : "deterministic" });
    });
    let oldNotifications = 0;
    const stop = resource.subscribe(day, () => { oldNotifications++; });
    const oldRun = resource.load(day);
    await flush();
    stop();
    const stoppedAt = oldNotifications;
    const next = "2026-10-12";
    let newNotifications = 0;
    resource.subscribe(next, () => { newNotifications++; });
    await resource.load(next);
    const completedAt = newNotifications;
    if (fails) oldModel.reject(new Error("过期错误"));
    else oldModel.resolve(report({ main_findings: ["旧日期总结"] }));
    await oldRun;
    assert.equal(oldNotifications, stoppedAt);
    assert.equal(newNotifications, completedAt);
    assert.equal(resource.getState(next).report?.end_date, next);
    assert.equal(resource.getState(next).summaryError, null);
  }
});

test("leaving before facts arrive does not start a model call; returning resumes once", async () => {
  const base = deferred<ReviewAgentReportResponse>();
  let modelCalls = 0;
  const resource = createReviewReportResource(async params => {
    if (!params.use_llm) return base.promise;
    modelCalls++;
    return report({ generation_mode: "llm" });
  });
  const stop = resource.subscribe(day, () => {});
  const load = resource.load(day);
  stop();
  base.resolve(report());
  await load;
  assert.equal(modelCalls, 0);
  resource.subscribe(day, () => {});
  await resource.load(day);
  assert.equal(modelCalls, 1);
});

test("failed fact loading has a manual retry and never initiates a model without facts", async () => {
  const calls: boolean[] = [];
  const resource = createReviewReportResource(async params => {
    calls.push(params.use_llm);
    if (calls.length === 1) throw new Error("本地统计暂不可用");
    return report();
  });
  resource.subscribe(day, () => {});
  await resource.load(day);
  await resource.load(day);
  assert.equal(resource.getState(day).error, "本地统计暂不可用");
  assert.deepEqual(calls, [false]);
  await resource.load(day, true);
  assert.deepEqual(calls, [false, false, true]);
  assert.equal(resource.getState(day).error, null);
});

function compile(path: string, imports: Record<string, unknown> = {}, exports = "") {
  const url = new URL(path, import.meta.url);
  const require = createRequire(url);
  const compiled = ts.transpileModule(readFileSync(url, "utf8") + exports, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const module = { exports: {} as Record<string, any> };
  new Function("require", "module", "exports", compiled)((id: string) => imports[id] ?? require(id.startsWith(".") ? `${id}.ts` : id), module, module.exports);
  return module.exports;
}
const summary = compile("../src/components/ReviewSummary.tsx");
const renderSummary = (value: ReviewAgentReportResponse, generating = false, error: string | null = null) => (
  renderToStaticMarkup(createElement(summary.ReviewSummary, { report: value, generating, error, onRegenerate() {} }))
);

test("the actual summary shows provenance, all evidence sections, fallback details and retry controls", () => {
  const llm = renderSummary(report({ generation_mode: "llm", llm_model: "mock-model" }));
  assert.ok(llm.includes("LLM 生成 · mock-model"));
  assert.ok(llm.includes("重新生成"));
  for (const value of ["样本中晋级较集中", "高分样本有偏差", "扩大样本后验证", "不会自动修改评分、权重或预测记录"]) {
    assert.ok(llm.includes(value));
  }
  const fallback = renderSummary(report({ generation_note: "模型返回无效，使用规则总结" }), false, "网络失败");
  for (const value of ["规则回退", "模型返回无效", "网络失败", "重试模型总结", 'role="alert"']) assert.ok(fallback.includes(value));
  assert.ok(renderSummary(report({ generation_mode: undefined })).includes("历史来源未标识"));
  assert.ok(renderSummary(report({ generation_mode: "legacy" })).includes("历史来源未标识"));
  const pending = renderSummary(report(), true);
  for (const value of ['aria-busy="true"', "disabled", "生成中", "统计与追踪仍可查看", "样本中晋级较集中"]) assert.ok(pending.includes(value));
});

test("the actual review panel retains deterministic statistics and tracking while the model is pending or failed", () => {
  for (const [generating, summaryError] of [[true, null], [false, "模型超时"]] as const) {
    const dashboard = compile("../src/components/ReviewDashboard.tsx", {
      "../api": {}, "./ReviewSummary": summary,
      "../hooks/useReviewReport": { useReviewReport: () => ({ report: report(), loading: false, error: null, generating, summaryError, retry() {}, regenerate() {} }) },
      "./Panel": { Panel: ({ children }: any) => createElement("section", null, children) },
    }, "\nexport { HighScoreReviewPanel };");
    const html = renderToStaticMarkup(createElement(dashboard.HighScoreReviewPanel, { latestTradeDate: day }));
    for (const value of ["Top10 1进2", "同期全部首板", "daily-top-review", "复盘总结", "样本中晋级较集中"]) assert.ok(html.includes(value));
    assert.ok(html.includes(generating ? "生成中" : "模型超时"));
  }
});
