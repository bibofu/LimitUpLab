import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import ts from "typescript";
import * as consolidation from "../src/consolidation.ts";
import type { ConsolidationPool, ObservationStrategy } from "../src/consolidation.ts";

const require = createRequire(import.meta.url);
const compiled = ts.transpileModule(readFileSync(new URL("../src/components/ConsolidationPanel.tsx", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
}).outputText;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function pool(strategy: ObservationStrategy, date = "2026-10-13", name = "已核验候选"): ConsolidationPool {
  return {
    strategy, strategy_version: "fixture-v1", generated_at: "2026-10-13T16:00:00+08:00",
    data_as_of: date, latest_data_date: "2026-10-13", available_dates: ["2026-10-13", "2026-10-12"],
    status: "ready", snapshot_kind: "recomputed_observation", calendar_source: "fixture", pool_count: 9, evaluated_count: 7,
    candidates: [{
      symbol: "600001", name, anchor_date: "2026-10-09", confirmed_date: date, state: "new", failed_conditions: [],
      consolidation_days: 2, anchor_close: 10, close: 10, range_low: 9.5, range_high: 10.2, range_pct: 7,
      anchor_change_pct: 0, volume_ratio: 0.6, peak_date: "2026-10-09", peak_price: 11.2, drawdown_pct: 11,
      source: "fixture", reasons: ["规则满足"], risks: ["研究样本"],
    }],
    evaluated_stocks: [], exclusions: {}, data_missing: [], warnings: [], rules: ["规则样例"],
  };
}

/** Execute the actual component and effects, including cleanup, without a browser dependency. */
function mount(fetcher: (date: string | undefined, strategy: ObservationStrategy) => Promise<ConsolidationPool>) {
  const values: any[] = [];
  const effects: (() => void)[] = [];
  let cursor = 0;
  let strategy: ObservationStrategy = "consolidation";
  const hooks = {
    useState(initial: any) {
      const index = cursor++;
      if (!(index in values)) values[index] = initial;
      return [values[index], (next: any) => { values[index] = typeof next === "function" ? next(values[index]) : next; }];
    },
    useEffect(effect: () => (() => void) | void, deps: unknown[]) {
      const index = cursor++;
      const previous = values[index];
      if (!previous || deps.some((value, position) => value !== previous.deps[position])) {
        const next = { deps, cleanup: undefined as (() => void) | void };
        effects.push(() => { previous?.cleanup?.(); next.cleanup = effect(); });
        values[index] = next;
      }
    },
  };
  const imports: Record<string, any> = {
    react: hooks, "../api": { fetchConsolidationPool: fetcher }, "../consolidation": consolidation,
    "../dashboardFormatters": { stockDetailPath: (symbol: string) => `/stock/${symbol}` },
    "./Panel": { Panel: () => null },
  };
  const module = { exports: {} as any };
  new Function("require", "module", "exports", compiled)((id: string) => imports[id] ?? require(id), module, module.exports);
  let nodes: any[] = [];
  let text: string[] = [];
  const flushEffects = () => effects.splice(0).forEach(effect => effect());
  function render(runEffects = true) {
    cursor = 0;
    nodes = [];
    text = [];
    function walk(node: any) {
      if (Array.isArray(node)) { node.forEach(walk); return; }
      if (typeof node === "string" || typeof node === "number") { text.push(String(node)); return; }
      if (!node?.props) return;
      nodes.push(node);
      walk(node.props.children);
      walk(node.props.actions);
    }
    walk(module.exports.ConsolidationPanel({ strategy }));
    if (runEffects) flushEffects();
  }
  render();
  return {
    async settle() { await new Promise(resolve => setImmediate(resolve)); render(); },
    render, flushEffects,
    text: () => text.join(" "),
    has: (predicate: (node: any) => boolean) => nodes.some(predicate),
    find(predicate: (node: any) => boolean) {
      const node = nodes.find(predicate);
      assert.ok(node, "expected rendered control");
      return node.props;
    },
    refresh() { this.find(node => node.props.className === "consolidation-refresh").onClick(); render(); },
    selectDate(date: string) { this.find(node => node.type === "select").onChange({ target: { value: date } }); render(); },
    selectStrategy(next: ObservationStrategy, runEffects = true) { strategy = next; render(runEffects); },
  };
}

test("first unresolved observation query stays quiet and does not claim an empty pool", async () => {
  const pending = deferred<ConsolidationPool>();
  const app = mount(() => pending.promise);
  assert.equal(app.has(node => node.props.role === "status"), false);
  assert.equal(app.text().includes("正在核对"), false);
  assert.equal(app.text().includes("暂无符合"), false);
  assert.equal(app.text().includes("近期涨停池"), false);
  assert.equal(app.has(node => node.props["aria-hidden"] === "true"), true);
  pending.resolve(pool("consolidation"));
  await app.settle();
  assert.equal(app.text().includes("已核验候选"), true);
});

test("same-query refresh and failure retain candidates and date without a loading status", async () => {
  let next = deferred<ConsolidationPool>();
  const app = mount(() => next.promise);
  next.resolve(pool("consolidation"));
  await app.settle();
  next = deferred<ConsolidationPool>();
  app.refresh();
  app.render();
  assert.equal(app.text().includes("已核验候选"), true);
  assert.equal(app.text().includes("2026-10-13"), true);
  assert.equal(app.has(node => node.props.role === "status"), false);
  assert.equal(app.find(node => node.props.className === "consolidation-refresh").disabled, true);
  next.reject(new Error("数据暂不可用"));
  await app.settle();
  assert.equal(app.text().includes("已核验候选"), true);
  assert.equal(app.has(node => node.props.role === "alert"), true);
  assert.equal(app.find(node => node.props.className === "consolidation-refresh").disabled, false);
  next = deferred<ConsolidationPool>();
  app.refresh();
  next.resolve(pool("consolidation", "2026-10-13", "更新后的候选"));
  await app.settle();
  assert.equal(app.text().includes("更新后的候选"), true);
  assert.equal(app.text().includes("已核验候选"), false);
  assert.equal(app.has(node => node.props.role === "alert"), false);
});

test("strategy switches hide another strategy before effects run and reject late results", async () => {
  const calls: { strategy: ObservationStrategy; pending: ReturnType<typeof deferred<ConsolidationPool>> }[] = [];
  const app = mount((_date, strategy) => {
    const pending = deferred<ConsolidationPool>();
    calls.push({ strategy, pending });
    return pending.promise;
  });
  calls[0].pending.resolve(pool("consolidation", "2026-10-13", "整理候选"));
  await app.settle();
  app.refresh();
  app.selectStrategy("drawdown", false);
  assert.equal(app.text().includes("整理候选"), false);
  app.flushEffects();
  calls[2].pending.resolve(pool("drawdown", "2026-10-13", "回撤候选"));
  await app.settle();
  calls[1].pending.resolve(pool("consolidation", "2026-10-13", "迟到整理候选"));
  await app.settle();
  assert.equal(app.text().includes("回撤候选"), true);
  assert.equal(app.text().includes("迟到整理候选"), false);
  app.selectStrategy("consolidation");
  assert.equal(app.text().includes("整理候选"), true);
  assert.equal(app.text().includes("迟到整理候选"), false);
  assert.equal(app.text().includes("回撤候选"), false);
});

test("date queries stay isolated and a failed unseen date never borrows another result", async () => {
  const calls: { date: string | undefined; pending: ReturnType<typeof deferred<ConsolidationPool>> }[] = [];
  const app = mount(date => {
    const pending = deferred<ConsolidationPool>();
    calls.push({ date, pending });
    return pending.promise;
  });
  calls[0].pending.resolve(pool("consolidation", "2026-10-13", "最新候选"));
  await app.settle();
  app.selectDate("2026-10-12");
  assert.equal(calls[1].date, "2026-10-12");
  assert.equal(app.text().includes("最新候选"), false);
  assert.equal(app.find(node => node.type === "select").value, "2026-10-12");
  calls[1].pending.reject(new Error("该日读取失败"));
  await app.settle();
  assert.equal(app.text().includes("最新候选"), false);
  assert.equal(app.text().includes("暂无符合"), false);
  assert.equal(app.has(node => node.props.role === "alert"), true);
  app.selectDate("");
  assert.equal(app.text().includes("最新候选"), true);
  assert.equal(app.has(node => node.props.role === "alert"), false);
});

test("a mismatched server response cannot replace the verified strategy/date result", async () => {
  let next = deferred<ConsolidationPool>();
  const app = mount(() => next.promise);
  next.resolve(pool("consolidation"));
  await app.settle();
  next = deferred<ConsolidationPool>();
  app.refresh();
  next.resolve(pool("drawdown", "2026-10-13", "错误策略"));
  await app.settle();
  assert.equal(app.text().includes("已核验候选"), true);
  assert.equal(app.text().includes("错误策略"), false);
  assert.equal(app.has(node => node.props.role === "alert"), true);
  next = deferred<ConsolidationPool>();
  app.selectDate("2026-10-12");
  next.resolve(pool("consolidation", "2026-10-13", "错误日期"));
  await app.settle();
  assert.equal(app.text().includes("错误日期"), false);
  assert.equal(app.text().includes("已核验候选"), false);
  assert.equal(app.has(node => node.props.role === "alert"), true);
});

test("a late failure from an abandoned date does not replace the current date or its status", async () => {
  const calls: ReturnType<typeof deferred<ConsolidationPool>>[] = [];
  const app = mount(() => {
    const pending = deferred<ConsolidationPool>();
    calls.push(pending);
    return pending.promise;
  });
  calls[0].resolve(pool("consolidation"));
  await app.settle();
  app.selectDate("2026-10-12");
  app.selectDate("2026-10-13");
  calls[2].resolve(pool("consolidation", "2026-10-13", "所选日期候选"));
  await app.settle();
  calls[1].reject(new Error("过期日期错误"));
  await app.settle();
  assert.equal(app.text().includes("所选日期候选"), true);
  assert.equal(app.text().includes("过期日期错误"), false);
  assert.equal(app.has(node => node.props.role === "alert"), false);
});

for (const reason of ["local_database", "daily_bars"]) {
  test(`historical query displays an undated ${reason} data gap without inventing the requested cutoff`, async () => {
    let next = deferred<ConsolidationPool>();
    const app = mount(() => next.promise);
    next.resolve(pool("consolidation"));
    await app.settle();
    next = deferred<ConsolidationPool>();
    app.selectDate("2026-10-12");
    const missing: ConsolidationPool = {
      ...pool("consolidation"), status: "data_missing", data_as_of: null, latest_data_date: null,
      candidates: [], evaluated_stocks: [], available_dates: [], pool_count: 0, evaluated_count: 0,
      data_missing: [reason],
    };
    next.resolve(missing);
    await app.settle();
    assert.equal(app.has(node => node.props.role === "alert"), false);
    assert.equal(app.text().includes(consolidation.consolidationReason(reason)), true);
    assert.equal(app.text().includes("不能据此判断全市场"), true);
    assert.equal(app.text().includes("基于最新本地收盘数据重算"), false);
    assert.equal(app.has(node => node.type === "strong" && node.props.children === "暂无"), true);
    assert.equal(app.find(node => node.type === "select").value, "2026-10-12");
    assert.equal(app.text().includes("已核验候选"), false);
    assert.equal(missing.data_as_of, null);
  });
}

test("an undated ready result is still rejected for a historical query", async () => {
  let next = deferred<ConsolidationPool>();
  const app = mount(() => next.promise);
  next.resolve(pool("consolidation"));
  await app.settle();
  next = deferred<ConsolidationPool>();
  app.selectDate("2026-10-12");
  next.resolve({ ...pool("consolidation", "2026-10-12", "未标日期的候选"), data_as_of: null });
  await app.settle();
  assert.equal(app.has(node => node.props.role === "alert"), true);
  assert.equal(app.text().includes("未标日期的候选"), false);
});

test("the component retains at most twelve query results", async () => {
  const dates = Array.from({ length: 12 }, (_, index) => `2026-09-${String(index + 1).padStart(2, "0")}`);
  const app = mount(async date => ({
    ...pool("consolidation", date ?? "2026-10-13", date ? `候选 ${date}` : "最新查询候选"),
    available_dates: dates,
  }));
  await app.settle();
  for (const date of dates) {
    app.selectDate(date);
    await app.settle();
  }
  app.selectDate(dates[0]);
  assert.equal(app.text().includes(`候选 ${dates[0]}`), true);
  await app.settle();
  app.selectDate("");
  // Twelve date queries evicted the older "latest" query; it is not reconstructed from another date.
  assert.equal(app.text().includes("最新查询候选"), false);
  assert.equal(app.text().includes("近期涨停池"), false);
  await app.settle();
  assert.equal(app.text().includes("最新查询候选"), true);
});
