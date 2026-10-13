import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import * as jsxRuntime from "react/jsx-runtime";
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
const reviewDigest = compile("../src/components/ReviewDigest.tsx");
const reviewSummary = compile("../src/components/ReviewSummary.tsx", { "./ReviewDigest": reviewDigest });
const reviewReport = compile("../src/hooks/useReviewReport.ts", { "../api": {} });
const review = compile("../src/components/ReviewDashboard.tsx", {
  "../api": {}, "./Panel": panel,
  "./ReviewSummary": reviewSummary, "../hooks/useReviewReport": reviewReport,
});
const stockResearch = compile("../src/components/StockResearchPanels.tsx", { "./Panel": panel });
const recommendationNews = compile("../src/components/RecommendationNewsBoard.tsx", {
  "../api": {},
  "../hooks/useFinanceNews": { useFinanceNews: () => ({ news: null, loading: true, error: null, fromCache: false, savedAt: null, reload() {} }) },
});
const recommendationIntelligence = {
  useRecommendationIntelligence: () => ({ intelligence: null, loading: true, error: null, fromCache: false, savedAt: null, reload() {} }),
  recommendationIntelligenceFor: () => null,
};
const consolidation = compile("../src/components/ConsolidationPanel.tsx", { "../api": {}, "./Panel": panel });
const premarket = compile("../src/pages/PremarketPage.tsx", {
  "../components/ConsolidationPanel": consolidation,
  "../components/Panel": panel,
  "../components/RecommendationNewsBoard": recommendationNews,
  "../hooks/useRecommendationIntelligence": recommendationIntelligence,
});

function renderApp(path: string, states: unknown[], overrides: Record<string, unknown> = {}, activations?: boolean[]) {
  let cursor = 0;
  const read = (enabled: boolean) => {
    activations?.push(enabled);
    const state = states[cursor++] as typeof pending;
    return enabled ? state : { ...state, data: null, loading: false, error: null };
  };
  const { App } = compile("../src/App.tsx", {
    "./api": {},
    "./components/ResourceSection": { ...sections, useResource: (_fetch: unknown, _keep: boolean, enabled = true) => read(enabled) },
    "./hooks/useMarketSummary": {
      useMarketSummary: ({ enabled }: { enabled: boolean }) => read(enabled),
      refreshRecommendationSnapshots() {},
    },
    "./components/Panel": panel,
    "./components/AgentChatDock": { AgentChatDock: () => createElement("aside", null, "Agent 会话可用") },
    "./components/ReviewDashboard": review,
    "./components/StockResearchPanels": stockResearch,
    "./pages/PremarketPage": premarket,
    "./hooks/useRecommendationIntelligence": recommendationIntelligence,
    "./hooks/useReviewReport": reviewReport,
    "./components/MarketKLineChart": {},
    ...overrides,
  });
  return renderToStaticMarkup(createElement(MemoryRouter, { initialEntries: [path] }, createElement(App)));
}

test("refresh targets the visible page and refreshes review facts only on review", () => {
  for (const [path, expectedResources, expectedReview] of [["/", 1, 0], ["/review", 2, 1], ["/stocks/continued-board", 1, 0]] as const) {
    let refreshReviewCalls = 0;
    let otherRefreshCalls = 0;
    let clickRefresh: (() => void) | undefined;
    const observe = (factory: (...args: any[]) => unknown) => (...args: any[]) => {
      const props = args[1];
      if (props?.["aria-label"] === "刷新当前页面数据") clickRefresh = props.onClick;
      return factory(...args);
    };
    renderApp(path, Array(7).fill({ ...pending, reload() { otherRefreshCalls++; } }), {
      "./hooks/useReviewReport": { refreshReviewFacts() { refreshReviewCalls++; } },
      "react/jsx-runtime": { ...jsxRuntime, jsx: observe(jsxRuntime.jsx), jsxs: observe(jsxRuntime.jsxs) },
    });
    assert.ok(clickRefresh);
    clickRefresh();
    assert.equal(refreshReviewCalls, expectedReview, path);
    assert.equal(otherRefreshCalls, expectedResources, path);
  }
});

test("routes activate only their visible resources and necessary date dependencies", () => {
  const routes: [string, boolean[]][] = [
    ["/", [true, false, false, false, false, false, false]],
    ["/recommendations", [false, false, false, false, false, false, false]],
    ["/stocks/limit-up-pool", [false, true, true, true, true, false, false]],
    ["/stocks/first-board", [false, true, false, false, false, true, false]],
    ["/stocks/continued-board", [false, false, true, false, false, false, false]],
    ["/stocks/failed", [false, false, false, true, false, false, false]],
    ["/stocks/recent-limit-up", [false, false, false, false, true, false, false]],
    ["/review", [false, true, false, false, false, false, true]],
    ["/stocks/600001", [false, false, false, false, false, true, false]],
  ];
  for (const [path, expected] of routes) {
    const activations: boolean[] = [];
    renderApp(path, Array(7).fill(pending), {}, activations);
    assert.deepEqual(activations, expected, path);
  }
});

test("recommendation refresh updates its snapshots without starting hidden dashboard reads", () => {
  let refreshCalls = 0;
  let hiddenCalls = 0;
  let clickRefresh: (() => void) | undefined;
  const observe = (factory: (...args: any[]) => unknown) => (...args: any[]) => {
    if (args[1]?.["aria-label"] === "刷新当前页面数据") clickRefresh = args[1].onClick;
    return factory(...args);
  };
  renderApp("/recommendations", Array(7).fill({ ...pending, reload() { hiddenCalls++; } }), {
    "./hooks/useMarketSummary": {
      useMarketSummary: () => pending,
      refreshRecommendationSnapshots() { refreshCalls++; },
    },
    "react/jsx-runtime": { ...jsxRuntime, jsx: observe(jsxRuntime.jsx), jsxs: observe(jsxRuntime.jsxs) },
  });
  assert.ok(clickRefresh);
  clickRefresh();
  assert.equal(refreshCalls, 1);
  assert.equal(hiddenCalls, 0);
});

test("the homepage date cannot be changed by cached data from a hidden route", () => {
  const summary = { trade_date: "2026-10-08", indices: [], max_board_height: 4, first_board_count: 20, continued_board_count: 5 };
  const html = renderApp("/", [ready(summary), ready([event]), pending, pending, pending, ready({ trade_date: "2026-10-09" }), pending]);
  assert.ok(html.includes("2026-10-08"));
  assert.ok(!html.includes("2026-10-09"));
});

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

test("a saved market date remains visible but cannot become the Agent default before revalidation", () => {
  const summary = { trade_date: "2026-10-08", indices: [], max_board_height: 4, first_board_count: 20, continued_board_count: 5 };
  for (const state of [
    { ...ready(summary), fromCache: true, loading: true },
    { ...ready(summary), fromCache: true, error: "更新失败" },
    { ...ready(summary), fromCache: false },
  ]) {
    let agentDate: string | undefined;
    const html = renderApp("/", [state, ...Array(6).fill(pending)], {
      "./components/AgentChatDock": { AgentChatDock: ({ tradeDate }: { tradeDate: string }) => {
        agentDate = tradeDate;
        return createElement("aside", null, "Agent 会话可用");
      } },
    });
    assert.ok(html.includes('dateTime="2026-10-08"'));
    assert.ok(html.includes(">20<"));
    assert.equal(agentDate, state.fromCache ? "" : "2026-10-08");
    if (state.loading) assert.ok(html.includes("上次结果 · 更新中"));
  }
});

test("the extracted premarket page preserves news and all URL-selected strategy panels", () => {
  for (const strategy of ["relay", "consolidation", "drawdown"]) {
    const html = renderApp(`/recommendations?strategy=${strategy}`, Array(7).fill(pending));
    assert.ok(html.includes('aria-label="盘前实时新闻"'));
    assert.ok(html.includes('aria-label="盘前策略"'));
    assert.equal(html.split('aria-selected="true"').length - 1, 1);
    assert.ok(html.includes(strategy === "relay"
      ? "正在读取统一的盘前排名与证据"
      : `id="${strategy}-panel"`));
  }
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

test("cached news remains readable with its source cutoff during background refresh and failure", () => {
  const news = {
    fetched_at: "2026-10-12T18:30:00+08:00", sources: ["测试资讯来源"],
    page: 1, page_size: 10, total: 1, total_pages: 1,
    items: [{ title: "已保存的市场快讯", summary: "公开资讯摘要", published_at: "2026-10-12T18:00:00+08:00",
      source: "测试资讯来源", url: "https://example.com/market-news", category: "A股" }],
  };
  for (const failed of [false, true]) {
    let retries = 0;
    let clickRetry: (() => void) | undefined;
    const observe = (factory: (...args: any[]) => unknown) => (...args: any[]) => {
      if (args[0] === "button" && args[1]?.children === "重新加载新闻") clickRetry = args[1].onClick;
      return factory(...args);
    };
    const { RecommendationNewsBoard } = compile("../src/components/RecommendationNewsBoard.tsx", {
      "../hooks/useFinanceNews": { useFinanceNews: () => ({ news, fromCache: true,
        loading: !failed, error: failed ? "新闻请求超时" : null, savedAt: 0,
        reload() { retries++; } }) },
      "react/jsx-runtime": { ...jsxRuntime, jsx: observe(jsxRuntime.jsx), jsxs: observe(jsxRuntime.jsxs) },
    });
    const html = renderToStaticMarkup(createElement(RecommendationNewsBoard));
    assert.ok(html.includes("已保存的市场快讯"));
    assert.ok(html.includes("测试资讯来源"));
    assert.ok(html.includes("上次来源抓取：2026-10-12 18:30:00（上海时间）"));
    assert.ok(html.includes("近24小时范围以该次抓取为准"));
    assert.ok(!html.includes("正在获取最新新闻..."));
    if (failed) {
      assert.ok(html.includes("更新失败，保留上次新闻快照"));
      assert.ok(clickRetry);
      clickRetry();
      assert.equal(retries, 1);
    } else {
      assert.ok(html.includes("上次结果 · 更新中"));
      assert.equal(clickRetry, undefined);
    }
  }
});

test("the real premarket page keeps cached candidates and their target date while refreshing or retrying", () => {
  const intelligence = {
    refresh_id: "cached-public-ranking", stage: "final", target_trade_date: "2026-10-13",
    relay_base_date: "2026-10-12", refreshed_at: "2026-10-13T09:20:00+08:00",
    finalized_at: "2026-10-13T09:25:00+08:00", warnings: [], relay_display_limit: 10,
    items: [{ strategy: "relay", name: "已保存的研究候选", symbol: "600001",
      base_trade_date: "2026-10-12", rank: 1, base_rank: 2, base_score: 70,
      draft_score: 72, dynamic_adjustment: 2, sector: "测试行业", position_label: "低位首板",
      update_reasons: ["已有结构化证据"], close_information_reasons: [], latest_news: [] }],
  };
  for (const failed of [false, true]) {
    let retries = 0;
    let clickRetry: (() => void) | undefined;
    const observe = (factory: (...args: any[]) => unknown) => (...args: any[]) => {
      if (args[0] === "button" && args[1]?.children === "重新加载候选") clickRetry = args[1].onClick;
      return factory(...args);
    };
    const cachedPremarket = compile("../src/pages/PremarketPage.tsx", {
      "../components/ConsolidationPanel": consolidation,
      "../components/Panel": panel,
      "../components/RecommendationNewsBoard": recommendationNews,
      "../hooks/useRecommendationIntelligence": {
        useRecommendationIntelligence: () => ({ intelligence, fromCache: true,
          loading: !failed, error: failed ? "候选请求超时" : null, savedAt: 0,
          reload() { retries++; } }),
      },
      "react/jsx-runtime": { ...jsxRuntime, jsx: observe(jsxRuntime.jsx), jsxs: observe(jsxRuntime.jsxs) },
    });
    const html = renderApp("/recommendations", Array(7).fill(pending), { "./pages/PremarketPage": cachedPremarket });
    assert.ok(html.includes('class="rating-top-card"'));
    assert.ok(html.includes("已保存的研究候选"));
    assert.ok(html.includes("2026-10-13 目标日"));
    assert.ok(html.includes("候选基准日：2026-10-12"));
    assert.ok(html.includes("快照更新：2026-10-13 09:20:00（上海时间）"));
    assert.ok(html.includes("固化时间：2026-10-13 09:25:00（上海时间）"));
    assert.ok(!html.includes("正在读取统一的盘前排名与证据"));
    if (failed) {
      assert.ok(html.includes("候选快照更新失败，保留上次结果"));
      assert.ok(clickRetry);
      clickRetry();
      assert.equal(retries, 1);
    } else {
      assert.ok(html.includes("显示上次候选快照，正在检查更新"));
      assert.equal(clickRetry, undefined);
    }
  }
});
