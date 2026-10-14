import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";
import * as snapshots from "../src/utils/publicSnapshot.ts";

function load(fetchFinanceNews: (page: number) => Promise<unknown>) {
  const source = readFileSync(new URL("../src/utils/marketSnapshots.ts", import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020,
  } }).outputText;
  const module = { exports: {} as any };
  new Function("require", "module", "exports", compiled)((name: string) => name === "./publicSnapshot"
    ? snapshots : name === "./latestNewsSnapshot"
      ? { createLatestNewsSnapshot: (options: any) => snapshots.createPublicSnapshot({ ...options, maxAgeMs: 604800000 }) }
      : { PUBLIC_DATA_SCOPE: "tests", fetchFinanceNews, fetchLatestFinanceNews: () => fetchFinanceNews(0),
      fetchMarketSummary: async () => ({}),
      fetchRecommendationIntelligence: async () => ({ stage: "draft", refreshed_at: "2026-10-13T17:00:00+08:00",
        target_trade_date: "2026-10-14", relay_base_date: "2026-10-13", finalized_at: null,
        relay_display_limit: 10, warnings: [], items: [] }) }, module, module.exports);
  return module.exports;
}
const pageData = (page: number) => ({ page, fetched_at: "2026-10-13T17:00:00+08:00",
  sources: ["synthetic test"], total: 30, total_pages: 3, items: [] });

test("a page clamped by the server is not reused when that page becomes available later", async () => {
  let count = 0;
  const resources = load(async () => pageData(++count === 1 ? 2 : 3));
  const original = resources.financeNewsSnapshot(3);
  await original.refresh();
  assert.equal(original.read().data.page, 2, "current caller can apply the server's last-page correction");
  const revisited = resources.financeNewsSnapshot(3);
  assert.notEqual(revisited, original);
  assert.equal(revisited.read().data, null, "old clamped page cannot redirect the new navigation");
  await revisited.refresh();
  assert.equal(revisited.read().data.page, 3);
  assert.equal(count, 2);
});

test("manual page refresh ignores previously visited but hidden news pages", async () => {
  const calls: number[] = [];
  const resources = load(async (page: number) => { calls.push(page); return pageData(page); });
  const first = resources.financeNewsSnapshot(1);
  const second = resources.financeNewsSnapshot(2);
  await first.refresh();
  await second.refresh();
  const stop = second.subscribe(() => {});
  calls.length = 0;
  await resources.refreshRecommendationSnapshots();
  assert.deepEqual(calls, [2]);
  stop();
});

test("a late page response stays with its own page resource", async () => {
  let finish!: (value: unknown) => void;
  const slow = new Promise(resolve => { finish = resolve; });
  const resources = load((page: number) => page === 1 ? slow : Promise.resolve(pageData(page)));
  const first = resources.financeNewsSnapshot(1);
  const second = resources.financeNewsSnapshot(2);
  const pending = first.refresh();
  await second.refresh();
  finish(pageData(1));
  await pending;
  assert.equal(second.read().data.page, 2);
  assert.equal(first.read().data.page, 1);
});

test("latest and historical page one have separate resources and refresh only the visible feed", async () => {
  const calls: number[] = [];
  const resources = load(async (page: number) => {
    calls.push(page);
    return { ...pageData(1), total_pages: page === 0 ? 1 : 3, total: page === 0 ? 10 : 30 };
  });
  const latest = resources.latestFinanceNewsSnapshot;
  const history = resources.financeNewsSnapshot(1);
  await latest.refresh();
  await history.refresh();
  assert.equal(latest.read().data.total, 10);
  assert.equal(history.read().data.total, 30);
  calls.length = 0;
  const stop = latest.subscribe(() => {});
  await resources.refreshRecommendationSnapshots();
  assert.deepEqual(calls, [0]);
  stop();
});
