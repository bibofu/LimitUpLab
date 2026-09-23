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
  buffer.append(0, "已确认📊结果");
  paint();
  buffer.reset();
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

test("an explicit retry preserves the partial answer until replay catches up", () => {
  const { buffer, published, paint } = fixture();
  buffer.reset("已显示的回答");
  buffer.append(0, "已显示");
  paint();
  assert.deepEqual(published, []);
  buffer.append(3, "的回答与后续");
  paint();
  assert.deepEqual(published, ["已显示的回答与后续"]);
});

test("missing offsets fail visibly instead of silently corrupting the answer", () => {
  const { buffer } = fixture();
  assert.throws(() => buffer.append(2, "缺口"), /不连续/);
  assert.throws(() => buffer.append(-1, "负值"), /不连续/);
});

test("coalesced SSE completion remains authoritative over queued chunks", async () => {
  const { buffer, published, paint } = fixture();
  const final = { session_id: "s", run_id: "r", answer: "最终📊回答", task_status: "complete" };
  const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  const data = frame("answer_start", { run_id: "r", answer_length: 6 })
    + frame("answer_delta", { offset: 0, delta: "最终📊" })
    + frame("answer_delta", { offset: 3, delta: "回答" })
    + frame("completed", final);
  const response = await streamChat("", { session_id: "s", message: "问题" }, event => {
    if (event.event === "answer_start") buffer.reset();
    if (event.event === "answer_delta") buffer.append(event.data.offset, event.data.delta);
  }, async () => new Response(data));
  buffer.dispose();
  published.push(response.answer);
  paint();
  assert.deepEqual(published, [final.answer]);
});
