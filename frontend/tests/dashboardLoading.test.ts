import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import ts from "typescript";
import { latestEventDate, matchingRatings } from "../src/utils/dashboardData.ts";
import type { FirstBoardRatingsResponse, LimitUpEvent } from "../src/types.ts";

function compile(path: string, imports: Record<string, unknown> = {}) {
  const url = new URL(path, import.meta.url);
  const require = createRequire(url);
  const source = readFileSync(url, "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const module = { exports: {} as Record<string, any> };
  new Function("require", "module", "exports", compiled)((id: string) => imports[id] ?? require(id.startsWith(".") ? `${id}.ts` : id), module, module.exports);
  return module.exports;
}

const pending = { data: null, loading: true, error: null, reload() {} };
const unavailable = { ...pending, loading: false, error: "数据请求超时，请重试" };
const ready = (data: unknown) => ({ ...pending, loading: false, data });
const event = { symbol: "600001", name: "测试股票", trade_date: "2026-10-09", first_limit_time: "09:45:00", last_limit_time: "14:00:00", board_height: 2, seal_count: 1, break_count: 0, amount: 1_000_000, turnover_rate: 2, industry: "测试行业", concept: "测试题材" } as LimitUpEvent;
const panel = { Panel: ({ title, children }: any) => createElement("section", null, createElement("h2", null, title), children) };
const sections = compile("../src/components/ResourceSection.tsx", {
  "../utils/asyncResource": await import("../src/utils/asyncResource.ts"),
});
const review = compile("../src/components/ReviewDashboard.tsx", { "../api": {}, "./Panel": panel });
const stockResearch = compile("../src/components/StockResearchPanels.tsx", { "./Panel": panel });
const recommendationNews = compile("../src/components/RecommendationNewsBoard.tsx", { "../api": {} });
const recommendationIntelligence = compile("../src/hooks/useRecommendationIntelligence.ts", { "../api": {} });

function renderApp(path: string, states: unknown[]) {
  let cursor = 0;
  const { App } = compile("../src/App.tsx", {
    "./api": {},
    "./components/ResourceSection": { ...sections, useResource: () => states[cursor++] },
    "./components/Panel": panel,
    "./components/AgentChatDock": { AgentChatDock: () => createElement("aside", null, "Agent 会话可用") },
    "./components/ReviewDashboard": review,
    "./components/StockResearchPanels": stockResearch,
    "./components/RecommendationNewsBoard": recommendationNews,
    "./hooks/useRecommendationIntelligence": recommendationIntelligence,
    "./components/ConsolidationPanel": {},
    "./components/MarketKLineChart": {},
  });
  return renderToStaticMarkup(createElement(MemoryRouter, { initialEntries: [path] }, createElement(App)));
}

test("the homepage keeps its market slots and Agent without transient pool cards while providers hang", () => {
  const html = renderApp("/", [pending, ready([event]), unavailable, pending, ready([]), pending, unavailable]);
  assert.ok(html.includes('aria-label="主导航"'));
  assert.ok(html.includes("Agent 会话可用"));
  assert.ok(html.includes("市场概览"));
  assert.ok(html.includes("市场概览加载中"));
  assert.ok(html.includes('class="market-snapshot"'));
  assert.ok(html.includes('aria-label="涨停概览"'));
  assert.ok(html.includes('href="/stocks/first-board"'));
  assert.ok(html.includes('aria-busy="true"'));
  assert.ok(!html.includes('aria-label="涨停池分类"'));
  assert.ok(!html.includes(">0<"));
});

test("market error and recovery keep the same slots and expose a local retry", () => {
  const states = [ready([event]), pending, pending, pending, pending, pending];
  const failed = renderApp("/", [unavailable, ...states]);
  const summary = { trade_date: "2026-10-08", indices: [], max_board_height: 4, first_board_count: 20, continued_board_count: 5 };
  const loaded = renderApp("/", [ready(summary), ...states]);
  for (const html of [failed, loaded]) {
    assert.ok(html.includes('class="market-snapshot"'));
    assert.ok(html.includes('aria-label="涨停概览"'));
    assert.equal(html.split("<article>").length - 1, 3);
    assert.ok(!html.includes('aria-label="涨停池分类"'));
  }
  assert.ok(failed.includes("市场概览暂不可用"));
  assert.ok(failed.includes('aria-label="重新加载市场概览"'));
  assert.ok(loaded.includes('dateTime="2026-10-08"'));
  assert.ok(loaded.includes("指数暂不可用"));
  const refreshing = renderApp("/", [{ ...ready(summary), loading: true }, ...states]);
  assert.ok(refreshing.includes("更新中 · "));
  assert.ok(refreshing.includes('dateTime="2026-10-08"'));
  assert.ok(refreshing.includes(">20<"));
  const stale = renderApp("/", [{ ...ready(summary), error: "请求超时" }, ...states]);
  assert.ok(stale.includes("更新失败 · 保留数据 "));
  assert.ok(stale.includes('dateTime="2026-10-08"'));
  assert.ok(stale.includes('aria-label="重新加载市场概览"'));
});

test("the pool route still exposes each independently loaded local count", () => {
  const html = renderApp("/stocks/limit-up-pool", [pending, ready([event]), unavailable, pending, ready([]), pending, unavailable]);
  assert.ok(html.includes('aria-label="涨停池分类"'));
  assert.ok(html.includes("1 只"));
  assert.ok(html.includes("数据日 2026-10-09"));
  assert.ok(html.includes('aria-label="重新加载连板"'));
});

test("a local stock list renders independently of missing summary, ratings and calendar", () => {
  const html = renderApp("/stocks/continued-board", [unavailable, pending, ready([event]), pending, pending, pending, unavailable]);
  assert.ok(html.includes("测试股票"));
  assert.ok(html.includes("2026-10-09"));
  assert.ok(!html.includes("数据加载失败"));
});

test("the review page isolates calendar failure from its independently loaded panels", () => {
  const html = renderApp("/review", [pending, ready([event]), pending, pending, pending, pending, unavailable]);
  assert.ok(html.includes("晋级统计"));
  assert.ok(html.includes("数据请求超时，请重试"));
  assert.ok(html.includes("龙虎榜"));
  assert.ok(html.includes('aria-label="重新加载晋级统计"'));
});

test("rating joins reject another date, mixed event dates and an undated empty list", () => {
  const ratings = { trade_date: "2026-10-09", candidates: [] } as unknown as FirstBoardRatingsResponse;
  assert.equal(matchingRatings([event], ratings), ratings);
  assert.equal(matchingRatings([{ ...event, trade_date: "2026-10-08" }], ratings), undefined);
  assert.equal(matchingRatings([event, { ...event, trade_date: "2026-10-08" }], ratings), undefined);
  assert.equal(matchingRatings([], ratings), undefined);
  assert.equal(latestEventDate(null), undefined);
  assert.equal(latestEventDate([]), undefined);
});

test("a delayed rating snapshot from another date never decorates the current stock table", () => {
  const staleRatings = {
    trade_date: "2026-10-08", candidates: [{ facts: { symbol: event.symbol }, score: 99 }], filtered_out: [],
  };
  const html = renderApp("/stocks/first-board", [pending, ready([{ ...event, board_height: 1 }]), pending, pending, pending, ready(staleRatings), pending]);
  assert.ok(html.includes("测试股票"));
  assert.ok(html.includes("评级数据日 2026-10-08 与名单日期不同，未合并评分。"));
  assert.ok(!html.includes(">99<"));
});
