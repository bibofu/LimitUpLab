import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test, { type TestContext } from "node:test";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import { MemoryRouter } from "react-router-dom";
import remarkGfm from "remark-gfm";
import ts from "typescript";
import * as verifiedReveal from "../src/utils/verifiedAnswerReveal.ts";

const require = createRequire(import.meta.url);
const compiled = ts.transpileModule(readFileSync(new URL("../src/components/AgentAnswerMarkdown.tsx", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020, esModuleInterop: true },
}).outputText;

// Run the actual renderer/hooks with a deterministic animation clock and real Markdown parsing.
function mount(t: TestContext, content: string, options: { animate?: boolean; reducedMotion?: boolean } = {}) {
  let now = 0;
  let nextFrame = 0;
  const frames = new Map<number, FrameRequestCallback>();
  const motionListeners = new Set<() => void>();
  const media = {
    matches: options.reducedMotion ?? false,
    addEventListener: (_: string, listener: () => void) => motionListeners.add(listener),
    removeEventListener: (_: string, listener: () => void) => motionListeners.delete(listener),
  };
  const fakeWindow = {
    matchMedia: () => media,
    requestAnimationFrame: (callback: FrameRequestCallback) => { frames.set(++nextFrame, callback); return nextFrame; },
    cancelAnimationFrame: (id: number) => frames.delete(id),
  };
  const restoreGlobals: (() => void)[] = [];
  for (const [key, value] of Object.entries({ window: fakeWindow, performance: { now: () => now } })) {
    const original = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, { value, configurable: true });
    restoreGlobals.push(() => { if (original) Object.defineProperty(globalThis, key, original); else Reflect.deleteProperty(globalThis, key); });
  }
  const values: any[] = [];
  const cleanups = new Map<number, () => void>();
  const effects: (() => void)[] = [];
  let cursor = 0;
  let dirty = false;
  let updates = 0;
  let completions = 0;
  const hooks = {
    ...React,
    memo: (component: any) => component,
    useState(initial: any) {
      const index = cursor++;
      if (!(index in values)) values[index] = typeof initial === "function" ? initial() : initial;
      return [values[index], (value: any) => { values[index] = typeof value === "function" ? value(values[index]) : value; dirty = true; }];
    },
    useRef(initial: any) {
      const index = cursor++;
      if (!(index in values)) values[index] = { current: initial };
      return values[index];
    },
    useEffect(effect: () => void | (() => void), deps: unknown[]) {
      const index = cursor++;
      if (!(index in values) || deps.some((value, position) => value !== values[index][position])) {
        effects.push(() => {
          cleanups.get(index)?.();
          const cleanup = effect();
          if (cleanup) cleanups.set(index, cleanup);
          else cleanups.delete(index);
        });
      }
      values[index] = deps;
    },
  };
  const imports: Record<string, any> = {
    react: hooks, "react-markdown": ReactMarkdown, "remark-gfm": remarkGfm,
    "../utils/verifiedAnswerReveal": verifiedReveal,
  };
  const module = { exports: {} as any };
  new Function("require", "module", "exports", compiled)((id: string) => imports[id] ?? require(id), module, module.exports);
  let props = { content, stockMentions: [{ symbol: "600101", name: "样例科技", trade_date: "2026-10-15" }], animate: options.animate ?? true, onRevealProgress: () => { updates++; }, onRevealComplete: () => { completions++; } };
  let tree: any;
  let html = "";
  function render() {
    do {
      dirty = false;
      cursor = 0;
      tree = module.exports.AgentAnswerMarkdown(props);
      html = renderToStaticMarkup(React.createElement(MemoryRouter, {}, tree));
      effects.splice(0).forEach(effect => effect());
    } while (dirty);
  }
  render();
  const unmount = () => { cleanups.forEach(cleanup => cleanup()); cleanups.clear(); };
  t.after(() => { unmount(); restoreGlobals.forEach(restore => restore()); });
  return {
    get html() { return html; },
    get updates() { return updates; },
    get completions() { return completions; },
    get linkRenderer() { return tree.props.children[0].props.components.a; },
    get scheduled() { return frames.size; },
    advance(milliseconds: number) {
      now += milliseconds;
      const pending = [...frames.values()];
      frames.clear();
      pending.forEach(callback => callback(now));
      render();
    },
    update(next: Partial<typeof props>) { props = { ...props, ...next }; render(); },
    reduceMotion() { media.matches = true; motionListeners.forEach(listener => listener()); render(); },
    showAll() {
      const button = tree.props.children.find((child: any) => child?.type === "button");
      assert.ok(button, "a progressive answer offers immediate full display");
      button.props.onClick();
      render();
    },
    unmount,
  };
}

test("new approved prose grows monotonically and completes within the display budget without callback resets", t => {
  const text = "已经核对的研究结果。".repeat(100);
  const app = mount(t, text);
  assert.equal(app.completions, 0, "a partially revealed answer cannot acknowledge complete display");
  assert.ok(app.html.includes("立即显示全部"));
  assert.ok(!app.html.includes(text));
  let previous = app.html.split("</p>")[0];
  for (let frame = 1; frame <= 29; frame++) {
    app.advance(80);
    const current = app.html.split("</p>")[0];
    assert.ok(current.startsWith(previous), "already visible approved text never changes or disappears");
    previous = current;
    app.update({ onRevealProgress: () => {} });
  }
  app.advance(80);
  assert.ok(app.html.includes(text));
  assert.ok(!app.html.includes("立即显示全部"));
  assert.equal(app.scheduled, 0);
  assert.equal(app.completions, 1, "the full committed answer acknowledges display once");
});

test("tables reveal whole rows with grounded links and show-all stops the pending animation", t => {
  const content = "| 排名 | 名称 |\n| --- | --- |\n| 1 | 样例科技 |\n| 2 | 样例制造 |\n| 3 | 样例能源 |";
  const app = mount(t, content);
  const linkRenderer = app.linkRenderer;
  assert.equal(app.html.split("<tr>").length - 1, 2);
  assert.ok(app.html.includes('href="/stocks/600101?'));
  assert.ok(app.html.includes(">样例科技</a>"));
  assert.ok(!app.html.includes("样例制造"));
  app.advance(150);
  assert.equal(app.linkRenderer, linkRenderer, "already revealed links retain their component identity and focus");
  assert.equal(app.html.split("<tr>").length - 1, 3);
  app.showAll();
  assert.equal(app.html.split("<tr>").length - 1, 4);
  assert.equal(app.scheduled, 0);
  assert.ok(!app.html.includes("立即显示全部"));
});

for (const options of [{ animate: false }, { reducedMotion: true }]) {
  test(`history or reduced-motion starts with the complete approved answer: ${JSON.stringify(options)}`, t => {
    const text = "完整研究结论。".repeat(20);
    const app = mount(t, text, options);
    assert.ok(app.html.includes(text));
    assert.ok(!app.html.includes("立即显示全部"));
    assert.equal(app.scheduled, 0);
  });
}

test("turning on reduced motion finishes an active display and stops all scheduled frames", t => {
  const text = "研究结论。".repeat(80);
  const app = mount(t, text);
  app.reduceMotion();
  assert.ok(app.html.includes(text));
  assert.equal(app.scheduled, 0);
});

test("ending a reveal expands the same source; changing content and unmounting cancel old frames", t => {
  const first = "第一份完整结论。".repeat(50);
  const next = "第二份完整结论。".repeat(40);
  const app = mount(t, first);
  app.advance(80);
  assert.ok(app.updates > 1, "height changes notify the chat's scrolling policy");
  app.update({ animate: false });
  assert.ok(app.html.includes(first));
  assert.equal(app.scheduled, 0);
  app.update({ content: next, animate: true });
  assert.ok(!app.html.includes(first));
  assert.ok(!app.html.includes(next));
  assert.equal(app.scheduled, 1);
  app.unmount();
  assert.equal(app.scheduled, 0);
});

for (const length of [20000, 20001]) {
  test(`the character budget boundary at ${length} avoids repeated parsing of large approved answers`, t => {
    const text = "研".repeat(length);
    const app = mount(t, text);
    assert.equal(app.html.includes(text), length > 20000);
    assert.equal(app.html.includes("立即显示全部"), length <= 20000);
    assert.equal(app.scheduled, length <= 20000 ? 1 : 0);
  });
}

for (const lines of [400, 401]) {
  test(`the line budget boundary at ${lines} bypasses large tables even below the character limit`, t => {
    const rows = Array.from({ length: lines - 2 }, (_, index) => `| ${index + 1} |`);
    const content = ["| 排名 |", "| --- |", ...rows].join("\n");
    const app = mount(t, content);
    assert.ok(content.length < 20000);
    assert.equal(app.html.split("<tr>").length - 1, lines > 400 ? lines - 1 : 2);
    assert.equal(app.html.includes("立即显示全部"), lines <= 400);
    assert.equal(app.scheduled, lines <= 400 ? 1 : 0);
  });
}
