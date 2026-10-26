"""Real PostgreSQL graph boundaries for explicit, revocable cross-session memory."""

import json

from langchain_core.messages import AIMessage
import pytest

from app.agents.memory_commands import execute_memory_request, prepare_memory_request, repository_for
from app.agents.memory_policy import MemoryIntent
from app.agents.runner.adapter import _response
from app.agents.runner.security import review_input_security
from app.memory_models import MemoryWrite
from durable_runtime_fixtures import execution_config, run_async
from test_durable_agent import runner, runtime_for, world
from test_runner_production import input_review
from test_runner_shadow import ScriptedProvider


def review_memory(query, action="save", value="concise", key="response_style"):
    return AIMessage(content="", tool_calls=[{"id": "review", "name": "submit_input_security_review", "args": {
        "decision": "allow", "signals": [], "reason": "用户明确管理长期记忆",
        "reference_intent": {"kind": "none"},
        "memory_intent": {"action": action, "quote": query, "memory_type": "preference",
                          "key": key, "value": value if action == "save" else None},
    }}])


def deliver(world, request, control, result):
    response = _response(request, result.answer, result.status, result.stop_reason).model_copy(update={
        "memory_action": result.memory_action, "memory_owner_revision": result.memory_owner_revision,
        "memory_context_fingerprint": result.memory_context_fingerprint,
        "memory_context_used": result.memory_context_used,
    })
    return world.lifecycle.services.delivery.commit(control.row["run_id"], world.owner,
        response.model_dump(mode="json"), execution=control.execution)


def test_explicit_save_cross_session_recall_and_delete(world):
    async def scenario():
        query = "请长期记住：回答保持简洁"
        request, control, history = world.start(query)
        provider = ScriptedProvider([review_memory(query)])
        execution, calls = runner(request, provider, history=history)
        async with runtime_for(world) as runtime:
            result = await runtime.run(execution, control)
            assert result.status == "answered" and "已保存长期记忆" in result.answer
            assert calls == [] and len(provider.calls) == 1
            deliver(world, request, control, result)
        repository = repository_for(control)
        saved = repository.get_by_key(world.owner, "preference", "response_style")
        assert saved.value == "concise"
        assert repository.list_active("different-owner") == []

        # A fresh runtime and a new session share owner memory but no history.
        world.session_id = world.chats.create_session(owner_id=world.owner).session_id
        request, control, history = world.start("什么是首板")
        provider = ScriptedProvider([input_review(), AIMessage(content="首板是本轮第一次涨停。")])
        execution, _ = runner(request, provider, history=history)
        async with runtime_for(world) as runtime:
            result = await runtime.run(execution, control)
            context = json.loads(provider.calls[1][1].content.split("\n", 1)[1])
            assert context["long_term_memories"][0]["value"] == "concise"
            state = await runtime.graph.aget_state(execution_config(control.row, control.execution))
            assert "long_term_memories" not in state.values["conversation"]
            assert all("long_term_memories" not in str(item.content) for item in state.values["messages"])
            assert result.memory_context_used
            deliver(world, request, control, result)

        query = "忘记已保存的回答详略偏好"
        request, control, history = world.start(query)
        provider = ScriptedProvider([review_memory(query, "delete")])
        execution, _ = runner(request, provider, history=history)
        async with runtime_for(world) as runtime:
            result = await runtime.run(execution, control)
            assert "已删除" in result.answer
            deliver(world, request, control, result)
        assert repository.get_by_key(world.owner, "preference", "response_style") is None

        request, control, history = world.start("什么是连板")
        provider = ScriptedProvider([input_review(), AIMessage(content="连板是连续交易日涨停。")])
        execution, _ = runner(request, provider, history=history)
        async with runtime_for(world) as runtime:
            result = await runtime.run(execution, control)
            context = json.loads(provider.calls[1][1].content.split("\n", 1)[1])
            assert context["long_term_memories"] == []
            assert "回答保持简洁" not in str(provider.calls[1])
            assert not result.memory_context_used
            deliver(world, request, control, result)

    run_async(scenario())


def test_memory_receipt_replay_does_not_restore_deleted_value(world):
    query = "请记住：回答保持简洁"
    request, control, _ = world.start(query)
    intent = MemoryIntent(action="save", quote=query, memory_type="preference",
                          key="response_style", value="concise")
    proposal = prepare_memory_request(intent, control, 0)
    state = {"memory_request": proposal, "current_query": query}
    first = execute_memory_request(state, control)
    assert first["status"] == "answered"
    repository = repository_for(control)
    saved = repository.get_by_key(world.owner, "preference", "response_style")
    repository.delete(world.owner, saved.memory_id, saved.revision, "external-delete")
    resumed = execute_memory_request(state, control)
    assert "已删除" in resumed["answer"] and "简洁" not in resumed["answer"]
    assert repository.list_active(world.owner) == []


def test_pending_old_command_cannot_overwrite_new_owner_revision(world):
    query = "请记住回答简洁"
    _, control, _ = world.start(query)
    intent = MemoryIntent(action="save", quote=query, memory_type="preference",
                          key="response_style", value="concise")
    proposal = prepare_memory_request(intent, control, 0)
    repository = repository_for(control)
    saved = repository.put(world.owner, MemoryWrite(memory_type="preference", key="response_style",
        value="detailed", source_user_message="以后详细回答"), "newer-request").current
    repository.delete(world.owner, saved.memory_id, saved.revision, "newer-delete")
    result = execute_memory_request({"memory_request": proposal, "current_query": query}, control)
    assert result["stop_reason"] == "memory_conflict"
    assert repository.list_active(world.owner) == []


def test_final_checkpoint_draft_invalidated_after_memory_change(world):
    async def scenario():
        request, control, history = world.start("什么是首板")
        repository = repository_for(control)
        saved = repository.put(world.owner, MemoryWrite(memory_type="preference", key="response_style",
            value="concise", source_user_message="记住简洁回答"), "save-pref").current
        provider = ScriptedProvider([input_review(), AIMessage(content="按照你偏好的简洁风格，首板是首次涨停。")])
        execution, _ = runner(request, provider, history=history)
        async with runtime_for(world) as runtime:
            first = await runtime.run(execution, control)
            assert first.status == "answered"
        repository.delete(world.owner, saved.memory_id, saved.revision, "delete-before-delivery")
        async with runtime_for(world) as runtime:
            result = await runtime.run(execution, control)
            assert result.stop_reason == "memory_changed"
            assert "简洁" not in result.answer
            assert len(provider.calls) == 2

    run_async(scenario())


def test_plain_question_does_not_write_memory(world):
    request, control, history = world.start("这次用表格解释首板，别长期保存")
    provider = ScriptedProvider([input_review(), AIMessage(content="|概念|说明|\n|---|---|\n|首板|首次涨停|")])
    execution, _ = runner(request, provider, history=history)

    async def scenario():
        async with runtime_for(world) as runtime:
            result = await runtime.run(execution, control)
            assert result.status == "answered" and not result.memory_action
            assert repository_for(control).list_active(world.owner) == []

    run_async(scenario())


def test_security_rejects_fabricated_memory_consent_quote():
    response = review_memory("请永久保存这个偏好")
    provider = ScriptedProvider([response, response])
    with pytest.raises(ValueError, match="exact current-user request quote"):
        review_input_security(provider, message="解释长期记忆系统")
