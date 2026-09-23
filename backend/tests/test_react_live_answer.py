"""Exercise incremental model output through the real ReAct answer boundary."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from app.agents.react_runtime import runtime
from app.agents.react_runtime.answer_delivery import AnswerDelivery
from app.agents.react_runtime.compliance import ComplianceReview
from app.agents.react_runtime.lifecycle import CURRENT_CONTROL
from app.agents.tools import TOOL_SCHEMAS
from app.models import AgentChatRequest
from app.services.prompt_security import PromptInjectionAssessment
from test_langchain_provider import provider_for


@pytest.fixture(autouse=True)
def allow_input(monkeypatch):
    monkeypatch.setattr(runtime, "review_input", lambda *a, **kw: PromptInjectionAssessment(
        decision="allow", signals=[], reason="fixture", request_kind="conversation"))
    monkeypatch.setattr(runtime, "review_answer", lambda *a, **kw: ComplianceReview(
        decision="allow", violations=[], reason="fixture"))


def execute(provider, events):
    registry = SimpleNamespace(events=[], schemas=lambda: TOOL_SCHEMAS, is_enabled=lambda _: True)
    return runtime.run(AgentChatRequest(session_id="stream", message="你好"), registry, provider,
                       answer_event=lambda name, payload: events.append((name, payload)))


def visible(events):
    return "".join(data["delta"] for name, data in events if name == "answer_delta")


def native_frame(delta, finish_reason=None):
    return ("data: " + json.dumps({
        "id": "live", "object": "chat.completion.chunk", "created": 1, "model": "test-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }, ensure_ascii=False) + "\n\n").encode()


def test_native_finish_text_arrives_before_model_eof_and_compliance(monkeypatch):
    events, states = [], []
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield native_frame({"content": "private model content must not be shown", "tool_calls": [{
                "index": 0, "id": "finish-1", "type": "function",
                "function": {"name": "finish", "arguments": '{"status":"complete","answer":"第一段📊'},
            }]})
            # The SDK cannot read the tail until the visible answer has arrived.
            assert visible(events) == "第一段📊"
            states.append("first_text_before_eof")
            yield native_frame({"tool_calls": [{"index": 0, "function": {"arguments": '，后续内容。"}'}}]}, "tool_calls")
            yield b"data: [DONE]\n\n"
        def close(self):
            states.append("closed")
    def review(*a, **kw):
        assert visible(events) == "第一段📊，后续内容。"
        states.append("review")
        return ComplianceReview(decision="allow", violations=[], reason="fixture")
    monkeypatch.setattr(runtime, "review_answer", review)
    def handle(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Stream())
    with provider_for(handle) as provider:
        result = execute(provider, events)
    assert states.index("first_text_before_eof") < states.index("review")
    assert "closed" in states
    assert result.answer == visible(events) and result.task_status == "complete"
    assert events[0][1]["provisional"] is True
    assert "private" not in visible(events)


class Model:
    def __init__(self):
        self.calls = 0
    def generate_messages(self, messages, tools, *, on_chunk, **kwargs):
        self.calls += 1
        answer = f"第{self.calls}版回答"
        args = {"status": "complete", "answer": answer}
        on_chunk(AIMessageChunk(content="", tool_call_chunks=[{
            "index": 0, "name": "finish", "id": str(self.calls),
            "args": json.dumps(args, ensure_ascii=False),
        }]))
        return AIMessage(content="", tool_calls=[{"name": "finish", "id": str(self.calls), "args": args}])


def test_rejected_draft_is_retracted_before_repaired_revision(monkeypatch):
    events = []
    def review(*a, **kw):
        reject = kw["answer"] == "第1版回答"
        return ComplianceReview(decision="reject" if reject else "allow",
            violations=["deterministic_prediction"] if reject else [], reason="fixture")
    monkeypatch.setattr(runtime, "review_answer", review)
    model = Model()
    result = execute(model, events)
    assert model.calls == 2 and result.answer == "第2版回答"
    assert [name for name, _ in events] == [
        "answer_start", "answer_delta", "answer_reset", "answer_start", "answer_delta"]
    assert [data["revision"] for name, data in events if name == "answer_start"] == [1, 2]


def test_cancellation_during_model_stream_closes_and_retracts_draft():
    events, closed = [], []
    control = SimpleNamespace(row={"started_at": datetime.now(timezone.utc).isoformat()},
                              save=lambda _: None, cancelled=lambda: bool(events))
    class CancelModel(Model):
        def generate_messages(self, *a, on_chunk, **kw):
            try:
                on_chunk(AIMessageChunk(content="", tool_call_chunks=[{
                    "index": 0, "name": "finish", "id": "f", "args": '{"answer":"生成中',
                }]))
                on_chunk(AIMessageChunk(content="", tool_call_chunks=[]))
                pytest.fail("Cancellation must stop generation before another chunk")
            finally:
                closed.append(True)
    token = CURRENT_CONTROL.set(control)
    try:
        result = execute(CancelModel(), events)
    finally:
        CURRENT_CONTROL.reset(token)
    assert result.task_status == "cancelled" and closed
    assert events[-1][0] == "answer_reset"


def test_table_placeholder_never_streams_and_rendered_evidence_fills_in_place():
    events = []
    delivery = AnswerDelivery(lambda name, payload: events.append((name, payload)))
    for text in ["已取得资料。\n{", "{evidence_", "table}}", "\n以上为资料。"]:
        delivery.feed(text)
    assert visible(events) == "已取得资料。\n"
    answer = "已取得资料。\n|代码|\n|---|\n|000001|\n以上为资料。"
    delivery.render(answer)
    assert visible(events) == answer
    assert len([name for name, _ in events if name == "answer_start"]) == 1
