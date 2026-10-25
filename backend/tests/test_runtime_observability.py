"""Trace metadata is joined across threads and exporter faults stay fail-open."""

from contextlib import contextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
import pytest

from app.services import agent_tracing
import app.main as main_module
from app.agents.runner import adapter
from app.agents.runner.durable_runtime import execution_context
from app.repositories.tool_result_repository import ToolResultRepository
from app.routers import agents
from app.services.agent_tracing import LangfuseTracer, trace_context, trace_metadata, trace_stage
from durable_runtime_fixtures import run_async
from test_agent_tracing import ACTIVE_SETTINGS, _FakeClient, _FakePropagate
from test_durable_agent import fixed_registry, list_answer, runner, runtime_for, tool_call, world
from test_runner_production import input_review
from test_runner_shadow import ScriptedProvider
from test_tracing_integration import _provider, USAGE
from tracing_support import RecordingTracer


def test_native_runtime_phase_spans_follow_same_run_through_worker_threads(world, monkeypatch):
    tracer = RecordingTracer()
    monkeypatch.setattr(agent_tracing, "_tracer", tracer)
    request, control, history = world.start()
    execution, _ = runner(request, ScriptedProvider([input_review(), tool_call(), list_answer()]), history=history)
    async def scenario():
        async with runtime_for(world) as runtime:
            result = await runtime.run(execution, control)
        assert result.status == "answered"
    run_async(scenario())
    names = {span.kwargs["name"] for span in tracer.stages}
    assert {"context_preparation", "memory_retrieval", "checkpoint_resume", "checkpoint_read",
            "input_review", "tool_execution", "final_validation"} <= names
    for span in tracer.stages:
        assert span.kwargs["metadata"]["run_id"] == control.row["run_id"]
        assert span.kwargs["metadata"]["thread_id"] == world.session_id
        assert span.finished["output"]["latency_ms"] >= 0
    prepared = [span.finished["output"] for span in tracer.stages if span.kwargs["name"] == "context_preparation"]
    assert any(item.get("context_version") == "context-v2" and item["estimated_input_tokens"] > 0 for item in prepared)
    tool = next(span for span in tracer.stages if span.kwargs["name"] == "tool_execution")
    assert tool.kwargs["metadata"]["tool_call_id"] == "tool-1"
    assert trace_metadata() == {}, "per-run observability context must be restored"


def test_generation_receives_join_keys_memory_cost_and_measured_usage(monkeypatch):
    tracer = RecordingTracer()
    monkeypatch.setattr(agent_tracing, "_tracer", tracer)
    provider = _provider(AIMessage(content="固定说明", response_metadata={"token_usage": USAGE}))
    with trace_context(run_id="run-a", thread_id="thread-a", session_id="thread-a", context_version="context-v2",
                       prompt_version="prompt-v3", model_version=provider.model, recovery_count=2,
                       memory_hit_count=1, memory_tokens=55, summary_triggered=True,
                       owner_id="not-exported", lease_token="not-exported"):
        provider.generate_messages([HumanMessage(content="解释首板")], [])
    recorded = tracer.generations[0]
    metadata = recorded.kwargs["metadata"]
    assert metadata["run_id"] == "run-a" and metadata["thread_id"] == "thread-a"
    assert metadata["recovery_count"] == 2 and metadata["memory_tokens"] == 55
    assert metadata["summary_triggered"] is True and metadata["context_version"] == "context-v2"
    assert "owner_id" not in metadata and "lease_token" not in metadata
    assert recorded.finished["usage"] == {"input": 12, "output": 4, "total": 16}


def test_receipt_replay_is_observed_without_adding_fields_to_saved_evidence(world, monkeypatch):
    tracer = RecordingTracer()
    monkeypatch.setattr(agent_tracing, "_tracer", tracer)
    _, control, _ = world.start()
    repository = ToolResultRepository(control)
    original = {"status": "ok", "rows": [{"symbol": "600001", "fixture_value": 7}]}
    call = {"id": "receipt-1", "name": "fixture", "args": {}}
    executed = []
    def operation():
        executed.append(True)
        return original
    with execution_context(SimpleNamespace(control=control)):
        assert repository.execute(call, operation) == original
        assert repository.execute(call, operation) == original
    assert executed == [True] and repository.read("receipt-1") == original
    claims = [span for span in tracer.stages if span.kwargs["metadata"].get("operation") == "claim_or_replay"]
    assert [span.finished["output"]["receipt_reused"] for span in claims] == [False, True]
    assert all(span.kwargs["metadata"]["tool_call_id"] == "receipt-1" for span in claims)


def test_real_http_delivery_has_phase_span_and_stable_thread_identity(postgresql_database, monkeypatch):
    tracer = RecordingTracer()
    provider = ScriptedProvider([input_review(), AIMessage(content="首板表示本轮首次涨停。")])
    tools, _ = fixed_registry()
    monkeypatch.setattr(agent_tracing, "_tracer", tracer)
    monkeypatch.setattr(agent_tracing, "configure_agent_tracer", lambda *args, **kwargs: tracer)
    monkeypatch.setattr(adapter, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(adapter, "AgentToolRegistry", lambda **_: tools)
    monkeypatch.setattr(agents, "get_limit_up_repository", lambda: SimpleNamespace(list_events=lambda: []))
    monkeypatch.setenv("LIMITUPLAB_AGENT_COMPLIANCE", "deterministic")
    with TestClient(main_module.app) as client:
        session = client.post("/api/agents/chat/sessions", json={"title": "Trace fixture"}).json()["session_id"]
        response = client.post("/api/agents/chat", json={"session_id": session, "message_id": "trace-1", "message": "解释首板"})
        assert response.status_code == 200 and response.json()["task_status"] == "complete"
    assert tracer.runs[0].kwargs["metadata"]["thread_id"] == session
    delivery = next(span for span in tracer.stages if span.kwargs["name"] == "final_delivery")
    assert delivery.kwargs["metadata"]["run_id"] == response.json()["run_id"]
    assert delivery.finished["output"]["status"] == "ok"
    assert tracer.runs[0].finished_event.wait(timeout=3)
    assert tracer.runs[0].finished["output"]["recovery_count"] == 0


class ClosingFailureClient(_FakeClient):
    @contextmanager
    def start_as_current_observation(self, **kwargs):
        with super().start_as_current_observation(**kwargs) as span:
            yield span
        raise RuntimeError("sk-secret-collector-credential")


@pytest.mark.parametrize("kind", ["run", "generation", "tool", "stage"])
def test_exporter_context_exit_failure_is_logged_without_credentials(kind, caplog):
    tracer = LangfuseTracer(ACTIVE_SETTINGS, client=ClosingFailureClient(), propagate=_FakePropagate())
    contexts = {"run": lambda: tracer.run(run_id="run", session_id="thread"),
                "generation": lambda: tracer.generation(name="model", model="fake"),
                "tool": lambda: tracer.tool(name="fixture"), "stage": lambda: tracer.stage(name="prepare")}
    reached = False
    with contexts[kind]():
        reached = True
    assert reached
    assert "agent_tracing_failure" in caplog.text and "RuntimeError" in caplog.text
    assert "sk-secret" not in caplog.text and "collector-credential" not in caplog.text


def test_exporter_exit_fault_does_not_replace_business_exception(caplog):
    tracer = LangfuseTracer(ACTIVE_SETTINGS, client=ClosingFailureClient(), propagate=_FakePropagate())
    with pytest.raises(ValueError, match="business-error"):
        with tracer.generation(name="model", model="fake"):
            raise ValueError("business-error")
    assert "sk-secret" not in caplog.text


def test_stage_setup_failure_does_not_break_the_business_body(monkeypatch, caplog):
    class BrokenTracer:
        def stage(self, **kwargs):
            raise RuntimeError("sk-secret-stage-credential")
    monkeypatch.setattr(agent_tracing, "_tracer", BrokenTracer())
    with trace_stage("context_preparation") as details:
        details["memory_hit_count"] = 2
        result = 42
    assert result == 42 and "phase=stage.enter" in caplog.text
    assert "sk-secret" not in caplog.text
