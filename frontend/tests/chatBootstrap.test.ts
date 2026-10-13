import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";
import * as http from "../src/utils/jsonRequest.ts";

const compiled = ts.transpileModule(readFileSync(new URL("../src/utils/chatBootstrap.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const module = { exports: {} as any };
new Function("require", "module", "exports", compiled)((id: string) => {
  assert.equal(id, "./jsonRequest");
  return http;
}, module, module.exports);
const { loadChatBootstrap } = module.exports;
const session = (id: string) => ({ session_id: id, messages: [] });
const list = (...ids: string[]) => ({ sessions: ids.map(session), generated_by: "verified-owner" });

function server(responses: (unknown | Error)[]) {
  const calls: string[] = [];
  return {
    calls,
    request: async (path: string, options: RequestInit) => {
      calls.push(path);
      assert.equal(options.cache, "no-store");
      const next = responses.shift();
      assert.notEqual(next, undefined, "unexpected extra request");
      if (next instanceof Error) throw next;
      return next;
    },
  };
}
const notFound = () => new http.HttpResponseError(404, "Not Found");

test("a supported bootstrap restores history in one request", async () => {
  const value = { ...list("current"), selected_session: session("current") };
  const api = server([value]);
  assert.deepEqual(await loadChatBootstrap(api.request, "current"), value);
  assert.deepEqual(api.calls, ["/api/agents/chat/bootstrap?session_id=current"]);
});

for (const status of [404, 405]) {
  test(`an unavailable bootstrap (${status}) recovers through verified list and detail`, async () => {
    const api = server([new http.HttpResponseError(status, "endpoint unavailable"), list("current"), session("current")]);
    const result = await loadChatBootstrap(api.request);
    assert.equal(result.selected_session.session_id, "current");
    assert.deepEqual(api.calls, ["/api/agents/chat/bootstrap", "/api/agents/chat/sessions?limit=30", "/api/agents/chat/sessions/current"]);
  });
}

test("verified empty history returns a normal empty result without creating or reading a session", async () => {
  const api = server([notFound(), list()]);
  assert.deepEqual(await loadChatBootstrap(api.request), { ...list(), selected_session: null });
  assert.equal(api.calls.length, 2);
});

test("a saved ID outside the first history page is read only after owner verification", async () => {
  const api = server([notFound(), list("recent"), session("saved/id")]);
  const result = await loadChatBootstrap(api.request, "saved/id");
  assert.equal(result.selected_session.session_id, "saved/id");
  assert.deepEqual(api.calls.slice(1), ["/api/agents/chat/sessions?limit=30", "/api/agents/chat/sessions/saved%2Fid"]);
});

test("a stale or foreign saved ID falls back to the server-verified current owner history", async () => {
  const api = server([notFound(), list("current"), notFound(), session("current")]);
  const result = await loadChatBootstrap(api.request, "previous-owner");
  assert.equal(result.selected_session.session_id, "current");
  assert.equal(result.generated_by, "verified-owner");
  assert.equal(api.calls.length, 4);
});

test("a deleted listed session reconciles once before treating the history as empty", async () => {
  const api = server([notFound(), list("deleted"), notFound(), list()]);
  assert.deepEqual(await loadChatBootstrap(api.request, "deleted"), { ...list(), selected_session: null });
  assert.equal(api.calls.length, 4);
});

test("continued deletion races stop with an error instead of unbounded retries", async () => {
  const lastError = notFound();
  const api = server([notFound(), list("deleted"), notFound(), list("also-deleted"), lastError]);
  await assert.rejects(loadChatBootstrap(api.request), error => error === lastError);
  assert.equal(api.calls.length, 5);
});

test("bootstrap auth, server and transport failures never trigger legacy reads", async () => {
  for (const error of [new http.HttpResponseError(401, "Unauthorized"), new http.HttpResponseError(403, "Forbidden"),
    new http.HttpResponseError(500, "Internal Server Error"), new TypeError("Failed to fetch"), new Error("Not Found")]) {
    const api = server([error]);
    await assert.rejects(loadChatBootstrap(api.request), caught => caught === error);
    assert.equal(api.calls.length, 1);
  }
});

test("failed legacy reads remain errors and cannot be mistaken for no conversations", async () => {
  for (const response of [[notFound(), new http.HttpResponseError(503, "Unavailable")],
    [notFound(), list("owned"), new http.HttpResponseError(403, "Forbidden")]]) {
    const error = response.at(-1);
    const api = server([...response]);
    await assert.rejects(loadChatBootstrap(api.request), caught => caught === error);
    assert.equal(api.calls.length, response.length);
  }
});
