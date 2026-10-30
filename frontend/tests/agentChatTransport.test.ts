import assert from "node:assert/strict";
import test from "node:test";
import { streamChat, streamExistingRun, taskStatusLabel } from "../src/utils/agentChatTransport.ts";
import type { AgentRunState } from "../src/types.ts";

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

test("split UTF-8 and CRLF frames stream candidate text and publish the final response", async () => {
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
  assert.deepEqual(events, [
    "accepted", "answer_start", "answer_delta", "answer_delta", "answer_reset", "progress", "completed",
  ]);
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

test("native graph status and tool metadata update progress without becoming an answer", async () => {
  const events: string[] = [];
  const result = await streamChat("", payload, event => events.push(event.event), async () => response(
    frame("status", { stage: "planning", message: "正在理解问题", event_version: 1 })
    + frame("tool_start", { stage: "tools", message: "正在执行数据查询", tool_call_id: "call-1" })
    + frame("tool_end", { stage: "tools", message: "数据查询已结束", status: "partial" })
    + frame("warning", { stage: "tools", message: "部分查询未能完成" })
    + frame("updates", { answer: "private-node-output" })
    + frame("completed", final), true,
  ));
  assert.equal(result.answer, final.answer);
  assert.deepEqual(events, ["accepted", "status", "tool_start", "tool_end", "warning", "completed"]);
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

test("candidate frames advance the replay cursor and are replaced by the final response", async () => {
  const events: string[] = [];
  const urls: string[] = [];
  await streamChat("", payload, event => events.push(event.event), async url => {
    urls.push(String(url));
    return urls.length === 1
      ? response("id: 8\r\n" + frame("answer_start", { revision: 1 })
        + "id: 9\r\n" + frame("answer_delta", { offset: 0, delta: "断线前" })
        + "id: 10\r\n" + frame("answer_reset", { message: "重写" }))
      : response("id: 9\r\n" + frame("answer_delta", { offset: 0, delta: "重复草稿" })
        + "id: 11\r\n" + frame("completed", final));
  });
  assert.ok(urls[1].endsWith("?after=10"));
  assert.deepEqual(events, [
    "accepted", "answer_start", "answer_delta", "answer_reset", "progress", "accepted", "completed",
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
  assert.equal(taskStatusLabel("partial"), "查询已结束");
  assert.equal(taskStatusLabel("cancelled"), "已取消");
  assert.equal(taskStatusLabel("error"), "执行失败");
  assert.equal(taskStatusLabel("success"), undefined);
  assert.equal(taskStatusLabel("toString"), undefined);
});

const paused: AgentRunState = { run_id: "r", session_id: "s", run_status: "paused", state_revision: 2,
  can_pause: false, can_resume: true, can_cancel: true, last_event_id: 9 };

test("paused is a successful stream outcome without a completed answer or automatic resume", async () => {
  const events: string[] = [];
  let calls = 0;
  const result = await streamChat("", payload, event => events.push(event.event), async () => {
    calls++;
    return response(frame("run_state", { ...paused, run_status: "pausing", state_revision: 1 })
      + "id: 9\r\n" + frame("run_state", paused));
  });
  assert.deepEqual(result, { paused: true, run: paused });
  assert.deepEqual(events, ["accepted", "run_state", "run_state"]);
  assert.equal(calls, 1);
});

test("resubscribing after resume is read-only and skips stale pause state", async () => {
  const events: string[] = [];
  const requests: { url: string; method: string }[] = [];
  const result = await streamExistingRun("", { ...paused, run_status: "resuming", state_revision: 3 },
    event => events.push(event.event), async (url, options) => {
      requests.push({ url: String(url), method: options?.method ?? "GET" });
      return response("id: 9\r\n" + frame("run_state", paused)
        + "id: 10\r\n" + frame("run_state", { ...paused, run_status: "running", state_revision: 4 })
        + frame("completed", final));
    }, { after: 8 });
  assert.deepEqual(result, final);
  assert.deepEqual(requests, [{ url: "/api/agents/chat/runs/r/stream?after=8", method: "GET" }]);
  assert.deepEqual(events, ["accepted", "run_state", "completed"]);
});

test("run state from another session is rejected instead of pausing the selected conversation", async () => {
  await assert.rejects(streamExistingRun("", paused, () => {}, async () => response(
    frame("run_state", { ...paused, session_id: "other" }),
  )), /当前会话不一致/);
});

test("detaching a view aborts its subscription without another GET or a control mutation", async () => {
  const controller = new AbortController();
  let calls = 0;
  await assert.rejects(streamExistingRun("", paused, () => {}, async (_url, init) => {
    calls++;
    assert.equal(init?.signal, controller.signal);
    controller.abort();
    throw new DOMException("Aborted", "AbortError");
  }, { signal: controller.signal }));
  assert.equal(calls, 1);
});

test("paused with cancellation requested keeps streaming until the formal cancelled result", async () => {
  const cancelling = { ...paused, cancel_requested: true, can_resume: false, can_cancel: false };
  const terminal = { ...final, task_status: "cancelled" };
  const events: string[] = [];
  const result = await streamExistingRun("", cancelling, event => events.push(event.event), async () => response(
    frame("run_state", cancelling) + frame("completed", terminal),
  ));
  assert.deepEqual(result, terminal);
  assert.deepEqual(events, ["accepted", "run_state", "completed"]);
});
