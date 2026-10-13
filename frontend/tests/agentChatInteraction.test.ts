import assert from "node:assert/strict";
import test from "node:test";
import {
  createChatOperationLock,
  draftAfterChatSubmit,
  readActiveChatSession,
  rememberActiveChatSession,
  shouldSubmitChatKey,
} from "../src/utils/agentChatInteraction.ts";

test("a pending session read blocks rapid switches and sends until it settles", async () => {
  const lock = createChatOperationLock();
  let resolve!: () => void;
  const pending = new Promise<void>(done => { resolve = done; });
  const calls: string[] = [];
  async function action(name: string) {
    const release = lock.acquire();
    if (!release) return;
    try {
      calls.push(name);
      await pending;
    } finally {
      release();
    }
  }
  const first = action("open A");
  await action("open B");
  await action("send");
  assert.deepEqual(calls, ["open A"]);
  resolve();
  await first;
  await action("send after restore");
  assert.deepEqual(calls, ["open A", "send after restore"]);
});

test("failed operations release the lock for retry and old releases cannot unlock a new request", async () => {
  const lock = createChatOperationLock();
  const release = lock.acquire()!;
  await assert.rejects((async () => {
    try { throw new Error("network unavailable"); } finally { release(); }
  })(), /network unavailable/);
  const releaseRetry = lock.acquire();
  assert.ok(releaseRetry);
  release();
  assert.equal(lock.acquire(), null);
  releaseRetry();
  assert.ok(lock.acquire());
});

test("Enter sends, while Shift+Enter and IME confirmation preserve the draft", () => {
  const enter = { key: "Enter", shiftKey: false, isComposing: false, keyCode: 13 };
  assert.equal(shouldSubmitChatKey(enter), true);
  assert.equal(shouldSubmitChatKey({ ...enter, shiftKey: true }), false);
  assert.equal(shouldSubmitChatKey({ ...enter, isComposing: true }), false);
  assert.equal(shouldSubmitChatKey(enter, true), false);
  // Some browsers reset isComposing before the final composition keydown.
  assert.equal(shouldSubmitChatKey({ ...enter, keyCode: 229 }), false);
  assert.equal(shouldSubmitChatKey({ ...enter, key: "a", keyCode: 65 }), false);
});

test("recommendation and retry buttons preserve a different unsent multiline question", () => {
  const draft = "查看昨日首板\n补充数据缺失情况";
  assert.equal(draftAfterChatSubmit(draft, "总结最新交易日的首板结构"), draft);
  assert.equal(draftAfterChatSubmit(draft, "重试的问题"), draft);
  assert.equal(draftAfterChatSubmit(draft), "");
});

test("unavailable browser storage does not prevent session restore or use", () => {
  const fail = () => { throw new Error("SecurityError"); };
  assert.equal(readActiveChatSession(fail), null);
  assert.doesNotThrow(() => rememberActiveChatSession(fail));
  assert.equal(readActiveChatSession(() => "saved-session"), "saved-session");
  let remembered: string | null = null;
  rememberActiveChatSession(() => { remembered = "new-session"; });
  assert.equal(remembered, "new-session");
});
