"""Historical snapshots are readable once, never a second summary writer."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.agents.runner.durable_runtime import DurableRuntime
from app.database import connect
from app.models import ChatSessionMessage
from app.repositories import ChatMemoryRepository, ChatSessionRepository
from app.services.memory_scope import has_session_summary_lineage
from test_durable_agent import world


def seed_legacy_snapshot(database_url, session_id, owner):
    """Represent a pre-migration row without reintroducing a runtime writer."""
    timestamp = datetime.now(timezone.utc).isoformat()
    with closing(connect(database_url)) as db:
        db.execute("""INSERT INTO chat_session_memories
            (session_id,owner_id,memory_version,summary,research_goal,stock_symbols_json,topics_json,
             date_scope,constraints_json,unresolved_questions_json,summarized_message_count,last_message_id,
             generation_mode,model,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (session_id, owner, "session-memory-v2", "旧首板研究摘要", "研究首板", '["600001"]', '["首板评级"]',
             "2026-10-23", '["只看主板"]', '[]', 8, "old-cursor", "deterministic", None, timestamp, timestamp))
        db.commit()


def seed_legacy_ledger(chats, session_id, owner):
    """Legacy imports still need complete, owned user/assistant source turns."""
    source = [
        ("past-user-1", "user", "本会话的研究代号是青竹47。"),
        ("past-assistant-1", "assistant", "已收到研究代号。"),
        ("past-user-2", "user", "研究范围是2026年10月23日的首板。"),
        ("past-assistant-2", "assistant", "已收到研究范围。"),
        ("past-user-3", "user", "需要解释首板评级。"),
        ("past-assistant-3", "assistant", "已解释评级。"),
        ("old-user", "user", "本次只看主板，研究代码600001。"),
        ("old-cursor", "assistant", "旧回答中的展示偏好不可作为用户事实。"),
        ("new-user", "user", "最近问题：继续核对上述研究范围。"),
        ("new-assistant", "assistant", "最近回答已完成。"),
    ]
    started = datetime(2026, 10, 23, tzinfo=timezone.utc)
    messages = [ChatSessionMessage(message_id=message_id, session_id=session_id, role=role,
                content=content, created_at=started + timedelta(seconds=index))
                for index, (message_id, role, content) in enumerate(source)]
    for message in messages:
        chats.append_message(message, owner_id=owner)
    return messages


def test_legacy_reader_checks_owner_and_session_deletion_removes_only_owned_snapshot(postgresql_database):
    chats = ChatSessionRepository(postgresql_database)
    chats.create_session(session_id="legacy-session", owner_id="owner")
    seed_legacy_snapshot(postgresql_database, "legacy-session", "owner")
    reader = ChatMemoryRepository(postgresql_database)
    legacy = reader.get_memory("legacy-session", owner_id="owner")
    assert legacy.summary == "旧首板研究摘要" and legacy.last_message_id == "old-cursor"
    assert legacy.constraints == ["只看主板"]
    assert reader.get_memory("legacy-session", owner_id="foreign") is None
    assert chats.delete_session("legacy-session", owner_id="owner")
    assert reader.get_memory("legacy-session", owner_id="owner") is None


def test_existing_graph_recovers_untrusted_legacy_summary_once_from_its_real_ledger(world, monkeypatch):
    from app.agents import conversation_recovery

    committed = seed_legacy_ledger(world.chats, world.session_id, world.owner)
    seed_legacy_snapshot(world.url, world.session_id, world.owner)
    request, control, history = world.start("继续研究首板")
    assert [item.message_id for item in history] == [item.message_id for item in committed]
    execution = SimpleNamespace(request=request, history=history, provider=None)
    conversation = DurableRuntime._conversation(None, {}, execution, control)
    assert conversation["imported_from"] == "session-memory-v2"
    assert has_session_summary_lineage(conversation)
    assert conversation["summary_recovered_from_ledger"]
    assert conversation["summary_recovery_source_count"] == 5
    assert conversation["last_committed_message_id"] == "new-assistant"
    # No compression is needed: preserve every original user fact exactly,
    # including the older facts which the unverified summary claimed to replace.
    assert conversation.get("conversation_summary", "") == ""
    assert [(item["message_id"], item["content"]) for item in conversation["recent_messages"]] == [
        (item.message_id, item.content) for item in committed if item.role == "user"]
    assert all(item["role"] == "user" for item in conversation["recent_messages"])
    with closing(connect(world.url)) as db:
        db.execute("UPDATE chat_session_memories SET summary='后来出现的旧表快照' WHERE session_id=?",
                   (world.session_id,))
        db.commit()

    def unexpected_recovery(*_args, **_kwargs):
        pytest.fail("verified graph lineage must not replay the legacy ledger again")

    monkeypatch.setattr(conversation_recovery, "recover_session_summary", unexpected_recovery)
    resumed = DurableRuntime._conversation(None, {"conversation": conversation}, execution, control)
    assert resumed["recent_messages"] == conversation["recent_messages"]
    assert resumed.get("conversation_summary", "") == ""
    assert has_session_summary_lineage(resumed)
    assert resumed["revision"] == conversation["revision"]
    # Graph summarization never rewrites the retained legacy record.
    assert ChatMemoryRepository(world.url).get_memory(world.session_id, owner_id=world.owner).summary == "后来出现的旧表快照"
