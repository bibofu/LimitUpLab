import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test, { type TestContext } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import { MemoryRouter } from "react-router-dom";
import remarkGfm from "remark-gfm";
import ts from "typescript";
import * as interaction from "../src/utils/agentChatInteraction.ts";
import * as transport from "../src/utils/agentChatTransport.ts";
import * as jsonRequest from "../src/utils/jsonRequest.ts";
import * as verifiedReveal from "../src/utils/verifiedAnswerReveal.ts";

// Drive the real component's event handlers and async state without a browser dependency.
const require = createRequire(import.meta.url);
const markdownModule = { exports: {} as any };
const markdownCompiled = ts.transpileModule(readFileSync(new URL("../src/components/AgentAnswerMarkdown.tsx", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020, esModuleInterop: true },
}).outputText;
new Function("require", "module", "exports", markdownCompiled)((id: string) => {
  if (id === "react-markdown") return ReactMarkdown;
  if (id === "remark-gfm") return remarkGfm;
  if (id === "../utils/verifiedAnswerReveal") return verifiedReveal;
  return require(id);
}, markdownModule, markdownModule.exports);
const compiled = ts.transpileModule(readFileSync(new URL("../src/components/AgentChatDock.tsx", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
}).outputText;
const session = (id: string) => ({ session_id: id, title: `会话 ${id}`, updated_at: "2026-10-13T10:00:00", messages: [] });
const final = { answer: "研究回答", stock_mentions: [], suggested_questions: [], task_status: "complete" };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function mount(t: TestContext, overrides: Record<string, any> = {}, savedSessionId?: string) {
  const values: any[] = [];
  const effects: (() => unknown)[] = [];
  const storage = new Map<string, string>();
  if (savedSessionId) storage.set("limituplab.activeChatSession", savedSessionId);
  const globals: Record<string, unknown> = {
    window: { localStorage: { getItem: (key: string) => storage.get(key), setItem: (key: string, value: string) => storage.set(key, value) }, confirm: () => true },
    requestAnimationFrame: () => 1,
    cancelAnimationFrame: () => {},
  };
  for (const [key, value] of Object.entries(globals)) {
    const original = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, { value, configurable: true });
    t.after(() => {
      if (original) Object.defineProperty(globalThis, key, original);
      else Reflect.deleteProperty(globalThis, key);
    });
  }
  let cursor = 0;
  const hooks = {
    useState(initial: any) {
      const index = cursor++;
      if (!(index in values)) values[index] = initial;
      return [values[index], (next: any) => { values[index] = typeof next === "function" ? next(values[index]) : next; }];
    },
    useRef(initial: any) {
      const index = cursor++;
      if (!(index in values)) values[index] = { current: initial };
      return values[index];
    },
    useCallback(callback: (...args: any[]) => any, deps: unknown[]) {
      const index = cursor++;
      if (!(index in values) || deps.some((value, position) => value !== values[index].deps[position])) values[index] = { callback, deps };
      return values[index].callback;
    },
    useEffect(effect: () => unknown, deps: unknown[]) {
      const index = cursor++;
      if (!(index in values) || deps.some((value, position) => value !== values[index][position])) effects.push(effect);
      values[index] = deps;
    },
  };
  const api = {
    fetchChatSessions: async () => ({ sessions: [session("a"), session("b")] }),
    fetchChatSession: async (id: string) => session(id),
    createChatSession: async () => session("new"),
    streamAgentChatMessage: async () => final,
    ...overrides,
  };
  if (!api.fetchChatBootstrap) api.fetchChatBootstrap = async (savedId?: string) => {
    const response = await api.fetchChatSessions();
    const target = response.sessions.find((item: any) => item.session_id === savedId) ?? response.sessions[0];
    return { ...response, selected_session: target ? await api.fetchChatSession(target.session_id) : null };
  };
  const imports: Record<string, any> = {
    react: hooks, "../api": api, "../utils/agentChatInteraction": interaction,
    "../utils/agentChatTransport": transport,
    "../utils/jsonRequest": jsonRequest,
    "./AgentAnswerMarkdown": markdownModule.exports,
    "./AgentChatProgress": { AgentChatProgress: () => null },
    "./AnswerFeedback": { AnswerFeedback: () => null },
    "../hooks/useAnswerQuality": { useAnswerQuality: () => ({ runs: {}, ready: true, error: false }), useAnswerDelivery: () => () => {} },
    "../qualityApi": { qualityApi: { delivery: overrides.qualityDelivery ?? (async () => {}) } },
    "../utils/agentQuality": { answerHash: async () => null },
  };
  const module = { exports: {} as any };
  new Function("require", "module", "exports", compiled)((id: string) => imports[id] ?? require(id), module, module.exports);
  let nodes: any[] = [];
  function render() {
    cursor = 0;
    nodes = [];
    function walk(node: any) {
      if (Array.isArray(node)) { node.forEach(walk); return; }
      if (!node?.props) return;
      nodes.push(node);
      walk(node.props.children);
    }
    walk(module.exports.AgentChatDock({ tradeDate: "2026-10-13" }));
    effects.splice(0).forEach(effect => effect());
  }
  render();
  return {
    async settle() { await new Promise(resolve => setImmediate(resolve)); render(); },
    render,
    find(predicate: (node: any) => boolean) {
      const node = nodes.find(predicate);
      assert.ok(node, "expected a matching rendered control");
      return node.props;
    },
    has(predicate: (node: any) => boolean) { return nodes.some(predicate); },
    answers() { return nodes.filter(node => node.type === markdownModule.exports.AgentAnswerMarkdown); },
    answerHtml() {
      return renderToStaticMarkup(createElement(MemoryRouter, {}, ...this.answers().map(node =>
        createElement(node.type, { ...node.props, animate: false }))));
    },
    input() { return this.find(node => node.type === "textarea"); },
    type(value: string) { this.input().onChange({ target: { value } }); render(); },
    submit() { this.find(node => node.type === "form" && node.props.className === "agent-chat-input").onSubmit({ preventDefault() {} }); },
  };
}

test("the compact composer grows with the draft and shrinks after clearing without a help row", async t => {
  const app = mount(t);
  await app.settle();
  assert.equal(app.input().rows, 1);
  assert.equal(app.input()["aria-describedby"], undefined);
  assert.equal(app.has(node => node.props.className === "chat-input-help"), false);
  const input = { style: { height: "" }, scrollHeight: 88 };
  app.input().ref.current = input;
  app.type("第一行\n第二行\n第三行");
  assert.equal(input.style.height, "90px");
  input.scrollHeight = 240;
  app.type("更长的多行草稿");
  assert.equal(input.style.height, "160px");
  input.scrollHeight = 40;
  app.type("");
  assert.equal(input.style.height, "42px");
});

test("reload restores verified history with one bootstrap call and no list/detail waterfall", async t => {
  const pending = deferred<any>();
  const calls: string[] = [];
  const saved = { ...session("saved"), messages: [{
    message_id: "old-message", role: "assistant", content: "已验证的历史回答", status: "success", metadata: {},
  }] };
  const app = mount(t, {
    fetchChatBootstrap: (savedId: string) => { calls.push(`bootstrap:${savedId}`); return pending.promise; },
    fetchChatSessions: () => { throw new Error("unexpected list request"); },
    fetchChatSession: () => { throw new Error("unexpected detail request"); },
    createChatSession: () => { throw new Error("unexpected create request"); },
  }, "saved");
  assert.equal(app.input().disabled, true);
  assert.equal(app.has(node => node.props.content === "已验证的历史回答"), false);
  pending.resolve({ sessions: [session("saved")], selected_session: saved });
  await app.settle();
  assert.deepEqual(calls, ["bootstrap:saved"]);
  assert.equal(app.input().disabled, false);
  assert.equal(app.has(node => node.props.content === "已验证的历史回答"), true);
  assert.equal(app.answers()[0].props.animate, false);
  assert.equal(app.has(node => node.props["aria-label"] === "新会话提示"), false);
});

test("verified empty conversations show a restrained invitation and accept input", async t => {
  const app = mount(t);
  assert.equal(app.has(node => node.props["aria-label"] === "新会话提示"), false);
  await app.settle();
  assert.equal(app.has(node => node.type === "strong" && node.props.children === "开始一段研究对话"), true);
  assert.equal(app.has(node => node.props.className === "agent-chat-prompts"), false);
  assert.equal(app.input().placeholder, "问今日市场情况、个股详情等");
  assert.equal(app.input().disabled, false);
  app.type("研究最近的首板结构");
  assert.equal(app.find(node => node.props["aria-label"] === "发送问题").disabled, false);
  app.submit();
  app.render();
  assert.equal(app.has(node => node.props["aria-label"] === "新会话提示"), false);
  await app.settle();
});

test("a missing chat service displays a Chinese retry message without implying an empty history", async t => {
  let calls = 0;
  const app = mount(t, {
    fetchChatBootstrap: async () => {
      if (++calls === 1) return jsonRequest.requestJson("/bootstrap", {}, async () => (
        Response.json({ detail: "Not Found" }, { status: 404 })
      ));
      return { sessions: [session("restored")], selected_session: session("restored") };
    },
  });
  await app.settle();
  assert.equal(app.has(node => node.type === "span" && node.props.children === "会话服务暂不可用，请重新加载会话。"), true);
  assert.equal(app.has(node => node.props.children === "Not Found"), false);
  assert.equal(app.has(node => node.props["aria-label"] === "新会话提示"), false);
  assert.equal(app.input().disabled, true);
  const retry = app.find(node => node.type === "button" && Array.isArray(node.props.children) && node.props.children.includes("重新加载会话"));
  assert.equal(retry.disabled, false);
  retry.onClick();
  await app.settle();
  assert.equal(calls, 2);
  assert.equal(app.input().disabled, false);
  assert.equal(app.has(node => node.props.role === "alert"), false);
  assert.equal(app.has(node => node.props["aria-label"] === "新会话提示"), true);
});

for (const [status, message] of [
  [403, "暂时无法访问这段会话，请重新加载会话。"],
  [429, "会话请求较多，请稍后重试。"],
  [503, "会话服务暂时异常，请稍后重试。"],
] as const) {
  test(`session errors use structured HTTP ${status} even when all server messages are identical`, async t => {
    const app = mount(t, {
      fetchChatBootstrap: () => jsonRequest.requestJson("/bootstrap", {}, async () => (
        Response.json({ detail: "Not Found" }, { status })
      )),
    });
    await app.settle();
    assert.equal(app.has(node => node.type === "span" && node.props.children === message), true);
    assert.equal(app.has(node => node.props.children === "Not Found"), false);
    assert.equal(app.has(node => node.props["aria-label"] === "新会话提示"), false);
  });
}

test("a stale saved ID accepts only the owner-verified selection returned by bootstrap", async t => {
  const app = mount(t, {
    fetchChatBootstrap: async () => ({ sessions: [session("current-owner")], selected_session: session("current-owner") }),
    fetchChatSession: () => { throw new Error("must not fetch the stale saved ID"); },
  }, "previous-owner");
  await app.settle();
  assert.equal(app.input().disabled, false);
  assert.equal(app.has(node => node.type === "strong" && node.props.children === "会话 current-owner"), true);
  assert.equal(app.has(node => node.type === "strong" && node.props.children === "会话 previous-owner"), false);
});

test("first visit creates a conversation only after the identity bootstrap has completed", async t => {
  const pending = deferred<any>();
  let created = 0;
  const app = mount(t, {
    fetchChatBootstrap: () => pending.promise,
    createChatSession: async () => { created++; return session("new"); },
  });
  assert.equal(created, 0);
  pending.resolve({ sessions: [], selected_session: null });
  await app.settle();
  assert.equal(created, 1);
  assert.equal(app.input().disabled, false);
});

test("initial load failure offers a working recovery control without reloading the page", async t => {
  let reads = 0;
  const app = mount(t, { fetchChatSessions: async () => {
    if (++reads === 1) throw new Error("会话服务暂不可用");
    return { sessions: [session("a")] };
  } });
  await app.settle();
  assert.equal(app.input().disabled, true);
  app.find(node => node.type === "button" && Array.isArray(node.props.children) && node.props.children.includes("重新加载会话")).onClick();
  await app.settle();
  assert.equal(app.input().disabled, false);
  assert.equal(reads, 2);
  assert.equal(app.has(node => node.props.role === "alert"), false);
});

test("rapid session selections cannot overlap or send into a loading conversation", async t => {
  const pending = deferred<ReturnType<typeof session>>();
  const reads: string[] = [];
  let sends = 0;
  const app = mount(t, {
    fetchChatSession: async (id: string) => { reads.push(id); return id === "b" ? pending.promise : session(id); },
    streamAgentChatMessage: async () => { sends++; return final; },
  });
  await app.settle();
  app.type("保留在会话 A 的草稿");
  const chooseB = app.find(node => node.type === "button" && node.props.className === "chat-session-select" && node.props.children[0].props.children[0].props.children === "会话 b");
  chooseB.onClick();
  chooseB.onClick();
  app.submit();
  app.render();
  assert.equal(app.input().disabled, true);
  assert.equal(sends, 0);
  assert.deepEqual(reads, ["a", "b"]);
  pending.resolve(session("b"));
  await app.settle();
  assert.equal(app.input().value, "");
  app.find(node => node.type === "button" && node.props.className === "chat-session-select" && node.props.children[0].props.children[0].props.children === "会话 a").onClick();
  await app.settle();
  assert.equal(app.input().value, "保留在会话 A 的草稿");
});

test("stream failure restores send, retries the original request, and preserves the next draft", async t => {
  const requests: any[] = [];
  const app = mount(t, { streamAgentChatMessage: async (payload: any, event: any) => {
    requests.push(payload);
    event({ event: "accepted", data: { run_id: "run-1" } });
    if (requests.length === 1) throw new Error("连接中断");
    return final;
  } });
  await app.settle();
  app.type("第一个问题");
  app.submit();
  await app.settle();
  assert.equal(app.has(node => node.props.className === "icon-button chat-cancel"), false);
  app.type("下一条\n尚未发送");
  assert.equal(app.find(node => node.props["aria-label"] === "发送问题").disabled, false);
  app.find(node => node.type === "button" && Array.isArray(node.props.children) && node.props.children.includes("重试上一个问题")).onClick();
  await app.settle();
  assert.equal(requests.length, 2);
  assert.deepEqual(requests[1], requests[0]);
  assert.equal(app.input().value, "下一条\n尚未发送");
  assert.equal(app.has(node => node.props.role === "alert"), false);
});

test("disconnect receipts bind only to this submission's accepted run across session switches", async t => {
  const receipts: unknown[][] = [];
  let requests = 0;
  const app = mount(t, {
    qualityDelivery: async (...values: unknown[]) => { receipts.push(values); throw new Error("receipt offline"); },
    streamAgentChatMessage: async (_payload: unknown, event: any) => {
      requests++;
      if (requests !== 2) event({ event: "accepted", data: { run_id: requests === 1 ? "run-a" : "run-b" } });
      throw new Error("连接中断");
    },
  });
  await app.settle(); app.type("问题 A"); app.submit(); await app.settle();
  assert.deepEqual(receipts, [["run-a", "interrupted", null]]);
  app.find(node => node.type === "button" && node.props.className === "chat-session-select" && node.props.children[0].props.children[0].props.children === "会话 b").onClick();
  await app.settle(); app.type("问题 B"); app.submit(); await app.settle();
  assert.equal(receipts.length, 1, "a rejected request without accepted cannot reuse another session's run ID");
  app.type("问题 B2"); app.submit(); await app.settle();
  assert.deepEqual(receipts, [["run-a", "interrupted", null], ["run-b", "interrupted", null]]);
  assert.equal(app.input().disabled, false, "best-effort receipt errors never block chat recovery");
});

test("user cancellation is not reported as a transport interruption", async t => {
  const pending = deferred<any>();
  const receipts: unknown[][] = [];
  const app = mount(t, {
    qualityDelivery: async (...values: unknown[]) => { receipts.push(values); },
    streamAgentChatMessage: (_payload: unknown, event: any) => { event({ event: "accepted", data: { run_id: "cancelled-run" } }); return pending.promise; },
    cancelAgentChatRun: async () => { pending.reject(new Error("cancelled stream")); },
  });
  await app.settle(); app.type("可取消的问题"); app.submit(); await app.settle();
  const cancel = app.find(node => node.props["aria-label"] === "取消任务");
  assert.equal(cancel.type, "button");
  assert.equal(cancel.title, "取消任务");
  assert.equal(cancel.children.props["aria-hidden"], "true");
  cancel.onClick(); await app.settle();
  assert.deepEqual(receipts, []);
});

test("the real input keeps IME confirmation and Shift+Enter from submitting", async t => {
  let sends = 0;
  const app = mount(t, { streamAgentChatMessage: async () => { sends++; return final; } });
  await app.settle();
  app.type("正在输入");
  const event = (overrides = {}) => ({ nativeEvent: { key: "Enter", keyCode: 13, isComposing: false, shiftKey: false, ...overrides }, preventDefault() {} });
  app.input().onCompositionStart();
  app.input().onKeyDown(event());
  app.input().onCompositionEnd();
  app.input().onKeyDown(event({ keyCode: 229 }));
  app.input().onKeyDown(event({ shiftKey: true }));
  assert.equal(sends, 0);
  app.input().onKeyDown(event());
  await app.settle();
  assert.equal(sends, 1);
});

for (const taskStatus of ["error", "cancelled"]) {
  test(`completed ${taskStatus} can restart with a new message ID without dropping the next draft`, async t => {
    const requests: any[] = [];
    const app = mount(t, { streamAgentChatMessage: async (payload: any) => {
      requests.push(payload);
      return requests.length === 1 ? { ...final, task_status: taskStatus } : final;
    } });
    await app.settle();
    app.type("需要重新执行的问题");
    app.submit();
    await app.settle();
    app.type("保留这条未发送的问题");
    app.find(node => node.type === "button" && Array.isArray(node.props.children) && node.props.children.includes("重新提问")).onClick();
    await app.settle();
    assert.equal(requests.length, 2);
    assert.equal(requests[1].message, requests[0].message);
    assert.notEqual(requests[1].message_id, requests[0].message_id);
    assert.equal(app.input().value, "保留这条未发送的问题");
    assert.equal(app.has(node => node.props.role === "alert"), false);
  });
}

test("updated default questions send their displayed text and preserve drafts and completed answers", async t => {
  const requests: any[] = [];
  let reads = 0;
  const app = mount(t, {
    fetchChatSessions: async () => {
      if (++reads > 1) throw new Error("会话列表暂不可用");
      return { sessions: [session("a")] };
    },
    fetchChatSession: async (id: string) => ({ ...session(id), messages: [{
      message_id: "old-message", role: "assistant", content: "已验证的历史回答", status: "success",
      metadata: { suggested_questions: [
        "按板块归纳最新首板候选", "这些候选的共同风险和数据缺口有哪些？", "复盘最近 5 个交易日的一进二晋级率",
      ] },
    }] }),
    streamAgentChatMessage: async (payload: any) => { requests.push(payload); return final; },
  });
  await app.settle();
  for (const prompt of ["最新的财经新闻", "市场最高标是哪个", "最新的龙虎榜情况"]) {
    assert.equal(app.has(node => node.type === "button" && node.props.children === prompt), true);
  }
  app.type("另一个尚未发送的问题");
  app.find(node => node.type === "button" && node.props.children === "最新的财经新闻").onClick();
  await app.settle();
  assert.equal(requests[0].message, "最新的财经新闻");
  assert.equal(app.has(node => node.props.className === "agent-chat-prompts"), false);
  assert.equal(app.input().value, "另一个尚未发送的问题");
  assert.equal(app.has(node => node.props.content === "研究回答"), true);
  assert.equal(app.has(node => node.type === "button" && Array.isArray(node.props.children) && node.props.children.includes("重试上一个问题")), false);
  assert.equal(app.find(node => node.props["aria-label"] === "发送问题").disabled, false);
});

const stockSuggestionSubject = "中电鑫龙（002298）";
const oldRatingQuestion = `${stockSuggestionSubject}的评分依据是什么？`;
const oldRiskQuestion = `${stockSuggestionSubject}的主要风险和数据缺口是什么？`;
const oldTrendQuestion = `${stockSuggestionSubject}最近 20 个交易日走势如何？`;
const recentTrendQuestion = `${stockSuggestionSubject}的近期走势如何？`;
const customStockQuestions = [`${stockSuggestionSubject}近期成交量如何变化？`, "对比同板块历史表现"];
for (const [label, savedQuestions, expectedQuestions] of [
  ["all stock defaults", [oldRatingQuestion, oldRiskQuestion, oldTrendQuestion], [recentTrendQuestion]],
  ["rating follow-ups", [oldRiskQuestion, oldTrendQuestion, "按板块归纳最新首板候选"], [recentTrendQuestion]],
  ["risk follow-ups", [oldRatingQuestion, oldTrendQuestion, "按板块归纳最新首板候选"], [recentTrendQuestion]],
  ["custom stock follow-ups", customStockQuestions, customStockQuestions],
] as const) {
  test(`restored ${label} preserve the intended questions and submit without losing drafts`, async t => {
    const requests: any[] = [];
    const app = mount(t, {
      fetchChatSession: async (id: string) => ({ ...session(id), messages: [{
        message_id: "stock-history", role: "assistant", content: "中电鑫龙历史研究回答", status: "success",
        metadata: {
          stock_mentions: [{ name: "中电鑫龙", symbol: "002298", trade_date: "2026-10-16" }],
          suggested_questions: savedQuestions,
        },
      }] }),
      streamAgentChatMessage: async (payload: any) => { requests.push(payload); return final; },
    });
    await app.settle();
    const prompts = app.find(node => node.props.className === "agent-chat-prompts").children;
    assert.deepEqual(prompts.map((node: any) => node.props.children), expectedQuestions);
    app.type("保留这条尚未发送的草稿");
    app.find(node => node.type === "button" && node.props.children === expectedQuestions[0]).onClick();
    await app.settle();
    assert.equal(requests.length, 1);
    assert.equal(requests[0].message, expectedQuestions[0]);
    assert.equal(app.input().value, "保留这条尚未发送的草稿");
    assert.equal(app.has(node => node.props.content === "中电鑫龙历史研究回答"), true);
  });
}

test("a deleted active session cannot receive questions when replacement loading fails", async t => {
  let deleted = false;
  let failReplacement = true;
  const app = mount(t, {
    deleteChatSession: async () => { deleted = true; },
    fetchChatSessions: async () => ({ sessions: deleted ? [session("b")] : [session("a"), session("b")] }),
    fetchChatSession: async (id: string) => {
      if (id === "b" && failReplacement) throw new Error("会话恢复失败");
      return session(id);
    },
  });
  await app.settle();
  app.find(node => node.props["aria-label"] === "删除会话 会话 a").onClick();
  await app.settle();
  assert.equal(deleted, true);
  assert.equal(app.input().disabled, true);
  failReplacement = false;
  app.find(node => node.type === "button" && Array.isArray(node.props.children) && node.props.children.includes("重新加载会话")).onClick();
  await app.settle();
  assert.equal(app.input().disabled, false);
  assert.equal(app.has(node => node.type === "strong" && node.props.children === "会话 b"), true);
});

function liveStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const response = new Response(new ReadableStream<Uint8Array>({ start(value) { controller = value; } }), {
    headers: { "X-Agent-Run-Id": "run-live" },
  });
  return {
    response,
    send(id: number, event: string, data: unknown) {
      controller.enqueue(new TextEncoder().encode(`id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
    },
    close() { controller.close(); },
  };
}

const hotRankingResponse = {
  ...final, run_id: "run-live", session_id: "a",
  answer: "已核对热股榜。\n\n| 排名 | 代码 | 名称 |\n| --- | --- | --- |\n| 1 | 600101 | 样例科技 |\n\n榜单仅反映关注度。",
  stock_mentions: [{ name: "样例科技", symbol: "600101", trade_date: "2026-10-15" }],
};

test("real SSE keeps candidate prose private and publishes one complete table with stock links", async t => {
  const stream = liveStream();
  const app = mount(t, {
    streamAgentChatMessage: (payload: any, onEvent: any) => transport.streamChat("", payload, onEvent, async () => stream.response),
  });
  await app.settle();
  app.type("当前的热门股票有哪些");
  app.submit();
  stream.send(1, "answer_start", { revision: 1, provisional: true });
  stream.send(2, "answer_delta", { offset: 0, delta: "未经校验的引言" });
  stream.send(3, "progress", { stage: "checking", message: "正在核对榜单和来源" });
  await app.settle();
  assert.equal(app.answers().length, 0);
  assert.equal(app.has(node => node.props.stage === "checking" && node.props.message === "正在核对榜单和来源"), true);
  assert.equal(app.has(node => node.props.className === "icon-button chat-cancel"), true);
  stream.send(4, "answer_reset", { message: "重新整理" });
  stream.send(5, "answer_start", { revision: 2, provisional: true });
  stream.send(6, "answer_delta", { offset: 0, delta: hotRankingResponse.answer });
  await app.settle();
  assert.equal(app.answers().length, 0, "even a complete candidate stays private until completed");
  stream.send(7, "completed", hotRankingResponse);
  stream.send(8, "completed", hotRankingResponse);
  await app.settle();
  assert.equal(app.answers().length, 1);
  assert.equal(app.answers()[0].props.animate, true);
  assert.equal(app.has(node => node.props.className === "chat-task-status" && node.props.children === "研究已完成"), true);
  const html = app.answerHtml();
  assert.equal(html.split("<table>").length - 1, 1);
  assert.equal(html.split("<tr>").length - 1, 2);
  assert.ok(html.includes('<a href="/stocks/600101?'));
  assert.ok(html.includes("trade_date=2026-10-15"));
  assert.ok(html.includes(">样例科技</a>"));
  assert.ok(!html.includes("未经校验的引言"));
  assert.equal(app.has(node => node.props.stage === "checking"), false);
});

test("SSE reconnection consumes legacy draft IDs and delivers one final message without replaying drafts", async t => {
  const first = liveStream();
  const second = liveStream();
  const calls: { url: string; method: string }[] = [];
  const app = mount(t, {
    streamAgentChatMessage: (payload: any, onEvent: any) => transport.streamChat("", payload, onEvent, async (url, init) => {
      calls.push({ url: String(url), method: init?.method ?? "GET" });
      return calls.length === 1 ? first.response : second.response;
    }),
  });
  await app.settle();
  app.type("当前的热门股票有哪些");
  app.submit();
  first.send(4, "answer_delta", { offset: 0, delta: "断线前的草稿" });
  first.close();
  await app.settle();
  assert.equal(app.answers().length, 0);
  assert.deepEqual(calls, [
    { url: "/api/agents/chat/stream", method: "POST" },
    { url: "/api/agents/chat/runs/run-live/stream?after=4", method: "GET" },
  ]);
  second.send(4, "answer_delta", { offset: 0, delta: "重放草稿" });
  second.send(5, "answer_delta", { offset: 6, delta: "接续草稿" });
  second.send(6, "progress", { stage: "checking", message: "正在核对最终结果" });
  await app.settle();
  assert.equal(app.answers().length, 0);
  second.send(7, "completed", hotRankingResponse);
  await app.settle();
  assert.equal(app.answers().length, 1);
  assert.equal(app.answers()[0].props.content, hotRankingResponse.answer);
});

for (const failure of ["server-error", "disconnect", "cancelled"] as const) {
  test(`real SSE ${failure} leaves no candidate answer visible`, async t => {
    const stream = liveStream();
    let calls = 0;
    const app = mount(t, {
      cancelAgentChatRun: async () => {
        stream.send(4, "completed", { ...final, answer: "本次任务已取消。", task_status: "cancelled" });
      },
      streamAgentChatMessage: (payload: any, onEvent: any) => transport.streamChat("", payload, onEvent, async () => {
        calls++;
        return calls === 1 ? stream.response : new Response("");
      }),
    });
    await app.settle();
    app.type("当前的热门股票有哪些");
    app.submit();
    stream.send(1, "answer_start", { revision: 1, provisional: true });
    stream.send(2, "answer_delta", { offset: 0, delta: "不能显示的候选榜单" });
    stream.send(3, "progress", { stage: "tools", message: "正在查询数据" });
    await app.settle();
    assert.equal(app.answers().length, 0);
    if (failure === "cancelled") app.find(node => node.props["aria-label"] === "取消任务").onClick();
    else if (failure === "server-error") stream.send(4, "error", { message: "任务中断，请重试恢复" });
    else stream.close();
    await app.settle();
    assert.ok(!app.answerHtml().includes("不能显示的候选榜单"));
    assert.equal(app.has(node => node.props.className === "icon-button chat-cancel"), false);
    assert.equal(app.answers().length, failure === "cancelled" ? 1 : 0);
    assert.equal(app.has(node => node.props.role === "alert"), true);
    assert.equal(calls, failure === "disconnect" ? 2 : 1);
    if (failure === "cancelled") {
      assert.equal(app.answers()[0].props.content, "本次任务已取消。");
      assert.equal(app.answers()[0].props.animate, false);
      assert.equal(app.has(node => node.props.className === "chat-task-status" && node.props.children === "已取消"), true);
    }
  });
}

test("starting a new question finishes the prior reveal without changing its complete source", async t => {
  const pending = deferred<any>();
  let calls = 0;
  const app = mount(t, { streamAgentChatMessage: async () => ++calls === 1 ? hotRankingResponse : pending.promise });
  await app.settle();
  app.type("第一条问题");
  app.submit();
  await app.settle();
  assert.equal(app.answers()[0].props.animate, true);
  app.type("下一条问题");
  app.submit();
  app.render();
  assert.equal(app.answers()[0].props.animate, false);
  assert.equal(app.answers()[0].props.content, hotRankingResponse.answer);
  pending.resolve(final);
  await app.settle();
});

test("session switching ends a current reveal immediately even while the session read is pending", async t => {
  const pending = deferred<any>();
  const app = mount(t, {
    streamAgentChatMessage: async () => hotRankingResponse,
    fetchChatSession: async (id: string) => id === "b" ? pending.promise : session(id),
  });
  await app.settle();
  app.type("第一条问题");
  app.submit();
  await app.settle();
  assert.equal(app.answers()[0].props.animate, true);
  app.find(node => node.type === "button" && node.props.className === "chat-session-select" && node.props.children[0].props.children[0].props.children === "会话 b").onClick();
  app.render();
  assert.equal(app.answers()[0].props.animate, false);
  pending.resolve({ ...session("b"), messages: [{ message_id: "saved", role: "assistant", content: hotRankingResponse.answer, metadata: {}, status: "success" }] });
  await app.settle();
  assert.equal(app.answers()[0].props.animate, false);
});

test("reveal height updates follow the bottom only until the reader scrolls away", async t => {
  const app = mount(t, { streamAgentChatMessage: async () => hotRankingResponse });
  await app.settle();
  app.type("第一条问题");
  app.submit();
  await app.settle();
  const revealProgress = app.answers()[0].props.onRevealProgress;
  app.type("下一条未发送的问题");
  assert.equal(app.answers()[0].props.onRevealProgress, revealProgress, "typing must not invalidate memoized historical answers");
  const container = { scrollTop: 0, scrollHeight: 1000, clientHeight: 300 };
  const messages = app.find(node => node.props["aria-label"] === "对话消息");
  messages.ref.current = container;
  app.answers()[0].props.onRevealProgress();
  assert.equal(container.scrollTop, 1000);
  container.scrollTop = 200;
  messages.onScroll({ currentTarget: container });
  container.scrollHeight = 1400;
  app.answers()[0].props.onRevealProgress();
  assert.equal(container.scrollTop, 200);
  app.render();
  assert.equal(app.has(node => node.props.className === "chat-scroll-latest"), true);

  container.scrollTop = container.scrollHeight - container.clientHeight - 63;
  messages.onScroll({ currentTarget: container });
  app.render();
  assert.equal(app.has(node => node.props.className === "chat-scroll-latest"), false);
  container.scrollHeight = 1600;
  app.answers()[0].props.onRevealProgress();
  assert.equal(container.scrollTop, 1600, "returning near the bottom resumes following new content");

  container.scrollTop = 200;
  messages.onScroll({ currentTarget: container });
  app.render();
  const viewport = app.find(node => node.props.className === "agent-chat-viewport");
  assert.equal(viewport.children[0].props["aria-label"], "对话消息");
  assert.equal(viewport.children[1].props.className, "chat-scroll-latest", "the control stays with the message viewport rather than adding a composer row");
  app.find(node => node.props.className === "chat-scroll-latest").onClick();
  app.render();
  assert.equal(container.scrollTop, 1600);
  assert.equal(app.has(node => node.props.className === "chat-scroll-latest"), false);
});
