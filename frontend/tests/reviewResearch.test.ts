import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import ts from "typescript";
import type { ReviewAgentReportResponse, ReviewFeatureResearch, ReviewFeatureStudy } from "../src/types.ts";

function compile(path: string, imports: Record<string, unknown> = {}) {
  const url = new URL(path, import.meta.url);
  const require = createRequire(url);
  const compiled = ts.transpileModule(readFileSync(url, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const module = { exports: {} as Record<string, any> };
  new Function("require", "module", "exports", compiled)((id: string) => imports[id] ?? require(id), module, module.exports);
  return module.exports;
}
const comparison = compile("../src/components/ResearchComparison.tsx");
const leaders = compile("../src/components/MarketLeaderReview.tsx", { "./ResearchComparison": comparison });
const summary = compile("../src/components/ReviewSummary.tsx", { "./ResearchComparison": comparison, "./MarketLeaderReview": leaders });

function study(): ReviewFeatureStudy {
  return {
    positive_count: 12, negative_count: 48, excluded_count: 9, basis: "仅统计截至观察日已成熟且有证据的样本", baseline_rate: 0.2, signals: [],
    features: [
      ["position_label", "首板位置"], ["float_market_cap", "流通市值"], ["first_limit_minutes", "首次封板"], ["break_count", "炸板次数"], ["turnover_rate", "换手率"],
    ].map(([key, label]) => ({
      key, label, positive_summary: "较好组画像", negative_summary: "对照组画像",
      positive_detail: "较好组原始统计说明", negative_detail: "对照组原始统计说明", positive_valid_count: 10, negative_valid_count: 40,
      buckets: [
        { label: "单例分档", positive_count: 1, negative_count: 0, sample_size: 1, positive_rate: null, baseline_rate: 0.25, delta_pp: null },
        { label: "常见分档", positive_count: 3, negative_count: 17, sample_size: 20, positive_rate: 0.15, baseline_rate: 0.25, delta_pp: -10 },
      ],
    })),
  };
}
function research(): ReviewFeatureResearch {
  return {
    candidate: study(), market: study(), market_start_date: "2026-09-10", market_end_date: "2026-10-09", notes: ["只使用首板当日已知特征"],
    market_detected_count: 2, market_matched_count: 1,
    market_leaders: [{
      symbol: "000001", name: "证据样本", first_board_date: "2026-09-20", max_board_height: 3, latest_date: "2026-09-24",
      status: "incomplete_chain", data_missing: ["event_date_missing:2026-09-25", "first_board_float_market_cap"],
      position_label: "低位启动首板", float_market_cap: null, first_limit_time: "09:45", break_count: 0, turnover_rate: 12.3,
    }],
  };
}
function render(value = research(), status = "ready", mode = "llm") {
  const report = {
    start_date: "2026-09-24", end_date: "2026-10-09", generation_mode: mode, feature_research: value,
    summary_insights: [
      { scope: "candidate", title: "候选位置差异", detail: "候选较好组和较差组的首板位置出现差异，需要同时查看有效样本数量。" },
      { scope: "market", title: "市场高标对照", detail: "具有某特征的成熟首板中仅一部分达到三板，不能从高标画像直接倒推成功率。" },
      { scope: "synthesis", title: "共同与不同之处", detail: "两种分组目标不同，本期特征差异只提供待验证的研究方向。" },
    ],
    warnings: [], time_audit_status: "checked", time_cohort_counts: { close_baseline: 60 }, generation_note: "未启用LLM旧说明",
  } as unknown as ReviewAgentReportResponse;
  return renderToStaticMarkup(createElement(MemoryRouter, null, createElement(summary.ReviewSummary, {
    report, summaryStatus: status, error: null, onRegenerate() {},
  })));
}

test("research summary shows three sourced conclusions and visibly distinct candidate/market groups", () => {
  const html = render();
  for (const text of ["本期结论", "AI归纳", "候选位置差异", "市场高标对照", "共同与不同之处", "↑ 表现较好", "↓ 表现较差", "累计上涨", "累计下跌", "达到3板及以上", "未达到3板", "未入组 9 个样本", "有效 10/12 个样本", "不是未来预测"]) assert.ok(html.includes(text), text);
  assert.ok(html.includes('class="research-positive"'));
  assert.ok(html.includes('class="research-negative"'));
  assert.ok(!html.includes("市场负收益"));
  assert.ok(!html.includes("未启用LLM旧说明"));
  assert.ok(html.includes("现行盘前终选前向资格样本：0 个"));
  assert.equal(html.split('class="research-contrast-table"').length - 1, 2);
});

test("market bucket rates use their own denominator and baseline; small samples stay unknown", () => {
  const html = render();
  const marketHtml = html.slice(html.indexOf('aria-label="全市场高标回溯"'));
  const firstFeature = marketHtml.slice(marketHtml.indexOf('class="research-rate-feature"'));
  const defaultBucket = firstFeature.slice(0, firstFeature.indexOf("<details>"));
  assert.ok(defaultBucket.includes("常见分档"));
  assert.ok(!defaultBucket.includes("单例分档"));
  assert.ok(html.includes("3/20 个首板达到3板"));
  assert.ok(html.includes("15.0%"));
  assert.ok(html.includes("同指标有效基准 25.0%"));
  assert.ok(html.includes("相差 -10.0 个百分点"));
  assert.ok(html.includes("1/1 个首板达到3板"));
  assert.ok(html.includes("样本不足，暂不比较差值"));
  assert.ok(!html.includes("100.0%"));
});

test("distribution details are collapsed while numeric coverage is shown only once", () => {
  const value = study();
  value.features[0].positive_detail = "有效 10/12 个样本；组内位置占比：低位 6/10；高位 4/10";
  value.features = [value.features[0]];
  const distribution = renderToStaticMarkup(createElement(comparison.ResearchComparison, { study: value }));
  assert.ok(distribution.includes('<small>有效 10/12 个样本</small><details class="research-distribution"><summary>完整分布</summary><span>有效 10/12 个样本；组内位置占比'));
  assert.ok(!distribution.includes('class="research-distribution" open'));
  const numeric = study();
  numeric.features = [{ ...numeric.features[1], positive_detail: "有效 10/12 个样本；中位数；中间50%：20–40亿元", negative_detail: "有效 40/48 个样本；中位数；中间50%：30–50亿元" }];
  const detail = renderToStaticMarkup(createElement(comparison.ResearchComparison, { study: numeric }));
  assert.equal(detail.split("有效 10/12 个样本").length - 1, 1);
  assert.ok(detail.includes("中位数；中间50%：20–40亿元"));
  assert.ok(!detail.includes('<small>有效'));
});

test("candidate outcome buckets are available in a collapsed section with their own positive-return labels", () => {
  const value = research();
  value.candidate.positive_count = 23;
  value.candidate.negative_count = 26;
  value.candidate.features[0].buckets[1] = { label: "低位首板", positive_count: 13, negative_count: 6, sample_size: 19, positive_rate: 13 / 19, baseline_rate: 0.4, delta_pp: 28.4 };
  const html = render(value);
  const candidate = html.slice(html.indexOf('aria-label="候选表现对照"'), html.indexOf('aria-label="全市场高标回溯"'));
  assert.ok(candidate.includes('<details class="research-rates research-candidate-rates"><summary>查看候选各特征的正收益比例</summary>'));
  assert.ok(candidate.includes("13/19 个候选累计正收益"));
  assert.ok(candidate.includes("68.4%"));
  assert.ok(candidate.includes("同指标有效基准 40.0%"));
  assert.ok(candidate.includes("相差 +28.4 个百分点"));
  assert.ok(!candidate.includes("达到3板"));
  assert.ok(candidate.includes("样本不足，暂不比较差值"));
});

test("high-board evidence uses verified heights, Chinese missing notes and links to stock details", () => {
  const html = render();
  for (const text of ["检测 2 个，已定位首板 1 个", "该轮已核验最高 3 板", "后续行情缺失", "当日行情缺失：2026-09-25", "首板当日流通市值缺失", 'href="/stocks/000001"', "0 次", "12.3%"]) assert.ok(html.includes(text), text);
  assert.ok(!html.includes("event_date_missing"));
  const unresolved = research();
  unresolved.market_leaders[0] = { ...unresolved.market_leaders[0], first_board_date: null, status: "unresolved" };
  assert.ok(render(unresolved).includes("来源报 3 板 / 待核验"));
  assert.ok(render(unresolved).includes("首板 待定位"));
});

test("generating deterministic research retains conclusions and does not mislabel them as AI or failed", () => {
  const html = render(research(), "generating", "deterministic");
  assert.ok(html.includes("本地事实归纳"));
  assert.ok(html.includes("候选位置差异"));
  assert.ok(html.includes("正在整理本期解读"));
  assert.ok(!html.includes("AI归纳"));
  assert.ok(!html.includes("未启用LLM旧说明"));
});

test("cross-cohort direction checks remain available as independent evidence", () => {
  const value = research();
  value.cross_checks = ["流通市值中位数：候选较好组更大，市场3板组更小；方向相反。"];
  const html = render(value);
  assert.ok(html.includes("查看跨样本方向核对"));
  assert.ok(html.includes(value.cross_checks[0]));
  assert.ok(!html.includes('class="review-direction-checks" open'));
});

test("absent market observations show unknown rates and explicit empty evidence", () => {
  const empty = research();
  empty.market = { positive_count: 0, negative_count: 0, excluded_count: 9, basis: "观察未成熟", baseline_rate: null, features: [], signals: [] };
  empty.market_leaders = [];
  const html = render(empty, "idle", "deterministic");
  assert.ok(html.includes("样本3板比例为 样本不足"));
  assert.ok(html.includes("暂缺可比较的首板特征"));
  assert.ok(html.includes("当前窗口暂无可展示的高标个案"));
});
