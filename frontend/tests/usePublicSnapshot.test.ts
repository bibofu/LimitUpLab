import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test, { type TestContext } from "node:test";
import ts from "typescript";
import { createPublicSnapshot, type PublicSnapshot } from "../src/utils/publicSnapshot.ts";

const compiled = ts.transpileModule(readFileSync(new URL("../src/hooks/usePublicSnapshot.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;

// Exercise the real hook's effect dependencies with the real snapshot store.
function mount(resource: PublicSnapshot<number>, enabled = true, intervalMs?: number) {
  const slots: { dependencies: unknown[]; cleanup?: () => void }[] = [];
  const effects: (() => void)[] = [];
  let cursor = 0;
  const hooks = {
    useSyncExternalStore(_subscribe: unknown, read: () => unknown) { return read(); },
    useEffect(effect: () => (() => void) | undefined, dependencies: unknown[]) {
      const index = cursor++;
      const previous = slots[index];
      if (!previous || dependencies.some((value, position) => !Object.is(value, previous.dependencies[position]))) {
        effects.push(() => {
          previous?.cleanup?.();
          slots[index] = { dependencies, cleanup: effect() };
        });
      }
    },
  };
  const module = { exports: {} as any };
  new Function("require", "module", "exports", compiled)((id: string) => {
    assert.equal(id, "react");
    return hooks;
  }, module, module.exports);
  let state: any;
  const render = (nextEnabled = enabled) => {
    enabled = nextEnabled;
    cursor = 0;
    state = module.exports.usePublicSnapshot(resource, enabled, intervalMs);
    effects.splice(0).forEach(effect => effect());
    return state;
  };
  render();
  return {
    render,
    current: () => state,
    async settle() { await new Promise(resolve => setImmediate(resolve)); return render(); },
    unmount() { slots.forEach(slot => slot.cleanup?.()); },
  };
}

function fixture() {
  let now = 1000;
  let calls = 0;
  let fail = false;
  const resource = createPublicSnapshot<number>({
    key: "hook-expiry", storage: () => null, now: () => now, maxAgeMs: 100,
    valid: (value): value is number => typeof value === "number",
    fetchValue: async () => {
      calls++;
      if (fail) throw new Error("暂不可用");
      return calls;
    },
  });
  return { resource, calls: () => calls, expire: () => { now += 101; }, fail: (value: boolean) => { fail = value; } };
}

test("a mounted snapshot that expires without polling starts a real replacement request", async () => {
  const data = fixture();
  await data.resource.refresh();
  const hook = mount(data.resource);
  await hook.settle();
  assert.equal(data.calls(), 1);
  data.expire();
  assert.equal(hook.render().data, null);
  const recovered = await hook.settle();
  assert.equal(data.calls(), 2);
  assert.equal(recovered.data, 2);
  assert.equal(recovered.loading, false);
  hook.unmount();
});

test("an expiry recovery failure stays retryable without an automatic retry loop", async () => {
  const data = fixture();
  await data.resource.refresh();
  const hook = mount(data.resource);
  data.expire();
  data.fail(true);
  hook.render();
  const failed = await hook.settle();
  assert.equal(failed.loading, false);
  assert.equal(failed.error, "暂不可用");
  for (let index = 0; index < 3; index++) {
    data.expire();
    await hook.settle();
  }
  assert.equal(data.calls(), 2);
  data.fail(false);
  await hook.current().reload();
  assert.equal((await hook.settle()).data, 3);
  hook.unmount();
});

test("first-load and expiry effects keep requests shared across mounted consumers", async () => {
  const data = fixture();
  const first = mount(data.resource);
  const second = mount(data.resource);
  await first.settle();
  await second.settle();
  assert.equal(data.calls(), 1);
  data.expire();
  first.render();
  second.render();
  await first.settle();
  await second.settle();
  assert.equal(data.calls(), 2);
  assert.equal(first.current().data, second.current().data);
  first.unmount();
  second.unmount();
});

test("disabled snapshots stay idle and expiry recovery preserves the existing polling interval", async (t: TestContext) => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "window");
  const timers = new Map<number, { callback: () => void; milliseconds: number }>();
  let started = 0;
  Object.defineProperty(globalThis, "window", { configurable: true, value: {
    setInterval(callback: () => void, milliseconds: number) {
      timers.set(++started, { callback, milliseconds });
      return started;
    },
    clearInterval(id: number) { timers.delete(id); },
  } });
  t.after(() => {
    if (original) Object.defineProperty(globalThis, "window", original);
    else Reflect.deleteProperty(globalThis, "window");
  });
  const data = fixture();
  await data.resource.refresh();
  const hook = mount(data.resource, false, 5000);
  data.expire();
  await hook.settle();
  assert.equal(data.calls(), 1);
  assert.equal(timers.size, 0);
  hook.render(true);
  await hook.settle();
  assert.equal(data.calls(), 2);
  assert.equal(timers.get(1)?.milliseconds, 5000);
  data.expire();
  hook.render();
  await hook.settle();
  assert.equal(data.calls(), 3);
  assert.equal(started, 1, "recovery must not reset the polling schedule");
  data.expire();
  timers.get(1)!.callback();
  await hook.settle();
  assert.equal(data.calls(), 4);
  hook.unmount();
  assert.equal(timers.size, 0);
});
