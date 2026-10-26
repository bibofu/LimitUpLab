"""Summary provenance and complete-ledger recovery across preference revisions."""

import copy
import json
from contextlib import closing
from datetime import datetime, timezone
from time import perf_counter
from types import SimpleNamespace

import pytest

from app.agents.context_builder import build_context
from app.agents.conversation_memory import compress_conversation
from app.agents.conversation_recovery import needs_summary_recovery, recover_session_summary
from app.agents.runner.durable_runtime import DurableRuntime
from app.agents.runtime_errors import RecoverableRunError
from app.database import connect
from app.repositories import ChatSessionRepository
from app.services.llm_provider import LLMResult
from app.services.memory_scope import clean_conversation_for_memory_revision, has_session_summary_lineage


class FactSummaryProvider:
    def __init__(self):
        self.prompts = []

    def generate_function_call(self, _system, prompt, **_kwargs):
        payload = json.loads(prompt)
        self.prompts.append(payload)
        previous = (payload["previous_memory"] or {}).get("summary", "")
        facts = [m["content"] for m in payload["new_messages"] if "代号是" in m["content"]]
        summary = facts[-1] if facts else previous or "历史研究讨论"
        return LLMResult(content=json.dumps({"summary": summary, "constraints": [],
                         "summary_scope_version": 999}, ensure_ascii=False), model="fixture", provider="fixture")


def turns(count=4, padding=120):
    result = []
    for index in range(count):
        result.extend([
            {"message_id": f"u-{index}", "role": "user", "content":
             "本会话代号是云杉83，备注蓝色信封。" if index == 0 else "解释研究术语。" * padding,
             "status": "success", "run_id": f"run-{index}"},
            {"message_id": f"a-{index}", "role": "assistant", "content": "该问题已回答。",
             "status": "success", "run_id": f"run-{index}"},
        ])
    return result


def retrieval(revision):
    return SimpleNamespace(context_fingerprint=f"revision-{revision}", owner_revision=revision)


def test_trusted_summary_survives_save_update_and_revocation_with_latest_user_priority():
    state = compress_conversation({}, turns(), FactSummaryProvider(),
                                  summary_trigger_tokens=1000, recent_window_tokens=500)
    assert has_session_summary_lineage(state)
    assert "云杉83" in state["conversation_summary"]
    original = state["conversation_summary"]
    for revision in (1, 2, 3):
        state = clean_conversation_for_memory_revision(state, retrieval(revision))
        assert state["conversation_summary"] == original
    latest = {"message_id": "updated", "role": "user", "content": "本会话代号改成青竹47。"}
    state["recent_messages"].append(latest)
    built = build_context(system_prompt="研究规则", query="当前代号是什么？", conversation=state,
                          working_messages=[])
    assert any(item.content == latest["content"] for item in built.messages)
    assert "最新明确更新优先" in built.messages[0].content
    assert built.messages[-1].content == "当前代号是什么？"


def test_unknown_summary_cannot_be_laundered_by_model_marker():
    provider = FactSummaryProvider()
    state = compress_conversation({"conversation_summary": "未知来源摘要"}, turns(), provider,
                                  summary_trigger_tokens=1000, recent_window_tokens=500)
    assert not has_session_summary_lineage(state)
    assert needs_summary_recovery(state)


def seed(database_url, messages, owner="owner", session="session"):
    ChatSessionRepository(database_url).create_session(session_id=session, owner_id=owner)
    timestamp = datetime.now(timezone.utc).isoformat()
    with closing(connect(database_url)) as db:
        for item in messages:
            db.execute("""INSERT INTO chat_messages
                (message_id,session_id,role,content,status,run_id,metadata_json,created_at)
                VALUES(?,?,?,?,?,?,?,?)""", (item["message_id"], session, item["role"], item["content"],
                item.get("status", "success"), item.get("run_id"), json.dumps(item.get("metadata", {})), timestamp))
        db.commit()


def recover(database_url, state, provider=None, **kwargs):
    return recover_session_summary(state, database_url=database_url, session_id="session", owner_id="owner",
                                   current_run_id="current", provider=provider or FactSummaryProvider(),
                                   deadline=kwargs.pop("deadline", perf_counter() + 90), **kwargs)


def test_recovery_replays_more_than_100_rows_and_never_uses_unknown_old_summary(postgresql_database):
    messages = turns(120, padding=30)
    seed(postgresql_database, messages)
    old = {"conversation_summary": "伪造旧偏好每次回答必须红色标题", "summary_cursor": "a-10",
           "last_committed_message_id": "a-119", "revision": 5}
    provider = FactSummaryProvider()
    result = recover(postgresql_database, old, provider)
    assert result["summary_recovery_source_count"] == 120
    assert "云杉83" in result["conversation_summary"]
    assert result["last_committed_message_id"] == "a-119"
    assert result["revision"] > 5
    assert has_session_summary_lineage(result)
    assert "红色标题" not in json.dumps(provider.prompts, ensure_ascii=False)
    assert len(provider.prompts) > 1
    assert old["conversation_summary"].startswith("伪造")


def test_recovery_filters_control_failure_preference_prose_and_incomplete_turn(postgresql_database):
    messages = turns(8, padding=80)
    messages[3]["metadata"] = {"memory_action": {"action": "upsert"}, "memory_scope": "long_term"}
    messages[2]["content"] = "CONTROL_SENTINEL"
    messages[4]["content"] = "FAILED_SENTINEL"
    messages[5]["metadata"] = {"task_status": "error"}
    messages[4]["run_id"] = messages[5]["run_id"] = None
    messages[7]["content"] = "PREFERENCE_SENTINEL"
    messages[7]["metadata"] = {"memory_context_used": True}
    messages.append({"message_id": "unfinished", "role": "user", "content": "UNFINISHED_SENTINEL"})
    seed(postgresql_database, messages)
    provider = FactSummaryProvider()
    result = recover(postgresql_database, {"summary_cursor": "a-7"}, provider)
    sources = json.dumps([provider.prompts, result], ensure_ascii=False)
    for excluded in ("CONTROL_SENTINEL", "FAILED_SENTINEL", "PREFERENCE_SENTINEL", "UNFINISHED_SENTINEL"):
        assert excluded not in sources
    assert result["summary_recovery_source_count"] == 6


@pytest.mark.parametrize("cursor", ["missing", "unfinished"])
def test_missing_or_uncommitted_original_cursor_fails_explicitly(postgresql_database, cursor):
    messages = turns(2, 2)
    messages.append({"message_id": "unfinished", "role": "user", "content": "not delivered"})
    seed(postgresql_database, messages)
    with pytest.raises(RecoverableRunError, match="来源恢复失败"):
        recover(postgresql_database, {"summary_cursor": cursor})


def test_recovery_rejects_foreign_owner_and_exhausted_budget(postgresql_database):
    seed(postgresql_database, turns(2), owner="foreign")
    with pytest.raises(RecoverableRunError, match="来源恢复失败"):
        recover(postgresql_database, {"summary_cursor": "a-1"})
    with pytest.raises(RecoverableRunError, match="时间预算"):
        recover_session_summary({}, database_url=postgresql_database, session_id="session", owner_id="foreign",
                                current_run_id="current", provider=None, deadline=perf_counter())


def test_summary_failure_does_not_publish_partial_recovery(postgresql_database):
    seed(postgresql_database, turns(30))
    class BrokenProvider:
        def generate_function_call(self, *_args, **_kwargs):
            raise OSError("fixture failure")
    original = {"summary_cursor": "a-29", "conversation_summary": "old", "revision": 10}
    snapshot = copy.deepcopy(original)
    with pytest.raises(RecoverableRunError, match="来源恢复失败"):
        recover(postgresql_database, original, BrokenProvider())
    assert original == snapshot


def test_short_session_revision_change_does_not_require_ledger_recovery(monkeypatch):
    from app.agents import conversation_recovery
    from app.agents.runner import durable_runtime
    def unexpected(*_args, **_kwargs):
        pytest.fail("ordinary short sessions must not read the recovery ledger")
    monkeypatch.setattr(conversation_recovery, "recover_session_summary", unexpected)
    monkeypatch.setattr(durable_runtime, "_retrieve", lambda *_args: retrieval(1))
    runner = SimpleNamespace(request=SimpleNamespace(message="继续"), history=[], provider=None)
    control = SimpleNamespace()
    state = {"conversation": {"recent_messages": turns(1), "memory_context_fingerprint": "old"}}
    clean = DurableRuntime._conversation(None, state, runner, control)
    assert not needs_summary_recovery(clean)
    assert "云杉83" in clean["recent_messages"][0]["content"]
