import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import ts from "typescript";
import { reviewDigest } from "./reviewDigestFixture.ts";
import type { DigestDistribution } from "../src/reviewDigestTypes.ts";

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
const nextOpeningCard = (html: string) => {
  const start = html.indexOf('class="digest-feature-strip digest-next-open"');
  assert.ok(start >= 0, "next-day opening belongs to the compact feature row");
  return html.slice(start, html.indexOf("<details", start));
};
const openingDistribution = (patch: Partial<DigestDistribution> = {}): DigestDistribution => ({
  key: "next_open_pct", label: "次日开盘", valid_count: 8, total_count: 10, baseline_valid_count: 40, baseline_total_count: 50, note: "按相邻真实交易日开盘计算。",
  buckets: ["低开", "平开", "高开0–3%", "高开3–7%", "高开≥7%"].map((label, index) => ({
    label, count: [2, 1, 3, 1, 1][index], share: [0.25, 0.125, 0.375, 0.125, 0.125][index],
    baseline_count: [10, 1, 20, 4, 5][index], baseline_share: [0.25, 0.025, 0.5, 0.1, 0.125][index],
  })), ...patch,
});

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
  assert.ok(preview.includes('aria-label="首板特征与次日开盘"'));
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
  assert.ok(!html.includes('class="digest-feature-strips"'));
  assert.ok(!html.includes('aria-label="次日开盘"'));
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
  const distribution = html.slice(html.indexOf('<details class="digest-evidence">'), html.indexOf('<details class="digest-evidence digest-stocks">'));
  for (const text of ["全集", "全部候选", "基准", "未提供"]) assert.ok(!distribution.includes(text), text);
});

test("next-day card shows its leading bucket and independent baseline while all five buckets stay in closed evidence", () => {
  const digest = reviewDigest();
  digest.excellent.distributions.push(openingDistribution());
  digest.excellent.observations.push({ id: "next", dimension: "next_open_pct", text: "次日有 2/8 的有效样本低开。", support_count: 2, sample_size: 8 });
  digest.excellent.selected_observation_ids = ["next"];
  const html = excellentPart(render(digest));
  const card = nextOpeningCard(html);
  for (const text of ["次日开盘", "占比最多", "高开0–3%", "3/8 · 37.5%", "该档全部候选 20/40 · 50.0%", "有效 8/10 · 相对首板收盘"]) assert.ok(card.includes(text), text);
  for (const text of ["低开", "平开", "高开3–7%", "高开≥7%", "3/10", "20/50", "<table", "<details"]) assert.ok(!card.includes(text), text);
  assert.ok(html.includes('class="digest-stage">次日</span><span>次日有 2/8'));
  const evidenceStart = html.indexOf('<details class="digest-evidence">');
  assert.ok(!html.slice(0, evidenceStart).includes("<table"));
  const evidence = html.slice(evidenceStart);
  for (const text of ["低开", "平开", "高开0–3%", "高开3–7%", "高开≥7%", "2/8 · 25.0%", "20/40 · 50.0%", "全部候选有效 40/50"]) assert.ok(evidence.includes(text), text);
  assert.ok(!html.includes('class="digest-evidence" open'));
});

test("a tied leading opening is marked as one of all tied buckets and compares that exact bucket", () => {
  const digest = reviewDigest();
  const distribution = openingDistribution();
  distribution.buckets = distribution.buckets.map((bucket, index) => ({ ...bucket, count: [3, 3, 1, 1, 0][index], share: [0.375, 0.375, 0.125, 0.125, 0][index] }));
  digest.excellent.distributions = [distribution];
  const card = nextOpeningCard(excellentPart(render(digest)));
  for (const text of ["并列最多之一（2档）", "低开", "3/8 · 37.5%", "该档全部候选 10/40 · 25.0%"]) assert.ok(card.includes(text), text);
  for (const text of ["平开", "20/40", "6/8", "75.0%", "集中", "占比最多"]) assert.ok(!card.includes(text), text);
});

test("unknown opening shares are not recomputed and unavailable baselines never become zero percent", () => {
  const digest = reviewDigest();
  const distribution = openingDistribution();
  distribution.buckets = distribution.buckets.map(bucket => ({ ...bucket, share: null, baseline_share: null }));
  digest.excellent.distributions = [distribution];
  let card = nextOpeningCard(excellentPart(render(digest)));
  assert.ok(card.includes("3/8"));
  assert.ok(card.includes("该档全部候选 20/40"));
  for (const text of ["37.5%", "50.0%", 'style="width']) assert.ok(!card.includes(text), text);
  for (const valid of [0, null]) {
    distribution.baseline_valid_count = valid;
    distribution.buckets = distribution.buckets.map(bucket => ({ ...bucket, baseline_count: valid }));
    card = nextOpeningCard(excellentPart(render(digest)));
    for (const text of ["0/0", "0.0%", "/null", "undefined"]) assert.ok(!card.includes(text), text);
    if (card.includes("该档全部候选")) assert.ok(card.includes("待核验"));
  }
});

test("leader opening cards never present candidate baselines even if the response carries them", () => {
  const digest = reviewDigest();
  digest.leaders.distributions.push(openingDistribution());
  const html = render(digest);
  const card = nextOpeningCard(html.slice(html.indexOf('class="digest-portrait digest-leaders"')));
  for (const text of ["高开0–3%", "3/8 · 37.5%", "有效 8/10"]) assert.ok(card.includes(text), text);
  assert.ok(!card.includes("全部候选"));
  assert.ok(!card.includes("20/40"));
});

test("next-day buckets with no effective samples show unknown instead of zero denominators", () => {
  const digest = reviewDigest();
  const opening = { key: "next_open_pct", label: "次日开盘", valid_count: 0, total_count: 10, baseline_valid_count: 0, baseline_total_count: 50, note: "待补齐",
    buckets: [{ label: "低开", count: 0, share: null, baseline_count: 0, baseline_share: null }] };
  digest.excellent.distributions = [opening];
  let html = excellentPart(render(digest));
  const card = nextOpeningCard(html);
  assert.ok(card.includes("暂无有效数据"));
  assert.ok(!card.includes("低开"));
  assert.ok(!card.includes("占比最多"));
  assert.ok(html.includes("有效 0/10"));
  assert.ok(html.includes("全部候选有效 0/50"));
  assert.ok(html.includes("<td>待核验</td>"));
  assert.ok(!html.includes("0/0"));
  digest.excellent.sample_size = 0;
  opening.total_count = 0;
  opening.baseline_total_count = 0;
  html = excellentPart(render(digest));
  assert.ok(!html.includes('class="digest-feature-strips"'));
  assert.ok(html.includes("本组暂无样本"));
  assert.ok(html.includes("全部候选暂无样本"));
  assert.ok(!html.includes("0/0"));
});

test("legacy optional fields stay unknown and a compact opening placeholder remains in all nonempty groups", () => {
  const html = render();
  assert.equal(html.split('class="digest-feature-strip digest-next-open"').length - 1, 3);
  for (const text of ["有效样本待核验", "日期待核验", "涨幅缺失"]) assert.ok(html.includes(text), text);
  assert.ok(!html.includes("龙虎榜"));
  assert.ok(!html.includes("undefined"));
  assert.ok(!html.includes("NaN"));
});

test("stock next-day evidence preserves dates and zero open while avoiding duplicate leader opening", () => {
  const digest = reviewDigest();
  Object.assign(digest.excellent.stocks[0], { next_trade_date: "2026-10-08", next_open_pct: 0 });
  Object.assign(digest.leaders.stocks[0], { next_trade_date: "2026-10-08", next_open_pct: 3.6 });
  const html = render(digest);
  const candidate = excellentPart(html);
  for (const text of ["次日开盘</b> 2026-10-08 · 0.0%", "相对首板收盘"]) assert.ok(candidate.includes(text), text);
  const leaders = html.slice(html.indexOf('class="digest-portrait digest-leaders"'));
  assert.equal(leaders.split("+3.6%").length - 1, 1);
  assert.ok(leaders.includes("次日开盘</b> 2026-10-08 · +3.6%"));
  assert.ok(leaders.includes("二板 2026-10-08"));
  assert.ok(!leaders.includes("<dt>开盘涨幅</dt>"));
});

test("five peer tiles include industry with theme and next-day outcome without implying concentration", () => {
  const digest = reviewDigest();
  const base = digest.excellent.distributions[0];
  const bucket = (label: string, count: number) => ({ label, count, share: count / 10, baseline_count: null, baseline_share: null });
  digest.excellent.distributions.push(
    { ...base, key: "concepts", label: "首板题材", valid_count: 10, buckets: ["题材甲", "题材乙", "题材丙", "题材丁", "题材戊"].map(label => bucket(label, 2)) },
    { ...base, key: "industry", label: "首板行业", valid_count: 10, buckets: [bucket("行业甲", 4), bucket("行业乙", 3), bucket("行业丙", 3)] },
    { ...base, key: "float_market_cap", label: "首板流通市值" },
    { ...base, key: "first_limit_time", label: "首板首次封板时间" },
    openingDistribution(),
  );
  digest.excellent.notes = ["各特征分别统计，不表示共同出现。", "题材可有多个标签。"];
  const html = excellentPart(render(digest));
  const preview = html.slice(0, html.indexOf('<details class="digest-more-notes">'));
  assert.equal(preview.split('class="digest-feature-strip"').length - 1, 4);
  assert.equal(preview.split('class="digest-feature-strip digest-next-open"').length - 1, 1);
  assert.ok(preview.indexOf("首板首次封板时间") < preview.indexOf('aria-label="次日开盘"'));
  for (const text of ["首板题材", "题材甲", "2/10 · 20.0%", "并列最多", "首板行业", "行业甲", "4/10 · 40.0%", "占比最多", "次日开盘"]) assert.ok(preview.includes(text), text);
  assert.ok(!preview.includes("集中"));
  assert.ok(!preview.includes("各特征分别统计"));
  assert.ok(html.includes('<details class="digest-more-notes"><summary>数据说明 · 2 条</summary>'));
  assert.ok(html.includes("各特征分别统计，不表示共同出现。"));
});
