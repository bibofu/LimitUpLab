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

test("split UTF-8 and CRLF frames hide legacy candidates and publish only the final response", async () => {
  const events: string[] = [];
  const result = await streamChat("", payload, event => events.push(event.event), async () => response(
    frame("answer_start", { run_id: "r", answer_length: null, revision: 1, provisional: true })
      + frame("answer_delta", { offset: 0, delta: "生成中" })
      + frame("answer_delta", { offset: 3, delta: "回答" })
      + frame("answer_reset", { message: "重新整理回答" })
      + frame("progress", { stage: "checking", message: "核对结果" })
      + frame("completed", final), true,
  ));
  assert.equal(result.answer, final.answer);
  assert.equal(result.task_status, "partial");
  assert.deepEqual(events, ["accepted", "progress", "completed"]);
});

test("the first completed frame is terminal even when duplicate completion or error follows", async () => {
  const events: string[] = [];
  const result = await streamChat("", payload, event => events.push(event.event), async () => response(
    frame("completed", final)
    + frame("completed", { ...final, answer: "重复结果" })
    + frame("error", { message: "过期事件" }),
  ));
  assert.equal(result.answer, final.answer);
  assert.deepEqual(events, ["accepted", "completed"]);
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

test("ignored legacy drafts advance the replay cursor and never reach the UI after reconnect", async () => {
  const events: string[] = [];
  const urls: string[] = [];
  await streamChat("", payload, event => events.push(event.event), async url => {
    urls.push(String(url));
    return urls.length === 1
      ? response("id: 8\r\n" + frame("answer_start", { revision: 1 })
        + "id: 9\r\n" + frame("answer_delta", { offset: 0, delta: "不能展示" })
        + "id: 10\r\n" + frame("answer_reset", { message: "重写" }))
      : response("id: 9\r\n" + frame("answer_delta", { offset: 0, delta: "重复草稿" })
        + "id: 11\r\n" + frame("completed", final));
  });
  assert.ok(urls[1].endsWith("?after=10"));
  assert.deepEqual(events, ["accepted", "progress", "accepted", "completed"]);
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
  assert.equal(taskStatusLabel("partial"), "查询已结束");
  assert.equal(taskStatusLabel("cancelled"), "已取消");
  assert.equal(taskStatusLabel("error"), "执行失败");
  assert.equal(taskStatusLabel("success"), undefined);
  assert.equal(taskStatusLabel("toString"), undefined);
});
