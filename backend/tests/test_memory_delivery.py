from datetime import datetime, timedelta, timezone
import json

from app.models import ChatSessionMessage
from app.repositories import ChatSessionRepository
from app.repositories.long_term_memory_repository import LongTermMemoryRepository
from app.services.memory_retrieval import retrieve_memories
from test_agent_run_services import registered_run, response


def preference(repo, owner, **changes):
    return repo.put(owner, {"memory_type": "preference", "key": "response_style", "value": "concise",
                           "source_user_message": "请跨会话记住回答简洁", **changes}, "save-style")


def with_memory(response, retrieval, action=None):
    return {**response, "memory_action": action, "memory_owner_revision": retrieval.owner_revision,
            "memory_context_fingerprint": retrieval.context_fingerprint,
            "memory_dependencies": list(retrieval.dependencies),
            "memory_context_used": bool(retrieval.items)}


def test_deletion_after_generation_blocks_old_candidate_inside_delivery_transaction(registered_run):
    services, row, owner, _ = registered_run
    repo = LongTermMemoryRepository(services.storage.database_url)
    memory = preference(repo, owner)
    recalled = retrieve_memories(repo, owner_id=owner, query="研究首板")
    repo.delete(owner, memory.current.memory_id, expected_revision=memory.current.revision, operation_id="delete-style")
    delivered = services.delivery.commit(row["run_id"], owner,
                                         with_memory(response(row, "按你的简洁偏好发布旧答案。"), recalled))
    assert delivered["task_status"] == "partial"
    assert delivered["stop_reason"] == "memory_changed"
    assert "旧答案" not in delivered["answer"]
    assert delivered["memory_context_used"] is False
    assert services.registry.delivered_response(row["run_id"], owner) == delivered


def test_expiry_without_owner_revision_write_still_invalidates_candidate(registered_run):
    services, row, owner, _ = registered_run
    repo = LongTermMemoryRepository(services.storage.database_url)
    memory = preference(repo, owner, expires_at=datetime.now(timezone.utc) + timedelta(days=1))
    recalled = retrieve_memories(repo, owner_id=owner, query="研究首板")
    with services.storage.connection() as db:
        db.execute("UPDATE long_term_memories SET expires_at=clock_timestamp()-interval '1 second' WHERE memory_id=?",
                   (memory.current.memory_id,))
    delivered = services.delivery.commit(row["run_id"], owner, with_memory(response(row), recalled))
    assert delivered["stop_reason"] == "memory_changed"
    assert delivered["memory_owner_revision"] == recalled.owner_revision
    assert delivered["memory_context_fingerprint"] != recalled.context_fingerprint


def test_memory_control_marks_both_chat_messages_without_erasing_user_history(registered_run):
    services, row, owner, request = registered_run
    chats = ChatSessionRepository(services.storage.database_url)
    chats.append_message(ChatSessionMessage(
        message_id="user_" + row["run_id"], session_id=row["session_id"], role="user",
        content=request.message, run_id=row["run_id"], created_at=row["started_at"],
    ), owner_id=owner)
    repo = LongTermMemoryRepository(services.storage.database_url)
    preference(repo, owner)
    recalled = retrieve_memories(repo, owner_id=owner, query="保存偏好")
    action = {"action": "save", "memory_type": "preference", "key": "response_style"}
    delivered = services.delivery.commit(row["run_id"], owner, with_memory(response(row, "已保存回答详略偏好。"), recalled, action))
    session = chats.get_session(row["session_id"], owner_id=owner)
    assert len(session.messages) == 2
    assert session.messages[0].content == request.message
    assert session.messages[0].metadata["memory_scope"] == "long_term"
    assert all(message.metadata["memory_action"] == action for message in session.messages)
    assert delivered["memory_context_fingerprint"] == recalled.context_fingerprint
    assert "value" not in json.dumps(action)


def test_unchanged_snapshot_preserves_normal_delivery(registered_run):
    services, row, owner, _ = registered_run
    repo = LongTermMemoryRepository(services.storage.database_url)
    recalled = retrieve_memories(repo, owner_id=owner, query="研究首板")
    delivered = services.delivery.commit(row["run_id"], owner, with_memory(response(row), recalled))
    assert delivered["task_status"] == "complete"
    assert delivered["memory_context_used"] is False


def test_unrelated_memory_change_does_not_invalidate_a_dependent_answer(registered_run):
    services, row, owner, _ = registered_run
    repo = LongTermMemoryRepository(services.storage.database_url)
    preference(repo, owner)
    recalled = retrieve_memories(repo, owner_id=owner, query="研究首板")
    assert recalled.dependencies
    repo.put(owner, {"memory_type": "preference", "key": "output_format",
                     "value": "table", "source_user_message": "以后使用表格"},
             "save-format")
    delivered = services.delivery.commit(row["run_id"], owner, with_memory(response(row), recalled))
    assert delivered["task_status"] == "complete"
    assert delivered["stop_reason"] == "answered"


def test_used_memory_update_invalidates_even_when_owner_fingerprint_is_current(registered_run):
    services, row, owner, _ = registered_run
    repo = LongTermMemoryRepository(services.storage.database_url)
    memory = preference(repo, owner)
    recalled = retrieve_memories(repo, owner_id=owner, query="研究首板")
    repo.put(owner, {"memory_type": "preference", "key": "response_style", "value": "detailed",
                     "expected_revision": memory.current.revision,
                     "source_user_message": "以后详细回答"}, "update-style")
    delivered = services.delivery.commit(row["run_id"], owner, with_memory(response(row), recalled))
    assert delivered["task_status"] == "partial"
    assert delivered["stop_reason"] == "memory_changed"


def test_management_revision_change_does_not_invalidate_answer(registered_run):
    services, row, owner, _ = registered_run
    repo = LongTermMemoryRepository(services.storage.database_url)
    memory = preference(repo, owner)
    recalled = retrieve_memories(repo, owner_id=owner, query="研究首板")
    with services.storage.connection() as db:
        db.execute("""UPDATE long_term_memories SET management_revision=management_revision+1
            WHERE memory_id=?""", (memory.current.memory_id,))
    delivered = services.delivery.commit(row["run_id"], owner, with_memory(response(row), recalled))
    assert delivered["task_status"] == "complete"


def test_empty_dependency_set_is_not_invalidated_by_new_memory(registered_run):
    services, row, owner, _ = registered_run
    repo = LongTermMemoryRepository(services.storage.database_url)
    recalled = retrieve_memories(repo, owner_id=owner, query="研究首板")
    assert recalled.dependencies == ()
    preference(repo, owner)
    delivered = services.delivery.commit(row["run_id"], owner, with_memory(response(row), recalled))
    assert delivered["task_status"] == "complete"


def test_delivery_enqueues_candidate_extraction_for_normal_answer(registered_run, monkeypatch):
    monkeypatch.setenv("LIMITUPLAB_MEMORY_AUTO_LEARN", "candidate")
    services, row, owner, _ = registered_run
    services.delivery.commit(row["run_id"], owner, response(row))
    with services.storage.connection() as db:
        jobs = db.execute("""SELECT status,source_message_id FROM memory_extraction_jobs
            WHERE run_id=?""", (row["run_id"],)).fetchall()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "pending"
    assert jobs[0]["source_message_id"] == "user_" + row["run_id"]
