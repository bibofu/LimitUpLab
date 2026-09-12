import assert from "node:assert/strict";
import test from "node:test";
import { streamChat, taskStatusLabel } from "../src/utils/agentChatTransport.ts";

const payload = { session_id: "s", message_id: "same", message: "查询" };
const final = { session_id: "s", run_id: "r", task_status: "partial", answer: "缺少部分来源" };
const frame = (name: string, data: unknown) => `event: ${name}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`;
function response(text: string, oneByte = false) {
  const data = new TextEncoder().encode(text);
  return new Response(new ReadableStream({ start(controller) {
    if (oneByte) for (const value of data) controller.enqueue(Uint8Array.of(value));
    else controller.enqueue(data);
    controller.close();
  } }), { headers: { "X-Agent-Run-Id": "r" } });
}

test("split UTF-8 and CRLF frames preserve the validated partial answer", async () => {
  const events: string[] = [];
  const result = await streamChat("", payload, event => events.push(event.event), async () => response(
    frame("answer_delta", { delta: "unreviewed" }) + frame("completed", final), true,
  ));
  assert.equal(result.answer, final.answer);
  assert.equal(result.task_status, "partial");
  assert.ok(!events.includes("answer_delta"));
});

test("disconnect reconnects once with cursor through GET, never another POST", async () => {
  const calls: { url: string; method: string }[] = [];
  const fetcher: typeof fetch = async (url, init) => {
    calls.push({ url: String(url), method: init?.method ?? "GET" });
    return calls.length === 1
      ? response("id: 7\r\n" + frame("progress", { stage: "tools", message: "query" }))
      : response(frame("completed", final));
  };
  assert.equal((await streamChat("", payload, () => {}, fetcher)).task_status, "partial");
  assert.deepEqual(calls, [
    { url: "/api/agents/chat/stream", method: "POST" },
    { url: "/api/agents/chat/runs/r/stream?after=7", method: "GET" },
  ]);
});

test("second disconnect fails without an unbounded retry", async () => {
  let calls = 0;
  await assert.rejects(streamChat("", payload, () => {}, async () => { calls++; return response(""); }));
  assert.equal(calls, 2);
});

test("server interruption is shown without retrying or spending another model call", async () => {
  let calls = 0;
  await assert.rejects(streamChat("", payload, () => {}, async () => {
    calls++;
    return response(frame("error", { message: "任务中断，请重试恢复" }));
  }), /任务中断/);
  assert.equal(calls, 1);
});

test("unknown and historical execution status are not labelled complete", () => {
  assert.equal(taskStatusLabel("partial"), "部分完成");
  assert.equal(taskStatusLabel("cancelled"), "已取消");
  assert.equal(taskStatusLabel("error"), "执行失败");
  assert.equal(taskStatusLabel("success"), undefined);
  assert.equal(taskStatusLabel("toString"), undefined);
});
