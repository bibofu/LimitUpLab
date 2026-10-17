import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import ts from "typescript";
import * as monitor from "../src/utils/qualityJudge.ts";
import type { AdminQualityClient, EvaluationRecord, QualityRunJob } from "../src/utils/adminQuality.ts";

const config = { enabled: true, manual_only: true, model: "judge-model", max_calls_per_job: 2, daily_call_limit: 20, max_concurrency: 1 };
const job = (status: QualityRunJob["status"]): QualityRunJob => ({ id: "j", job_kind: "llm_judge", status, attempts: 1, available_at: 1767225600, last_error: null, updated_at: "2026-01-01T00:00:00Z" });
const report = (id: string): EvaluationRecord => ({ evaluation_id: id, bundle_id: "bundle", evaluator_kind: "llm_judge", evaluator_version: "judge-v1", execution_status: "succeeded", verdict: "unknown", report: { checks: [], metadata: {} } });
const api = (overrides: Record<string, unknown> = {}): AdminQualityClient => ({
  judgeConfig: async () => config,
  judge: async () => ({ created: true, job_id: "j", bundle_id: "bundle" }),
  runJobs: async () => ({ items: [], total: 0 }),
  evaluations: async () => ({ items: [], total: 0 }), ...overrides,
} as AdminQualityClient);

test("Judge observation is bounded, stops on deferred or terminal jobs and never enqueues", async () => {
  let reads = 0, waits = 0, enqueues = 0;
  const client = api({
    judge: async () => { enqueues++; },
    runJobs: async () => { reads++; return { items: [job("running")], total: 1 }; },
  });
  const options = { maxPolls: 3, wait: async () => { waits++; } };
  assert.equal(await monitor.watchJudge(client, "run", "j", new AbortController().signal, () => {}, options), "waiting");
  assert.equal(reads, 3); assert.equal(waits, 2); assert.equal(enqueues, 0);
  for (const [item, expected] of [
    [job("dead"), "failed"], [job("done"), "done"],
    [{ ...job("pending"), available_at: 4070908800 }, "deferred"],
  ] as const) {
    reads = 0;
    const result = await monitor.watchJudge(api({ runJobs: async () => { reads++; return { items: [item], total: 1 }; } }), "run", "j", new AbortController().signal, () => {}, options);
    assert.equal(result, expected); assert.equal(reads, 1);
  }
});

test("job scheduling timestamps accept API Unix seconds and legacy ISO timestamps", () => {
  const now = Date.parse("2026-10-17T08:00:00Z"), next = now / 1000 + 3600;
  assert.equal(monitor.qualityJobAvailableAt(next), now + 3600000);
  assert.equal(monitor.judgeJobState({ ...job("pending"), available_at: next }, now), "deferred");
  assert.equal(monitor.judgeJobState({ ...job("pending"), available_at: now / 1000 }, now), "active");
  assert.equal(monitor.qualityJobAvailableAt("2026-10-17T09:00:00Z"), now + 3600000);
});

test("aborting an old observation cannot publish its late report", async () => {
  const controller = new AbortController(); let finish!: (value: unknown) => void, published = 0;
  const client = api({ evaluations: () => new Promise(resolve => { finish = resolve; }) });
  const result = monitor.watchJudge(client, "old-run", "j", controller.signal, () => published++);
  controller.abort(); finish({ items: [report("old")], total: 1 });
  await assert.rejects(result, { name: "AbortError" }); assert.equal(published, 0);
});

const require = createRequire(import.meta.url);
function load(file: string, imports: Record<string, unknown>, extra = "") {
  const code = ts.transpileModule(readFileSync(new URL(file, import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const module = { exports: {} as any };
  new Function("require", "module", "exports", code + extra)((id: string) => imports[id] ?? require(id), module, module.exports);
  return module.exports;
}
function panel(props: any, target?: { file: string; entry: string; imports: Record<string, unknown>; extra?: string }) {
  const values: any[] = [], effects: (() => void)[] = [], cleanups = new Map<number, () => void>();
  let cursor = 0, dirty = false, nodes: any[] = [];
  const hooks = {
    useState(initial: any) { const index = cursor++; if (!(index in values)) values[index] = initial; return [values[index], (value: any) => { values[index] = typeof value === "function" ? value(values[index]) : value; dirty = true; }]; },
    useRef(initial: any) { const index = cursor++; if (!(index in values)) values[index] = { current: initial }; return values[index]; },
    useEffect(effect: () => any, deps: unknown[]) {
      const index = cursor++;
      if (!(index in values) || deps.some((value, i) => value !== values[index][i])) effects.push(() => {
        cleanups.get(index)?.(); const cleanup = effect(); if (typeof cleanup === "function") cleanups.set(index, cleanup);
      });
      values[index] = deps;
    },
  };
  const Reports = () => null;
  const component = target ? load(target.file, { react: hooks, ...target.imports }, target.extra) : load("../src/components/QualityJudgePanel.tsx", { react: hooks, "../utils/qualityJudge": monitor, "./QualityEvaluationReports": { QualityEvaluationReports: Reports } });
  const render = () => {
    do {
      cursor = 0; dirty = false; nodes = [];
      const tree = component[target?.entry ?? "QualityJudgePanel"](props);
      const walk = (node: any) => { if (Array.isArray(node)) return node.forEach(walk); if (node?.props) { nodes.push(node); walk(node.props.children); } };
      walk(tree); effects.splice(0).forEach(effect => effect());
    } while (dirty);
  };
  render();
  return {
    render, find(predicate: (node: any) => boolean) { const node = nodes.find(predicate); assert.ok(node); return node.props; },
    button(label: string) { return this.find(node => node.type === "button" && node.props.children === label); },
    reports() { return this.find(node => node.type === Reports).evaluations; },
    update(next: any) { props = next; render(); },
    async settle() { await new Promise(resolve => setImmediate(resolve)); render(); },
    unmount() { cleanups.forEach(cleanup => cleanup()); },
  };
}

test("actual Judge panel starts only on click, blocks duplicate submission and preserves reports after failure", async () => {
  let enqueues = 0, completed = 0, reject!: (error: Error) => void, ready = false;
  const client = api({
    judge: () => { enqueues++; return new Promise((resolve, no) => { reject = no; if (ready) resolve({ created: true, job_id: "j", bundle_id: "bundle" }); }); },
    evaluations: async () => ({ items: [report("existing")], total: 1 }),
    runJobs: async () => ({ items: ready ? [job("done")] : [], total: ready ? 1 : 0 }),
  });
  const app = panel({ api: client, runId: "run", onCompleted: () => completed++ });
  await app.settle(); assert.equal(enqueues, 0);
  app.button("发起语义评审").onClick(); app.button("发起语义评审").onClick();
  await app.settle(); assert.equal(enqueues, 1); assert.equal(app.button("发起语义评审").disabled, true);
  reject(new Error("request unavailable")); await app.settle();
  assert.equal(app.reports().items[0].evaluation_id, "existing");
  assert.equal(app.button("发起语义评审").disabled, false);
  assert.ok(app.find(node => node.props.role === "alert")); assert.equal(completed, 0);
  ready = true; app.button("发起语义评审").onClick(); await app.settle();
  assert.equal(enqueues, 2); assert.equal(completed, 1);
  assert.match(app.find(node => node.props.role === "status").children, /已完成/);
  app.unmount();
});

test("disabled Judge configuration keeps manual controls disabled but historical reports readable", async () => {
  const app = panel({ api: api({ judgeConfig: async () => ({ ...config, enabled: false, reason: "尚未配置模型凭据" }), evaluations: async () => ({ items: [report("prior")], total: 1 }) }), runId: "run" });
  await app.settle();
  assert.equal(app.button("发起语义评审").disabled, true);
  assert.equal(app.reports().items[0].evaluation_id, "prior");
  assert.ok(app.find(node => node.type === "p" && node.props.children === "尚未配置模型凭据"));
  app.unmount();
});

test("refresh after a deferred task completes updates case evidence without starting a model call", async () => {
  let finished = false, completed = 0, calls = 0;
  const client = api({
    judge: async () => { calls++; },
    runJobs: async () => ({ items: [finished ? job("done") : { ...job("pending"), available_at: 4070908800 }], total: 1 }),
  });
  const app = panel({ api: client, runId: "run", onCompleted: () => completed++ });
  await app.settle(); assert.match(app.find(node => node.props.role === "status").children, /延期/);
  finished = true; app.button("刷新评审状态").onClick(); await app.settle();
  assert.equal(completed, 1); assert.equal(calls, 0); app.unmount();
});

test("switching runs cancels prior requests and cannot replace the new run with a late result", async () => {
  let finish!: (value: unknown) => void, oldSignal: AbortSignal | undefined;
  const client = api({ evaluations: (run: string, signal: AbortSignal) => run === "old" ? new Promise(resolve => { finish = resolve; oldSignal = signal; }) : Promise.resolve({ items: [report("new")], total: 1 }) });
  const app = panel({ api: client, runId: "old" }); await app.settle();
  app.update({ api: client, runId: "new" }); await app.settle();
  assert.equal(oldSignal?.aborted, true); assert.equal(app.reports().items[0].evaluation_id, "new");
  finish({ items: [report("old")], total: 1 }); await app.settle();
  assert.equal(app.reports().items[0].evaluation_id, "new"); app.unmount();
});

test("actual report component labels semantic findings as suspected and shows grounded quotations", () => {
  const { QualityEvaluationReports } = load("../src/components/QualityEvaluationReports.tsx", {});
  const { createElement } = require("react"), { renderToStaticMarkup } = require("react-dom/server");
  const evaluation = { ...report("r"), verdict: "fail", report: { checks: [{
    check_id: "intent", dimension: "intent_delivery", verdict: "fail", summary: "遗漏用户要求", evidence: { findings: [{
      answer_quote: { ref_id: "answer", text: "只分析了甲" }, requirement_quote: { ref_id: "request", text: "请同时分析甲与乙" },
      summary: "回答未交付第二个对象", references: [{ ref_id: "request", kind: "question", value: "请同时分析甲与乙" }],
    }] },
  }] } };
  const html = renderToStaticMarkup(createElement(QualityEvaluationReports, { evaluations: { items: [evaluation, { ...report("failed"), execution_status: "error" }], total: 2 } }));
  assert.match(html, /疑似语义问题/); assert.match(html, /意图与交付/); assert.match(html, /只分析了甲/);
  assert.match(html, /请同时分析甲与乙/); assert.match(html, /回答未交付第二个对象/); assert.match(html, /对应证据/);
  assert.match(html, /尚无有效评审结论/); assert.doesNotMatch(html, /发现明确不一致/);
});

test("completing a semantic review updates evidence without replacing the human form or its revision", async () => {
  const admin = await import("../src/utils/adminQuality.ts"), json = await import("../src/utils/jsonRequest.ts");
  const Judge = () => null;
  const item = { case_id: "case", run_id: "run", status: "pending", severity: "medium", revision: 1, updated_at: "2026-01-01T00:00:00Z" };
  let revision = 1;
  const detail = () => ({ case: { ...item, revision }, signals: [], reviews: [], trace_snapshots: [], run: { request: {}, response: {}, delivery_status: "unknown" } });
  const app = panel({ adminKey: "ephemeral", onLogout: () => {} }, {
    file: "../src/pages/QualityReviewPage.tsx", entry: "QualityWorkspace", extra: "\nexports.QualityWorkspace = QualityWorkspace;",
    imports: {
      "../utils/adminQuality": admin, "../utils/jsonRequest": json, "../qualityAdmin.css": {},
      "../components/QualityJudgePanel": { QualityJudgePanel: Judge, ManualRunJudge: () => null },
      "../qualityApi": { qualityRequest: async (path: string) => path.includes("/cases/case") ? detail() : path.endsWith("/jobs") ? { counts: {}, failed: [] } : { items: [item], total: 1 } },
    },
  });
  await app.settle(); app.find(node => node.props.className === "quality-case-row").onClick(); app.render(); await app.settle();
  const before = app.find(node => typeof node.type === "function" && node.type.name === "QualityCaseReviewForm");
  revision = 2; app.find(node => node.type === Judge).onCompleted(); await app.settle();
  const after = app.find(node => typeof node.type === "function" && node.type.name === "QualityCaseReviewForm");
  assert.equal(after.item, before.item, "background evidence updates must preserve the form's original input object and revision");
  assert.equal(after.item.revision, 1); app.unmount();
});
