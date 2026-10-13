import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import * as jsxRuntime from "react/jsx-runtime";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import ts from "typescript";

function compile(path: string, imports: Record<string, unknown>) {
  const url = new URL(path, import.meta.url);
  const require = createRequire(url);
  const compiled = ts.transpileModule(readFileSync(url, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const module = { exports: {} as Record<string, any> };
  new Function("require", "module", "exports", compiled)((id: string) => imports[id]
    ?? require(id.startsWith(".") ? `${id}.ts` : id), module, module.exports);
  return module.exports;
}

const news = {
  fetched_at: "2026-10-12T18:30:00+08:00", sources: ["测试资讯来源"],
  page: 1, page_size: 10, total: 1, total_pages: 1,
  items: [{ title: "已保存的市场快讯", summary: "公开资讯摘要", published_at: "2026-10-12T18:00:00+08:00",
    source: "测试资讯来源", url: "https://example.com/news", category: "A股" }],
};
const intelligence = {
  stage: "final", target_trade_date: "2026-10-13", relay_base_date: "2026-10-12",
  refreshed_at: "2026-10-13T09:20:00+08:00", finalized_at: "2026-10-13T09:25:00+08:00",
  warnings: [], relay_display_limit: 10,
  items: [{ strategy: "relay", symbol: "600001", name: "研究候选", base_trade_date: "2026-10-12",
    rank: 1, base_rank: 2, draft_score: 72, base_score: 70, dynamic_adjustment: 2,
    update_reasons: [], close_information_reasons: [], latest_news: [], position_label: null }],
};

function render(kind: "news" | "premarket", state: Record<string, unknown>) {
  let retry: (() => void) | undefined;
  const observe = (factory: (...args: any[]) => unknown) => (...args: any[]) => {
    if (args[0] === "button" && args[1]?.children === (kind === "news" ? "重新加载新闻" : "重新加载候选")) {
      retry = args[1].onClick;
    }
    return factory(...args);
  };
  const runtime = { ...jsxRuntime, jsx: observe(jsxRuntime.jsx), jsxs: observe(jsxRuntime.jsxs) };
  const value = { loading: false, error: null, fromCache: false, savedAt: 0, reload() {}, ...state };
  const component = kind === "news"
    ? compile("../src/components/RecommendationNewsBoard.tsx", {
      "../hooks/useFinanceNews": { useFinanceNews: () => value }, "react/jsx-runtime": runtime,
    }).RecommendationNewsBoard
    : compile("../src/pages/PremarketPage.tsx", {
      "../hooks/useRecommendationIntelligence": { useRecommendationIntelligence: () => value },
      "../components/ConsolidationPanel": { ConsolidationPanel: () => null },
      "../components/RecommendationNewsBoard": { RecommendationNewsBoard: () => null },
      "../components/Panel": { Panel: ({ title, children }: any) => createElement("section", null, createElement("h2", null, title), children) },
      "react/jsx-runtime": runtime,
    }).PremarketPage;
  return {
    html: renderToStaticMarkup(createElement(MemoryRouter, { initialEntries: ["/recommendations"] }, createElement(component))),
    retry,
  };
}

test("news metadata and content are identical across cached background reads", () => {
  const ready = render("news", { news }).html;
  for (const state of [{ loading: true }, { loading: true, fromCache: true }, { fromCache: true }]) {
    assert.equal(render("news", { news, ...state }).html, ready);
  }
  assert.ok(ready.includes("来源抓取：2026-10-12 18:30:00（上海时间）"));
  assert.ok(ready.includes("近 24 小时（以来源抓取为截止）"));
  assert.ok(ready.includes("已保存的市场快讯"));
  assert.ok(!ready.includes("更新中"));
  assert.ok(!ready.includes("自动更新"));
  assert.ok(!ready.includes("state-spinner"));
});

test("unknown news is a quiet placeholder and a verified empty result remains visible during refresh", () => {
  const unknown = render("news", { news: null, loading: true }).html;
  assert.ok(unknown.includes("来源抓取：—"));
  assert.ok(!unknown.includes("没有市场快讯"));
  assert.ok(!unknown.includes("共 0 条"));
  assert.ok(!unknown.includes("正在"));
  assert.ok(!unknown.includes("state-spinner"));
  const empty = { ...news, total: 0, items: [] };
  const ready = render("news", { news: empty }).html;
  assert.equal(render("news", { news: empty, loading: true, fromCache: true }).html, ready);
  assert.ok(ready.includes("该次抓取的近 24 小时范围内没有市场快讯"));
});

test("news errors retain dated content and expose a working retry", () => {
  let retries = 0;
  const result = render("news", { news, error: "request failed", reload() { retries++; } });
  assert.ok(result.html.includes("新闻更新失败"));
  assert.ok(result.html.includes("已保存的市场快讯"));
  assert.ok(result.html.includes("2026-10-12 18:30:00"));
  assert.ok(result.retry);
  result.retry();
  assert.equal(retries, 1);
  assert.ok(render("news", { news: null, error: "request failed" }).html.includes("新闻暂不可用"));
});

test("candidate cards and snapshot dates are identical across cached background reads", () => {
  const ready = render("premarket", { intelligence }).html;
  for (const state of [{ loading: true }, { loading: true, fromCache: true }, { fromCache: true }]) {
    assert.equal(render("premarket", { intelligence, ...state }).html, ready);
  }
  assert.ok(ready.includes('class="rating-top-card"'));
  assert.ok(ready.includes("2026-10-13 目标日"));
  assert.ok(ready.includes("候选基准日：2026-10-12"));
  assert.ok(ready.includes("固化时间：2026-10-13 09:25:00（上海时间）"));
  assert.ok(!ready.includes("正在"));
  assert.ok(!ready.includes("state-spinner"));
});

test("unknown candidates never masquerade as an empty published snapshot", () => {
  const unknown = render("premarket", { intelligence: null, loading: true }).html;
  assert.ok(unknown.includes('class="discovery-state" aria-hidden="true">—'));
  assert.ok(!unknown.includes("没有可展示"));
  assert.ok(!unknown.includes("正在"));
  assert.ok(!unknown.includes("state-spinner"));
  const empty = { ...intelligence, items: [] };
  const ready = render("premarket", { intelligence: empty }).html;
  assert.equal(render("premarket", { intelligence: empty, loading: true, fromCache: true }).html, ready);
  assert.ok(ready.includes("该快照没有可展示的盘前候选"));
  assert.ok(ready.includes("2026-10-13 目标日"));
});

test("candidate failures provide one retry notice without withdrawing dated results", () => {
  let retries = 0;
  const result = render("premarket", { intelligence, error: "request failed", reload() { retries++; } });
  assert.ok(result.html.includes("研究候选"));
  assert.equal(result.html.split("候选快照更新失败").length - 1, 1);
  assert.ok(result.html.includes("2026-10-13 目标日"));
  assert.ok(result.retry);
  result.retry();
  assert.equal(retries, 1);
});

test("silent rendering preserves expired drafts and explicit historical fallback provenance", () => {
  const draft = { ...intelligence, stage: "draft", target_trade_date: "2020-01-06", finalized_at: null };
  const expired = render("premarket", { intelligence: draft, loading: true, fromCache: true }).html;
  assert.ok(expired.includes("2020-01-06 目标日"));
  assert.ok(expired.includes("09:30 开盘截止"));
  assert.ok(expired.includes("未作为正式榜单固化"));
  const fallback = { ...intelligence, display_context: { is_history_fallback: true,
    latest_target_trade_date: "2026-10-14", latest_stage: "missed_cutoff",
    latest_refreshed_at: "2026-10-14T10:00:00+08:00", latest_warnings: ["当前日历记录存在缺口"] } };
  const historical = render("premarket", { intelligence: fallback, loading: true, fromCache: true }).html;
  assert.ok(historical.includes("当前目标日 2026-10-14"));
  assert.ok(historical.includes("下方回退展示历史快照"));
  assert.ok(historical.includes("候选所属2026-10-13 目标日"));
  assert.ok(historical.includes("当前日历记录存在缺口"));
});
