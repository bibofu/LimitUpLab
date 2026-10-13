import assert from "node:assert/strict";
import test from "node:test";
import { createPublicSnapshot } from "../src/utils/publicSnapshot.ts";

interface Data { trade_date: string; count: number }
const valid = (value: unknown): value is Data => !!value && typeof value === "object"
  && typeof (value as Data).trade_date === "string" && Number.isFinite((value as Data).count);
const first: Data = { trade_date: "2026-10-12", count: 5 };
const next: Data = { trade_date: "2026-10-13", count: 8 };

function storage() {
  const values = new Map<string, string>();
  return { values, getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
    removeItem: (key: string) => { values.delete(key); } };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test("hard refresh synchronously restores the dated snapshot while one request revalidates", async () => {
  const disk = storage();
  let now = 10_000;
  const options = { key: "api-a:overview", valid, maxAgeMs: 60_000, now: () => now, storage: () => disk };
  await createPublicSnapshot({ ...options, fetchValue: async () => first }).refresh();
  now += 1000;
  const pending = deferred<Data>();
  let calls = 0;
  const reloaded = createPublicSnapshot({ ...options, fetchValue: () => { calls++; return pending.promise; } });
  assert.deepEqual(reloaded.read(), { data: first, fromCache: true, savedAt: 10_000, loading: false, error: null });
  const one = reloaded.refresh();
  const two = reloaded.refresh();
  assert.equal(one, two);
  assert.deepEqual(reloaded.read().data, first);
  assert.equal(reloaded.read().loading, true);
  await Promise.resolve();
  assert.equal(calls, 1);
  pending.resolve(next);
  await one;
  assert.deepEqual(reloaded.read(), { data: next, fromCache: false, savedAt: now, loading: false, error: null });
});

test("a recent route remount reuses public data, explicit refresh still contacts the server", async () => {
  let calls = 0;
  const resource = createPublicSnapshot({ key: "latest", valid, maxAgeMs: 60_000, storage: () => null,
    fetchValue: async () => { calls++; return first; } });
  let published = 0;
  const unsubscribe = resource.subscribe(() => { published++; });
  assert.equal(resource.isObserved(), true);
  await resource.refresh();
  await resource.refresh();
  assert.equal(calls, 1);
  await resource.refresh(true);
  assert.equal(calls, 2);
  unsubscribe();
  assert.equal(resource.isObserved(), false);
  assert.ok(published >= 4);
});

test("failed revalidation retains the original snapshot date/time and reports the failure", async () => {
  let now = 10_000;
  let fail = false;
  const resource = createPublicSnapshot({ key: "failure", valid, maxAgeMs: 60_000,
    storage: () => null, now: () => now,
    fetchValue: async () => { if (fail) throw new Error("离线"); return first; } });
  await resource.refresh();
  now += 10_000;
  fail = true;
  await resource.refresh(true);
  assert.deepEqual(resource.read(), { data: first, savedAt: 10_000, fromCache: true, loading: false, error: "离线" });
  now = 71_000;
  assert.equal(resource.read().data, null);
  assert.equal(resource.read().savedAt, null);
  assert.equal(resource.read().error, "离线");
});

test("expired, future, corrupt and incompatible stored snapshots never render", async () => {
  for (const raw of ["bad-json", "null", JSON.stringify({ version: 0, savedAt: 9000, data: first }),
    JSON.stringify({ version: 1, savedAt: 20_000, data: first }),
    JSON.stringify({ version: 1, savedAt: 0, data: first }),
    JSON.stringify({ version: 1, savedAt: 9000, data: { count: "wrong" } })]) {
    const disk = storage();
    disk.setItem("limituplab.public-snapshot.v1:key", raw);
    const resource = createPublicSnapshot({ key: "key", valid, maxAgeMs: 5000, now: () => 10_000,
      storage: () => disk, fetchValue: async () => next });
    assert.equal(resource.read().data, null);
    await resource.refresh();
    assert.deepEqual(resource.read().data, next);
  }
});

test("storage refusal and full quotas do not block fresh public data", async () => {
  const broken = () => { throw new Error("Storage unavailable"); };
  const resource = createPublicSnapshot({ key: "blocked", valid, maxAgeMs: 60_000,
    storage: broken, fetchValue: async () => first });
  assert.equal(resource.read().data, null);
  await resource.refresh();
  assert.deepEqual(resource.read().data, first);
  assert.equal(resource.read().error, null);
});

test("API origins and news pages cannot reuse each other's snapshots", async () => {
  const disk = storage();
  const base = { valid, maxAgeMs: 60_000, storage: () => disk, fetchValue: async () => first };
  await createPublicSnapshot({ ...base, key: "origin-a:news-page-1" }).refresh();
  for (const key of ["origin-b:news-page-1", "origin-a:news-page-2", "origin-a:overview"]) {
    assert.equal(createPublicSnapshot({ ...base, key }).read().data, null);
  }
});

test("an invalid network result cannot overwrite a valid saved snapshot", async () => {
  const disk = storage();
  const base = { key: "shape", valid, maxAgeMs: 60_000, storage: () => disk };
  await createPublicSnapshot({ ...base, fetchValue: async () => first }).refresh();
  const reloaded = createPublicSnapshot({ ...base, fetchValue: async () => ({} as Data) });
  await reloaded.refresh();
  assert.deepEqual(reloaded.read().data, first);
  assert.equal(reloaded.read().fromCache, true);
  assert.ok(reloaded.read().error);
  assert.deepEqual(createPublicSnapshot({ ...base, fetchValue: async () => next }).read().data, first);
});

test("first load errors clear on retry and old readers can unmount without discarding shared work", async () => {
  let calls = 0;
  const resource = createPublicSnapshot({ key: "retry", valid, maxAgeMs: 60_000, storage: () => null,
    fetchValue: async () => { if (++calls === 1) throw new Error("暂不可用"); return next; } });
  const stop = resource.subscribe(() => {});
  await resource.refresh();
  assert.equal(resource.read().data, null);
  assert.equal(resource.read().error, "暂不可用");
  const retry = resource.refresh();
  stop();
  await retry;
  assert.deepEqual(resource.read().data, next);
  assert.equal(resource.read().error, null);
});
