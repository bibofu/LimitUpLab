import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import ts from "typescript";
import * as admin from "../src/utils/adminQuality.ts";
import * as json from "../src/utils/jsonRequest.ts";

test("all admin operations send the in-memory key as a header, preserve filters and version guards", async () => {
  const calls: { path: string; init: RequestInit }[] = [];
  const client = admin.createAdminQualityClient(async (path, init) => { calls.push({ path, init: init! }); return {} as any; }, "test-ephemeral-key");
  await client.list({ ...admin.emptyCaseFilters(), status: "pending", symptom: "漏答 对象", version: "v 2" }, 20);
  await client.detail("case/one");
  await client.review("case/one", { status: "confirmed", expected_revision: 3, note: "证据已核对", symptom: "漏答", root_cause: "", assignee: "", fix_version: "", duplicate_of: null, severity: "medium" });
  await client.jobs(); await client.process();
  await client.judgeConfig(); await client.judge("run/one");
  await client.evaluations("run/one"); await client.runJobs("run/one");
  for (const call of calls) {
    assert.equal(new Headers(call.init.headers).get("X-LimitUpLab-Admin-Key"), "test-ephemeral-key");
    assert.ok(!call.path.includes("test-ephemeral-key"));
  }
  const query = new URL(calls[0].path, "http://localhost").searchParams;
  assert.equal(query.get("symptom"), "漏答 对象"); assert.equal(query.get("offset"), "20");
  assert.equal(calls[1].path, "/api/admin/quality/cases/case%2Fone");
  assert.equal(JSON.parse(calls[2].init.body as string).expected_revision, 3);
  assert.equal(calls[2].init.method, "PATCH");
  assert.equal(calls[6].path, "/api/admin/quality/runs/run%2Fone/judge");
  assert.equal(calls[6].init.method, "POST");
  assert.equal(calls[6].init.body, "{}");
  assert.equal(calls[7].path, "/api/admin/quality/runs/run%2Fone/evaluations");
  assert.equal(calls[8].path, "/api/admin/quality/runs/run%2Fone/quality-jobs");
});

test("review transitions require confirmation before closure and allow reopening", () => {
  assert.ok(!admin.reviewTransitions("pending").includes("closed"));
  assert.ok(admin.reviewTransitions("verifying").includes("closed"));
  assert.ok(admin.reviewTransitions("closed").includes("pending"));
  const draft = { status: "closed", note: "回归验证通过", symptom: "漏答", root_cause: "", fix_version: "", duplicate_of: null } as admin.CaseReview;
  assert.ok(admin.validateReview(draft));
  assert.equal(admin.validateReview({ ...draft, root_cause: "上下文截断", fix_version: "fix-sha" }), null);
});

test("the actual review form preserves the draft after a revision conflict and blocks double submission", async () => {
  const source = readFileSync(new URL("../src/pages/QualityReviewPage.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 } }).outputText;
  const values: any[] = []; let cursor = 0;
  const hooks = {
    useState(initial: any) { const index = cursor++; if (!(index in values)) values[index] = initial; return [values[index], (value: any) => { values[index] = typeof value === "function" ? value(values[index]) : value; }]; },
    useRef(initial: any) { const index = cursor++; if (!(index in values)) values[index] = { current: initial }; return values[index]; },
    useEffect() { cursor++; },
  };
  const require = createRequire(import.meta.url), module = { exports: {} as any };
  const imports = { react: hooks, "../utils/adminQuality": admin, "../utils/jsonRequest": json, "../qualityApi": {}, "../qualityAdmin.css": {}, "../components/QualityJudgePanel": {} };
  new Function("require", "module", "exports", code)((id: string) => imports[id] ?? require(id), module, module.exports);
  const calls: admin.CaseReview[] = []; let reject!: (error: Error) => void, saved = 0;
  const props = {
    item: { status: "confirmed", revision: 2, symptom: "遗漏对象", root_cause: "", assignee: "", severity: "medium", fix_version: "", duplicate_of: null },
    save: (draft: admin.CaseReview) => { calls.push(draft); return new Promise((_, no) => { reject = no; }); },
    onSaved: () => saved++, reload: () => {},
  };
  let tree: any, nodes: any[];
  const render = () => {
    cursor = 0; nodes = []; tree = module.exports.QualityCaseReviewForm(props);
    const walk = (node: any) => { if (Array.isArray(node)) return node.forEach(walk); if (node?.props) { nodes.push(node); walk(node.props.children); } };
    walk(tree);
  };
  render();
  nodes.find(node => node.type === "textarea" && node.props.required).props.onChange({ target: { value: "已核对完整工具返回" } }); render();
  const first = tree.props.onSubmit({ preventDefault() {} }); tree.props.onSubmit({ preventDefault() {} }); render();
  assert.equal(calls.length, 1); assert.equal(calls[0].expected_revision, 2);
  assert.equal(nodes.find(node => node.type === "fieldset").props.disabled, true);
  reject(new json.HttpResponseError(409, "changed")); await first; render();
  assert.equal(saved, 0);
  assert.equal(nodes.find(node => node.type === "textarea" && node.props.required).props.value, "已核对完整工具返回");
  assert.ok(nodes.find(node => node.props.role === "alert").props.children.includes("重新加载"));
});
