import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test, { type TestContext } from "node:test";
import ts from "typescript";
import * as interaction from "../src/utils/agentChatInteraction.ts";
import * as transport from "../src/utils/agentChatTransport.ts";
import * as answerBuffer from "../src/utils/agentAnswerBuffer.ts";
import * as jsonRequest from "../src/utils/jsonRequest.ts";

// Drive the real component's event handlers and async state without a browser dependency.
const require = createRequire(import.meta.url);
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
    "../utils/agentChatTransport": transport, "../utils/agentAnswerBuffer": answerBuffer,
    "../utils/jsonRequest": jsonRequest,
    "./AgentAnswerMarkdown": { AgentAnswerMarkdown: () => null },
    "./AgentChatProgress": { AgentChatProgress: () => null },
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
    input() { return this.find(node => node.type === "textarea"); },
    type(value: string) { this.input().onChange({ target: { value } }); render(); },
    submit() { this.find(node => node.type === "form" && node.props.className === "agent-chat-input").onSubmit({ preventDefault() {} }); },
  };
}

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
  assert.equal(app.has(node => node.props["aria-label"] === "新会话提示"), false);
});

test("verified empty conversations show a restrained invitation and accept input", async t => {
  const app = mount(t);
  assert.equal(app.has(node => node.props["aria-label"] === "新会话提示"), false);
  await app.settle();
  assert.equal(app.has(node => node.type === "strong" && node.props.children === "开始一段研究对话"), true);
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
  assert.equal(app.has(node => node.props.className === "chat-cancel"), false);
  app.type("下一条\n尚未发送");
  assert.equal(app.find(node => node.props["aria-label"] === "发送问题").disabled, false);
  app.find(node => node.type === "button" && Array.isArray(node.props.children) && node.props.children.includes("重试上一个问题")).onClick();
  await app.settle();
  assert.equal(requests.length, 2);
  assert.deepEqual(requests[1], requests[0]);
  assert.equal(app.input().value, "下一条\n尚未发送");
  assert.equal(app.has(node => node.props.role === "alert"), false);
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

test("suggested questions keep an unsent draft and failed sidebar refresh keeps the completed answer", async t => {
  const requests: any[] = [];
  let reads = 0;
  const app = mount(t, {
    fetchChatSessions: async () => {
      if (++reads > 1) throw new Error("会话列表暂不可用");
      return { sessions: [session("a")] };
    },
    streamAgentChatMessage: async (payload: any) => { requests.push(payload); return final; },
  });
  await app.settle();
  app.type("另一个尚未发送的问题");
  app.find(node => node.type === "button" && node.props.children === "总结最新交易日的首板结构").onClick();
  await app.settle();
  assert.equal(requests[0].message, "总结最新交易日的首板结构");
  assert.equal(app.input().value, "另一个尚未发送的问题");
  assert.equal(app.has(node => node.props.content === "研究回答"), true);
  assert.equal(app.has(node => node.type === "button" && Array.isArray(node.props.children) && node.props.children.includes("重试上一个问题")), false);
  assert.equal(app.find(node => node.props["aria-label"] === "发送问题").disabled, false);
});

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
