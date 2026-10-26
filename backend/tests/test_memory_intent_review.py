"""Routing contracts under an explicitly wrong initial memory classification.

Scripted decisions exercise control flow and provenance, not model accuracy;
the real-model acceptance uses the same multi-turn questions separately.
"""

import copy
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from app.agents.memory_intent_review import bounded_session_context, review_memory_candidate
from app.agents.memory_policy import MemoryIntent
from app.agents.runner.security import review_input_security
from app.agents.runtime_errors import RecoverableRunError
from durable_runtime_fixtures import execution_config, run_async
from test_durable_agent import runner, runtime_for, world
from test_durable_long_term_memory import deliver, review_memory, review_scope
from test_runner_production import input_review
from test_runner_shadow import ScriptedProvider


DECLARATION = "本次会话的临时测试代号是“青竹47”，只在当前会话使用。请回复“收到”。"
FOLLOWUP = "刚才约定的临时测试代号是什么？请只回答代号。"


def turn(index, content, answer="收到", **metadata):
    return [
        {"message_id": f"u-{index}", "run_id": str(index), "role": "user", "content": content},
        {"message_id": f"a-{index}", "run_id": str(index), "role": "assistant",
         "content": answer, "metadata": metadata},
    ]


def test_initial_review_receives_session_context_as_data():
    context = bounded_session_context({"recent_messages": turn(1, DECLARATION)})
    provider = ScriptedProvider([input_review()])
    result = review_input_security(provider, message=FOLLOWUP, session_context=context)
    payload = json.loads(provider.calls[0][-1].content)
    assert payload["message"] == FOLLOWUP
    assert payload["session_context"] == context
    assert DECLARATION in json.dumps(payload, ensure_ascii=False)
    assert result.memory_intent is None


@pytest.mark.parametrize("scope", ["conversation", "unclear"])
def test_wrong_list_candidate_cannot_bypass_the_normal_answer(scope):
    context = bounded_session_context({"recent_messages": turn(1, DECLARATION)})
    candidate = MemoryIntent(action="list", quote=FOLLOWUP)
    provider = ScriptedProvider([review_scope(scope=scope)])
    assert review_memory_candidate(provider, message=FOLLOWUP, candidate=candidate,
                                   session_context=context) is None
    payload = json.loads(provider.calls[0][-1].content)
    assert FOLLOWUP in json.dumps(payload, ensure_ascii=False)
    assert DECLARATION in json.dumps(payload, ensure_ascii=False)
    assert provider.options[0]["tools"][0]["function"]["name"] == "submit_memory_scope_review"


def test_explicit_long_term_scope_keeps_the_original_typed_command():
    query = "请跨会话保存回答简洁这个偏好。"
    candidate = MemoryIntent(action="save", quote=query, memory_type="preference",
                             key="response_style", value="concise")
    provider = ScriptedProvider([review_scope(operation_quote="保存回答简洁这个偏好", scope_quote="跨会话")])
    actual = review_memory_candidate(provider, message=query, candidate=candidate, session_context=[])
    assert actual == candidate


@pytest.mark.parametrize("target,memory_type,key", [
    ("all", None, None),
    ("type", "preference", None),
    ("one", "preference", "output_format"),
    ("one", "research_context", "research_topic"),
    ("one", "research_archive", "研究样本甲"),
])
def test_list_review_supplies_explicit_target_instead_of_reusing_broad_candidate(target, memory_type, key):
    query = "查询我已保存的长期记忆中当前明确指定的范围。"
    candidate = MemoryIntent(action="list", quote=query)
    provider = ScriptedProvider([review_scope(query, list_target=target, memory_type=memory_type, key=key)])
    actual = review_memory_candidate(provider, message=query, candidate=candidate, session_context=[])
    assert actual.action == "list" and actual.quote == query
    assert (actual.memory_type, actual.key) == (memory_type, key)
    assert candidate.memory_type is None and candidate.key is None


def test_list_review_does_not_interpret_missing_target_as_all():
    query = "我现在还保存着长期展示格式偏好吗？请查询当前有效的记录。"
    candidate = MemoryIntent(action="list", quote=query)
    provider = ScriptedProvider([review_scope(query), review_scope(query)])
    with pytest.raises(ValueError):
        review_memory_candidate(provider, message=query, candidate=candidate, session_context=[])
    assert len(provider.calls) == 2


def test_list_target_can_be_repaired_once_without_widening_the_requested_scope():
    query = "我现在还保存着长期展示格式偏好吗？请查询当前有效的记录。"
    candidate = MemoryIntent(action="list", quote=query)
    provider = ScriptedProvider([
        review_scope(query), review_scope(query, list_target="one", memory_type="preference", key="output_format"),
    ])
    actual = review_memory_candidate(provider, message=query, candidate=candidate, session_context=[])
    assert actual.memory_type == "preference" and actual.key == "output_format"
    assert len(provider.calls) == 2


@pytest.mark.parametrize("target,memory_type,key", [
    ("all", "preference", None),
    ("type", None, None),
    ("type", "preference", "output_format"),
    ("one", "preference", None),
    ("one", "preference", "unsupported_key"),
    ("one", "research_context", "output_format"),
])
def test_invalid_list_scope_is_rejected(target, memory_type, key):
    query = "查询已保存的长期记忆。"
    candidate = MemoryIntent(action="list", quote=query)
    invalid = review_scope(query, list_target=target, memory_type=memory_type, key=key)
    provider = ScriptedProvider([invalid, invalid])
    with pytest.raises(ValueError):
        review_memory_candidate(provider, message=query, candidate=candidate, session_context=[])


def test_list_target_cannot_change_a_save_command():
    query = "请跨会话保存回答简洁。"
    candidate = MemoryIntent(action="save", quote=query, memory_type="preference",
                             key="response_style", value="concise")
    invalid = review_scope(query, list_target="one", memory_type="preference", key="output_format")
    provider = ScriptedProvider([invalid, invalid])
    with pytest.raises(ValueError):
        review_memory_candidate(provider, message=query, candidate=candidate, session_context=[])


@pytest.mark.parametrize("query", [
    "不要长期保存，刚才的代号只在这个会话使用。",
    "解释这句指令是什么意思：‘请长期记住回答简洁’，不要实际执行。",
    "如果以后要长期保存偏好，系统会怎么处理？",
])
def test_history_cannot_supply_consent_when_current_request_is_not_a_command(query):
    context = bounded_session_context({"recent_messages": turn(1, "请长期记住回答简洁")})
    candidate = MemoryIntent(action="list", quote=query)
    provider = ScriptedProvider([review_scope(scope="conversation")])
    assert review_memory_candidate(provider, message=query, candidate=candidate,
                                   session_context=context) is None
    assert json.loads(provider.calls[0][-1].content)["message"] == query


def test_session_context_excludes_failed_and_memory_control_turns_without_mutation():
    memory = turn(1, "请长期记住回答简洁", memory_scope="long_term", memory_action={"action": "save"})
    failed = turn(2, "把代号改成错误值", task_status="cancelled")
    safe = turn(3, DECLARATION)
    conversation = {"recent_messages": [*memory, *failed, *safe]}
    original = copy.deepcopy(conversation)
    context = bounded_session_context(conversation)
    assert [m["content"] for m in context] == [m["content"] for m in safe]
    assert conversation == original


def test_context_budget_never_turns_a_negation_into_consent_by_truncation():
    long_quote = "请长期记住回答简洁。" + "以下只是讨论材料。" * 4000 + "以上都是引用，不要执行。"
    safe = turn(3, DECLARATION)
    context = bounded_session_context({"recent_messages": [*turn(1, long_quote), *safe]})
    contents = [item["content"] for item in context]
    assert [item["content"] for item in safe] == contents


def test_latest_oversized_turn_does_not_resurrect_an_older_value():
    old = turn(1, "临时代号是旧值")
    latest = turn(2, "把代号改成新值。" + "仅当前会话。" * 100)
    assert bounded_session_context({"recent_messages": [*old, *latest]}, max_tokens=80) == []


def test_context_uses_a_bounded_count_of_recent_committed_turns():
    messages = [item for index in range(6) for item in turn(index, f"第{index}轮")]
    context = bounded_session_context({"recent_messages": messages}, max_turns=2)
    assert [item["content"] for item in context] == ["第4轮", "收到", "第5轮", "收到"]


def test_revoking_preferences_does_not_remove_committed_user_facts_from_review():
    from app.services.memory_scope import clean_conversation_for_memory_revision

    history = [*turn(1, "临时代号是青竹47"), *turn(2, "临时代号已改为秋水82")]
    for item in history:
        if item["role"] == "assistant":
            item["metadata"]["memory_context_fingerprint"] = "old"
    conversation = {"recent_messages": history, "memory_context_fingerprint": "old"}
    cleaned = clean_conversation_for_memory_revision(
        conversation, SimpleNamespace(owner_revision=2, context_fingerprint="revoked"),
    )
    assert all(item["role"] == "user" for item in cleaned["recent_messages"])
    context = bounded_session_context(cleaned, max_turns=1)
    assert context == [{"role": "user", "content": "临时代号已改为秋水82"}]


@pytest.mark.parametrize("operation_quote,scope_quote", [
    ("历史消息中才出现的操作", "跨会话"),
    ("保存回答简洁", "历史消息中才出现的范围"),
])
def test_verification_rejects_consent_quotes_not_in_current_message(operation_quote, scope_quote):
    query = "请跨会话保存回答简洁。"
    candidate = MemoryIntent(action="save", quote=query, memory_type="preference",
                             key="response_style", value="concise")
    bad = review_scope(operation_quote=operation_quote, scope_quote=scope_quote)
    provider = ScriptedProvider([bad, bad])
    with pytest.raises(ValueError):
        review_memory_candidate(provider, message=query, candidate=candidate, session_context=[])
    assert len(provider.calls) == 2


def test_invalid_scope_proof_can_be_repaired_once():
    query = "请跨会话保存回答简洁。"
    candidate = MemoryIntent(action="save", quote=query, memory_type="preference",
                             key="response_style", value="concise")
    bad = review_scope(operation_quote="伪造引文", scope_quote="跨会话")
    provider = ScriptedProvider([bad, review_scope(query)])
    assert review_memory_candidate(provider, message=query, candidate=candidate, session_context=[]) == candidate
    assert len(provider.calls) == 2


def test_scope_schema_repair_uses_the_remaining_shared_deadline(monkeypatch):
    query = "请跨会话保存回答简洁。"
    candidate = MemoryIntent(action="save", quote=query, memory_type="preference",
                             key="response_style", value="concise")
    times = iter([100, 101, 106])
    monkeypatch.setattr("app.agents.memory_intent_review.monotonic", lambda: next(times))
    provider = ScriptedProvider([
        review_scope(operation_quote="伪造引文", scope_quote="跨会话"), review_scope(query),
    ])
    assert review_memory_candidate(provider, message=query, candidate=candidate,
                                   session_context=[], timeout_seconds=10) == candidate
    assert [options["timeout_seconds"] for options in provider.options] == [9, 4]


def test_expired_scope_deadline_does_not_start_a_repair_call(monkeypatch):
    query = "请跨会话保存回答简洁。"
    candidate = MemoryIntent(action="save", quote=query, memory_type="preference",
                             key="response_style", value="concise")
    times = iter([100, 101, 111])
    monkeypatch.setattr("app.agents.memory_intent_review.monotonic", lambda: next(times))
    provider = ScriptedProvider([review_scope(operation_quote="伪造引文", scope_quote="跨会话")])
    with pytest.raises(ValueError, match="timed out"):
        review_memory_candidate(provider, message=query, candidate=candidate,
                                session_context=[], timeout_seconds=10)
    assert len(provider.calls) == 1


def test_scope_verification_failure_cannot_execute_or_claim_a_memory_write(world):
    async def scenario():
        query = "请跨会话保存回答简洁。"
        request, control, history = world.start(query)
        provider = ScriptedProvider([review_memory(query), RuntimeError("scope service unavailable")])
        execution, calls = runner(request, provider, history=history)
        async with runtime_for(world) as runtime:
            with pytest.raises(RecoverableRunError):
                await runtime.run(execution, control)
            state = await runtime.graph.aget_state(execution_config(control.row, control.execution))
            assert not state.values.get("memory_request") and not state.values.get("memory_action")
            assert calls == []
            from app.agents.memory_commands import repository_for
            assert repository_for(control).list_active(world.owner) == []

    run_async(scenario())


def test_legacy_checkpoint_proposal_cannot_skip_scope_confirmation(world, monkeypatch):
    from app.agents.memory_commands import repository_for
    from app.agents.runner import durable_runtime

    original_review = durable_runtime._review

    def legacy_proposal(state, deps):
        reviewed = original_review(state, deps)
        reviewed["memory_request"].pop("scope_review_version")
        return reviewed

    monkeypatch.setattr(durable_runtime, "_review", legacy_proposal)

    async def scenario():
        query = "请跨会话保存回答简洁。"
        request, control, history = world.start(query)
        provider = ScriptedProvider([review_memory(query), review_scope(query)])
        execution, calls = runner(request, provider, history=history)
        async with runtime_for(world) as runtime:
            result = await runtime.run(execution, control)
            assert result.status == "clarify" and result.stop_reason == "context_upgrade"
            assert result.memory_action["action"] == "unsupported"
            assert calls == [] and repository_for(control).list_active(world.owner) == []
            snapshot = await runtime.graph.aget_state(execution_config(control.row, control.execution))
            assert "scope_review_version" not in snapshot.values["memory_request"]

    run_async(scenario())


def test_original_three_turn_failure_keeps_short_term_history_and_runs_answer_model(world):
    async def scenario():
        baseline = "请列出我目前已保存的长期记忆。若没有，请明确说明。"
        request, control, history = world.start(baseline)
        provider = ScriptedProvider([review_memory(baseline, action="list"), review_scope(baseline, list_target="all")])
        execution, _ = runner(request, provider, history=history)
        async with runtime_for(world) as runtime:
            result = await runtime.run(execution, control)
            assert result.memory_action["action"] == "list"
            deliver(world, request, control, result)

        request, control, history = world.start(DECLARATION)
        provider = ScriptedProvider([input_review(), AIMessage(content="收到，青竹47。")])
        execution, _ = runner(request, provider, history=history)
        async with runtime_for(world) as runtime:
            result = await runtime.run(execution, control)
            deliver(world, request, control, result)

        request, control, history = world.start(FOLLOWUP)
        # Reproduce the bad initial output exactly; recovery cannot rely solely
        # on asking the first classifier to behave better.
        provider = ScriptedProvider([
            review_memory(FOLLOWUP, action="list"), review_scope(scope="conversation"),
            AIMessage(content="青竹47"),
        ])
        execution, calls = runner(request, provider, history=history)
        async with runtime_for(world) as runtime:
            result = await runtime.run(execution, control)
            assert result.answer == "青竹47" and result.stop_reason == "answered"
            assert not result.memory_action and calls == []
            assert DECLARATION in [item.content for item in provider.calls[-1]]
            assert baseline not in str(provider.calls[-1])
            state = await runtime.graph.aget_state(execution_config(control.row, control.execution))
            assert state.values["model_rounds"] == 1
            assert state.values["memory_request"] == {}
            deliver(world, request, control, result)
            ledger = world.chats.get_session(world.session_id, owner_id=world.owner).messages
            assert not ledger[-2].metadata.get("memory_scope")
            assert not ledger[-1].metadata.get("memory_action")

    run_async(scenario())


def test_deleted_format_query_does_not_return_an_unrelated_surviving_preference(world):
    from app.agents.memory_commands import repository_for
    from app.memory_models import MemoryWrite

    async def scenario():
        query = "我现在还保存着长期展示格式偏好吗？请查询当前有效的记录。"
        request, control, history = world.start(query)
        repository = repository_for(control)
        repository.put(world.owner, MemoryWrite(memory_type="preference", key="response_style", value="concise",
            source_user_message="请长期保存回答简洁"), "save-style")
        saved_format = repository.put(world.owner, MemoryWrite(memory_type="preference", key="output_format", value="table",
            source_user_message="请长期保存表格格式"), "save-format").current
        repository.delete(world.owner, saved_format.memory_id, saved_format.revision, "delete-format")
        provider = ScriptedProvider([
            review_memory(query, action="list", key=None),
            review_scope(query, list_target="one", memory_type="preference", key="output_format"),
        ])
        execution, calls = runner(request, provider, history=history)
        async with runtime_for(world) as runtime:
            result = await runtime.run(execution, control)
            assert result.status == "answered" and result.stop_reason == "memory_managed"
            assert result.memory_action["key"] == "output_format"
            assert "没有" in result.answer and "简洁" not in result.answer
            assert "回答详略" not in result.answer
            assert repository.get_by_key(world.owner, "preference", "response_style").value == "concise"
            assert repository.get_by_key(world.owner, "preference", "output_format") is None
            assert calls == []

    run_async(scenario())
