import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createPublicSnapshot } from "../src/utils/publicSnapshot.ts";

function compile(path: string, imports: Record<string, unknown> = {}) {
  const source = readFileSync(new URL(path, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020,
  } }).outputText;
  const module = { exports: {} as any };
  new Function("require", "module", "exports", compiled)((name: string) => imports[name], module, module.exports);
  return module.exports;
}
const { createLatestNewsSnapshot, LATEST_NEWS_MAX_AGE_MS } = compile("../src/utils/latestNewsSnapshot.ts", {
  "./publicSnapshot": { createPublicSnapshot },
});
const { followNewsRefresh } = compile("../src/utils/newsRefreshPolling.ts");
const origin = Date.parse("2026-10-15T10:00:00+08:00");
const data = (changes = {}) => ({ fetched_at: new Date(origin).toISOString(), window_hours: 24,
  sources: ["公开快讯"], page: 1, page_size: 10, total: 1, total_pages: 1,
  items: [{ title: "旧标题", published_at: new Date(origin).toISOString(), source: "公开快讯",
    summary: "", url: "https://example.com/news", category: "市场", relevance_score: 1 }],
  stale: false, refreshing: false, refresh_error: null, ...changes });
const valid = (value: any) => !!value && typeof value.fetched_at === "string" && Array.isArray(value.items)
  && value.page === 1 && value.total_pages === 1;
const prefix = "limituplab.public-snapshot.v1:";

function fixture() {
  let now = origin;
  const values = new Map<string, string>();
  const storage = { getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } };
  return { values, advance: (ms: number) => { now += ms; },
    create: (fetchValue = async () => data()) => createLatestNewsSnapshot({ key: "news-latest", legacyKey: "news-page-1",
      fetchValue, valid, storage: () => storage, now: () => now }) };
}

test("latest batch survives a visit after 15 minutes and replaces old titles only when new data arrives", async () => {
  const f = fixture();
  await f.create().refresh();
  f.advance(30 * 60_000);
  let finish!: (value: unknown) => void;
  const pending = new Promise(resolve => { finish = resolve; });
  const resource = f.create(() => pending as any);
  assert.equal(resource.read().data.items[0].title, "旧标题");
  assert.equal(resource.read().data.stale, true);
  const updating = resource.refresh();
  assert.equal(resource.read().data.fetched_at, data().fetched_at);
  finish(data({ fetched_at: new Date(origin + 30 * 60_000).toISOString(),
    items: [{ ...data().items[0], title: "新标题" }] }));
  await updating;
  assert.equal(resource.read().data.items[0].title, "新标题");
  assert.equal(resource.read().data.stale, false);
});

test("failed latest revalidation preserves the original batch and source timestamp", async () => {
  const f = fixture();
  await f.create().refresh();
  f.advance(60 * 60_000);
  const resource = f.create(async () => { throw new Error("超时"); });
  await resource.refresh();
  assert.equal(resource.read().data.items[0].title, "旧标题");
  assert.equal(resource.read().data.fetched_at, data().fetched_at);
  assert.equal(resource.read().error, "超时");
});

test("source age bounds browser persistence even when an old response was saved recently", async () => {
  const f = fixture();
  await f.create().refresh();
  f.advance(LATEST_NEWS_MAX_AGE_MS + 1);
  const raw = JSON.parse(f.values.get(`${prefix}news-latest`)!);
  raw.savedAt = origin + LATEST_NEWS_MAX_AGE_MS;
  f.values.set(`${prefix}news-latest`, JSON.stringify(raw));
  assert.equal(f.create().read().data, null);
});

test("a nearly seven-day-old server batch expires in memory by source time and cannot be renewed by saving", async () => {
  const f = fixture();
  const old = data({ fetched_at: new Date(origin - LATEST_NEWS_MAX_AGE_MS + 60 * 60_000).toISOString(), stale: true });
  let calls = 0;
  const resource = f.create(async () => { calls++; return old; });
  await resource.refresh();
  assert.equal(resource.read().data.items[0].title, "旧标题");
  f.advance(2 * 60 * 60_000);
  const expired = resource.read();
  assert.equal(expired.data, null);
  assert.equal(resource.read(), expired, "empty reads keep stable external-store identity");
  await resource.refresh();
  assert.equal(resource.read().data, null, "another old server result is rejected");
  assert.ok(resource.read().error);
  for (let index = 0; index < 3; index++) resource.read();
  assert.equal(calls, 2, "reads do not retry failed requests");
  assert.equal(f.create().read().data, null, "hard reload has the same source-age boundary");
});

test("legacy first archive page migrates as a dated unverified batch without its full archive count", () => {
  const f = fixture();
  f.values.set(`${prefix}news-page-1`, JSON.stringify({ version: 1, savedAt: origin,
    data: data({ total: 600, total_pages: 60 }) }));
  const restored = f.create().read();
  assert.equal(restored.data.items[0].title, "旧标题");
  assert.equal(restored.data.total, 1);
  assert.equal(restored.data.total_pages, 1);
  assert.equal(restored.data.stale, true);
  assert.equal(restored.fromCache, true);
  assert.equal(restored.data.fetched_at, data().fetched_at);
  assert.ok(f.values.has(`${prefix}news-page-1`), "history is not removed or overwritten");
});

function clock() {
  let now = origin;
  let id = 0;
  const timers = new Map<number, { callback: () => void; at: number }>();
  const options = { now: () => now, schedule(callback: () => void, delay: number) {
    timers.set(++id, { callback, at: now + delay }); return id;
  }, cancel(timer: number) { timers.delete(timer); } };
  return { options, timers, async next() {
    const entry = [...timers].sort((left, right) => left[1].at - right[1].at)[0];
    assert.ok(entry, "expected one scheduled poll");
    timers.delete(entry[0]); now = entry[1].at; entry[1].callback();
    await new Promise(resolve => setImmediate(resolve));
  } };
}

test("server refresh completion is polled immediately despite the normal 30-second freshness gate", async () => {
  const c = clock();
  let calls = 0;
  const resource = createPublicSnapshot({ key: "poll", storage: () => null, now: c.options.now,
    maxAgeMs: LATEST_NEWS_MAX_AGE_MS, valid: valid as any,
    fetchValue: async () => data({ refreshing: ++calls < 3, stale: calls < 3 }) });
  await resource.refresh();
  const stop = followNewsRefresh(resource, () => assert.fail("should finish"), c.options);
  await c.next();
  assert.equal(calls, 2);
  await c.next();
  assert.equal(calls, 3);
  assert.equal(resource.read().data?.refreshing, false);
  assert.equal(c.timers.size, 0);
  stop();
});

test("polling stops after errors even if the retained old batch still says refreshing", async () => {
  const c = clock();
  let calls = 0;
  const resource = createPublicSnapshot({ key: "poll-error", storage: () => null, maxAgeMs: LATEST_NEWS_MAX_AGE_MS,
    valid: valid as any, fetchValue: async () => { if (++calls > 1) throw new Error("网络失败"); return data({ refreshing: true }); } });
  await resource.refresh();
  followNewsRefresh(resource, () => assert.fail("must stop on error"), c.options);
  await c.next();
  assert.equal(calls, 2);
  assert.equal(c.timers.size, 0);
  assert.equal(resource.read().data?.items[0].title, "旧标题");
});

test("polling has a bounded budget, manual follow-up gets a new budget, and cleanup removes timers", async () => {
  const c = clock();
  let timeouts = 0;
  const resource = createPublicSnapshot({ key: "poll-budget", storage: () => null, maxAgeMs: LATEST_NEWS_MAX_AGE_MS,
    valid: valid as any, fetchValue: async () => data({ refreshing: true }) });
  await resource.refresh();
  const options = { ...c.options, budgetMs: 3000 };
  const stop = followNewsRefresh(resource, () => timeouts++, options);
  await c.next(); await c.next(); await c.next();
  assert.equal(timeouts, 1);
  assert.equal(c.timers.size, 0);
  stop();
  const retry = followNewsRefresh(resource, () => timeouts++, options);
  assert.equal(c.timers.size, 1);
  await c.next();
  assert.equal(timeouts, 1);
  retry();
  assert.equal(c.timers.size, 0);
});

test("unmount during a shared in-flight poll cannot schedule another request", async () => {
  const c = clock();
  let finish!: (value: unknown) => void;
  let calls = 0;
  const resource = createPublicSnapshot({ key: "poll-unmount", storage: () => null, maxAgeMs: LATEST_NEWS_MAX_AGE_MS,
    valid: valid as any, fetchValue: () => ++calls === 1 ? Promise.resolve(data({ refreshing: true }))
      : new Promise(resolve => { finish = resolve; }) });
  await resource.refresh();
  const stop = followNewsRefresh(resource, () => assert.fail("unmounted"), c.options);
  await c.next();
  stop();
  finish(data({ refreshing: true }));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(c.timers.size, 0);
  assert.equal(calls, 2);
});
