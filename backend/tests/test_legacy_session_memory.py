"""Historical snapshots are readable once, never a second summary writer."""

from contextlib import closing
from datetime import datetime, timezone
from types import SimpleNamespace

from app.agents.runner.durable_runtime import DurableRuntime
from app.database import connect
from app.repositories import ChatMemoryRepository, ChatSessionRepository
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


def test_existing_graph_imports_legacy_summary_only_once_and_honors_its_cursor(world):
    request, control, _ = world.start("继续研究首板")
    seed_legacy_snapshot(world.url, world.session_id, world.owner)
    history = [
        {"message_id": "old-user", "role": "user", "content": "旧问题"},
        {"message_id": "old-cursor", "role": "assistant", "content": "旧回答"},
        {"message_id": "new-user", "role": "user", "content": "最近问题"},
        {"message_id": "new-assistant", "role": "assistant", "content": "最近回答"},
    ]
    execution = SimpleNamespace(request=request, history=history, provider=None)
    conversation = DurableRuntime._conversation(None, {}, execution, control)
    assert conversation["imported_from"] == "session-memory-v2"
    assert conversation["conversation_summary"] == "旧首板研究摘要"
    assert conversation["summary_cursor"] == "old-cursor"
    assert [item["message_id"] for item in conversation["recent_messages"]] == ["new-user", "new-assistant"]
    with closing(connect(world.url)) as db:
        db.execute("UPDATE chat_session_memories SET summary='后来出现的旧表快照' WHERE session_id=?",
                   (world.session_id,))
        db.commit()
    resumed = DurableRuntime._conversation(None, {"conversation": conversation}, execution, control)
    assert resumed["conversation_summary"] == "旧首板研究摘要"
    assert resumed["revision"] == conversation["revision"]
    # Graph summarization never rewrites the retained legacy record.
    assert ChatMemoryRepository(world.url).get_memory(world.session_id, owner_id=world.owner).summary == "后来出现的旧表快照"
