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

test("24-hour news keeps source cutoff and content visible while describing the refresh", () => {
  const ready = render("news", { news }).html;
  for (const state of [{ loading: true }, { loading: true, fromCache: true }, { news: { ...news, refreshing: true, stale: true } }]) {
    const updating = render("news", { news, ...state }).html;
    assert.ok(updating.includes("已保存的市场快讯"));
    assert.ok(updating.includes("来源抓取：2026-10-12 18:30:00（上海时间）"));
    assert.ok(updating.includes("显示上次快讯，正在更新"));
  }
  assert.ok(ready.includes("来源抓取：2026-10-12 18:30:00（上海时间）"));
  assert.ok(ready.includes("近 24 小时（以来源抓取为截止）"));
  assert.ok(ready.includes("共 1 条"));
  assert.ok(ready.includes("查看最新快讯"));
  assert.ok(!ready.includes("最近一批快讯"));
  assert.ok(ready.includes("已保存的市场快讯"));
});

test("unknown 24-hour news has an explicit loading state and verified empty results stay distinct", () => {
  const unknown = render("news", { news: null, loading: true }).html;
  assert.ok(unknown.includes("来源抓取：—"));
  assert.ok(!unknown.includes("没有市场快讯"));
  assert.ok(!unknown.includes("共 0 条"));
  assert.ok(unknown.includes("正在获取近24小时新闻"));
  const empty = { ...news, total: 0, items: [] };
  const ready = render("news", { news: empty }).html;
  assert.ok(render("news", { news: empty, loading: true, fromCache: true }).html.includes("该次抓取的近 24 小时范围内没有市场快讯"));
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

test("server refresh failures and bounded polling keep old news with retry feedback", () => {
  const failed = render("news", { news: { ...news, stale: true, refreshing: false, refresh_error: "upstream error" } });
  assert.ok(failed.html.includes("新闻更新失败，继续显示上次快讯"));
  assert.ok(failed.html.includes("已保存的市场快讯"));
  assert.ok(!failed.html.includes("upstream error"));
  assert.ok(failed.retry);
  const waiting = render("news", { news: { ...news, refreshing: true }, waitingTooLong: true });
  assert.ok(waiting.html.includes("更新时间较长，继续显示上次快讯"));
  assert.ok(waiting.retry);
});

test("the news board starts with 24-hour pagination and keeps the optional latest batch independent", () => {
  const slots: unknown[] = [];
  let cursor = 0;
  const requests: [number, string][] = [];
  let buttons = new Map<string, () => void>();
  const observe = (factory: (...args: any[]) => unknown) => (...args: any[]) => {
    if (args[0] === "button") buttons.set(String(args[1]?.["aria-label"] ?? args[1]?.children), args[1]?.onClick);
    return factory(...args);
  };
  const { RecommendationNewsBoard } = compile("../src/components/RecommendationNewsBoard.tsx", {
    react: {
      useState(initial: unknown) {
        const slot = cursor++;
        if (!(slot in slots)) slots[slot] = initial;
        return [slots[slot], (next: any) => { slots[slot] = typeof next === "function" ? next(slots[slot]) : next; }];
      },
      useEffect() {},
      useMemo: (factory: () => unknown) => factory(),
    },
    "../hooks/useFinanceNews": { useFinanceNews(page: number, mode: string) {
      requests.push([page, mode]);
      return { news: { ...news, page, total_pages: mode === "history" ? 3 : 1,
        total: mode === "history" ? 30 : 1,
        items: [{ ...news.items[0], title: mode === "history" ? `历史第${page}页` : "最新标题" }] },
      loading: false, error: null, reload() {} };
    } },
    "react/jsx-runtime": { ...jsxRuntime, jsx: observe(jsxRuntime.jsx), jsxs: observe(jsxRuntime.jsxs) },
  });
  const renderBoard = () => {
    cursor = 0; buttons = new Map();
    return renderToStaticMarkup(RecommendationNewsBoard());
  };
  let html = renderBoard();
  assert.ok(html.includes("历史第1页"));
  assert.ok(html.includes("近 24 小时（以来源抓取为截止）"));
  assert.ok(html.includes("共 30 条"));
  assert.ok(html.includes("市场快讯分页"));
  buttons.get("2")!();
  html = renderBoard();
  assert.ok(html.includes("历史第2页"));
  buttons.get("下一页")!();
  html = renderBoard();
  assert.ok(html.includes("历史第3页"));
  buttons.get("上一页")!();
  html = renderBoard();
  assert.ok(html.includes("历史第2页"));
  buttons.get("查看最新快讯")!();
  html = renderBoard();
  assert.ok(html.includes("最新标题"));
  assert.ok(!html.includes("历史第2页"));
  assert.ok(!html.includes("市场快讯分页"));
  buttons.get("查看近24小时新闻")!();
  html = renderBoard();
  assert.ok(html.includes("历史第1页"));
  assert.ok(html.includes("市场快讯分页"));
  assert.deepEqual(requests, [[1, "history"], [2, "history"], [3, "history"], [2, "history"], [1, "latest"], [1, "history"]]);
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
