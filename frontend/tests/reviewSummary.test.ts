import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import ts from "typescript";
import { createReviewReportResource } from "../src/utils/reviewReportResource.ts";
import type { ReviewReportState, ReviewSummaryStatus } from "../src/utils/reviewReportResource.ts";
import type { ReviewAgentReportResponse } from "../src/types.ts";
import { reviewDigest } from "./reviewDigestFixture.ts";

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
  const published: ReviewReportState[] = [];
  const resource = createReviewReportResource(params => {
    calls.push(params);
    return params.use_llm ? model.promise : base.promise;
  });
  const stop = resource.subscribe(day, () => {});
  const first = resource.load(day);
  stop();
  resource.subscribe(day, () => { published.push(resource.getState(day)); });
  await resource.load(day);
  assert.equal(calls.length, 1);
  const facts = report();
  base.resolve(facts);
  await flush();
  assert.equal(resource.getState(day).report, facts);
  assert.equal(resource.getState(day).loading, false);
  assert.equal(resource.getState(day).generating, true);
  assert.equal(resource.getState(day).summaryStatus, "generating");
  assert.ok(published.filter(state => state.report === facts).every(state => state.summaryStatus === "generating"));
  await resource.load(day);
  await resource.regenerate(day);
  assert.deepEqual(calls, [false, true].map(use_llm => ({ end_date: day, top_per_day: 10, follow_days: 5, use_llm })));
  const refreshed = report({ sample_size: 12, generation_mode: "llm", main_findings: ["回填后的总结"] });
  model.resolve(refreshed);
  await first;
  assert.equal(resource.getState(day).report, refreshed);
  assert.equal(resource.getState(day).summaryStatus, "ready");
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
  assert.equal(resource.getState(day).summaryStatus, "error");
  assert.equal(resource.getState(day).report?.sample_size, 10);
  await resource.load(day);
  assert.equal(modelCalls, 1);
  const pending = resource.regenerate(day);
  await resource.regenerate(day);
  assert.equal(modelCalls, 2);
  assert.equal(resource.getState(day).summaryStatus, "generating");
  assert.equal(resource.getState(day).report?.sample_size, 10);
  retry.resolve(report({ generation_note: "模型不可用，使用规则总结" }));
  await pending;
  assert.equal(resource.getState(day).summaryError, null);
  assert.equal(resource.getState(day).report?.generation_mode, "deterministic");
  assert.equal(resource.getState(day).summaryStatus, "fallback");
});

test("initial rule notes are published only with generating, and a real model fallback has its own stage", async () => {
  const pending = deferred<ReviewAgentReportResponse>();
  const baseline = report({ generation_note: "本次未启用 LLM，展示规则总结。" });
  const published: ReviewReportState[] = [];
  const resource = createReviewReportResource(async params => params.use_llm ? pending.promise : baseline);
  resource.subscribe(day, () => { published.push(resource.getState(day)); });
  const task = resource.load(day);
  await flush();
  const withFacts = published.filter(state => state.report !== null);
  assert.equal(withFacts.length, 1);
  assert.equal(withFacts[0].summaryStatus, "generating");
  assert.equal(withFacts[0].summaryError, null);
  pending.resolve(report({ generation_note: "LLM 请求失败，已回退规则总结。" }));
  await task;
  assert.equal(resource.getState(day).summaryStatus, "fallback");
  assert.equal(resource.getState(day).report?.generation_note, "LLM 请求失败，已回退规则总结。");
});

test("regeneration keeps the previous model report while exposing the current request stage", async () => {
  const old = report({ generation_mode: "llm", generation_note: "旧模型生成说明" });
  const pending = deferred<ReviewAgentReportResponse>();
  let calls = 0;
  const resource = createReviewReportResource(async params => {
    if (!params.use_llm) return report();
    return ++calls === 1 ? old : pending.promise;
  });
  resource.subscribe(day, () => {});
  await resource.load(day);
  assert.equal(resource.getState(day).summaryStatus, "ready");
  const task = resource.regenerate(day);
  assert.equal(resource.getState(day).summaryStatus, "generating");
  assert.equal(resource.getState(day).summaryError, null);
  assert.equal(resource.getState(day).report, old);
  pending.reject(new Error("本次模型超时"));
  await task;
  assert.equal(resource.getState(day).summaryStatus, "error");
  assert.equal(resource.getState(day).summaryError, "本次模型超时");
  assert.equal(resource.getState(day).report, old);
});

test("unidentified model results are errors rather than a claimed successful summary or rule fallback", async () => {
  for (const generation_mode of ["legacy", undefined] as const) {
    const resource = createReviewReportResource(async () => report({ generation_mode }));
    resource.subscribe(day, () => {});
    await resource.load(day);
    assert.equal(resource.getState(day).summaryStatus, "error");
    assert.equal(resource.getState(day).summaryError, "模型总结来源未确认，请重试。");
  }
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
  assert.equal(resource.getState(day).summaryStatus, "idle");
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

test("refreshing facts invalidates an in-flight model without automatically paying for another", async () => {
  for (const fails of [false, true]) {
    const oldModel = deferred<ReviewAgentReportResponse>();
    const refreshedFacts = report({ sample_size: 15, main_findings: ["新事实"] });
    const calls: Array<{ use_llm: boolean; refresh_facts?: boolean }> = [];
    const resource = createReviewReportResource(async params => {
      calls.push(params);
      if (params.use_llm && calls.length === 2) return oldModel.promise;
      return params.refresh_facts ? refreshedFacts : report({ generation_mode: params.use_llm ? "llm" : "deterministic" });
    });
    resource.subscribe(day, () => {});
    const original = resource.load(day);
    await flush();
    assert.equal(resource.getState(day).generating, true);
    await resource.refreshFacts();
    assert.equal(resource.getState(day).report, refreshedFacts);
    assert.equal(resource.getState(day).summaryStatus, "idle");
    if (fails) oldModel.reject(new Error("旧请求错误"));
    else oldModel.resolve(report({ sample_size: 10, generation_mode: "llm", main_findings: ["旧模型总结"] }));
    await original;
    await resource.load(day);
    assert.equal(resource.getState(day).report, refreshedFacts);
    assert.equal(resource.getState(day).summaryError, null);
    assert.equal(resource.getState(day).generating, false);
    assert.equal(resource.getState(day).summaryStatus, "idle");
    assert.deepEqual(calls.map(value => [value.use_llm, value.refresh_facts]), [[false, undefined], [true, undefined], [false, true]]);
    await resource.regenerate(day);
    assert.equal(calls.length, 4);
    assert.equal(resource.getState(day).report?.generation_mode, "llm");
  }
});

test("refresh discards inactive cached reports and refetches them without repeating model work on return", async () => {
  const calls: Array<{ use_llm: boolean; refresh_facts?: boolean }> = [];
  const resource = createReviewReportResource(async params => {
    calls.push(params);
    return report({ generation_mode: params.use_llm ? "llm" : "deterministic", sample_size: params.refresh_facts ? 20 : 10 });
  });
  const stop = resource.subscribe(day, () => {});
  await resource.load(day);
  stop();
  await resource.refreshFacts();
  assert.equal(calls.length, 2);
  assert.equal(resource.getState(day).report, null);
  assert.equal(resource.getState(day).summaryStatus, "idle");
  resource.subscribe(day, () => {});
  await resource.load(day);
  await resource.load(day);
  assert.equal(calls.length, 3);
  assert.deepEqual(calls[2], { end_date: day, top_per_day: 10, follow_days: 5, use_llm: false, refresh_facts: true });
  assert.equal(resource.getState(day).report?.sample_size, 20);
  assert.equal(resource.getState(day).summaryStatus, "idle");
});

test("refresh supersedes an unfinished initial fact load and its late errors", async () => {
  for (const fails of [false, true]) {
    const oldFacts = deferred<ReviewAgentReportResponse>();
    const calls: boolean[] = [];
    const fresh = report({ sample_size: 30 });
    const resource = createReviewReportResource(async params => {
      calls.push(params.use_llm);
      return params.refresh_facts ? fresh : oldFacts.promise;
    });
    resource.subscribe(day, () => {});
    const initial = resource.load(day);
    await resource.refreshFacts();
    if (fails) oldFacts.reject(new Error("旧事实请求错误"));
    else oldFacts.resolve(report());
    await initial;
    assert.equal(resource.getState(day).report, fresh);
    assert.equal(resource.getState(day).error, null);
    assert.deepEqual(calls, [false, false]);
  }
});

test("failed fact refresh removes the stale narrative and retries fresh facts without a model call", async () => {
  let refreshAttempts = 0;
  let modelCalls = 0;
  const resource = createReviewReportResource(async params => {
    if (params.use_llm) modelCalls++;
    if (params.refresh_facts && ++refreshAttempts === 1) throw new Error("刷新暂不可用");
    return report({ generation_mode: params.use_llm ? "llm" : "deterministic" });
  });
  resource.subscribe(day, () => {});
  await resource.load(day);
  await resource.refreshFacts();
  assert.equal(resource.getState(day).report, null);
  assert.equal(resource.getState(day).error, "刷新暂不可用");
  await resource.load(day, true);
  assert.equal(refreshAttempts, 2);
  assert.equal(modelCalls, 1);
  assert.equal(resource.getState(day).report?.generation_mode, "deterministic");
  assert.equal(resource.getState(day).summaryStatus, "idle");
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
const digest = compile("../src/components/ReviewDigest.tsx");
const summary = compile("../src/components/ReviewSummary.tsx", { "./ReviewDigest": digest });
const renderSummary = (value: ReviewAgentReportResponse, summaryStatus: ReviewSummaryStatus = "ready", error: string | null = null) => (
  renderToStaticMarkup(createElement(MemoryRouter, null, createElement(summary.ReviewSummary, { report: value, summaryStatus, error, onRegenerate() {} })))
);

test("ready summary renders the new digest and never revives old findings", () => {
  const html = renderSummary(report({
    generation_mode: "llm", llm_model: "mock-model", review_digest: reviewDigest(),
  }));
  for (const value of ["LLM 解读 · mock-model", "AI解读", "重新生成", "整体表现", "优秀候选", "较差候选", "市场三板及以上", "42.0 亿元", "09:45"]) assert.ok(html.includes(value), value);
  for (const value of ["样本中晋级较集中", "成功特征", "失败特征", "高分样本有偏差", "扩大样本后验证"]) assert.ok(!html.includes(value));
  assert.ok(html.includes('<details class="review-summary-scope"><summary>样本口径与数据说明</summary>'));
  assert.ok(html.includes("不是未来概率或因果结论"));
});

test("initial generation and refreshed idle states never show a baseline or previous failure note", () => {
  const base = report({ review_digest: reviewDigest(), generation_note: "本次未启用 LLM，使用规则回退。" });
  for (const status of ["generating", "idle"] as const) {
    const html = renderSummary(base, status);
    assert.ok(!html.includes("未启用 LLM"));
    assert.ok(!html.includes("规则回退"));
    assert.ok(!html.includes("查看原因"));
    assert.ok(html.includes("优秀候选"));
    assert.ok(html.includes("本地说明"));
    assert.ok(!html.includes("AI解读"));
    assert.ok(html.includes(status === "generating" ? "正在整理本期解读" : "本地事实已就绪"));
    assert.equal(html.includes('aria-busy="true"'), status === "generating");
    assert.equal(html.includes("disabled"), status === "generating");
  }
});

test("regeneration retains evidence and labels the previous model summary without its stale failure note", () => {
  const previous = reviewDigest();
  previous.excellent.summary = "上轮有限样本观察。";
  const html = renderSummary(report({
    generation_mode: "llm", review_digest: previous, generation_note: "上轮原因不应当作本轮错误",
  }), "generating");
  assert.ok(html.includes("上次AI解读"));
  assert.ok(html.includes("上轮有限样本观察。"));
  assert.ok(!html.includes("上轮原因不应当作本轮错误"));
  assert.ok(html.includes("42.0 亿元"));
});

test("only actual fallback or errors expose a short failure status with collapsible current reasons", () => {
  const fallback = renderSummary(report({ review_digest: reviewDigest(), generation_note: "模型返回无效，已使用本地统计。" }), "fallback");
  assert.ok(fallback.includes("本次使用本地事实"));
  assert.ok(fallback.includes('<details class="review-summary-reason"><summary>查看原因</summary>'));
  assert.ok(fallback.includes("模型返回无效"));
  assert.ok(fallback.includes("重试解读"));
  assert.ok(fallback.includes('role="alert"'));
  const error = renderSummary(report({ generation_note: "过期未启用说明" }), "error", "本次请求超时");
  assert.ok(error.includes("本次请求超时"));
  assert.ok(!error.includes("过期未启用说明"));
});

test("legacy responses ask for a refreshed digest instead of showing old cards or conclusions", () => {
  for (const review_digest of [undefined, null]) {
    const old = { ...report({ review_digest }), summary_headline: "旧模型结论不再展示", feature_summary: { cards: [{ label: "旧特征卡" }] } };
    const html = renderSummary(old, "idle");
    assert.ok(html.includes("新报告待刷新"));
    for (const label of ["旧模型结论不再展示", "旧特征卡", "review-feature-card", "research-contrast-table"]) assert.ok(!html.includes(label));
    assert.ok(!html.includes("0.0%"));
  }
});

test("collapsed scope preserves checked forward eligibility counts without inventing zeros for legacy reports", () => {
  const checked = renderSummary(report({
    time_audit_status: "checked", time_cohort_counts: { close_baseline: 20, legacy_close: 30, historical_backtest: 10 },
  }), "idle");
  for (const value of ["现行盘前终选前向资格样本：0 个", "收盘基线：20 个", "旧版收盘：30 个", "历史补算：10 个"]) assert.ok(checked.includes(value));
  const legacy = renderSummary(report({ time_audit_status: "unverified", generation_mode: "legacy" }), "idle");
  assert.ok(legacy.includes("现行盘前终选前向资格样本：未核验"));
  assert.ok(!legacy.includes("现行盘前终选前向资格样本：0 个"));
  const eligible = renderSummary(report({ time_audit_status: "checked", time_cohort_counts: { premarket_final: 5 } }), "idle");
  assert.ok(eligible.includes("现行盘前终选前向资格样本：5 个"));
});

test("post bars retain actual trading-day gaps and only legacy bars fall back to array order", () => {
  const dashboard = compile("../src/components/ReviewDashboard.tsx", {
    "../api": {}, "./ReviewSummary": summary, "../hooks/useReviewReport": {}, "./Panel": {},
  }, "\nexport { ReviewPostBars };");
  const bar = { trade_date: day, open: 10, high: 10, low: 10, close: 10, change_pct: 0, return_from_base_pct: 0 };
  const html = renderToStaticMarkup(createElement(dashboard.ReviewPostBars, {
    expectedCount: 3,
    bars: [{ ...bar, trading_day_offset: 0 }, { ...bar, trade_date: "2026-10-13", trading_day_offset: 2, return_from_base_pct: 20 }],
  }));
  assert.ok(html.includes("<small>D+1</small><strong>缺缓存</strong>"));
  assert.ok(html.includes("<small>D+2</small><strong>+20.0%</strong>"));
  const legacy = renderToStaticMarkup(createElement(dashboard.ReviewPostBars, {
    expectedCount: 2, bars: [bar, { ...bar, trade_date: "2026-10-12", return_from_base_pct: 10 }],
  }));
  assert.ok(legacy.includes("<small>D+1</small><strong>+10.0%</strong>"));
  const unknown = renderToStaticMarkup(createElement(dashboard.ReviewPostBars, {
    expectedCount: 2, bars: [bar, { ...bar, trading_day_offset: null, return_from_base_pct: 10 }],
  }));
  assert.ok(unknown.includes("<small>D+1</small><strong>缺缓存</strong>"));
});

test("the actual review panel retains deterministic statistics and tracking while the model is pending or failed", () => {
  for (const [generating, summaryError] of [[true, null], [false, "模型超时"]] as const) {
    const dashboard = compile("../src/components/ReviewDashboard.tsx", {
      "../api": {}, "./ReviewSummary": summary,
      "../hooks/useReviewReport": { useReviewReport: () => ({ report: report({ sample_size: 60, review_digest: reviewDigest() }), loading: false, error: null, generating, summaryStatus: generating ? "generating" : "error", summaryError, retry() {}, regenerate() {} }) },
      "./Panel": { Panel: ({ children }: any) => createElement("section", null, children) },
    }, "\nexport { HighScoreReviewPanel };");
    const html = renderToStaticMarkup(createElement(MemoryRouter, null, createElement(dashboard.HighScoreReviewPanel, { latestTradeDate: day })));
    for (const value of ["Top10 1进2", "同期全部首板", "daily-top-review", "复盘总结", "42.0 亿元", "五日追踪样本", "复盘候选共 50 只", "完整追踪报告（60 条）"]) assert.ok(html.includes(value), value);
    assert.ok(html.includes(generating ? "生成中" : "模型超时"));
  }
});
