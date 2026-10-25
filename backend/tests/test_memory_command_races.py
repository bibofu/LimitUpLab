"""Content must stay bound to the exact memory snapshot that produced it."""

from types import SimpleNamespace

import pytest

from app.agents import memory_commands
from app.agents.memory_policy import MemoryIntent
from app.agents.runner.durable_runtime import DurableRuntime
from app.memory_models import MemoryWrite
from app.repositories.long_term_memory_repository import LongTermMemoryRepository
from test_durable_agent import world


def saved_preference(repository, owner, **changes):
    fields = dict(memory_type="preference", key="response_style", value="concise",
                  source_user_message="请长期记住回答简洁")
    fields.update(changes)
    return repository.put(owner, MemoryWrite(**fields), "seed-preference").current


def supersede(repository, owner, record, change):
    if change == "delete":
        repository.delete(owner, record.memory_id, record.revision, "racing-delete")
    else:
        repository.put(owner, MemoryWrite(
            memory_type="preference", key="response_style", value="detailed",
            expected_revision=record.revision, source_user_message="以后回答详细",
        ), "racing-update")


def assert_old_confirmation_suppressed(result):
    assert result["status"] == "partial"
    assert result["stop_reason"] == "memory_changed"
    assert "简洁" not in result["answer"]
    assert "已保存长期记忆" not in result["answer"]
    assert result["memory_context_used"] is False


@pytest.mark.parametrize("change", ["delete", "update"])
def test_listing_cannot_relabel_old_snapshot_as_current(world, monkeypatch, change):
    query = "列出已保存的长期记忆"
    _, control, _ = world.start(query)
    repository = memory_commands.repository_for(control)
    record = saved_preference(repository, world.owner)
    intent = MemoryIntent(action="list", quote=query)
    proposal = memory_commands.prepare_memory_request(intent, control, repository.get_owner_revision(world.owner))
    original = memory_commands._listing

    def read_then_change(repository, owner, intent):
        old_answer_and_snapshot = original(repository, owner, intent)
        supersede(repository, owner, record, change)
        return old_answer_and_snapshot

    monkeypatch.setattr(memory_commands, "_listing", read_then_change)
    result = memory_commands.execute_memory_request(
        {"memory_request": proposal, "current_query": query}, control,
    )
    assert_old_confirmation_suppressed(result)
    current = repository.read_context(world.owner)
    assert result["memory_context_fingerprint"] == current["context_fingerprint"]


@pytest.mark.parametrize("change", ["delete", "update"])
def test_mutation_confirmation_cannot_relabel_its_receipt_snapshot(world, monkeypatch, change):
    query = "请长期记住回答简洁"
    _, control, _ = world.start(query)
    intent = MemoryIntent(action="save", quote=query, memory_type="preference",
                          key="response_style", value="concise")
    proposal = memory_commands.prepare_memory_request(intent, control, 0)
    original = LongTermMemoryRepository.put

    def commit_then_change(self, owner, write, operation_id, **kwargs):
        committed = original(self, owner, write, operation_id, **kwargs)
        if operation_id.endswith(":memory"):
            supersede(self, owner, committed.current, change)
        return committed

    monkeypatch.setattr(LongTermMemoryRepository, "put", commit_then_change)
    result = memory_commands.execute_memory_request(
        {"memory_request": proposal, "current_query": query}, control,
    )
    assert_old_confirmation_suppressed(result)
    current = memory_commands.repository_for(control).read_context(world.owner)
    assert result["memory_context_fingerprint"] == current["context_fingerprint"]


def test_new_turn_revokes_summary_before_invoking_summarizer(world, monkeypatch):
    request, control, _ = world.start("继续研究首板")
    repository = memory_commands.repository_for(control)
    record = saved_preference(repository, world.owner)
    previous = repository.read_context(world.owner)
    repository.delete(world.owner, record.memory_id, record.revision, "delete-before-summary")
    marker = "已撤回的长期简洁偏好"
    state = {"conversation": {"conversation_summary": marker, "active_constraints": [marker],
                              "memory_context_fingerprint": previous["context_fingerprint"]}}
    history = [
        {"message_id": "u-large", "role": "user", "content": "研究首板" * 1000},
        {"message_id": "a-old", "role": "assistant", "content": marker,
         "metadata": {"memory_context_used": True,
                      "memory_context_fingerprint": previous["context_fingerprint"]}},
        {"message_id": "u-latest", "role": "user", "content": "只看主板"},
    ]
    captured = []

    def summarize(*, existing, messages, llm_provider):
        captured.append((existing, messages))
        return {"summary": "用户研究首板", "constraints": []}, "llm_function_call", "fixture"

    monkeypatch.setattr("app.agents.conversation_memory.generate_summary", summarize)
    execution = SimpleNamespace(request=request, history=history, provider=object())
    result = DurableRuntime._conversation(None, state, execution, control)
    assert len(captured) == 1
    existing, sent_messages = captured[0]
    assert existing is None or marker not in existing.model_dump_json()
    assert all(marker not in message.content for message in sent_messages)
    assert result["summary_triggered"]
    assert marker not in str(result)
