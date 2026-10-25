"""Budget receipts, unavailable-host behavior and context cost attribution."""

from langchain_core.messages import AIMessage
import pytest

from app.agents.run_lifecycle import CURRENT_CONTROL
from app.agents.runtime_errors import RecoverableRunError
from app.services.llm_provider import capture_llm_usage, current_llm_usage
from durable_runtime_fixtures import run_async
from test_durable_agent import world, runner, runtime_for, tool_call, list_answer
from test_runner_production import input_review
from test_runner_shadow import ScriptedProvider


def test_tool_budget_exhaustion_has_no_nonexistent_receipt(world):
    request, control, history = world.start()
    calls = [tool_call(f"call-{index}", index + 1).tool_calls[0] for index in range(9)]
    provider = ScriptedProvider([input_review(), AIMessage(content="", tool_calls=calls), list_answer()])
    execution, _ = runner(request, provider, history=history)

    async def scenario():
        async with runtime_for(world) as runtime:
            result = await runtime.run(execution, control)
            assert result.tool_count == 8
            assert len(result.tool_messages) == 9
            assert result.tool_messages[-1]["status"] == "error"
            assert "budget" in result.tool_messages[-1]["error"].lower()
            assert all("result_ref" not in item for item in result.tool_messages)
            with world.journal.connection() as db:
                assert db.execute("SELECT count(*) FROM agent_tool_results").fetchone()[0] == 8
    run_async(scenario())


def test_unavailable_host_does_not_fall_back_to_uncheckpointed_execution(world, monkeypatch):
    request, control, history = world.start()
    provider = ScriptedProvider([])
    execution, calls = runner(request, provider, history=history)
    monkeypatch.setattr("app.agents.runner.durable_runtime.get_runtime", lambda: None)
    token = CURRENT_CONTROL.set(control)
    try:
        with pytest.raises(RecoverableRunError):
            execution.run()
    finally:
        CURRENT_CONTROL.reset(token)
    assert provider.calls == [] and calls == []


def test_context_diagnostics_reach_model_usage_record(world):
    class MeteredProvider(ScriptedProvider):
        def generate_messages(self, messages, tools, **kwargs):
            current_llm_usage().begin_call(self.model)
            return super().generate_messages(messages, tools, **kwargs)

    request, control, history = world.start()
    provider = MeteredProvider([input_review(), AIMessage(content="固定研究说明。")])
    execution, _ = runner(request, provider, history=history)
    async def scenario():
        with capture_llm_usage() as tracker:
            async with runtime_for(world) as runtime:
                await runtime.run(execution, control)
            context = tracker.calls[-1].context
            assert context["stage"] == "agent"
            assert context["context_version"] == "context-v2"
            assert context["run_id"] == control.row["run_id"]
            assert context["estimated_input_tokens"] > 0
            assert context["recovery_count"] == 0
    run_async(scenario())
