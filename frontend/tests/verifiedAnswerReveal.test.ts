import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { revealVerifiedTree, type RevealNode } from "../src/utils/verifiedAnswerReveal.ts";

function render(content: string, visibleUnits = Infinity) {
  let total = -1;
  const html = renderToStaticMarkup(createElement(ReactMarkdown, {
    children: content,
    remarkPlugins: [remarkGfm],
    rehypePlugins: [() => (tree: RevealNode) => { total = revealVerifiedTree(tree, visibleUnits); }],
  }));
  return { html, total };
}

function original(content: string) {
  return renderToStaticMarkup(createElement(ReactMarkdown, { children: content, remarkPlugins: [remarkGfm] }));
}

const table = [
  "| 排名 | 股票 | 说明 |",
  "| --- | --- | --- |",
  ...Array.from({ length: 30 }, (_, index) => `| ${index + 1} | [样例${index + 1}](https://example.test/${index + 1}) | A\\|B |`),
].join("\n");

test("a verified 30-row table starts with its complete header and first linked row", () => {
  assert.equal(render(table, 0).html, "");
  const first = render(table, 1);
  assert.equal(first.total, 30);
  assert.equal(first.html.split("<tr>").length - 1, 2);
  assert.ok(first.html.includes("<th>说明</th>"));
  assert.ok(first.html.includes('<a href="https://example.test/1">样例1</a>'));
  assert.ok(first.html.includes("<td>A|B</td>"), "escaped pipes retain their parsed cell");
  assert.ok(!first.html.includes("样例2"));
  for (let visible = 2; visible <= 30; visible += 1) {
    const frame = render(table, visible);
    assert.equal(frame.total, 30);
    assert.equal(frame.html.split("<tr>").length - 1, visible + 1);
    assert.ok(frame.html.includes(`样例${visible}</a>`));
  }
  assert.equal(render(table, 30).html, original(table));
});

test("reference links, inline code and images appear as whole inline objects", () => {
  const content = [
    "[这是不可拆分的完整研究资料链接][source] `完整行内代码` ![图示](https://example.test/image.png)",
    "",
    "[source]: https://example.test/research \"资料\"",
  ].join("\n");
  const first = render(content, 1);
  assert.equal(first.total, 3);
  assert.ok(first.html.includes('<a href="https://example.test/research" title="资料">这是不可拆分的完整研究资料链接</a>'));
  assert.ok(!first.html.includes("<code>"));
  assert.ok(render(content, 2).html.includes("<code>完整行内代码</code>"));
  assert.equal(render(content, 3).html, original(content));
});

test("text advances by complete grapheme clusters through emphasis and headings", () => {
  const family = "👨‍👩‍👧‍👦";
  const combined = "e\u0301";
  const content = `## **${"甲".repeat(11)}${family}${combined}乙**\n\n尾段`;
  const first = render(content, 1);
  assert.equal(first.total, 3);
  assert.equal(first.html, `<h2><strong>${"甲".repeat(11)}${family}</strong></h2>\n`);
  const second = render(content, 2);
  assert.ok(second.html.includes(`${family}${combined}乙</strong>`));
  assert.ok(!second.html.includes("尾段"));
  assert.equal(render(content, first.total).html, original(content));
});

test("combining marks and flags are never exposed as partial graphemes", () => {
  const content = `${"中".repeat(11)}e\u0301🇨🇳后续`;
  assert.equal(render(content, 1).html, `<p>${"中".repeat(11)}e\u0301</p>`);
  assert.equal(render(content, 2).html, original(content));
});

test("fenced code, blockquotes and each list item stay intact", () => {
  const content = [
    "```text", "| 不应拆成表格 |", "[原样](text)", "```", "",
    "> 第一段引用", ">", "> 第二段引用", "",
    "3. 首项含有 **完整加粗**", "4. 次项含有 [链接](https://example.test)",
  ].join("\n");
  assert.equal(render(content, 0).html.trim(), "");
  const first = render(content, 1);
  assert.equal(first.total, 4);
  assert.ok(first.html.includes('<pre><code class="language-text">| 不应拆成表格 |\n[原样](text)\n</code></pre>'));
  assert.ok(!first.html.includes("<blockquote>"));
  const second = render(content, 2).html;
  assert.ok(second.includes("第一段引用") && second.includes("第二段引用"));
  const third = render(content, 3).html;
  assert.ok(third.includes('<ol start="3">'));
  assert.ok(third.includes("<strong>完整加粗</strong>"));
  assert.ok(!third.includes("次项"));
  assert.equal(render(content, 4).html, original(content));
});

test("prose then rows then caveat appear in order with a stable full-tree total", () => {
  const content = `前言\n\n${table}\n\n说明：用于研究。`;
  const total = render(content).total;
  assert.equal(total, 32);
  assert.equal(render(content, 1).html, "<p>前言</p>\n\n");
  assert.equal(render(content, 2).html.split("<tr>").length - 1, 2);
  assert.ok(!render(content, 31).html.includes("说明：用于研究。"));
  for (const count of [0, 1, 2, 17, 31, 32, Infinity]) assert.equal(render(content, count).total, total);
  assert.equal(render(content, total).html, original(content));
  assert.equal(render(content).html, original(content));
});

test("full budgets preserve AST identity and zero budgets ignore root whitespace", () => {
  const makeTree = (): RevealNode => ({ type: "root", children: [
    { type: "text", value: "\n" },
    { type: "element", tagName: "p", properties: { id: "kept" }, children: [{ type: "text", value: "正文" }] },
    { type: "text", value: "\n" },
  ] });
  for (const budget of [1, Infinity]) {
    const tree = makeTree();
    const children = tree.children;
    const snapshot = structuredClone(tree);
    assert.equal(revealVerifiedTree(tree, budget), 1);
    assert.equal(tree.children, children);
    assert.deepEqual(tree, snapshot);
  }
  for (const budget of [0, -1, NaN, -Infinity]) {
    const tree = makeTree();
    assert.equal(revealVerifiedTree(tree, budget), 1);
    assert.deepEqual(tree.children, [{ type: "text", value: "\n" }, { type: "text", value: "\n" }]);
  }
});
