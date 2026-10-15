import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import { MemoryRouter } from "react-router-dom";
import remarkGfm from "remark-gfm";
import ts from "typescript";
import * as verifiedReveal from "../src/utils/verifiedAnswerReveal.ts";

// Exercise the actual component and Markdown plugins, not the chat dock's render stub.
const require = createRequire(import.meta.url);
const compiled = ts.transpileModule(readFileSync(new URL("../src/components/AgentAnswerMarkdown.tsx", import.meta.url), "utf8"), {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    jsx: ts.JsxEmit.ReactJSX,
    target: ts.ScriptTarget.ES2020,
    esModuleInterop: true,
  },
}).outputText;
const componentModule = { exports: {} as any };
new Function("require", "module", "exports", compiled)((id: string) => {
  if (id === "react-markdown") return ReactMarkdown;
  if (id === "remark-gfm") return remarkGfm;
  if (id === "../utils/verifiedAnswerReveal") return verifiedReveal;
  return require(id);
}, componentModule, componentModule.exports);
const { AgentAnswerMarkdown } = componentModule.exports;

const stockMentions = [
  { symbol: "600101", name: "样例科技", trade_date: "2026-10-15" },
  { symbol: "000202", name: "样例制造", trade_date: "2026-10-15" },
];
const introduction = "以下为合成热股榜样例，仅用于渲染测试。";
const caveat = "榜单仅反映关注度，不能据此判断投资价值。";
const table = [
  "| 排名 | 代码 | 名称 | 排名变化 |",
  "| --- | --- | --- | --- |",
  "| 1 | 600101 | 样例科技 | 持平 |",
  "| 2 | 000202 | 样例制造 | 上升 |",
].join("\n");

function render(content: string) {
  return renderToStaticMarkup(createElement(MemoryRouter, {},
    createElement(AgentAnswerMarkdown, { content, stockMentions })));
}

function assertEvidenceTable(html: string) {
  assert.ok(html.startsWith(`<p>${introduction}</p>\n<table>`), html);
  assert.ok(html.endsWith(`</table>\n<p>${caveat}</p>`), html);
  assert.equal(html.split("<table>").length - 1, 1);
  assert.equal(html.split("<tr>").length - 1, 3, "the caveat must not become a table row");
  assert.ok(html.includes("<th>排名变化</th>"));
  assert.ok(html.includes("<td>持平</td>"));
  assert.ok(html.includes("<td>上升</td>"));
  for (const mention of stockMentions) {
    const query = new URLSearchParams({ name: mention.name, trade_date: mention.trade_date });
    const href = `/stocks/${mention.symbol}?${query.toString().split("&").join("&amp;")}`;
    assert.ok(html.includes(`href="${href}"`), "grounded table names must retain their stock links");
    assert.ok(html.includes(`>${mention.name}</a>`));
  }
}

test("evidence tables render with separate prose and grounded stock links", () => {
  assertEvidenceTable(render([introduction, table, caveat].join("\n\n")));
});

test("literal backslashes and Windows paths retain their meaning in prose and code", () => {
  const path = String.raw`C:\new\reports`;
  const literalNewline = String.raw`\n`;
  const code = `print("${literalNewline}")`;
  const html = render([
    `示例路径 ${path}；字面量 ${literalNewline}。`,
    `行内代码：\`${path}\` 与 \`${literalNewline}\`。`,
    ["```text", path, code, "```"].join("\n"),
  ].join("\n\n"));
  assert.ok(html.includes(`<p>示例路径 ${path}；字面量 ${literalNewline}。</p>`), html);
  assert.ok(html.includes(`<code>${path}</code>`));
  assert.ok(html.includes(`<code>${literalNewline}</code>`));
  assert.ok(html.includes(`<pre><code class="language-text">${path}\nprint(&quot;${literalNewline}&quot;)\n</code></pre>`));
});

test("stock names inside code stay code and explicit links keep their destinations", () => {
  const html = render("`样例科技` 与 [样例制造](https://example.test/research)。");
  assert.ok(html.includes("<code>样例科技</code>"));
  assert.ok(html.includes('href="https://example.test/research"'));
  assert.ok(html.includes('rel="noreferrer" target="_blank"'));
  assert.ok(!html.includes('href="/stocks/'));
});

// Frontend-only installs remain usable; full checkouts also verify the Python renderer contract.
const backendDirectory = fileURLToPath(new URL("../../backend/", import.meta.url));
const backendPython = process.env.LIMITUPLAB_TEST_PYTHON || [
  new URL("../../backend/.venv/Scripts/python.exe", import.meta.url),
  new URL("../../backend/.venv/bin/python", import.meta.url),
].map(fileURLToPath).find(existsSync);

test("backend evidence output renders as a real table without changing raw ranking values", {
  skip: backendPython ? false : "backend Python environment is not installed",
}, () => {
  const script = `
import json
import sys
from app.agents.react_runtime.contracts import Finish
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.rendering import render_answer

fixture = json.load(sys.stdin)
store = EvidenceStore()
key = store.add(tool="hot_stock_ranking", state="ok", arguments={"limit": 2}, payload={"items": fixture["rows"]})
final = Finish(status="complete", answer=fixture["answer"], table={"evidence_id": key, "columns": fixture["columns"]})
answer = render_answer(final, store)
code_answer = render_answer(final.model_copy(update={"answer": fixture["code_answer"]}), store)
print(json.dumps({"answer": answer, "code_answer": code_answer,
                  "raw_trends": [row["rank_trend"] for row in store.get(key)["rows"]]}, ensure_ascii=False))
`;
  const path = String.raw`C:\new\reports`;
  const literalNewline = String.raw`\n`;
  const output = execFileSync(backendPython!, ["-B", "-X", "utf8", "-c", script], {
    cwd: backendDirectory,
    encoding: "utf8",
    timeout: 20_000,
    input: JSON.stringify({
      // One real newline is a valid model layout; the server must isolate the final table.
      answer: [introduction, "{{evidence_table}}", caveat].join("\n"),
      code_answer: ["{{evidence_table}}", `    ${path}`, `    ${literalNewline}`].join("\n"),
      rows: stockMentions.map((stock, index) => ({ ...stock, rank: index + 1, rank_trend: index ? "up" : "flat" })),
      columns: [
        { field: "rank", label: "排名" },
        { field: "symbol", label: "代码" },
        { field: "name", label: "名称" },
        { field: "rank_trend", label: "排名变化" },
      ],
    }),
  });
  const result = JSON.parse(output);
  assert.deepEqual(result.raw_trends, ["flat", "up"]);
  assertEvidenceTable(render(result.answer));
  const codeHtml = render(result.code_answer);
  assert.ok(codeHtml.startsWith("<table>"));
  assert.ok(codeHtml.endsWith(`</table>\n<pre><code>${path}\n${literalNewline}\n</code></pre>`),
    "server block separation must retain the following code block's indentation and literal backslashes");
});
