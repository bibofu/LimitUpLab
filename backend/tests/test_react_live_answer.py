"""Candidate prose stays internal until the final validated response is returned."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from app.agents.react_runtime import runtime
from app.agents.react_runtime.compliance import ComplianceReview
from app.agents.react_runtime.lifecycle import CURRENT_CONTROL
from app.agents.tools import TOOL_SCHEMAS, ToolResult
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


def native_frame(delta, finish_reason=None):
    return ("data: " + json.dumps({
        "id": "live", "object": "chat.completion.chunk", "created": 1, "model": "test-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }, ensure_ascii=False) + "\n\n").encode()


def test_native_finish_chunks_remain_private_through_eof_and_compliance(monkeypatch):
    events, states = [], []
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield native_frame({"content": "private model content must not be shown", "tool_calls": [{
                "index": 0, "id": "finish-1", "type": "function",
                "function": {"name": "finish", "arguments": '{"status":"complete","answer":"第一段📊'},
            }]})
            assert events == []
            states.append("private_chunk_before_eof")
            yield native_frame({"tool_calls": [{"index": 0, "function": {"arguments": '，后续内容。"}'}}]}, "tool_calls")
            yield b"data: [DONE]\n\n"
        def close(self):
            states.append("closed")
    def review(*a, **kw):
        assert events == []
        assert kw["answer"] == "第一段📊，后续内容。"
        states.append("review")
        return ComplianceReview(decision="allow", violations=[], reason="fixture")
    monkeypatch.setattr(runtime, "review_answer", review)
    def handle(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Stream())
    with provider_for(handle) as provider:
        result = execute(provider, events)
    assert states.index("private_chunk_before_eof") < states.index("review")
    assert "closed" in states
    assert result.answer == "第一段📊，后续内容。" and result.task_status == "complete"
    assert events == [], "Only the HTTP persistence boundary may publish the final response"


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


def test_compliance_rejection_and_repair_never_publish_candidate_revisions(monkeypatch):
    events = []
    def review(*a, **kw):
        assert events == []
        reject = kw["answer"] == "第1版回答"
        return ComplianceReview(decision="reject" if reject else "allow",
            violations=["deterministic_prediction"] if reject else [], reason="fixture")
    monkeypatch.setattr(runtime, "review_answer", review)
    model = Model()
    result = execute(model, events)
    assert model.calls == 2 and result.answer == "第2版回答"
    assert events == []


def test_cancellation_during_model_stream_closes_without_publishing_candidates():
    events, closed, cancelled = [], [], []
    control = SimpleNamespace(row={"started_at": datetime.now(timezone.utc).isoformat()},
                              save=lambda _: None, cancelled=lambda: bool(cancelled))
    class CancelModel(Model):
        def generate_messages(self, *a, on_chunk, **kw):
            try:
                on_chunk(AIMessageChunk(content="", tool_call_chunks=[{
                    "index": 0, "name": "finish", "id": "f", "args": '{"answer":"生成中',
                }]))
                assert events == []
                cancelled.append(True)
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
    assert events == []


@pytest.mark.parametrize("with_callback", [True, False])
def test_native_chunks_enforce_deadline_even_without_answer_callback(monkeypatch, with_callback):
    events, closed = [], []
    clock = [100.0]
    monkeypatch.setattr(runtime, "perf_counter", lambda: clock[0])
    class ExpiringModel(Model):
        def generate_messages(self, *a, on_chunk, **kw):
            try:
                on_chunk(AIMessageChunk(content="", tool_call_chunks=[]))
                clock[0] += 1000
                on_chunk(AIMessageChunk(content="", tool_call_chunks=[]))
                pytest.fail("The native stream must stop when the run deadline is exceeded")
            finally:
                closed.append(True)
    registry = SimpleNamespace(events=[], schemas=lambda: TOOL_SCHEMAS, is_enabled=lambda _: True)
    result = runtime.run(AgentChatRequest(session_id="deadline", message="你好"), registry,
                         ExpiringModel(), answer_event=(lambda name, data: events.append((name, data)))
                         if with_callback else None)
    assert closed and result.stop_reason == "budget_exhausted"
    assert events == []


@pytest.mark.parametrize("with_callback", [True, False])
def test_raw_fields_trigger_one_rewrite_without_publishing_invalid_text(with_callback):
    class FieldModel(Model):
        def generate_messages(self, messages, tools, *, on_chunk=None, **kwargs):
            self.calls += 1
            answer = ("指标包括break_count与next_open_pct。" if self.calls == 1
                      else "可以查看盘中炸板次数与次日开盘涨幅。")
            if self.calls == 2:
                assert "natural Chinese" in messages[-1].content
            args = {"status": "complete", "answer": answer}
            if on_chunk:
                for part in ['{"answer":"指标包括break_', 'count与next_open_pct。"}'] if self.calls == 1 else [json.dumps(args, ensure_ascii=False)]:
                    on_chunk(AIMessageChunk(content="", tool_call_chunks=[{
                        "index": 0, "name": "finish" if 'answer' in part else None,
                        "id": str(self.calls), "args": part}]))
            return AIMessage(content="", tool_calls=[{"name": "finish", "id": str(self.calls), "args": args}])
    events, provider = [], FieldModel()
    if with_callback:
        response = execute(provider, events)
    else:
        registry = SimpleNamespace(events=[], schemas=lambda: TOOL_SCHEMAS, is_enabled=lambda _: True)
        response = runtime.run(AgentChatRequest(session_id="plain", message="你好"), registry, provider)
    assert response.task_status == "complete" and provider.calls == 2
    assert response.answer == "可以查看盘中炸板次数与次日开盘涨幅。"
    assert events == []


@pytest.mark.parametrize("repair", [True, False])
def test_malformed_hot_ranking_table_stays_private_and_gets_only_one_repair(monkeypatch, repair):
    events, tool_calls, reviewed = [], [], []
    raw_rows = [{"symbol": "600101", "name": "样例科技", "rank_trend": "flat"}]

    def review(*a, **kw):
        assert events == [], "Even the fully rendered table is private during compliance review"
        assert "| 样例科技 | 持平 |" in kw["answer"]
        reviewed.append(kw["answer"])
        return ComplianceReview(decision="allow", violations=[], reason="fixture")

    monkeypatch.setattr(runtime, "review_answer", review)

    def ranking(**arguments):
        tool_calls.append(arguments)
        return ToolResult(name="hot_stock_ranking", input=arguments, summary="合成热股榜",
                          output={"items": raw_rows, "source": "synthetic test fixture"})

    class RankingModel:
        calls = 0

        def generate_messages(self, messages, tools, *, on_chunk=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(content="", tool_calls=[{
                    "name": "hot_stock_ranking", "args": {"limit": 1}, "id": "ranking",
                }])
            key = next(json.loads(m.content)["evidence_id"] for m in messages
                       if isinstance(m, ToolMessage) and m.tool_call_id == "ranking")
            if self.calls == 3:
                assert "Table placeholder" in messages[-1].content
            separator = "\n\n" if self.calls == 3 and repair else r"\n\n"
            args = {"status": "complete", "answer": separator.join([
                "以下为合成榜单。", "{{evidence_table}}", "说明：仅反映关注度。",
            ]), "evidence_ids": [key], "table": {"evidence_id": key, "columns": [
                {"field": "name", "label": "股票名称"}, {"field": "rank_trend", "label": "排名趋势"},
            ]}}
            if on_chunk:
                on_chunk(AIMessageChunk(content="", tool_call_chunks=[{
                    "index": 0, "name": "finish", "id": str(self.calls),
                    "args": json.dumps(args, ensure_ascii=False),
                }]))
            return AIMessage(content="", tool_calls=[{"name": "finish", "id": str(self.calls), "args": args}])

    registry = SimpleNamespace(events=[], schemas=lambda: TOOL_SCHEMAS, is_enabled=lambda _: True,
                               hot_stock_ranking=ranking)
    model = RankingModel()
    result = runtime.run(AgentChatRequest(session_id="ranking-format", message="当前的热门股票有哪些"),
                         registry, model, answer_event=lambda name, data: events.append((name, data)))
    assert model.calls == 3 and len(tool_calls) == 1
    assert raw_rows[0]["rank_trend"] == "flat"
    assert events == []
    if repair:
        assert result.task_status == "complete"
        assert reviewed == [result.answer]
        assert "\n\n| 股票名称 | 排名趋势 |\n" in result.answer
        assert "| 样例科技 | 持平 |\n\n说明：仅反映关注度。" in result.answer
        assert r"\n" not in result.answer
    else:
        assert result.task_status == "partial" and result.stop_reason == "validation_failed"
        assert reviewed == []
        assert "| 股票名称" not in result.answer
