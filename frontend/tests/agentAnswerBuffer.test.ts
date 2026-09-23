import assert from "node:assert/strict";
import test from "node:test";
import { createAgentAnswerBuffer } from "../src/utils/agentAnswerBuffer.ts";
import { streamChat } from "../src/utils/agentChatTransport.ts";

function fixture() {
  const frames = new Map<number, () => void>();
  const published: string[] = [];
  let id = 0;
  const buffer = createAgentAnswerBuffer(
    content => published.push(content),
    callback => { frames.set(++id, callback); return id; },
    handle => { frames.delete(handle); },
  );
  return { buffer, frames, published, paint: () => [...frames.values()].forEach(callback => callback()) };
}

test("a burst of Unicode chunks is painted once, without slicing previously received text", () => {
  const { buffer, frames, published, paint } = fixture();
  for (let offset = 0; offset < 1000; offset += 2) buffer.append(offset, "中📊");
  assert.equal(frames.size, 1);
  assert.deepEqual(published, []);
  paint();
  assert.deepEqual(published, ["中📊".repeat(500)]);
  assert.equal(frames.size, 0);
});

test("reconnect replay neither clears nor shortens the visible answer", () => {
  const { buffer, published, paint } = fixture();
  buffer.start(1);
  buffer.append(0, "已确认📊结果");
  paint();
  buffer.start(1);
  buffer.append(0, "已确认");
  paint();
  assert.deepEqual(published, ["已确认📊结果"]);
  buffer.append(3, "📊结果完整");
  paint();
  assert.equal(published.at(-1), "已确认📊结果完整");
});

test("completion and unmount discard queued drafts; interruption can flush received content", () => {
  const { buffer, published, paint, frames } = fixture();
  buffer.append(0, "接收片段");
  buffer.flush();
  assert.deepEqual(published, ["接收片段"]);
  buffer.append(4, "尾部");
  buffer.dispose();
  buffer.append(0, "过期事件");
  paint();
  assert.equal(frames.size, 0);
  assert.deepEqual(published, ["接收片段"]);
});

test("a new revision clears an old draft immediately, including queued characters", () => {
  const { buffer, published, paint } = fixture();
  buffer.start(1);
  buffer.append(0, "旧回答");
  paint();
  buffer.append(3, "未发布");
  buffer.start(2);
  assert.deepEqual(published, ["旧回答", ""]);
  buffer.append(0, "新回答📊");
  paint();
  assert.equal(published.at(-1), "新回答📊");
});

test("withdrawal immediately clears a draft and cancels its scheduled paint", () => {
  const { buffer, published, paint, frames } = fixture();
  buffer.start(1);
  buffer.append(0, "待撤回");
  paint();
  buffer.append(3, "尾部");
  buffer.reset();
  assert.equal(frames.size, 0);
  paint();
  buffer.start(1);
  buffer.append(0, "待撤回");
  paint();
  assert.deepEqual(published, ["待撤回", ""]);
  buffer.start(2);
  buffer.append(0, "修正");
  paint();
  assert.equal(published.at(-1), "修正");
});

test("missing offsets fail visibly instead of silently corrupting the answer", () => {
  const { buffer } = fixture();
  assert.throws(() => buffer.append(2, "缺口"), /不连续/);
  assert.throws(() => buffer.append(-1, "负值"), /不连续/);
  buffer.append(0, "已有");
  assert.throws(() => buffer.append(0, "不同"), /冲突/);
});

test("read-only reconnect continues the same Unicode buffer after the last applied event", async () => {
  const { buffer, published, paint } = fixture();
  const frame = (id: number, event: string, data: unknown) => `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  let calls = 0;
  const result = await streamChat("", { session_id: "s", message: "问题" }, event => {
    if (event.event === "answer_start") buffer.start(event.data.revision);
    if (event.event === "answer_delta") { buffer.append(event.data.offset, event.data.delta); paint(); }
  }, async (url) => {
    calls++;
    if (calls === 1) return new Response(
      frame(1, "answer_start", { run_id: "r", revision: 1, provisional: true, answer_length: null })
      + frame(2, "answer_delta", { offset: 0, delta: "实时📊" }),
      { headers: { "X-Agent-Run-Id": "r" } },
    );
    assert.equal(String(url), "/api/agents/chat/runs/r/stream?after=2");
    assert.deepEqual(published, ["实时📊"]);
    return new Response(frame(2, "answer_delta", { offset: 0, delta: "实时📊" })
      + frame(3, "answer_delta", { offset: 3, delta: "续传" })
      + frame(4, "completed", { answer: "最终回答" }));
  });
  buffer.dispose();
  assert.deepEqual(published, ["实时📊", "实时📊续传"]);
  assert.equal(result.answer, "最终回答");
});

test("a fresh POST retry replays revisions from zero without keeping a failed draft", async () => {
  const { buffer, published, paint } = fixture();
  const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  await streamChat("", { session_id: "s", message: "问题" }, event => {
    if (event.event === "answer_start") buffer.start(event.data.revision);
    if (event.event === "answer_delta") { buffer.append(event.data.offset, event.data.delta); paint(); }
    if (event.event === "answer_reset") buffer.reset();
  }, async (_url, init) => {
    assert.equal(init?.method, "POST");
    return new Response(frame("answer_start", { revision: 1 })
      + frame("answer_delta", { offset: 0, delta: "被撤回的回答" })
      + frame("answer_reset", { message: "正在重新整理回答" })
      + frame("answer_start", { revision: 2 })
      + frame("answer_delta", { offset: 0, delta: "新的回答" })
      + frame("completed", { answer: "新的回答" }));
  });
  assert.deepEqual(published, ["被撤回的回答", "", "新的回答"]);
});

test("coalesced SSE completion remains authoritative over queued chunks", async () => {
  const { buffer, published, paint } = fixture();
  const final = { session_id: "s", run_id: "r", answer: "最终📊回答", task_status: "complete" };
  const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  const data = frame("answer_start", { run_id: "r", answer_length: null, revision: 1, provisional: true })
    + frame("answer_delta", { offset: 0, delta: "最终📊" })
    + frame("answer_delta", { offset: 3, delta: "回答" })
    + frame("completed", final);
  const response = await streamChat("", { session_id: "s", message: "问题" }, event => {
    if (event.event === "answer_start") buffer.start(event.data.revision);
    if (event.event === "answer_delta") buffer.append(event.data.offset, event.data.delta);
  }, async () => new Response(data));
  buffer.dispose();
  published.push(response.answer);
  paint();
  assert.deepEqual(published, [final.answer]);
});
