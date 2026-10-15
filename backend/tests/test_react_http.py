"""Real loopback HTTP against the production routes, with frozen model/data."""

import json
import socket
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from langchain_core.messages import AIMessage

from app.agents.react_runtime import runtime as runtime_module
from app.agents.react_runtime.compliance import ComplianceReview
from app.agents.react_runtime.lifecycle import Journal
from app.agents.react_runtime.runtime import run
from app.agents.tools import TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest, AgentChatResponse
from app.repositories import SQLiteChatSessionRepository
from app.routers import agents, react_chat
from app.security import current_owner_id
from app.services.prompt_security import PromptInjectionAssessment
from test_react_lifecycle import Model


@pytest.fixture(autouse=True)
def allow_compliance_review(monkeypatch):
    monkeypatch.setattr(
        runtime_module,
        "review_answer",
        lambda *args, **kwargs: ComplianceReview(
            decision="allow", violations=[], reason="test fixture allows research answer",
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "review_input",
        lambda *args, **kwargs: PromptInjectionAssessment(
            decision="allow", signals=[], reason="test fixture allows normal input", request_kind="research",
        ),
    )


@pytest.fixture(params=["react", "legacy", "task"])
def http_server(tmp_path, monkeypatch, request):
    monkeypatch.setenv("LIMITUPLAB_DATABASE_PATH", str(tmp_path / "http.sqlite"))
    monkeypatch.setenv("LIMITUPLAB_AGENT_RUNTIME", request.param)
    state = SimpleNamespace(calls=0, block=False, entered=threading.Event(), release=threading.Event())
    def ranking(**args):
        state.calls += 1
        state.entered.set()
        if state.block:
            assert state.release.wait(10)
        return ToolResult(name="hot_stock_ranking", input=args, summary="fixture", output={"items": [{"symbol": "000001"}]})
    registry = SimpleNamespace(events=[], schemas=lambda: TOOL_SCHEMAS, is_enabled=lambda _: True, hot_stock_ranking=ranking)
    monkeypatch.setattr(agents, "answer_first_board_chat", lambda request, **kw: run(request, registry, Model(), progress=kw.get("progress_callback")))
    monkeypatch.setattr(agents, "prepare_session_context", lambda **kw: ([], None))
    monkeypatch.setattr(agents, "get_limit_up_repository", lambda: SimpleNamespace(list_events=lambda: []))
    monkeypatch.setattr(agents, "_begin_agent_request", lambda *a, **kw: (SimpleNamespace(release=lambda: None), None, None))
    monkeypatch.setattr(agents, "_finish_agent_request", lambda *a, **kw: None)
    app = FastAPI()
    def owner(request: Request):
        return request.headers.get("x-fixture-owner", "owner")
    app.dependency_overrides[current_owner_id] = owner
    app.include_router(agents.router, prefix="/api/agents")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline: time.sleep(.01)
    assert server.started
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}/api/agents", trust_env=False, timeout=10) as client:
            yield client, state
    finally:
        state.release.set()
        for worker in list(react_chat._workers.values()): worker.join(5)
        server.should_exit = True
        thread.join(5)
        sock.close()
        assert not thread.is_alive()


PAYLOAD = {"session_id": "http-s", "message_id": "http-m", "message": "查询热榜，缺失时说明缺口"}


def test_rejected_start_does_not_poison_retry_or_create_a_chat_answer(http_server, monkeypatch):
    client, state = http_server
    begin = agents._begin_agent_request
    def rejected(*args, **kwargs):
        raise HTTPException(429, "请求较多，请稍后重试", headers={"Retry-After": "2"})
    monkeypatch.setattr(agents, "_begin_agent_request", rejected)
    rejected_response = client.post("/chat/stream", json=PAYLOAD)
    assert rejected_response.status_code == 429
    assert rejected_response.headers["retry-after"] == "2"
    assert state.calls == 0
    assert not SQLiteChatSessionRepository().get_session("http-s", owner_id="owner").messages
    with Journal().connection() as db:
        assert db.execute("SELECT count(*) FROM react_runs").fetchone()[0] == 0
    monkeypatch.setattr(agents, "_begin_agent_request", begin)
    result = client.post("/chat", json=PAYLOAD).json()
    assert result["task_status"] == "partial" and state.calls == 1
    assert client.post("/chat", json=PAYLOAD).json() == result
    messages = SQLiteChatSessionRepository().get_session("http-s", owner_id="owner").messages
    assert [message.role for message in messages] == ["user", "assistant"]


def test_rejected_resume_preserves_checkpoint_and_completed_tools(http_server, monkeypatch):
    client, state = http_server
    SQLiteChatSessionRepository().ensure_session("http-s", owner_id="owner")
    journal = Journal()
    row, _ = journal.create(AgentChatRequest(**PAYLOAD), "owner")
    run_id = row["run_id"]
    checkpoint = {"saved": "research progress"}
    journal.checkpoint(run_id, "owner", checkpoint)
    call = {"id": "saved-tool", "name": "fixture", "args": {}}
    journal.begin_call(run_id, call)
    journal.finish_call(run_id, call["id"], {"ok": True})
    def rejected(*args, **kwargs):
        raise HTTPException(429, "请求较多，请稍后重试")
    monkeypatch.setattr(agents, "_begin_agent_request", rejected)
    assert client.post("/chat", json=PAYLOAD).status_code == 429
    restored = journal.get(run_id, "owner")
    assert restored["active"] and restored["response_json"] is None
    assert json.loads(restored["checkpoint_json"]) == checkpoint
    assert journal.begin_call(run_id, call) == {"ok": True}
    assert not SQLiteChatSessionRepository().get_session("http-s", owner_id="owner").messages
    assert state.calls == 0


@pytest.mark.parametrize("failure_stage", ["message", "thread"])
def test_start_failure_releases_lease_and_allows_original_request_retry(http_server, monkeypatch, failure_stage):
    client, state = http_server
    releases, finished = [], []
    monkeypatch.setattr(agents, "_begin_agent_request", lambda *a, **kw: (
        SimpleNamespace(release=lambda: releases.append(True)), None, None,
    ))
    monkeypatch.setattr(agents, "_finish_agent_request", lambda *a, **kw: finished.append(kw))
    target = SQLiteChatSessionRepository if failure_stage == "message" else threading.Thread
    method = "append_message" if failure_stage == "message" else "start"
    original = getattr(target, method)
    def fail(*args, **kwargs):
        if failure_stage == "thread" and not args[0].name.startswith("react-"):
            return original(*args, **kwargs)
        raise RuntimeError("fixture startup unavailable")
    monkeypatch.setattr(target, method, fail)
    assert client.post("/chat", json=PAYLOAD).status_code == 503
    assert len(releases) == 1 and len(finished) == 1 and finished[0]["error"]
    assert not react_chat._workers and state.calls == 0
    monkeypatch.setattr(target, method, original)
    result = client.post("/chat", json=PAYLOAD).json()
    assert result["task_status"] == "partial" and state.calls == 1
    messages = SQLiteChatSessionRepository().get_session("http-s", owner_id="owner").messages
    assert [message.role for message in messages] == ["user", "assistant"]


def test_only_progress_is_public_until_the_final_answer_is_saved(http_server, monkeypatch):
    client, state = http_server
    saving, finish = threading.Event(), threading.Event()
    original_finish = Journal.finish
    final_text = "研究名单如下。\n\n| 代码 | 名称 |\n| --- | --- |\n| 000001 | 平安银行 |"
    def save(journal, *args):
        saving.set()
        assert finish.wait(10)
        return original_finish(journal, *args)
    monkeypatch.setattr(Journal, "finish", save)
    def answer(request, progress_callback, **kwargs):
        state.calls += 1
        progress_callback("answering", "正在生成回答")
        state.entered.set()
        assert state.release.wait(10)
        return AgentChatResponse(session_id=request.session_id, intent="react_research", answer=final_text,
                                 stock_mentions=[{"symbol": "000001", "name": "平安银行"}],
                                 task_status="complete", generated_by="test")
    monkeypatch.setattr(agents, "answer_first_board_chat", answer)
    try:
        with client.stream("POST", "/chat/stream", json=PAYLOAD) as response:
            run_id = response.headers["x-agent-run-id"]
            received, cursor = [], 0
            for line in response.iter_lines():
                received.append(line)
                if line.startswith("id:"):
                    cursor = int(line[3:])
                if line.startswith("data:") and "正在生成回答" in line:
                    break
            assert state.entered.wait(5)
            assert not state.release.is_set()
            assert Journal().get(run_id, "owner")["response_json"] is None
            assert "event: completed" not in received
            assert not any("answer_" in line or "平安银行" in line for line in received)
            assert cursor > 0
        with client.stream("GET", f"/chat/runs/{run_id}/stream?after={cursor}") as response:
            state.release.set()
            lines, received = response.iter_lines(), []
            for line in lines:
                received.append(line)
                if line.startswith("data:") and "正在保存研究结果" in line:
                    break
            assert saving.wait(5)
            assert not any("answer_" in line or "平安银行" in line for line in received)
            assert Journal().get(run_id, "owner")["response_json"] is None
            finish.set()
            received.extend(lines)
    finally:
        state.release.set()
        finish.set()
    assert received.count("event: completed") == 1
    final = next(json.loads(line[5:]) for line in received if line.startswith("data:") and '"answer"' in line)
    assert final["answer"] == final_text
    assert final["stock_mentions"][0]["symbol"] == "000001"
    assert final == json.loads(Journal().get(run_id, "owner")["response_json"])
    assert all(event["event"] == "progress" for event in Journal().events(run_id, "owner"))
    replay = client.get(f"/chat/runs/{run_id}/stream")
    assert "event: completed" in replay.text
    assert "event: answer_start" not in replay.text and "event: answer_delta" not in replay.text
    assert state.calls == 1


def test_explicit_resume_never_replays_old_persisted_drafts(http_server, monkeypatch):
    client, state = http_server
    SQLiteChatSessionRepository().ensure_session("http-s", owner_id="owner")
    journal = Journal()
    row, _ = journal.create(AgentChatRequest(**PAYLOAD), "owner")
    journal.event(row["run_id"], "answer_start", {"revision": 3})
    journal.event(row["run_id"], "answer_delta", {"offset": 0, "delta": "旧草稿"})
    journal.event(row["run_id"], "answer_reset", {"message": "旧撤回"})
    def prepare(**kwargs):
        state.entered.set()
        assert state.release.wait(10)
        return [], None
    monkeypatch.setattr(agents, "prepare_session_context", prepare)
    def answer(request, **kwargs):
        state.calls += 1
        return AgentChatResponse(session_id=request.session_id, intent="react_research", answer="恢复回答",
                                 task_status="complete", generated_by="test")
    monkeypatch.setattr(agents, "answer_first_board_chat", answer)
    with client.stream("POST", "/chat/stream", json=PAYLOAD) as response:
        lines = response.iter_lines()
        received, cursor = [], 0
        for line in lines:
            received.append(line)
            if line.startswith("id:"):
                cursor = int(line[3:])
            if "正在准备会话与研究数据" in line:
                break
        assert state.entered.wait(5)
        assert not state.release.is_set()
        assert journal.get(row["run_id"], "owner")["response_json"] is None
        assert not any("answer_" in line or "旧草稿" in line or "旧撤回" in line for line in received)
    with client.stream("GET", f"/chat/runs/{row['run_id']}/stream?after={cursor}") as response:
        state.release.set()
        replay = response.read().decode()
        assert "恢复回答" in replay and replay.count("event: completed") == 1
        assert "旧草稿" not in replay and "event: answer_" not in replay
    revisions = [json.loads(event["payload_json"])["revision"] for event in journal.events(row["run_id"], "owner")
                 if event["event"] == "answer_start"]
    assert revisions == [3] and state.calls == 1


def test_save_failure_never_publishes_candidate_and_original_request_can_resume(http_server, monkeypatch):
    client, state = http_server
    original_finish = Journal.finish
    recorded = []
    def answer(request, **kwargs):
        state.calls += 1
        return AgentChatResponse(session_id=request.session_id, intent="react_research", answer="仅保存后可见的正文",
                                 task_status="complete", generated_by="test")
    def unavailable(*args, **kwargs):
        raise OSError("fixture storage unavailable")
    monkeypatch.setattr(agents, "answer_first_board_chat", answer)
    monkeypatch.setattr(agents, "_finish_agent_request", lambda *a, **kw: recorded.append(kw))
    monkeypatch.setattr(Journal, "finish", unavailable)
    failed = client.post("/chat/stream", json=PAYLOAD)
    run_id = failed.headers["x-agent-run-id"]
    assert "event: error" in failed.text and "event: completed" not in failed.text
    assert "仅保存后可见的正文" not in failed.text and "event: answer_" not in failed.text
    assert Journal().get(run_id, "owner")["response_json"] is None
    assert [m.role for m in SQLiteChatSessionRepository().get_session("http-s", owner_id="owner").messages] == ["user"]
    assert recorded[-1]["error"] is not None and recorded[-1]["response"] is None
    monkeypatch.setattr(Journal, "finish", original_finish)
    result = client.post("/chat", json=PAYLOAD).json()
    assert result["run_id"] == run_id and result["answer"] == "仅保存后可见的正文"
    assert client.post("/chat", json=PAYLOAD).json() == result and state.calls == 2


def test_cancel_during_save_publishes_only_the_committed_cancelled_result(http_server, monkeypatch):
    client, state = http_server
    original_finish = Journal.finish
    def save(journal, *args):
        state.entered.set()
        assert state.release.wait(10)
        return original_finish(journal, *args)
    def answer(request, **kwargs):
        return AgentChatResponse(session_id=request.session_id, intent="react_research", answer="不应显示的候选结论",
                                 task_status="complete", generated_by="test")
    monkeypatch.setattr(agents, "answer_first_board_chat", answer)
    monkeypatch.setattr(Journal, "finish", save)
    with client.stream("POST", "/chat/stream", json=PAYLOAD) as response:
        run_id = response.headers["x-agent-run-id"]
        lines, received = response.iter_lines(), []
        for line in lines:
            received.append(line)
            if "正在保存研究结果" in line:
                break
        assert state.entered.wait(5)
        assert client.post(f"/chat/runs/{run_id}/cancel").status_code == 200
        state.release.set()
        received.extend(lines)
    assert received.count("event: completed") == 1
    assert not any("不应显示的候选结论" in line or "event: answer_" in line for line in received)
    result = json.loads(Journal().get(run_id, "owner")["response_json"])
    assert result["task_status"] == "cancelled"
    assert result["answer"] == "任务已取消，未继续生成研究结论。"


def test_stream_wakeup_preserves_updates_between_poll_and_wait(monkeypatch):
    """A commit after polling but before waiting must not incur the poll delay."""
    async def read_frames():
        journal = SimpleNamespace(
            get=lambda *_: {"response_json": None},
            events=lambda *_: [],
        )
        worker = SimpleNamespace(is_alive=lambda: True)
        monkeypatch.setitem(react_chat._workers, "wakeup-run", worker)
        response = react_chat.stream(journal, {"run_id": "wakeup-run", "session_id": "s"}, "owner")
        frames = response.body_iterator
        assert "event: accepted" in await anext(frames)
        assert "heartbeat" in await anext(frames)
        # This models the exact gap after DB polling, before Condition.wait_for.
        react_chat.notify_streams()
        def unexpected_wait(_timeout=None):
            pytest.fail("A committed update was missed before the condition wait")
        monkeypatch.setattr(react_chat._updates, "wait", unexpected_wait)
        assert "heartbeat" in await anext(frames)
        await frames.aclose()
    import asyncio
    asyncio.run(read_frames())


def test_progress_arrives_before_blocking_input_review_and_final_answer(http_server, monkeypatch):
    client, state = http_server
    def review(*_args, **_kwargs):
        state.entered.set()
        assert state.release.wait(10)
        return PromptInjectionAssessment(
            decision="allow", signals=[], reason="fixture", request_kind="research",
        )
    monkeypatch.setattr(runtime_module, "review_input", review)
    with client.stream("POST", "/chat/stream", json=PAYLOAD) as response:
        run_id = response.headers["x-agent-run-id"]
        lines = response.iter_lines()
        received = []
        for line in lines:
            received.append(line)
            if "正在检查请求与研究范围" in line:
                break
        assert state.entered.wait(5)
        assert "event: answer_start" not in received
        assert any("正在准备会话与研究数据" in line for line in received)
        state.release.set()
        received.extend(lines)
    text = "\n".join(received)
    assert "event: completed" in text
    progress = [json.loads(event["payload_json"])["message"] for event in Journal().events(run_id, "owner")
                if event["event"] == "progress"]
    assert progress.index("正在整理结果并校验回答") < progress.index("正在保存研究结果")


def test_http_idempotency_reconnect_owner_and_delete(http_server):
    client, state = http_server
    response = client.post("/chat", json=PAYLOAD)
    assert response.status_code == 200
    result = response.json()
    key = result["run_id"]
    assert result["task_status"] == "partial"
    assert client.post("/chat", json=PAYLOAD).json() == result
    assert state.calls == 1
    assert client.post("/chat", json={**PAYLOAD, "message": "different"}).status_code == 409
    for path in (f"/chat/runs/{key}", f"/chat/runs/{key}/stream"):
        assert client.get(path, headers={"x-fixture-owner": "other"}).status_code == 404
    assert client.post(f"/chat/runs/{key}/cancel", headers={"x-fixture-owner": "other"}).status_code == 404
    events = Journal().events(key, "owner")
    replay = client.get(f"/chat/runs/{key}/stream", params={"after": events[-1]["seq"]})
    assert "event: answer_start" not in replay.text and "event: answer_delta" not in replay.text
    assert "event: completed" in replay.text
    assert "event: progress" not in replay.text
    assert '"task_status": "partial"' in replay.text or '"task_status":"partial"' in replay.text
    assert state.calls == 1
    assert client.delete("/chat/sessions/http-s").status_code == 200
    assert client.get(f"/chat/runs/{key}").status_code == 404


def test_disconnect_does_not_cancel_and_explicit_cancel_releases_session(http_server):
    client, state = http_server
    state.block = True
    with client.stream("POST", "/chat/stream", json=PAYLOAD) as response:
        assert response.status_code == 200
        key = response.headers["x-agent-run-id"]
        for line in response.iter_lines():
            if line.startswith("data:"): break
    assert state.entered.wait(5)
    status = client.get(f"/chat/runs/{key}").json()
    assert status["active"] and not status["cancel_requested"]
    assert client.post("/chat", json={**PAYLOAD, "message_id": "second"}).status_code == 409
    assert client.delete("/chat/sessions/http-s").status_code == 409
    assert client.post(f"/chat/runs/{key}/cancel").status_code == 200
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = client.get(f"/chat/runs/{key}").json()
        if not status["active"]: break
        time.sleep(.02)
    assert not status["active"] and status["response"]["task_status"] == "cancelled"
    assert state.calls == 1
    state.release.set()
    replay = client.post("/chat", json=PAYLOAD).json()
    assert replay["task_status"] == "cancelled" and state.calls == 1


def test_cancel_interrupted_run_does_not_restart_model(http_server):
    client, state = http_server
    SQLiteChatSessionRepository().ensure_session("http-s", owner_id="owner")
    journal = Journal()
    row, _ = journal.create(AgentChatRequest(**PAYLOAD), "owner")
    key = row["run_id"]
    assert client.get(f"/chat/runs/{key}").json()["interrupted"]
    assert "event: error" in client.get(f"/chat/runs/{key}/stream").text
    assert state.calls == 0
    assert client.post(f"/chat/runs/{key}/cancel").status_code == 200
    result = client.get(f"/chat/runs/{key}").json()
    assert not result["active"] and result["response"]["task_status"] == "cancelled"
    assert state.calls == 0


def test_http_publishes_plain_model_draft_after_typed_finish(http_server):
    client, _state = http_server
    guarded_registry = SimpleNamespace(
        events=[],
        schemas=lambda: TOOL_SCHEMAS,
        is_enabled=lambda _: True,
    )

    class PlainAnswer:
        calls = 0

        def generate_messages(self, messages, tools, **kwargs):
            self.calls += 1
            answer = "贵州茅台今天涨停，成交额100亿元。"
            if self.calls == 1:
                return AIMessage(content=answer)
            if self.calls == 2:
                return AIMessage(content="", tool_calls=[{"name": "finish", "id": "finish",
                    "args": {"status": "complete", "answer": answer}}])
            return AIMessage(content="", tool_calls=[{"name": "finish", "id": "repair",
                "args": {"status": "clarify", "answer": "本轮尚未取得行情证据。"}}])

    agents.answer_first_board_chat = lambda request, **kwargs: run(
        request,
        guarded_registry,
        PlainAnswer(),
        progress=kwargs.get("progress_callback"),
    )
    response = client.post("/chat", json={
        "session_id": "finish-gate-http",
        "message_id": "finish-gate-message",
        "message": "查询贵州茅台今天行情",
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_status"] == "clarify"
    assert "尚未取得行情证据" in payload["answer"]
    checks = [
        trace["output"]
        for trace in payload["tool_results"]
        if trace["name"] == "react_answer_check"
    ]
    assert checks[0]["passed"] is False
    assert "require evidence" in checks[0]["reason"]
    assert checks[1] == {"passed": True, "status": "clarify", "missing": []}
