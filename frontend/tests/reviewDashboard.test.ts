import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

import type { DailyBoardPromotionReport, DailyBoardPromotionStat } from "../src/types.ts";

// Compile the real TSX for Node's test runner; API calls are disabled and the
// surrounding Panel is reduced to its title/children. React renders the body.
const require = createRequire(import.meta.url);
const source = readFileSync(new URL("../src/components/ReviewDashboard.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(`${source}\nexport { DailyBoardPromotionPanel };`, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
}).outputText;
const module = { exports: {} as Record<string, unknown> };
new Function("require", "module", "exports", compiled)((id: string) => {
  if (id === "../api") return {};
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
