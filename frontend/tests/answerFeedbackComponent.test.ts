import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

import * as answerFeedback from "../src/utils/answerFeedback.ts";

const require = createRequire(import.meta.url);
const compiled = ts.transpileModule(
  readFileSync(new URL("../src/components/AnswerFeedback.tsx", import.meta.url), "utf8"),
  {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
    },
  },
).outputText;
const component = { exports: {} as any };
const imports: Record<string, any> = {
  react: require("react"),
  "lucide-react": require("lucide-react"),
  "../api": {
    saveAnswerFeedback: async () => null,
    deleteAnswerFeedback: async () => ({ deleted: true }),
  },
  "../utils/answerFeedback": answerFeedback,
};
new Function("require", "module", "exports", compiled)(
  (id: string) => imports[id] ?? require(id),
  component,
  component.exports,
);

const value = {
  run_id: "run_feedback",
  vote: "down" as const,
  reasons: ["data_error"],
  comment: "日期不对",
  created_at: "2026-10-28T00:00:00Z",
  updated_at: "2026-10-28T00:00:00Z",
};

test("a restored downvote renders its pressed state and retraction", () => {
  const markup = renderToStaticMarkup(
    createElement(component.exports.AnswerFeedback, { runId: "run_feedback", initial: value }),
  );
  assert.match(markup, /aria-label="回答反馈"/);
  assert.match(markup, /aria-label="点踩"/);
  assert.match(markup, /aria-pressed="true"/);
  assert.match(markup, /撤销/);
});

test("an unanswered message renders both neutral vote buttons", () => {
  const markup = renderToStaticMarkup(
    createElement(component.exports.AnswerFeedback, { runId: "run_feedback", initial: null }),
  );
  assert.equal((markup.match(/aria-pressed="false"/g) ?? []).length, 2);
  assert.doesNotMatch(markup, /撤销/);
});
