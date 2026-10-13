import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import * as jsxRuntime from "react/jsx-runtime";
import ts from "typescript";
import * as asyncResource from "../src/utils/asyncResource.ts";

const require = createRequire(import.meta.url);
const compiled = ts.transpileModule(readFileSync(new URL("../src/components/ResourceSection.tsx", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
}).outputText;
let retry: (() => void) | undefined;
const observe = (factory: (...args: any[]) => unknown) => (...args: any[]) => {
  if (args[1]?.["aria-label"] === "重新加载股票列表") retry = args[1].onClick;
  return factory(...args);
};
const module = { exports: {} as any };
new Function("require", "module", "exports", compiled)((id: string) => {
  if (id === "../utils/asyncResource") return asyncResource;
  if (id === "react/jsx-runtime") return { ...jsxRuntime, jsx: observe(jsxRuntime.jsx), jsxs: observe(jsxRuntime.jsxs) };
  return require(id);
}, module, module.exports);
function render(data: string[] | null, loading: boolean, error: string | null, reload = () => {}) {
  retry = undefined;
  return renderToStaticMarkup(createElement(module.exports.ResourceSection, {
    label: "股票列表", resource: { data, loading, error, reload },
    children: (rows: string[]) => createElement("div", { "data-testid": "rows" }, rows.join(" / ") || "列表为空"),
  }));
}

test("retained stock lists keep their content and stable status slot during updating and failure", () => {
  for (const [loading, error] of [[false, null], [true, null], [false, "请求超时"]] as const) {
    let reloads = 0;
    const html = render(["2026-10-13 股票"], loading, error, () => { reloads++; });
    assert.ok(html.includes("2026-10-13 股票"));
    assert.ok(html.includes('class="resource-refresh-status"'));
    assert.ok(!html.includes('class="resource-notice"'));
    if (loading) assert.ok(html.includes("正在更新，显示上次结果"));
    if (error) {
      assert.ok(html.includes("更新失败，保留上次结果"));
      assert.ok(html.includes('title="请求超时"'));
      assert.ok(retry);
      retry();
      assert.equal(reloads, 1);
    } else assert.equal(retry, undefined);
  }
});

test("initial loads stay distinct from a verified empty stock list", () => {
  const pending = render(null, true, null);
  assert.ok(pending.includes("正在加载"));
  assert.ok(!pending.includes('data-testid="rows"'));
  const empty = render([], false, null);
  assert.ok(empty.includes("列表为空"));
  assert.ok(!empty.includes("正在加载"));
});
