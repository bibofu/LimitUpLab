"""Frozen evidence remains scoped, reproducible and honest about missing material."""

from database_support import isolated_database_url

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from app.agents.react_runtime.lifecycle import Journal, RunControl
from app.models import AgentChatRequest
from app.repositories import ChatSessionRepository
from app.services.quality_evidence import build_evidence_bundle, capture_evidence_bundle, content_hash
from app.services.quality_trace import record_snapshot, rendering_snapshot


def material():
    return {"run_id": "run_one", "session_id": "session", "started_at": "2026-10-17T01:00:00+00:00",
            "request": {"message": "研究数据"}, "response": {"answer": "已取得研究数据。", "task_status": "complete"},
            "checkpoint": {"runtime": {"evidence": {}}, "state": {}}, "calls": [],
            "assistant_messages": [{"message_id": "answer_run_one", "run_id": "run_one",
                                    "role": "assistant", "content": "已取得研究数据。"}],
            "context": [], "diagnostic_snapshots": [], "delivery_events": []}


def snapshot(identity, stage, payload, **extra):
    return {"id": identity, "stage": stage, "payload": payload, "omissions": [], **extra}


def test_bundle_identity_is_canonical_timestamp_independent_and_content_sensitive():
    data = material()
    first = build_evidence_bundle({**data, "captured_at": "then"})
    second = build_evidence_bundle({**dict(reversed(list(data.items()))), "captured_at": "later"})
    assert first["manifest"]["material_hash"] == second["manifest"]["material_hash"]
    assert first["manifest"]["captured_at"] != second["manifest"]["captured_at"]
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})
    data["calls"].append({"call_id": "call", "result": {"value": 2}})
    assert build_evidence_bundle(data)["manifest"]["material_hash"] != first["manifest"]["material_hash"]
    assert first["calls"] == []


def test_credentials_hidden_provider_metadata_and_nonfinite_values_are_explicit(monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_API_KEY", "long-configured-secret")
    data = material()
    data["response"]["answer"] = "long-configured-secret"
    data["calls"] = [{"result": {"api_key": "another-secret", "value": float("nan")}}]
    data["checkpoint"]["state"]["messages"] = [{"additional_kwargs": {"reasoning": "private-chain"}}]
    frozen = build_evidence_bundle(data)
    serialized = json.dumps(frozen)
    assert "long-configured-secret" not in serialized and "another-secret" not in serialized
    assert "private-chain" not in serialized and frozen["calls"][0]["result"]["value"] is None
    assert frozen["manifest"]["answer_hash"] != frozen["manifest"]["captured_answer_hash"]
    assert {item["reason"] for item in frozen["manifest"]["omissions"]} >= {
        "credential_redaction_or_depth_limit", "private_provider_metadata_excluded", "non_finite_recorded_number"}
    assert data["checkpoint"]["state"]["messages"][0]["additional_kwargs"]["reasoning"] == "private-chain"


def test_input_receipt_requires_matching_unique_attempt_id_not_matching_round():
    data = material()
    data["diagnostic_snapshots"] = [
        snapshot(1, "model_input", {"round": 1, "messages": []}),
        snapshot(2, "model_output", {"round": 1}),
        snapshot(3, "model_input", {"model_call_id": "attempt-a", "round": 2}),
        snapshot(4, "model_output", {"model_call_id": "attempt-a", "round": 2, "status": "returned"}),
        snapshot(5, "model_input", {"model_call_id": "attempt-b", "round": 3}),
        snapshot(6, "model_error", {"model_call_id": "attempt-b", "round": 3}),
        snapshot(7, "model_input", {"model_call_id": "attempt-c"}, omissions=[{"path": ["messages"], "reason": "limit"}]),
        snapshot(8, "model_output", {"model_call_id": "attempt-c", "status": "returned"}),
    ]
    calls = build_evidence_bundle(data)["model_calls"]
    assert [call["input_status"] for call in calls] == [
        "attempted_unconfirmed", "response_linked", "attempted_unconfirmed", "response_linked"]
    assert calls[1]["output_snapshot_ids"] == [4]
    assert calls[3]["input_capture_complete"] is False


def test_duplicate_input_ids_do_not_prove_which_model_input_returned():
    data = material()
    data["diagnostic_snapshots"] = [
        snapshot(1, "model_input", {"model_call_id": "duplicate", "messages": ["first"]}),
        snapshot(2, "model_input", {"model_call_id": "duplicate", "messages": ["second"]}),
        snapshot(3, "model_output", {"model_call_id": "duplicate", "status": "returned"}),
    ]
    assert all(call["input_status"] == "attempted_unconfirmed" for call in build_evidence_bundle(data)["model_calls"])


def test_diagnostic_source_redaction_reaches_all_derived_views(monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_API_KEY", "configured-long-secret")
    data = material()
    data["diagnostic_snapshots"] = [
        snapshot(1, "answer_rendering", {"rendered_answer": "configured-long-secret"}),
        snapshot(2, "provenance", {"compute_sha256": "known"}),
    ]
    frozen = build_evidence_bundle(data)
    assert {item["path"] for item in frozen["manifest"]["omissions"]} >= {
        "/diagnostic_snapshots", "/renderings", "/provenance", "/model_calls"}
    assert frozen["renderings"][0]["rendered_answer"] == "[redacted]"


def test_deep_old_diagnostics_do_not_raise_or_rewrite_input():
    data = material()
    deep = {"leaf": "value"}
    for _ in range(500):
        deep = {"nested": deep}
    data["checkpoint"]["runtime"]["evidence"] = deep
    frozen = build_evidence_bundle(data)
    assert any(item["reason"] == "bundle_depth_limit" for item in frozen["manifest"]["omissions"])
    assert frozen["response"]["answer"] == data["response"]["answer"]
    assert data["checkpoint"]["runtime"]["evidence"]["nested"] is deep["nested"]


def test_deep_checkpoint_does_not_prevent_atomic_answer_persistence(tmp_path):
    path = isolated_database_url('deep.sqlite')
    ChatSessionRepository(path).ensure_session("s", owner_id="owner")
    journal = Journal(path)
    row, _ = journal.create(AgentChatRequest(session_id="s", message_id="m", message="研究"), "owner")
    # A valid JSON document can be deeper than Python deepcopy safely handles.
    deep_json = '{"runtime":{"evidence":' + '{"nested":' * 500 + '{}' + '}' * 500 + '}}'
    with journal.connection() as db:
        db.execute("UPDATE react_runs SET checkpoint_json=? WHERE run_id=?", (deep_json, row["run_id"]))
    result = journal.finish(row["run_id"], "owner", {"answer": "研究结果。", "intent": "research", "task_status": "complete"})
    with journal.connection() as db:
        bundle = json.loads(db.execute("SELECT payload_json FROM quality_evidence_bundles WHERE run_id=?", (row["run_id"],)).fetchone()[0])
        assert db.execute("SELECT count(*) FROM chat_messages WHERE run_id=?", (row["run_id"],)).fetchone()[0] == 1
    assert result["task_status"] == "complete"
    assert any(item["reason"] == "bundle_depth_limit" for item in bundle["manifest"]["omissions"])


def test_bounds_preserve_omission_hash_and_do_not_present_partial_section_as_complete(monkeypatch):
    from app.services import quality_evidence
    monkeypatch.setattr(quality_evidence, "MAX_SECTION_BYTES", 1000)
    data = material()
    data["checkpoint"]["runtime"]["evidence"] = {"rows": [{"name": "字" * 1000}]}
    data["diagnostic_snapshots"] = [snapshot(1, "model_input", {"messages": [{"content": "字" * 1000}]})]
    frozen = build_evidence_bundle(data)
    assert frozen["checkpoint"] is None and frozen["diagnostic_snapshots"] is None
    assert frozen["model_calls"] is None and frozen["renderings"] is None
    omitted = {item["path"]: item for item in frozen["manifest"]["omissions"]}
    assert omitted["/checkpoint"]["reason"] == "bundle_size_limit"
    assert len(omitted["/checkpoint"]["sha256"]) == 64
    assert frozen["response"]["answer"] == data["response"]["answer"]


@pytest.fixture
def saved(tmp_path):
    path = isolated_database_url('evidence.sqlite')
    chats = ChatSessionRepository(path)
    chats.ensure_session("s", owner_id="owner")
    chats.ensure_session("foreign", owner_id="other")
    journal = Journal(path)
    row, _ = journal.create(AgentChatRequest(session_id="s", message_id="m", message="研究"), "owner")
    response = journal.finish(row["run_id"], "owner", {"answer": "研究结果。", "intent": "research", "task_status": "complete"})
    return journal, row, response


def test_capture_scopes_context_and_final_message_to_original_run(saved):
    journal, row, response = saved
    start = datetime.fromisoformat(row["started_at"])
    prior = (start - timedelta(seconds=5)).astimezone(timezone(timedelta(hours=8))).isoformat()
    later = (start + timedelta(seconds=5)).astimezone(timezone(timedelta(hours=-5))).isoformat()
    with journal.connection() as db:
        for identity, session, date, text in [("before", "s", prior, "prior context"),
                                             ("after", "s", later, "later answer"),
                                             ("alien", "foreign", prior, "another owner")]:
            db.execute("""INSERT INTO chat_messages(message_id,session_id,role,content,status,metadata_json,created_at)
                VALUES(?,?,'user',?,'success','{}',?)""", (identity, session, text, date))
        journal_row = dict(db.execute("SELECT * FROM react_runs WHERE run_id=?", (row["run_id"],)).fetchone())
        frozen = capture_evidence_bundle(db, journal_row, response, capture_mode="historical")
        assert [message["message_id"] for message in frozen["context"]] == ["before"]
        assert frozen["assistant_messages"][0]["run_id"] == row["run_id"]
        assert "another owner" not in json.dumps(frozen) and "later answer" not in json.dumps(frozen)
        assert frozen["manifest"]["capture_mode"] == "historical"
        with pytest.raises(KeyError):
            capture_evidence_bundle(db, {**journal_row, "owner_id": "other"}, response)


def test_invalid_old_json_and_missing_call_result_are_unknown_material(saved):
    journal, row, response = saved
    with journal.connection() as db:
        db.execute("INSERT INTO react_calls VALUES(?,?,?,?)", (row["run_id"], "bad", "invalid", None))
        db.execute("INSERT INTO react_calls VALUES(?,?,?,?)", (row["run_id"], "good", '["query",[["trade_date","2026-10-16"]]]', '{"items":[]}'))
        frozen = capture_evidence_bundle(db, {**row, "checkpoint_json": "invalid"}, response)
    assert frozen["calls"][0]["arguments"] is None and frozen["calls"][0]["result"] is None
    assert frozen["calls"][1]["arguments"] == {"trade_date": "2026-10-16"}
    assert {item["reason"] for item in frozen["manifest"]["missing"]} >= {"invalid_recorded_json", "not_recorded"}


def test_frozen_copy_survives_source_trace_changes_and_keeps_raw_vs_model_view(saved):
    journal, row, response = saved
    control = RunControl(journal, row)
    record_snapshot(control, "tool_end", {"call_id": "call", "result": {"rows": [1, 2, 3]}})
    record_snapshot(control, "model_input", {"model_call_id": "m1", "messages": [{"content": '{"rows":[1],"truncated":true}'}]})
    with journal.connection() as db:
        frozen = capture_evidence_bundle(db, row, response)
        db.execute("DELETE FROM quality_trace_snapshots WHERE run_id=?", (row["run_id"],))
    assert frozen["diagnostic_snapshots"][0]["payload"]["result"]["rows"] == [1, 2, 3]
    assert frozen["model_calls"][0]["input_status"] == "attempted_unconfirmed"
    assert not any(item["reason"] == "credential_redaction_or_depth_limit" for item in frozen["manifest"]["omissions"])


def test_render_snapshot_records_original_finish_and_matching_code_dependencies():
    from app.agents.react_runtime.contracts import Finish
    from app.agents.react_runtime.evidence import EvidenceStore
    from app.agents.react_runtime.task_contract import OutputContract
    from app.services.quality_trace import component_fingerprints
    final = Finish(status="complete", answer="{{evidence_table}}")
    captured = rendering_snapshot(final, EvidenceStore(), OutputContract(), "rendered table", origin="gate")
    assert captured["finish_input"]["answer"] == "{{evidence_table}}"
    assert captured["rendered_answer"] == "rendered table"
    assert captured["renderer_sha256"] == component_fingerprints()["renderer_sha256"]
    assert len(component_fingerprints()["compute_sha256"]) == 64


def test_runtime_correlates_failed_retry_and_success_with_distinct_model_call_ids(monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage
    from app.agents.react_runtime import runtime
    captured = []
    monkeypatch.setattr(runtime, "record_snapshot", lambda control, stage, payload: captured.append((stage, payload)))
    class Provider:
        attempts = 0
        def generate_messages(self, *args, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise TimeoutError("test timeout")
            return AIMessage(content="", tool_calls=[{"name": "finish", "id": "done",
                "args": {"answer": "研究助手", "status": "complete"}}])
    runner = runtime.Run(AgentChatRequest(session_id="s", message="介绍用途"), SimpleNamespace(schemas=lambda: []),
                         Provider(), [], None)
    runner.agent({"messages": [HumanMessage(content="介绍用途")]})
    runner.agent({"messages": [HumanMessage(content="介绍用途")]})
    assert [stage for stage, _ in captured] == ["model_input", "model_error", "model_input", "model_output"]
    assert captured[0][1]["model_call_id"] == captured[1][1]["model_call_id"]
    assert captured[2][1]["model_call_id"] == captured[3][1]["model_call_id"]
    assert captured[0][1]["model_call_id"] != captured[2][1]["model_call_id"]
