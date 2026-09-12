"""ReAct recovery tests use SQLite and native messages, never the old planner."""

import json
import sqlite3
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agents.react_runtime.lifecycle import CURRENT_CONTROL, Journal, RunConflict, RunControl
from app.agents.react_runtime.runtime import run
from app.agents.tools import TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest, AgentChatResponse
from app.repositories import SQLiteChatSessionRepository


@pytest.fixture
def journal(tmp_path):
    path = tmp_path / "react.sqlite"
    SQLiteChatSessionRepository(path).ensure_session("s", owner_id="owner")
    return Journal(path)


def request(**kwargs):
    return AgentChatRequest(session_id="s", message_id="m", message="研究事实", **kwargs)


def final(status="partial"):
    return AgentChatResponse(session_id="s", intent="react_research", answer="部分数据缺失。",
        task_status=status, stop_reason="answered", generated_by="react-runtime-v1").model_dump(mode="json")


def test_idempotency_conflict_and_owner_scope(journal):
    row, fresh = journal.create(request(), "owner")
    assert fresh
    assert journal.create(request(), "owner") == (row, False)
    altered = request().model_copy(update={"message": "different"})
    with pytest.raises(RunConflict): journal.create(altered, "owner")
    with pytest.raises(RunConflict): journal.create(request().model_copy(update={"message_id": "new"}), "owner")
    with pytest.raises(KeyError): journal.create(request(), "other")
    for operation in (journal.get, journal.events, journal.cancel):
        with pytest.raises(KeyError): operation(row["run_id"], "other")


def test_finalization_atomic_immutable_and_task_status_distinct(journal):
    row, _ = journal.create(request(), "owner")
    key = row["run_id"]
    # Inject a write failure after agent_runs insert: all writes must roll back.
    with journal.connection() as db:
        db.execute("CREATE TRIGGER fail_answer BEFORE INSERT ON chat_messages BEGIN SELECT RAISE(ABORT, 'fixture'); END")
    with pytest.raises(sqlite3.IntegrityError): journal.finish(key, "owner", final())
    with journal.connection() as db:
        assert db.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_answer")
    assert journal.get(key, "owner")["active"]
    response = journal.finish(key, "owner", final())
    assert journal.finish(key, "owner", final("complete")) == response
    with journal.connection() as db:
        saved = db.execute("SELECT * FROM agent_runs").fetchone()
        assert saved["status"] == "success" and json.loads(saved["output_json"])["task_status"] == "partial"
        assert db.execute("SELECT count(*) FROM chat_messages").fetchone()[0] == 1
    assert not journal.get(key, "owner")["active"]


def test_cancel_wins_finalization_and_session_deletion_cascades(journal):
    row, _ = journal.create(request(), "owner")
    key = row["run_id"]
    chats = SQLiteChatSessionRepository(journal.database_path)
    with pytest.raises(RunConflict): chats.delete_session("s", owner_id="owner")
    journal.event(key, "progress", {"message": "query"})
    journal.checkpoint(key, "owner", {"evidence": "private"})
    journal.begin_call(key, {"id": "c", "name": "test", "args": {}})
    journal.cancel(key, "owner")
    assert journal.finish(key, "owner", final("complete"))["task_status"] == "cancelled"
    assert not chats.delete_session("s", owner_id="other")
    assert chats.delete_session("s", owner_id="owner")
    with journal.connection() as db:
        for table in ("react_runs", "react_calls", "react_events", "chat_messages", "agent_runs"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_call_journal_replays_completed_and_exposes_uncertainty(journal):
    row, _ = journal.create(request(), "owner")
    call = {"id": "c", "name": "test", "args": {"symbol": "000001"}}
    key = row["run_id"]
    assert journal.begin_call(key, call) is None
    assert journal.begin_call(key, call)["error_type"] == "UncertainExecution"
    journal.finish_call(key, "c", {"ok": True, "payload": {"value": 1}})
    assert journal.begin_call(key, call)["payload"] == {"value": 1}
    with pytest.raises(RunConflict): journal.begin_call(key, {**call, "args": {}})


def native(name, args, key):
    return AIMessage(content="", tool_calls=[{"id": key, "name": name, "args": args}])


class Model:
    def generate_messages(self, messages, tools, **kwargs):
        observations = [m for m in messages if isinstance(m, ToolMessage)]
        if not observations:
            return native("hot_stock_ranking", {"limit": 2}, "query")
        evidence = json.loads(observations[-1].content)
        return native("finish", {"status": "partial", "answer": "来源缺失，研究尚未完成。",
            "missing": ["完整来源"], "evidence_ids": [evidence["evidence_id"]]}, "answer")


def test_crash_between_tool_and_checkpoint_never_reexecutes_completed_call(journal):
    row, _ = journal.create(request(), "owner")
    calls = []
    def ranking(**args):
        calls.append(args)
        return ToolResult(name="hot_stock_ranking", input=args, summary="fixture", output={"items": [{"symbol": "000001"}]})
    registry = SimpleNamespace(events=[], schemas=lambda: TOOL_SCHEMAS, is_enabled=lambda _: True, hot_stock_ranking=ranking)
    class Crash(BaseException): pass
    class CrashingControl(RunControl):
        def save(self, payload):
            if payload["state"]["resume_node"] == "observe": raise Crash()
            super().save(payload)
    token = CURRENT_CONTROL.set(CrashingControl(journal, row))
    try:
        with pytest.raises(Crash): run(request(), registry, Model())
    finally: CURRENT_CONTROL.reset(token)
    restored = journal.get(row["run_id"], "owner")
    assert json.loads(restored["checkpoint_json"])["state"]["resume_node"] == "tools_node"
    token = CURRENT_CONTROL.set(RunControl(journal, restored))
    try: response = run(request(), registry, Model())
    finally: CURRENT_CONTROL.reset(token)
    assert response.task_status == "partial" and len(calls) == 1
    execution = next(t.output for t in response.tool_results if t.name == "react_execution")
    assert execution["tool_calls"] == 1 and execution["model_calls"] == 2


def test_cancel_before_first_model(journal):
    row, _ = journal.create(request(), "owner")
    journal.cancel(row["run_id"], "owner")
    registry = SimpleNamespace(events=[], schemas=lambda: TOOL_SCHEMAS, is_enabled=lambda _: True)
    token = CURRENT_CONTROL.set(RunControl(journal, row))
    try: response = run(request(), registry, object())
    finally: CURRENT_CONTROL.reset(token)
    assert response.task_status == "cancelled" and not response.tool_calls
