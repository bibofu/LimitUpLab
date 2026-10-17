import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import ts from "typescript";
import * as quality from "../src/utils/agentQuality.ts";
import { requestJson } from "../src/utils/jsonRequest.ts";

test("quality transport scopes credentials to stable encoded run IDs and writes nullable votes", async () => {
  const calls: { url: string; init: RequestInit }[] = [];
  const client = quality.createQualityClient((path, init) => requestJson(path, init, async (url, options) => {
    calls.push({ url: String(url), init: options! });
    return Response.json({ feedback: quality.emptyFeedback(), runs: {} });
  }));
  await client.feedback("run/a", quality.emptyFeedback());
  await client.session("session/a");
  await client.delivery("run/a", "displayed", "digest");
  assert.equal(calls[0].url, "/api/agents/chat/runs/run%2Fa/feedback");
  assert.equal(calls[0].init.credentials, "include");
  assert.equal(calls[0].init.method, "PUT");
  assert.deepEqual(JSON.parse(calls[0].init.body as string), { vote: null, reasons: [], comment: "" });
  assert.equal(calls[1].url, "/api/agents/chat/sessions/session%2Fa/quality");
  assert.deepEqual(JSON.parse(calls[2].init.body as string), { event: "displayed", answer_hash: "digest" });
});

test("answer fingerprints preserve UTF-8 content and whitespace", async () => {
  assert.equal(await quality.answerHash("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  assert.notEqual(await quality.answerHash("证据"), await quality.answerHash("证据 "));
});

const require = createRequire(import.meta.url);
function harness(file: string, imports: Record<string, unknown>, entry: string, initialProps: any) {
  const source = readFileSync(new URL(file, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020,
  } }).outputText;
  const state: any[] = [];
  const effects: (() => void)[] = [];
  const cleanups = new Map<number, () => void>();
  let cursor = 0, dirty = false;
  const hooks = {
    useState(initial: unknown) {
      const index = cursor++;
      if (!(index in state)) state[index] = initial;
      return [state[index], (value: any) => { state[index] = typeof value === "function" ? value(state[index]) : value; dirty = true; }];
    },
    useRef(initial: unknown) { const index = cursor++; if (!(index in state)) state[index] = { current: initial }; return state[index]; },
    useEffect(effect: () => any, deps: unknown[]) {
      const index = cursor++;
      if (!(index in state) || deps.some((value, i) => value !== state[index][i])) effects.push(() => {
        cleanups.get(index)?.(); const cleanup = effect(); if (typeof cleanup === "function") cleanups.set(index, cleanup);
      });
      state[index] = deps;
    },
  };
  const module = { exports: {} as any };
  new Function("require", "module", "exports", compiled)((id: string) => id === "react" ? hooks : imports[id] ?? require(id), module, module.exports);
  let props = initialProps, result: any, nodes: any[];
  function render() {
    do {
      cursor = 0; dirty = false; nodes = [];
      result = module.exports[entry](props);
      const walk = (node: any) => { if (Array.isArray(node)) return node.forEach(walk); if (node?.props) { nodes.push(node); walk(node.props.children); } };
      walk(result); effects.splice(0).forEach(effect => effect());
    } while (dirty);
    return result;
  }
  render();
  return { render, get result() { return result; }, update(next: any) { props = next; render(); },
    find(predicate: (node: any) => boolean) { const node = nodes.find(predicate); assert.ok(node); return node.props; },
    async settle() { await new Promise(resolve => setImmediate(resolve)); render(); },
    unmount() { cleanups.forEach(cleanup => cleanup()); },
  };
}

test("feedback restores, blocks duplicate clicks, preserves failure retry and toggles withdrawal", async () => {
  const calls: quality.AnswerFeedbackValue[] = [];
  let resolve!: (value: any) => void;
  let reject!: (value: Error) => void;
  const app = harness("../src/components/AnswerFeedback.tsx", {
    "../utils/agentQuality": quality,
    "../qualityApi": { qualityApi: { feedback: (_: string, value: quality.AnswerFeedbackValue) => {
      calls.push(value); return new Promise((yes, no) => { resolve = yes; reject = no; });
    } } },
  }, "AnswerFeedback", { runId: "r", initial: { vote: "down", reasons: ["incomplete"], comment: "漏了第二问" }, ready: true });
  const up = () => app.find(node => node.type === "button" && node.props.children?.[1] === "有帮助");
  assert.equal(up()["aria-pressed"], false);
  up().onClick(); up().onClick(); app.render();
  assert.equal(calls.length, 1); assert.equal(up().disabled, true);
  reject(new Error("offline")); await app.settle();
  app.find(node => node.type === "button" && node.props.children === "重试").onClick();
  assert.deepEqual(calls[1], calls[0]);
  resolve({ feedback: calls[1] }); await app.settle();
  assert.equal(up()["aria-pressed"], true);
  assert.deepEqual(calls[1], { vote: "up", reasons: [], comment: "" });
  up().onClick(); resolve({ feedback: calls[2] }); await app.settle();
  assert.equal(up()["aria-pressed"], false); assert.equal(calls[2].vote, null);
  app.unmount();
});

test("session quality ignores a slow response from a previously selected session", async () => {
  const responses = new Map<string, (value: unknown) => void>();
  const app = harness("../src/hooks/useAnswerQuality.ts", {
    "../utils/agentQuality": quality,
    "../qualityApi": { qualityApi: { session: (id: string) => new Promise(resolve => responses.set(id, resolve)) } },
  }, "useAnswerQuality", "a");
  app.update("b");
  responses.get("b")!({ runs: { b: { feedback: quality.emptyFeedback() } } }); await app.settle();
  responses.get("a")!({ runs: { a: { feedback: { vote: "up" } } } }); await app.settle();
  assert.deepEqual(Object.keys(app.result.runs), ["b"]);
  assert.equal(app.result.ready, true);
  app.unmount();
});
