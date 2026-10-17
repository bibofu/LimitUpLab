import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.react_runtime.lifecycle import Journal, RunControl
from app.models import AgentChatRequest
from app.repositories import SQLiteChatSessionRepository
from app.services.quality_trace import (
    MAX_TEXT_CHARS, bounded_snapshot, prune_trace_snapshots, record_snapshot,
    scrub_private_data, visible_messages,
)


def test_snapshots_redact_credentials_preserve_evidence_and_mark_omissions(monkeypatch):
    monkeypatch.setenv("EXAMPLE_API_KEY", "private-example-credential")
    original = {"raw": {"api_key": "unconfigured-secret", "value": 1.2},
                "content": '{"authorization":"Bearer other", "rows":[1,2]}',
                "question": "uses private-example-credential", "long": "字" * (MAX_TEXT_CHARS + 5)}
    captured = json.loads(bounded_snapshot(original))
    assert captured["payload"]["raw"] == {"api_key": "[redacted]", "value": 1.2}
    assert json.loads(captured["payload"]["content"])["authorization"] == "[redacted]"
    assert captured["payload"]["question"] == "uses [redacted]"
    assert len(captured["payload"]["long"]) == MAX_TEXT_CHARS
    assert captured["omissions"][0]["original_chars"] == MAX_TEXT_CHARS + 5
    assert original["raw"]["api_key"] == "unconfigured-secret"


def test_visible_messages_do_not_collect_hidden_reasoning_or_provider_metadata():
    messages = [HumanMessage(content="研究问题"),
                ToolMessage(content='{"preview":[1],"truncated":true}', tool_call_id="t1"),
                AIMessage(content="", additional_kwargs={"reasoning_content": "private reasoning"},
                          response_metadata={"authorization": "private-header"},
                          tool_calls=[{"name": "finish", "id": "f", "args": {"answer": "候选答案"}}])]
    captured = visible_messages(messages)
    serialized = json.dumps(captured)
    assert "private reasoning" not in serialized and "private-header" not in serialized
    assert captured[1]["content"] == messages[1].content
    assert captured[2]["tool_calls"][0]["args"]["answer"] == "候选答案"


def test_private_snapshot_retention_and_session_deletion(tmp_path):
    path = tmp_path / "trace.sqlite"
    chats = SQLiteChatSessionRepository(path)
    chats.ensure_session("s", owner_id="owner")
    journal = Journal(path)
    row, _ = journal.create(AgentChatRequest(session_id="s", message_id="m", message="研究"), "owner")
    control = RunControl(journal, row)
    record_snapshot(control, "model_input", {"messages": [{"content": "preview"}]})
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    with journal.connection() as db:
        db.execute("INSERT INTO quality_trace_snapshots(run_id,stage,captured_at,payload_json) VALUES(?,?,?,?)",
                   (row["run_id"], "old", old, "{}"))
        assert prune_trace_snapshots(db) == 1
        saved = db.execute("SELECT * FROM quality_trace_snapshots").fetchone()
        assert saved["stage"] == "model_input"
        # Same cascade as the session deletion API, including active diagnostic rows.
        db.execute("DELETE FROM chat_sessions WHERE session_id='s'")
        assert db.execute("SELECT count(*) FROM quality_trace_snapshots").fetchone()[0] == 0


def test_diagnostic_storage_failure_is_not_an_agent_failure(caplog):
    record_snapshot(SimpleNamespace(), "model_input", {"secret": "never-log-this"})
    assert "quality_snapshot_failed" in caplog.text
    assert "never-log-this" not in caplog.text
    assert scrub_private_data({"total_tokens": 100}) == {"total_tokens": 100}
