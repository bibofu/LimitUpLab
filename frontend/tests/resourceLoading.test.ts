import assert from "node:assert/strict";
import test from "node:test";
import { createResourceLoader, type ResourceState } from "../src/utils/asyncResource.ts";
import { DASHBOARD_TIMEOUT_MS, GET_TIMEOUT_MS, requestJson } from "../src/utils/jsonRequest.ts";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test("a pending index and failed calendar do not withhold available local events", async () => {
  const states = new Map<string, ResourceState<unknown>>();
  const pending = deferred<string>();
  const summary = createResourceLoader(() => pending.promise, value => states.set("summary", value));
  const events = createResourceLoader(async () => ["2026-10-08 local event"], value => states.set("events", value));
  const calendar = createResourceLoader(async () => { throw new Error("calendar unavailable"); }, value => states.set("calendar", value));
  const summaryRun = summary.reload();
  await Promise.all([events.reload(), calendar.reload()]);
  assert.equal(states.get("summary")?.loading, true);
  assert.deepEqual(states.get("events")?.data, ["2026-10-08 local event"]);
  assert.equal(states.get("calendar")?.data, null);
  assert.equal(states.get("calendar")?.error, "calendar unavailable");
  summary.cancel();
  pending.resolve("obsolete");
  await summaryRun;
});

test("refresh aborts the old request and ignores its late success or failure", async () => {
  for (const lateFailure of [false, true]) {
    const old = deferred<string>();
    const fresh = deferred<string>();
    const states: ResourceState<string>[] = [];
    const signals: AbortSignal[] = [];
    const loader = createResourceLoader(signal => {
      signals.push(signal);
      return signals.length === 1 ? old.promise : fresh.promise;
    }, value => states.push(value));
    const first = loader.reload();
    const second = loader.reload();
    assert.equal(signals[0].aborted, true);
    fresh.resolve("2026-10-09");
    await second;
    if (lateFailure) old.reject(new Error("old failure"));
    else old.resolve("2026-10-08");
    await first;
    assert.equal(states.at(-1)?.data, "2026-10-09");
    assert.equal(states.at(-1)?.error, null);
    const count = states.length;
    loader.cancel();
    assert.equal(signals[1].aborted, true);
    assert.equal(states.length, count);
  }
});

test("GET timeout aborts a hanging body and the resource can retry successfully", async () => {
  let signal: AbortSignal | undefined;
  const fetcher: typeof fetch = async (_url, init) => {
    signal = init?.signal as AbortSignal;
    return new Response(new ReadableStream());
  };
  const states: ResourceState<{ date: string }>[] = [];
  let attempts = 0;
  const loader = createResourceLoader(async external => {
    attempts++;
    return requestJson<{ date: string }>("/data", { signal: external }, attempts === 1 ? fetcher : async () => Response.json({ date: "2026-10-09" }), 10);
  }, value => states.push(value));
  await loader.reload();
  assert.equal(signal?.aborted, true);
  assert.match(states.at(-1)?.error ?? "", /超时/);
  assert.equal(states.at(-1)?.data, null);
  await loader.reload();
  assert.deepEqual(states.at(-1)?.data, { date: "2026-10-09" });
});

test("external cancellation settles even if fetch ignores its AbortSignal", async () => {
  const controller = new AbortController();
  let received: AbortSignal | undefined;
  const read = requestJson("/data", { signal: controller.signal }, async (_url, init) => {
    received = init?.signal as AbortSignal;
    return new Promise<Response>(() => {});
  }, 1000);
  controller.abort();
  await assert.rejects(read, { name: "AbortError" });
  assert.equal(received?.aborted, true);
});

test("POST remains independent of the read timeout and errors retain backend detail", async () => {
  const response = deferred<Response>();
  const mutation = requestJson("/mutation", { method: "POST" }, async () => response.promise, 1);
  await new Promise(resolve => setTimeout(resolve, 10));
  response.resolve(Response.json({ saved: true }));
  assert.deepEqual(await mutation, { saved: true });
  await assert.rejects(requestJson("/data", {}, async () => Response.json({ detail: "请稍后重试" }, { status: 429 })), /请稍后重试/);
});

test("dashboard reads use a short budget while ordinary market/review reads keep 120 seconds", async t => {
  const scheduled: number[] = [];
  const original = globalThis.setTimeout;
  t.mock.method(globalThis, "setTimeout", (callback: () => void, delay: number) => {
    scheduled.push(delay);
    return original(callback, delay);
  });
  const fetcher: typeof fetch = async () => Response.json({ ok: true });
  await requestJson("/api/market/overview", {}, fetcher, DASHBOARD_TIMEOUT_MS);
  await requestJson("/api/stocks/600001/market-data", {}, fetcher);
  await requestJson("/api/agents/chat", { method: "POST" }, fetcher);
  assert.deepEqual(scheduled, [20_000, 120_000]);
  assert.equal(GET_TIMEOUT_MS, 120_000);
});
