import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import { streamReviewOverview, type ReviewOverviewEvent } from "../src/utils/reviewOverviewStream.ts";
import { createReviewOverviewResource } from "../src/utils/reviewOverviewResource.ts";

const date = "2026-10-13";
const result = { text: "已核验的完整复盘", model: "offline", as_of_date: date };
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const frame = (event: string, data: unknown) => `event: ${event}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`;
function response(text: string) {
  const bytes = new TextEncoder().encode(text);
  return new Response(new ReadableStream({ start(controller) {
    for (let index = 0; index < bytes.length; index += 2) controller.enqueue(bytes.slice(index, index + 2));
    controller.close();
  } }));
}

test("actual SSE handles split Unicode/CRLF and publishes drafts before authoritative completion", async () => {
  const events: ReviewOverviewEvent[] = [];
  let requests = 0;
  const text = ": heartbeat\r\n\r\n" + frame("delta", { text: "市场首板" })
    + frame("checking", { message: "正在核验" }) + frame("completed", result);
  const completed = await streamReviewOverview("", date, event => events.push(event), new AbortController().signal,
    async (_url, options) => {
      requests++;
      assert.equal(options?.method, "POST");
      assert.deepEqual(JSON.parse(options?.body as string), { end_date: date });
      return response(text);
    });
  assert.deepEqual(completed, result);
  assert.deepEqual(events.map(event => event.event), ["delta", "checking"]);
  assert.equal(events[0].data.text, "市场首板");
  assert.equal(requests, 1);
});

test("disconnect, server rejection and wrong-date completion fail without an automatic retry", async () => {
  for (const text of [frame("delta", { text: "尚未核验" }), frame("error", { message: "未通过核验" }),
    frame("completed", { ...result, as_of_date: "2026-10-12" })]) {
    let requests = 0;
    await assert.rejects(streamReviewOverview("", date, () => {}, new AbortController().signal,
      async () => { requests++; return response(text); }));
    assert.equal(requests, 1);
  }
});

test("resource starts only on demand, streams once, then permits a fresh generation", async () => {
  const pending = deferred<typeof result>();
  let calls = 0;
  let emit!: (event: ReviewOverviewEvent) => void;
  const resource = createReviewOverviewResource(async (_date, callback) => { calls++; emit = callback; return pending.promise; });
  const states: string[] = [];
  resource.subscribe(() => states.push(resource.getState().status));
  assert.equal(resource.getState().status, "idle");
  assert.equal(calls, 0);
  const task = resource.generate(date);
  await resource.generate(date);
  assert.equal(calls, 1);
  emit({ event: "delta", data: { text: "市场概况" } });
  assert.equal(resource.getState().text, "市场概况");
  emit({ event: "checking", data: { message: "核验中" } });
  assert.equal(resource.getState().status, "checking");
  pending.resolve(result);
  await task;
  assert.equal(resource.getState().text, result.text);
  assert.equal(resource.getState().status, "ready");
  await resource.generate(date);
  assert.equal(calls, 2);
  assert.ok(states.includes("checking"));
});

test("failed validation withdraws the draft; refresh/unmount aborts and ignores late responses", async () => {
  let emit!: (event: ReviewOverviewEvent) => void;
  let signal!: AbortSignal;
  const pending = deferred<typeof result>();
  const resource = createReviewOverviewResource(async (_date, callback, abort) => {
    emit = callback; signal = abort; return pending.promise;
  });
  const task = resource.generate(date);
  emit({ event: "delta", data: { text: "未核验的草稿" } });
  pending.reject(new Error("未通过核验"));
  await task;
  assert.equal(resource.getState().status, "error");
  assert.equal(resource.getState().text, "");
  const next = resource.generate(date);
  resource.reset();
  assert.equal(signal.aborted, true);
  emit({ event: "delta", data: { text: "旧响应" } });
  await next;
  assert.equal(resource.getState().status, "idle");
  assert.equal(resource.getState().text, "");
});

function compile(path: string, imports: Record<string, unknown>) {
  const url = new URL(path, import.meta.url);
  const require = createRequire(url);
  const compiled = ts.transpileModule(readFileSync(url, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const module = { exports: {} as any };
  new Function("require", "module", "exports", compiled)((id: string) => imports[id] ?? require(id), module, module.exports);
  return module.exports;
}

test("the actual overview stays empty until its button is clicked, then renders streamed prose", async () => {
  const pending = deferred<typeof result>();
  let calls = 0;
  let emit!: (event: ReviewOverviewEvent) => void;
  const resource = createReviewOverviewResource(async (_date, callback) => { calls++; emit = callback; return pending.promise; });
  let factsLoading = false;
  const effects: (() => void | (() => void))[] = [];
  const component = compile("../src/components/ReviewOverview.tsx", {
    react: { useMemo: () => resource, useSyncExternalStore: (_subscribe: unknown, getState: () => unknown) => getState(),
      useEffect: (effect: () => void | (() => void)) => effects.push(effect) },
    "../api": {}, "../hooks/useReviewReport": { useReviewReport: () => ({ report: {}, loading: factsLoading }) },
    "../utils/reviewOverviewResource": { createReviewOverviewResource: () => resource },
  });
  const render = () => component.ReviewOverview({ endDate: date });
  let tree = render();
  let html = renderToStaticMarkup(tree);
  assert.ok(html.includes("生成复盘总结"));
  assert.ok(!html.includes('aria-label="整体表现"'));
  assert.equal(calls, 0);
  tree.props.children[0].props.children[1].props.onClick();
  assert.equal(calls, 1);
  emit({ event: "delta", data: { text: "市场连板与首板表现" } });
  html = renderToStaticMarkup(render());
  assert.ok(html.includes("市场连板与首板表现"));
  assert.ok(html.includes("内容待核验"));
  assert.ok(html.includes("disabled"));
  pending.resolve(result);
  await new Promise(resolve => setImmediate(resolve));
  html = renderToStaticMarkup(render());
  assert.ok(html.includes(result.text));
  assert.ok(html.includes("重新生成"));
  factsLoading = true;
  render();
  effects.at(-1)!();
  assert.equal(resource.getState().status, "idle");
});

test("the review page places the launch entry before the high-score tracking panel", () => {
  const overview = () => null;
  const dashboard = compile("../src/components/ReviewDashboard.tsx", {
    "../api": {}, "../hooks/useReviewReport": {}, "./ReviewSummary": {},
    "./ReviewOverview": { ReviewOverview: overview },
    "../dashboardFormatters": {}, "../relayRanking": {}, "./Panel": {},
  });
  const tree = dashboard.ReviewDashboard({ dailyBoardPromotion: null, latestTradeDate: date });
  assert.equal(tree.props.children[0].type, overview);
  assert.equal(tree.props.children[0].props.endDate, date);
  assert.equal(tree.props.children[1].props.latestTradeDate, date);
  const keys = tree.props.children.filter((child: any) => child?.key).map((child: any) => child.key);
  assert.equal(new Set(keys).size, keys.length);
});
