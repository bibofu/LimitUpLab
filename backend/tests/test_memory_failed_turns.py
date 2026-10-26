"""Canceled/failed management intent cannot return through ordinary history."""

import json

from langchain_core.messages import AIMessage
import pytest

from app.agents import memory_commands
from app.agents.context_builder import build_context
from app.agents.conversation_memory import compress_conversation
from app.agents.runtime_errors import RecoverableRunError
from app.routers.react_chat import failed_response
from app.services.memory_scope import clean_conversation_for_memory_revision
from durable_runtime_fixtures import execution_config, run_async
from test_durable_agent import runner, runtime_for, world
from test_durable_long_term_memory import review_memory, review_scope
from test_memory_retrieval import SnapshotRepository, retrieve
from test_runner_production import input_review
from test_runner_shadow import ScriptedProvider


COMMAND = "请长期记住：以后默认简洁回答"


def failed_turn(task_status, status="success"):
    return [
        {"message_id": "u-cancel", "role": "user", "content": COMMAND, "run_id": "cancel"},
        {"message_id": "a-cancel", "role": "assistant", "content": "本轮未完成", "run_id": "cancel",
         "status": status, "metadata": {"task_status": task_status}},
    ]


def successful_turns():
    return [item for index in range(3) for item in (
        {"message_id": f"u-{index}", "role": "user", "run_id": f"ok-{index}",
         "content": "只看主板首板评级，关注风险。" * 8},
        {"message_id": f"a-{index}", "role": "assistant", "run_id": f"ok-{index}",
         "content": "已展示来源、日期与数据缺口。" * 8, "metadata": {"task_status": "partial"}},
    )]


@pytest.mark.parametrize("task_status,status", [
    ("cancelled", "success"), ("error", "error"), ("refuse", "success"), (None, "error"),
])
def test_failed_turn_cannot_enter_prompt_or_real_rolling_summary(task_status, status):
    history = [*failed_turn(task_status, status), *successful_turns()]
    prompt = build_context(system_prompt="研究规则", query="下一题", working_messages=[],
                           conversation={"recent_messages": history})
    assert COMMAND not in str(prompt.messages)
    state = compress_conversation({}, history, summary_trigger_tokens=1000, recent_window_tokens=500)
    assert state["summary_triggered"]
    assert COMMAND not in json.dumps(state, ensure_ascii=False)
    assert any("只看主板" in item for item in state["active_constraints"])
    assert state["last_committed_message_id"] == "a-2"


def test_late_failed_response_removes_user_already_in_recent_and_advances_cursor():
    user, assistant = failed_turn("error", "error")
    first = compress_conversation({}, [user])
    assert first["recent_messages"] == [user]
    second = compress_conversation(first, [user, assistant])
    assert second["recent_messages"] == []
    assert second["last_committed_message_id"] == assistant["message_id"]
    assert compress_conversation(second, [user, assistant]) == second


def test_delete_rebuild_cannot_promote_canceled_command_to_session_constraint():
    state = {"memory_context_fingerprint": "old", "conversation_summary": COMMAND,
             "active_constraints": [COMMAND], "recent_messages": failed_turn("cancelled")}
    current = retrieve(SnapshotRepository([], revision=2, fingerprint="deleted"))
    cleaned = clean_conversation_for_memory_revision(state, current)
    assert COMMAND not in json.dumps(cleaned, ensure_ascii=False)
    assert cleaned["recent_messages"] == [] and cleaned["active_constraints"] == []


@pytest.mark.parametrize("write_committed", [False, True])
def test_crash_before_memory_checkpoint_then_cancel_never_revives_intent(world, monkeypatch, write_committed):
    async def scenario():
        request, control, history = world.start(COMMAND)
        provider = ScriptedProvider([review_memory(COMMAND), review_scope(COMMAND)])
        execution, _ = runner(request, provider, history=history)
        original = memory_commands.execute_memory_request

        def interrupted(state, current_control):
            if write_committed:
                original(state, current_control)
            raise RuntimeError("injected crash before memory node checkpoint")

        with monkeypatch.context() as patch:
            patch.setattr(memory_commands, "execute_memory_request", interrupted)
            async with runtime_for(world) as runtime:
                with pytest.raises(RecoverableRunError):
                    await runtime.run(execution, control)
                snapshot = await runtime.graph.aget_state(execution_config(control.row, control.execution))
                assert snapshot.next == ("memory",)
                assert snapshot.values["memory_request"] and not snapshot.values.get("memory_action")

        # This is the same public terminal document used by the cancel endpoint
        # when the interrupted worker no longer owns a live task.
        world.lifecycle.cancel(control.row["run_id"], world.owner)
        delivered = world.lifecycle.services.delivery.commit(control.row["run_id"], world.owner,
            failed_response(control.row, "cancelled").model_dump(mode="json"), execution=control.execution)
        assert delivered["task_status"] == "cancelled" and not delivered["memory_action"]
        ledger = world.chats.get_session(world.session_id, owner_id=world.owner).messages
        assert ledger[0].content == COMMAND and not ledger[0].metadata
        assert ledger[-1].status == "success"
        repository = memory_commands.repository_for(control)
        saved = repository.get_by_key(world.owner, "preference", "response_style")
        assert bool(saved) == write_committed
        if saved:
            repository.delete(world.owner, saved.memory_id, saved.revision, "explicit-revocation")
        control.execution.release()

        request, next_control, history = world.start("什么是首板")
        next_provider = ScriptedProvider([input_review(), AIMessage(content="首板是连续涨停中的首个涨停板。")])
        execution, _ = runner(request, next_provider, history=history)
        async with runtime_for(world) as runtime:
            result = await runtime.run(execution, next_control)
            assert result.status == "answered" and not result.memory_context_used
            assert COMMAND not in str(next_provider.calls)
            snapshot = await runtime.graph.aget_state(execution_config(next_control.row, next_control.execution))
            assert COMMAND not in json.dumps(snapshot.values["conversation"], ensure_ascii=False)
            assert repository.list_active(world.owner) == []

    run_async(scenario())
