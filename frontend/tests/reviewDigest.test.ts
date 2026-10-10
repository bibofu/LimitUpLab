import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import ts from "typescript";
import { reviewDigest } from "./reviewDigestFixture.ts";

const url = new URL("../src/components/ReviewDigest.tsx", import.meta.url);
const require = createRequire(url);
const compiled = ts.transpileModule(readFileSync(url, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
}).outputText;
const module = { exports: {} as Record<string, any> };
new Function("require", "module", "exports", compiled)(require, module, module.exports);
const render = (digest = reviewDigest()) => renderToStaticMarkup(createElement(MemoryRouter, null,
  createElement(module.exports.ReviewDigestContent, { digest, summarySource: "AI解读" })));
const excellentPart = (html: string) => html.slice(html.indexOf('class="digest-portrait digest-excellent"'), html.indexOf('class="digest-portrait digest-weak"'));

test("digest preserves the authoritative headline, four-part order and separate five-day windows", () => {
  const digest = reviewDigest();
  const html = render(digest);
  assert.ok(html.includes(digest.overview.headline));
  const positions = ["整体表现", "优秀候选", "较差候选", "市场三板及以上"].map(label => html.indexOf(`aria-label="${label}"`));
  assert.ok(positions.every(position => position >= 0));
  assert.deepEqual([...positions].sort((left, right) => left - right), positions);
  for (const text of ["候选 <b>50", "优秀 <b>10", "较差 <b>10", "普通 <b>26", "缺失 / 未观察 <b>4", "上涨 ≥9.8%", "下跌超过 5%", "2026-09-25 — 2026-10-08", "2026-09-28 — 2026-10-09", "含截至日近 5 个交易日"]) assert.ok(html.includes(text), text);
  assert.ok(html.includes('digest-portrait digest-excellent'));
  assert.ok(html.includes('digest-portrait digest-weak'));
});

test("selected deterministic observations retain backend order and omit unselected or unknown IDs", () => {
  const digest = reviewDigest();
  digest.excellent.selected_observation_ids = ["excellent:seal", "absent", "excellent:position", "excellent:seal"];
  const html = excellentPart(render(digest));
  assert.ok(html.indexOf("十点前封板占本组") < html.indexOf("低位启动占本组"));
  assert.equal(html.split("十点前封板占本组").length - 1, 1);
  assert.ok(!html.includes("未被选择的说明"));
  assert.ok(!html.includes("undefined"));
  assert.ok(!html.includes("absent"));
});

test("full distributions and baseline denominators are available only in a closed evidence block", () => {
  const html = excellentPart(render());
  const evidenceStart = html.indexOf('<details class="digest-evidence"><summary>完整特征分布 · 与全部候选对照</summary>');
  assert.ok(evidenceStart > 0);
  const preview = html.slice(0, evidenceStart);
  assert.ok(preview.includes('aria-label="主要首板特征分布"'));
  assert.ok(preview.includes("5/8 · 62.5%"));
  assert.ok(!preview.includes("<table>"));
  assert.ok(!preview.includes("其他位置"));
  const full = html.slice(evidenceStart);
  for (const text of ["本组有效 8/10", "全部候选有效 25/50", "其他位置", "3/8 · 37.5%", "13/25 · 52.0%", "缺失位置不补成默认类别"]) assert.ok(full.includes(text), text);
  assert.ok(!html.includes('class="digest-evidence" open'));
});

test("small and empty groups never invent percentages or a completed AI explanation", () => {
  const digest = reviewDigest();
  const group = digest.excellent;
  group.sample_size = 1;
  group.summary = "只有一条记录，暂不概括特征模式。";
  group.observations = [];
  group.selected_observation_ids = [];
  group.distributions[0] = { ...group.distributions[0], valid_count: 1, total_count: 1, baseline_valid_count: null, baseline_total_count: null,
    buckets: [{ label: "孤例", count: 1, share: null, baseline_count: null, baseline_share: null }] };
  let html = excellentPart(render(digest));
  for (const text of ["1 个样本", "1/1", "比例不足", "特征证据尚不足"]) assert.ok(html.includes(text), text);
  for (const text of ["AI解读", "100.0%", "0.0%", "NaN", 'style="width']) assert.ok(!html.includes(text), text);
  assert.ok(html.includes("本地说明"));
  group.summary = "";
  assert.ok(!excellentPart(render(digest)).includes('class="digest-interpretation"'));
  group.sample_size = 0;
  group.summary = "无样本时不应展示的旧模型解释";
  html = excellentPart(render(digest));
  assert.ok(html.includes("没有符合条件的可观察样本"));
  assert.ok(!html.includes("无样本时不应展示的旧模型解释"));
  assert.ok(!html.includes("AI解读"));
});

test("candidate stock evidence exposes dates, observed days, returns and original first-board fields", () => {
  const html = excellentPart(render());
  for (const text of ['<details class="digest-evidence digest-stocks">', 'href="/stocks/600001"', "首板 2026-09-30", "已观察 3 个交易日", "累计 +12.3%", "测试行业", "测试题材", "首板位置", "低位启动", "42.0 亿元", "09:45", "0 次 / 8.2%", "10.00 / 11.23"]) assert.ok(html.includes(text), text);
  assert.ok(!html.includes('class="digest-evidence digest-stocks" open'));
});

test("leader evidence separates first and second boards and preserves actual zero values and missing data", () => {
  const digest = reviewDigest();
  digest.leaders.stocks[0].second_limit_time = null;
  digest.leaders.stocks[0].observed_days = null;
  digest.leaders.stocks[0].data_missing = ["二板首次封板时间缺失"];
  const all = render(digest);
  const html = all.slice(all.indexOf('class="digest-portrait digest-leaders"'));
  for (const text of ['class="digest-stage">首板', 'class="digest-stage">二板', "二板换手板 7/10", "该轮最高 4 板", "二板 2026-10-08", "+3.6%", "炸板次数", "0 次", "14.2%", "换手板", "二板首次封板时间缺失"]) assert.ok(html.includes(text), text);
  assert.ok(!html.includes("undefined"));
  assert.ok(!html.includes("已观察"));
  assert.ok(html.includes('<summary>完整特征分布</summary>'));
  assert.ok(html.includes('<thead><tr><th>特征</th><th>本组</th></tr></thead>'));
  for (const text of ["全集", "全部候选", "基准", "未提供"]) assert.ok(!html.includes(text), text);
});

test("four compact tiles include industry with theme and label tied or low top shares without implying concentration", () => {
  const digest = reviewDigest();
  const base = digest.excellent.distributions[0];
  const bucket = (label: string, count: number) => ({ label, count, share: count / 10, baseline_count: null, baseline_share: null });
  digest.excellent.distributions.push(
    { ...base, key: "concepts", label: "首板题材", valid_count: 10, buckets: ["题材甲", "题材乙", "题材丙", "题材丁", "题材戊"].map(label => bucket(label, 2)) },
    { ...base, key: "industry", label: "首板行业", valid_count: 10, buckets: [bucket("行业甲", 4), bucket("行业乙", 3), bucket("行业丙", 3)] },
    { ...base, key: "float_market_cap", label: "首板流通市值" },
    { ...base, key: "first_limit_time", label: "首板首次封板时间" },
  );
  digest.excellent.notes = ["各特征分别统计，不表示共同出现。", "题材可有多个标签。"];
  const html = excellentPart(render(digest));
  const preview = html.slice(0, html.indexOf('<details class="digest-more-notes">'));
  assert.equal(preview.split('class="digest-feature-strip"').length - 1, 4);
  for (const text of ["首板题材", "题材甲", "2/10 · 20.0%", "并列最多", "首板行业", "行业甲", "4/10 · 40.0%", "占比最多"]) assert.ok(preview.includes(text), text);
  assert.ok(!preview.includes("集中"));
  assert.ok(!preview.includes("各特征分别统计"));
  assert.ok(html.includes('<details class="digest-more-notes"><summary>数据说明 · 2 条</summary>'));
  assert.ok(html.includes("各特征分别统计，不表示共同出现。"));
});
