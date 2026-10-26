"""Real native checkpoints preserve work across explicit pause and resume."""

import asyncio
from datetime import date
from uuid import uuid4

from langchain_core.messages import AIMessage
import pytest

from app.agents.run_pause import PausedRunError
from app.agents.runtime_errors import RecoverableRunError, RunPauseRequested
from durable_runtime_fixtures import execution_config, run_async
from test_durable_agent import list_answer, runner, runtime_for, tool_call, world
from test_runner_production import input_review
from test_runner_shadow import ScriptedProvider


def request_pause(control):
    service = control.lifecycle.services.controls
    row = service.read(control.row["run_id"], control.row["owner_id"])
    return service.request(control.row["run_id"], control.row["owner_id"], "pause", uuid4().hex,
                           row["control_revision"])


def acknowledge(control, outcome):
    return control.lifecycle.services.controls.ack_paused(
        outcome.run_id, control.row["owner_id"], expected_revision=outcome.pause_revision,
        checkpoint_id=outcome.checkpoint_id, pause_mode=outcome.resume_mode,
        interrupt_id=outcome.interrupt_id, execution=control.execution,
    )


def resume(control, outcome):
    row = acknowledge(control, outcome)
    return control.lifecycle.services.controls.request(
        outcome.run_id, control.row["owner_id"], "resume", uuid4().hex, row["control_revision"],
    )


class PauseAfterResponse(ScriptedProvider):
    def __init__(self, responses, control, after=2):
        super().__init__(responses)
        self.control, self.after = control, after

    def generate_messages(self, *args, **kwargs):
        value = super().generate_messages(*args, **kwargs)
        if len(self.calls) == self.after:
            request_pause(self.control)
        return value


def test_pause_before_first_checkpoint_never_starts_a_model_or_summary(world):
    request, control, history = world.start()
    execution, calls = runner(request, ScriptedProvider([]), history=history)
    request_pause(control)

    async def scenario():
        async with runtime_for(world) as runtime:
            with pytest.raises(PausedRunError) as error:
                await runtime.run(execution, control)
            assert error.value.outcome.checkpoint_id is None
            assert error.value.outcome.resume_mode == "checkpoint"
            assert execution.provider.calls == [] and calls == []
            assert (await runtime.graph.aget_state(execution_config(control.row, control.execution))).values == {}
            acknowledge(control, error.value.outcome)
            assert world.lifecycle.get(control.row["run_id"], world.owner)["response_json"] is None
    run_async(scenario())


def test_native_interrupt_resumes_tool_plan_without_repeating_model_or_budget(world):
    request, control, history = world.start()
    provider = PauseAfterResponse([input_review(), tool_call()], control)
    execution, calls = runner(request, provider, history=history)

    async def scenario():
        async with runtime_for(world) as runtime:
            with pytest.raises(PausedRunError) as error:
                await runtime.run(execution, control)
            outcome = error.value.outcome
            snapshot = await runtime.graph.aget_state(execution_config(control.row, control.execution))
            assert outcome.resume_mode == "interrupt" and outcome.interrupt_id
            assert snapshot.next == ("pause",) and len(snapshot.interrupts) == 1
            assert outcome.checkpoint_id == snapshot.config["configurable"]["checkpoint_id"]
            assert snapshot.values["model_rounds"] == 1 and snapshot.values["tool_count"] == 0
            remaining = snapshot.values["remaining_seconds"]
            assert calls == [] and len(provider.calls) == 2
            acknowledge(control, outcome)
            with pytest.raises(PausedRunError):
                await runtime.run(execution, control)
            assert len(provider.calls) == 2
        resume(control, outcome)
        resumed, resumed_calls = runner(request, ScriptedProvider([list_answer()]), history=history)
        async with runtime_for(world) as runtime:
            result = await runtime.run(resumed, control)
            final = await runtime.graph.aget_state(execution_config(control.row, control.execution))
            assert result.model_rounds == 2 and result.tool_count == 1
            assert len(resumed.provider.calls) == 1 and len(resumed_calls) == 1
            assert final.values["remaining_seconds"] <= remaining
            assert final.interrupts == () and final.next == ()
            assert not final.values["pause_next_node"]
            world.deliver(request, control, result)
        assert world.lifecycle.get(control.row["run_id"], world.owner)["recovery_count"] == 0
    run_async(scenario())


def test_pause_between_tools_keeps_receipt_and_only_runs_unfinished_tool(world):
    request, control, history = world.start()
    two = AIMessage(content="", tool_calls=[tool_call("one", 1).tool_calls[0], tool_call("two", 2).tool_calls[0]])
    execution, calls = runner(request, ScriptedProvider([input_review(), two]), history=history)
    execute = execution.execute

    def pause_after_first(call):
        value = execute(call)
        request_pause(control)
        return value

    execution.execute = pause_after_first
    async def scenario():
        async with runtime_for(world) as runtime:
            with pytest.raises(PausedRunError) as error:
                await runtime.run(execution, control)
            state = await runtime.graph.aget_state(execution_config(control.row, control.execution))
            assert state.values["tool_count"] == 1
            assert [item["id"] for item in state.values["pending"]] == ["two"]
            assert state.values["tool_messages"][0]["result_ref"] == "one"
            outcome = error.value.outcome
        resume(control, outcome)
        resumed, later_calls = runner(request, ScriptedProvider([list_answer()]), history=history)
        async with runtime_for(world) as runtime:
            result = await runtime.run(resumed, control)
            assert result.tool_count == 2 and result.model_rounds == 2
            assert [item["recent_trade_days"] for item in calls] == [1]
            assert [item["recent_trade_days"] for item in later_calls] == [2]
            with world.lifecycle.connection() as db:
                assert db.execute("SELECT count(*) FROM agent_tool_results").fetchone()[0] == 2
    run_async(scenario())


def test_pause_after_final_checkpoint_freezes_delivery_then_uses_no_model(world):
    request, control, history = world.start()
    provider = PauseAfterResponse([input_review(), AIMessage(content="固定的研究说明。")], control)
    execution, calls = runner(request, provider, history=history)

    async def scenario():
        async with runtime_for(world) as runtime:
            with pytest.raises(PausedRunError) as error:
                await runtime.run(execution, control)
            outcome = error.value.outcome
            state = await runtime.graph.aget_state(execution_config(control.row, control.execution))
            assert outcome.resume_mode == "delivery" and outcome.interrupt_id is None
            assert state.values["done"] and state.next == ()
            assert world.lifecycle.get(control.row["run_id"], world.owner)["response_json"] is None
        resume(control, outcome)
        resumed, _ = runner(request, ScriptedProvider([]), history=history)
        async with runtime_for(world) as runtime:
            result = await runtime.run(resumed, control)
            assert result.answer == "固定的研究说明。" and resumed.provider.calls == [] and calls == []
            world.deliver(request, control, result)
    run_async(scenario())


def test_crash_then_pause_freezes_ordinary_checkpoint_before_any_retry(world):
    request, control, history = world.start()
    execution, calls = runner(request, ScriptedProvider([input_review(), tool_call(), ConnectionError("offline")]), history=history)

    async def scenario():
        async with runtime_for(world) as runtime:
            with pytest.raises(RecoverableRunError):
                await runtime.run(execution, control)
        request_pause(control)
        resumed, later_calls = runner(request, ScriptedProvider([list_answer()]), history=history)
        async with runtime_for(world) as runtime:
            outcome = await runtime.pause(control)
            assert outcome.resume_mode == "checkpoint"
            with pytest.raises(PausedRunError):
                await runtime.run(resumed, control)
            assert resumed.provider.calls == [] and later_calls == []
            resume(control, outcome)
            result = await runtime.run(resumed, control)
            assert result.tool_count == 1 and result.model_rounds == 2
            assert len(calls) == 1 and later_calls == []
    run_async(scenario())


def test_resume_gate_checkpoint_without_interrupt_does_not_pause_twice(world, monkeypatch):
    request, control, history = world.start()
    execution, _ = runner(request, PauseAfterResponse([input_review(), tool_call()], control), history=history)

    async def scenario():
        async with runtime_for(world) as runtime:
            stream = runtime.graph.astream
            def stop_before_pause(*args, **kwargs):
                return stream(*args, **kwargs, interrupt_before=["pause"])
            monkeypatch.setattr(runtime.graph, "astream", stop_before_pause)
            with pytest.raises(PausedRunError) as error:
                await runtime.run(execution, control)
            snapshot = await runtime.graph.aget_state(execution_config(control.row, control.execution))
            assert snapshot.next == ("pause",) and not snapshot.interrupts
            assert error.value.outcome.resume_mode == "checkpoint"
            outcome = error.value.outcome
        resume(control, outcome)
        resumed, calls = runner(request, ScriptedProvider([list_answer()]), history=history)
        async with runtime_for(world) as runtime:
            result = await runtime.run(resumed, control)
            assert result.status == "answered" and len(calls) == 1
    run_async(scenario())


def test_resume_freezes_request_date_in_model_prompt_and_gateway(world, monkeypatch):
    request, control, history = world.start()
    control.row["started_at"] = "2026-10-23T23:30:00+00:00"
    execution, _ = runner(request, PauseAfterResponse([input_review(), tool_call()], control), history=history)

    async def scenario():
        async with runtime_for(world) as runtime:
            with pytest.raises(PausedRunError) as error:
                await runtime.run(execution, control)
            outcome = error.value.outcome
            state = await runtime.graph.aget_state(execution_config(control.row, control.execution))
            assert state.values["reference_date"] == "2026-10-24"
        resume(control, outcome)
        monkeypatch.setattr("app.agents.runner.runner.current_query_reference_date", lambda: date(2026, 10, 28))
        resumed, _ = runner(request, ScriptedProvider([list_answer()]), history=history)
        assert resumed.reference_date == date(2026, 10, 28)
        async with runtime_for(world) as runtime:
            await runtime.run(resumed, control)
        assert resumed.gateway.reference_date == date(2026, 10, 24)
        assert "当前服务端日期：2026-10-24" in resumed.provider.calls[0][0].content
    run_async(scenario())


def test_sync_bridge_preserves_pause_exception_and_readonly_reconciliation(world):
    request, control, history = world.start()
    execution, _ = runner(request, ScriptedProvider([]), history=history)
    request_pause(control)
    async def scenario():
        async with runtime_for(world) as runtime:
            with pytest.raises(PausedRunError) as error:
                await asyncio.to_thread(runtime.run_sync, execution, control)
            observed = await asyncio.to_thread(runtime.pause_sync, control)
            assert observed == error.value.outcome and execution.provider.calls == []
    run_async(scenario())


def test_cancelled_native_pause_never_executes_pending_tool(world):
    request, control, history = world.start()
    execution, calls = runner(request, PauseAfterResponse([input_review(), tool_call()], control), history=history)
    async def scenario():
        async with runtime_for(world) as runtime:
            with pytest.raises(PausedRunError) as error:
                await runtime.run(execution, control)
            acknowledge(control, error.value.outcome)
            world.lifecycle.cancel(control.row["run_id"], world.owner)
            result = await runtime.run(execution, control)
            assert result.status == "cancelled" and calls == []
    run_async(scenario())


def test_pause_checkpoint_failure_is_not_acknowledged_as_paused(world, monkeypatch):
    request, control, history = world.start()
    execution, calls = runner(request, PauseAfterResponse([input_review(), tool_call()], control), history=history)
    async def scenario():
        async with runtime_for(world) as runtime:
            persist = runtime.saver.aput
            async def reject_gate(config, checkpoint, metadata, versions):
                if checkpoint["channel_values"].get("pause_next_node"):
                    raise ConnectionError("injected pause checkpoint failure")
                return await persist(config, checkpoint, metadata, versions)
            monkeypatch.setattr(runtime.saver, "aput", reject_gate)
            with pytest.raises(RecoverableRunError):
                await runtime.run(execution, control)
            row = world.lifecycle.get(control.row["run_id"], world.owner)
            assert row["run_status"] == "pausing" and row["pause_checkpoint_id"] is None
            assert row["response_json"] is None and calls == []
            outcome = await runtime.pause(control)
            assert outcome.resume_mode == "checkpoint" and outcome.checkpoint_id
    run_async(scenario())


def test_db_pause_signal_during_node_preserves_ordinary_checkpoint(world):
    request, control, history = world.start()
    execution, calls = runner(request, ScriptedProvider([input_review(), tool_call()]), history=history)
    def forbid_new_work(_call):
        raise RunPauseRequested(request_pause(control))
    execution.execute = forbid_new_work
    async def scenario():
        async with runtime_for(world) as runtime:
            with pytest.raises(PausedRunError) as error:
                await runtime.run(execution, control)
            assert error.value.outcome.resume_mode == "checkpoint"
            outcome = error.value.outcome
        assert calls == []
        resume(control, outcome)
        resumed, later_calls = runner(request, ScriptedProvider([list_answer()]), history=history)
        async with runtime_for(world) as runtime:
            result = await runtime.run(resumed, control)
            assert result.tool_count == 1 and len(later_calls) == 1
    run_async(scenario())


@pytest.mark.parametrize("field,bad_value", [
    ("pause_checkpoint_id", "different-checkpoint"),
    ("pause_interrupt_id", "different-interrupt"),
    ("pause_mode", "delivery"),
])
def test_user_resume_is_bound_to_confirmed_checkpoint_and_interrupt(world, field, bad_value):
    request, control, history = world.start()
    execution, calls = runner(request, PauseAfterResponse([input_review(), tool_call()], control), history=history)
    async def scenario():
        async with runtime_for(world) as runtime:
            with pytest.raises(PausedRunError) as error:
                await runtime.run(execution, control)
            control.row = resume(control, error.value.outcome)
            await runtime.validate_resume(control)
            control.row[field] = bad_value
            with pytest.raises(RecoverableRunError, match="检查点"):
                await asyncio.to_thread(runtime.validate_resume_sync, control)
            assert calls == [] and len(execution.provider.calls) == 2
    run_async(scenario())


def test_pause_before_memory_write_keeps_intent_unapplied_until_resume(world):
    from app.agents.memory_commands import repository_for
    from test_durable_long_term_memory import review_memory
    query = "请长期记住：回答保持简洁"
    request, control, history = world.start(query)
    execution, _ = runner(request, PauseAfterResponse([review_memory(query)], control, after=1), history=history)
    async def scenario():
        async with runtime_for(world) as runtime:
            with pytest.raises(PausedRunError) as error:
                await runtime.run(execution, control)
            assert repository_for(control).list_active(world.owner) == []
            control.row = resume(control, error.value.outcome)
            await runtime.validate_resume(control)
        resumed, _ = runner(request, ScriptedProvider([]), history=history)
        async with runtime_for(world) as runtime:
            result = await runtime.run(resumed, control)
            assert result.status == "answered" and result.stop_reason == "memory_managed"
            assert repository_for(control).get_by_key(world.owner, "preference", "response_style").value == "concise"
            assert resumed.provider.calls == []
    run_async(scenario())
