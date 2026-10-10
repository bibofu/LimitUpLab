import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import ts from "typescript";

import type { DailyBoardPromotionReport, DailyBoardPromotionStat, ReviewAgentPick, ReviewPromotionComparison } from "../src/types.ts";

// Compile the real TSX for Node's test runner; API calls are disabled and the
// surrounding Panel is reduced to its title/children. React renders the body.
const require = createRequire(import.meta.url);
const source = readFileSync(new URL("../src/components/ReviewDashboard.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(`${source}\nexport { DailyBoardPromotionPanel, DailyTopReview, sortReviewPicksForSummary };`, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
}).outputText;
const module = { exports: {} as Record<string, unknown> };
new Function("require", "module", "exports", compiled)((id: string) => {
  if (id === "../api") return {};
  if (id === "../hooks/useReviewReport" || id === "./ReviewSummary") return {};
  if (id === "../dashboardFormatters") return require("../src/dashboardFormatters.ts");
  if (id === "../relayRanking") return require("../src/relayRanking.ts");
  if (id === "./Panel") return {
    Panel: ({ title, children }: { title: string; children: unknown }) => createElement(
      "section", null, createElement("h2", null, title), children,
    ),
  };
  return require(id);
}, module, module.exports);
const DailyBoardPromotionPanel = module.exports.DailyBoardPromotionPanel as (
  props: { report: DailyBoardPromotionReport },
) => ReturnType<typeof createElement>;
interface DailyTopProps {
  activeSelection: string;
  failedPicks: ReviewAgentPick[];
  groupedPicks: Record<string, ReviewAgentPick[]>;
  onSelect: (selection: string) => void;
  picks: ReviewAgentPick[];
  promotionComparisons: Record<string, ReviewPromotionComparison>;
  successfulPicks: ReviewAgentPick[];
  trackDates: string[];
}
const DailyTopReview = module.exports.DailyTopReview as (props: DailyTopProps) => ReturnType<typeof createElement>;
const sortReviewPicksForSummary = module.exports.sortReviewPicksForSummary as (
  picks: ReviewAgentPick[], direction: "asc" | "desc",
) => ReviewAgentPick[];

function pick(index: number, returnPct: number): ReviewAgentPick {
  const tradeDate = index % 2 ? "2026-09-29" : "2026-09-30";
  const nextDate = index % 2 ? "2026-09-30" : "2026-10-08";
  return {
    trade_date: tradeDate, symbol: `600${String(index).padStart(3, "0")}`, name: `追踪样本${index}`,
    concept: "测试题材", position_label: "低位启动", score: 80 + index / 10, rating: "A", confidence: 0.7,
    prediction_source: "historical_backtest", data_as_of: tradeDate, evaluation_label: returnPct > 0 ? "success" : "miss",
    outcome_ready: true, promoted_to_second_board: returnPct > 0, next_high_pct: null, next_close_pct: null,
    next_open_to_high_pct: null, next_open_to_low_pct: null, next_open_to_close_pct: null,
    three_day_high_pct: null, three_day_close_pct: null, three_day_open_to_close_pct: null,
    max_drawdown_from_next_open_3d: null, reasons: [], risks: [], expected_post_bar_count: 2, post_bar_cache_complete: true,
    post_bars: [
      { trading_day_offset: 0, trade_date: tradeDate, open: 10, high: 10, low: 10, close: 10, change_pct: 0, return_from_base_pct: 0 },
      { trading_day_offset: 1, trade_date: nextDate, open: 10, high: 12, low: 8, close: 10 * (1 + returnPct / 100), change_pct: returnPct, return_from_base_pct: returnPct },
    ],
  };
}

function renderDailyTop(props: DailyTopProps) {
  // Old report text must remain ignored even when supplied by a legacy caller.
  const legacyProps = { ...props, successfulPatterns: ["旧正收益画像"], failedPatterns: ["旧负收益画像"], adjustmentSuggestions: ["旧评分假设"] };
  return renderToStaticMarkup(createElement(MemoryRouter, null, createElement(DailyTopReview, legacyProps)));
}

function stat(tradeDate: string): DailyBoardPromotionStat {
  return {
    trade_date: tradeDate, previous_trade_date: "2026-09-28", sample_size: 10,
    promoted_count: 2, probability: 0.2, first_board_sample_size: 8,
    first_board_promoted_count: 1, first_board_probability: 0.125,
    continued_board_sample_size: 2, continued_board_promoted_count: 1,
    continued_board_probability: 0.5, buckets: [], promoted_stocks: [],
  };
}

function render(report: DailyBoardPromotionReport) {
  return renderToStaticMarkup(createElement(DailyBoardPromotionPanel, { report }));
}

test("empty promotion statistics still expose the latest event date and missing-data warning", () => {
  const html = render({
    items: [], latest_event_date: "2026-10-08",
    warnings: ["晋级统计缺少前一交易日收盘数据。"],
  });
  assert.ok(html.includes("最新事件数据日：2026-10-08"));
  assert.ok(html.includes("暂无可计算的晋级统计"));
  assert.ok(html.includes("晋级统计缺少前一交易日收盘数据。"));
  assert.ok(html.includes("暂时无法计算晋级率"));
  assert.ok(!html.includes("2026-10-08 晋级观察"));
});

test("older computed statistics keep their own date while the newer event date is explicit", () => {
  const html = render({
    items: [stat("2026-09-29"), stat("2026-09-30")], latest_event_date: "2026-10-08",
    warnings: ["10月8日晋级统计尚不可计算。"],
  });
  assert.ok(html.includes("最新事件数据日：2026-10-08"));
  assert.ok(html.includes("最新可计算晋级统计日：2026-09-30"));
  assert.ok(html.includes("2026-09-30 晋级观察"));
  assert.ok(!html.includes("2026-10-08 晋级观察"));
  assert.ok(html.includes("10月8日晋级统计尚不可计算。"));
});

test("complete current statistics do not add a generic missing-data notice", () => {
  const html = render({ items: [stat("2026-10-08")], latest_event_date: "2026-10-08", warnings: [] });
  assert.ok(html.includes("2026-10-08 晋级观察"));
  assert.ok(!html.includes("最新可计算晋级统计日"));
  assert.ok(!html.includes('role="status"'));
});

for (const [selection, direction, label, sign] of [
  ["review-success", "desc", "表现较好", 1], ["review-miss", "asc", "表现较差", -1],
] as const) {
  test(`${label} keeps ordered Top10 stocks and dated D+ tracking without old patterns or scoring hypotheses`, () => {
    const all = Array.from({ length: 12 }, (_, index) => pick(index + 1, sign * (index + 1)));
    const sorted = sortReviewPicksForSummary(all, direction);
    const html = renderDailyTop({
      activeSelection: selection, picks: sorted, successfulPicks: sign > 0 ? sorted : [], failedPicks: sign < 0 ? sorted : [],
      groupedPicks: {}, trackDates: [], promotionComparisons: {}, onSelect: () => {},
    });
    const body = html.slice(html.indexOf('<section class="daily-top-body">'));
    for (const text of [label, "共 12 只", direction === "desc" ? "从高到低展示前 10 只" : "从低到高展示前 10 只", "首板至今", "走势追踪", "2026-09-30 / 600012", "2026-09-29 / 600011", "D+1", "D+5", "待收盘", sign > 0 ? "+12.0%" : "-12.0%", sign > 0 ? "累计上涨" : "累计下跌"]) assert.ok(body.includes(text), text);
    assert.equal(body.split('class="review-pick-row ').length - 1, 10);
    assert.ok(body.indexOf("追踪样本12<") < body.indexOf("追踪样本11<"));
    assert.ok(body.includes("追踪样本3<"));
    for (const text of ["追踪样本2<", "追踪样本1<", "review-pattern-summary", "历史画像", "旧正收益画像", "旧负收益画像", "旧评分假设", "待验证的评分假设", "review-promotion-comparison"]) assert.ok(!body.includes(text), text);
    assert.ok(body.includes('href="/stocks/600012"'));
    assert.ok(body.includes('title="2026-10-08 收盘'));
    assert.equal(all[0].name, "追踪样本1", "sorting does not mutate the report");
  });
}

test("date-specific Top10 still shows promotion denominators and dated tracking after performance summaries are removed", () => {
  const picks = [pick(2, 10)];
  const tradeDate = "2026-09-30";
  const promotion: ReviewPromotionComparison = {
    trade_date: tradeDate, next_trade_date: "2026-10-08", outcome_ready: true,
    top_pick_sample_size: 10, top_pick_promoted_count: 3, top_pick_promotion_rate: 0.3,
    market_first_board_sample_size: 20, market_promoted_count: 4, market_promotion_rate: 0.2, promotion_rate_delta: 0.1,
  };
  const html = renderDailyTop({ activeSelection: tradeDate, picks, groupedPicks: { [tradeDate]: picks }, trackDates: [tradeDate],
    successfulPicks: picks, failedPicks: [], promotionComparisons: { [tradeDate]: promotion }, onSelect: () => {} });
  for (const text of [tradeDate, "Top10 追踪", "1进2 3/10 · 全部 4/20", "预测 Top10 1进2", "当天全部首板 1进2", "30.0%", "20.0%", "+10.0 个百分点", "2026-10-08", "测试题材", "追踪样本2", "已晋级二板", "D+1", "D+5"]) assert.ok(html.includes(text), text);
  for (const text of ["首板至今", "review-pattern-summary", "旧正收益画像", "旧负收益画像", "旧评分假设"]) assert.ok(!html.includes(text), text);
});
