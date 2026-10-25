"""Instrumentation tests: provider calls and tool executions reach the tracer."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from app.agents.runner.runner import AgentRunner
from app.agents.runner.tool_view import dump
from app.services import agent_tracing
from app.services.langchain_provider import LangChainChatProvider

from tracing_support import RecordingTracer


USAGE = {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}


class _StubChatModel:
    root_client = None
    extra_body = {"thinking": {"type": "disabled"}}

    def __init__(self, message):
        self.message = message

    def model_copy(self, update=None):
        return self

    def bind(self, **kwargs):
        return self

    def bind_tools(self, tools, **kwargs):
        return self

    def invoke(self, messages, config=None):
        return self.message


class _Gateway:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error

    def validate(self, call):
        if self.error is not None:
            raise self.error
        return call["args"]

    def execute(self, name, arguments):
        return self.value


@pytest.fixture(autouse=True)
def _restore_tracer():
    original = agent_tracing._tracer
    yield
    agent_tracing._tracer = original


def _provider(message=None) -> LangChainChatProvider:
    return LangChainChatProvider(
        api_key="test-key",
        model="deepseek-v4-flash",
        base_url="http://example.invalid",
        timeout_seconds=1,
        planner_max_tokens=16,
        thinking_enabled=False,
        max_attempts=1,
        native_function_calling_enabled=True,
        chat_model=_StubChatModel(message or AIMessage(content="ok")),
    )


def test_text_generation_reports_model_usage_and_output():
    tracer = RecordingTracer()
    agent_tracing._tracer = tracer
    provider = _provider()
    message = AIMessage(content="涨停 42 家", response_metadata={"token_usage": USAGE})

    result = provider._run(RunnableLambda(lambda _: message), "系统提示", "用户问题")

    generation = tracer.generations[0]
    assert generation.kwargs["name"] == "grounded_answer"
    assert generation.kwargs["model"] == "deepseek-v4-flash"
    assert generation.kwargs["input"] == {"system": "系统提示", "user": "用户问题"}
    assert generation.finished["output"] == "涨停 42 家"
    assert generation.finished["usage"] == {"input": 12, "output": 4, "total": 16}
    assert result.total_tokens == 16


def test_native_turn_reports_tool_calls_as_generation_output():
    tracer = RecordingTracer()
    agent_tracing._tracer = tracer
    message = AIMessage(
        content="",
        tool_calls=[{
            "name": "limit_up_events",
            "args": {"trade_date": "2026-10-23"},
            "id": "call_1",
            "type": "tool_call",
        }],
        response_metadata={"token_usage": USAGE},
    )
    provider = _provider(message)

    result = provider.generate_messages([HumanMessage(content="研究")], [])

    generation = tracer.generations[0]
    assert generation.kwargs["name"] == "react_decision"
    assert generation.kwargs["input"] == [{"role": "human", "content": "研究"}]
    assert generation.finished["output"]["tool_calls"][0]["name"] == "limit_up_events"
    assert generation.finished["usage"] == {"input": 12, "output": 4, "total": 16}
    assert result.tool_calls[0]["id"] == "call_1"


def test_tool_execution_reports_empty_status_from_cache():
    tracer = RecordingTracer()
    agent_tracing._tracer = tracer
    cached = {"status": "empty", "summary": "当日无事件", "rows": []}
    signature = dump(["limit_up_events", sorted({"trade_date": "2026-10-23"}.items())])
    runner = SimpleNamespace(
        gateway=_Gateway(), cache={signature: cached}, durable=False,
    )
    runner._execute = lambda call: AgentRunner._execute(runner, call)

    value = AgentRunner.execute(
        runner, {"name": "limit_up_events", "args": {"trade_date": "2026-10-23"}}
    )

    assert value is cached
    span = tracer.tools[0]
    assert span.kwargs["name"] == "limit_up_events"
    assert span.kwargs["arguments"] == {"trade_date": "2026-10-23"}
    assert span.finished["status"] == "empty"
    assert span.finished["output"] is cached


def test_tool_validation_failure_is_recorded_and_not_raised():
    tracer = RecordingTracer()
    agent_tracing._tracer = tracer
    runner = SimpleNamespace(
        gateway=_Gateway(error=ValueError("Tool not registered")), cache={}, durable=False,
    )
    runner._execute = lambda call: AgentRunner._execute(runner, call)

    value = AgentRunner.execute(runner, {"name": "unknown_tool", "args": {}})

    assert value["status"] == "error"
    assert value["error"] == "Tool not registered"
    assert tracer.tools[0].finished["status"] == "error"
