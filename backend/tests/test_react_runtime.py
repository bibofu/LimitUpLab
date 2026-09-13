"""Behavioral graph tests: actual tool messages drive the next model decision."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.react_runtime.contracts import Compute
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.runtime import run
from app.agents.tools import TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest, ChatSessionMessage


def call(name, args, key):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": key}])


def registry(**methods):
    return SimpleNamespace(events=[], profile="test", schemas=lambda: TOOL_SCHEMAS,
                           is_enabled=lambda _: True, **methods)


def test_react_observes_error_and_selects_next_tool():
    def news(**args):
        raise ValueError("source unavailable")
    def kline(symbol, days=20, end_date: date | None = None):
        assert days == 12 and end_date == date(2026, 5, 15)
        return ToolResult(name="stock_kline", input={"symbol": symbol},
                          output={"symbol": symbol, "trend": "震荡"}, summary="行情证据")
    class Model:
        calls = 0
        def generate_messages(self, messages, tools, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return call("stock_news", {"symbol": "000001"}, "n")
            latest = [m for m in messages if isinstance(m, ToolMessage)][-1]
            if self.calls == 2:
                assert latest.tool_call_id == "n" and "execution failed" in latest.content
                return call("stock_kline", {"symbol": "000001", "days": 12, "end_date": "2026-05-15"}, "k")
            import json
            evidence_id = json.loads(latest.content)["evidence_id"]
            return call("finish", {"status": "partial", "answer": "新闻查询失败；行情证据显示震荡。",
                                   "evidence_ids": [evidence_id], "missing": ["新闻"]}, "f")
    response = run(AgentChatRequest(session_id="r", message="新闻失败时继续查12日K线"),
                   registry(stock_news=news, stock_kline=kline), Model())
    assert response.task_status == "partial"
    assert response.tool_calls == ["stock_news", "stock_kline"]
    assert len([t for t in response.tool_results if t.name == "react_observe"]) == 3


def test_policy_rejection_is_paired_and_does_not_execute():
    class Model:
        calls = 0
        def generate_messages(self, messages, tools, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return call("stock_kline", {"symbol": "000001", "days": -1}, "bad")
            assert any(isinstance(m, ToolMessage) and m.tool_call_id == "bad" and "rejected" in m.content for m in messages)
            return call("finish", {"status": "clarify", "answer": "请明确查询窗口。"}, "done")
    response = run(AgentChatRequest(session_id="r", message="查询"), registry(), Model())
    assert response.task_status == "clarify"
    assert response.tool_calls == []


def test_rank_slice_and_difference_preserve_entities():
    store = EvidenceStore()
    a = store.add(tool="test", state="ok", arguments={}, payload={"items": [
        {"symbol": str(i), "score": 100 - i} for i in range(1, 8)
    ]})
    b = store.compute(Compute(evidence_id=a, sort_by="score", offset=3, limit=3))
    assert [r["symbol"] for r in store.get(b)["rows"]] == ["4", "5", "6"]
    c = store.compute(Compute(evidence_id=a, operation="difference", other_id=b))
    assert [r["symbol"] for r in store.get(c)["rows"]] == ["1", "2", "3", "7"]


def test_public_entry_defaults_to_react(monkeypatch):
    from app.agents.chat import answer_first_board_chat
    monkeypatch.delenv("LIMITUPLAB_AGENT_RUNTIME", raising=False)
    class Model:
        def generate_messages(self, *args, **kwargs):
            return call("finish", {"status": "refuse", "answer": "我可以提供研究事实，不能提供交易指令。"}, "f")
    response = answer_first_board_chat(AgentChatRequest(session_id="r", message="给我买卖指令"), [],
                                      llm_provider=Model(), tool_registry=registry())
    assert response.generated_by == "react-runtime-v2"
    assert response.task_status == "refuse"


def test_evidence_page_is_not_silently_retruncated():
    store = EvidenceStore()
    key = store.add(tool="test", state="ok", arguments={},
                    payload={"items": [{"symbol": str(i)} for i in range(35)], "source": "fixture"})
    view = store.view(key, 0, 30)
    assert len(view["rows"]) == 30 and view["truncated"]
    assert view["sources"] == ["fixture"]
    assert len(store.view(key, 30, 30)["rows"]) == 5


def test_kline_schema_matches_single_entity_invocation():
    import pytest
    from app.agents.react_runtime.tools import ToolGateway
    gateway = ToolGateway(registry(), EvidenceStore())
    definition = next(d for d in gateway.definitions() if d["function"]["name"] == "stock_kline")
    assert definition["function"]["parameters"]["properties"]["symbol"]["type"] == "string"
    with pytest.raises(ValueError, match="one stock"):
        gateway.validate({"name": "stock_kline", "args": {"symbol": ["000001", "000002"]}})


def test_plain_model_text_must_be_repaired_through_finish():
    class Model:
        calls = 0

        def generate_messages(self, messages, tools, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(content="贵州茅台今天涨停，成交额100亿元。")
            assert any(
                isinstance(message, HumanMessage)
                and "typed finish tool" in str(message.content)
                for message in messages
            )
            return call("finish", {
                "status": "partial",
                "answer": "没有取得行情证据，无法核验该结论。",
                "missing": ["今日行情"],
            }, "finish")

    response = run(
        AgentChatRequest(session_id="r", message="查询贵州茅台今天行情"),
        registry(),
        Model(),
    )

    assert response.task_status == "partial"
    assert response.stop_reason == "answered"
    checks = [trace.output for trace in response.tool_results if trace.name == "react_answer_check"]
    assert checks[0]["passed"] is False
    assert checks[-1]["passed"] is True


def test_complete_research_answer_requires_evidence_but_greeting_does_not():
    class ResearchModel:
        calls = 0

        def generate_messages(self, messages, tools, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return call("finish", {
                    "status": "complete",
                    "answer": "贵州茅台今天涨停。",
                }, "unsupported")
            return call("finish", {
                "status": "partial",
                "answer": "没有取得今日行情证据。",
                "missing": ["今日行情"],
            }, "repaired")

    research = run(
        AgentChatRequest(session_id="r", message="查询贵州茅台今天行情"),
        registry(),
        ResearchModel(),
    )
    greeting = run(
        AgentChatRequest(session_id="r", message="你好"),
        registry(),
        type("GreetingModel", (), {
            "generate_messages": lambda self, *args, **kwargs: call(
                "finish", {"status": "complete", "answer": "你好，我可以协助做收盘研究。"}, "hello"
            )
        })(),
    )

    assert research.task_status == "partial"
    assert greeting.task_status == "complete"


def test_satisfied_requirement_must_bind_and_retain_evidence():
    class Model:
        calls = 0

        def generate_messages(self, messages, tools, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return call("update_task", {"requirements": [{
                    "id": "market",
                    "description": "查询今日行情",
                    "source_text": "查询今日行情",
                    "status": "satisfied",
                    "evidence_ids": [],
                }]}, "task")
            assert any(
                isinstance(message, ToolMessage)
                and "must cite evidence" in str(message.content)
                for message in messages
            )
            return call("finish", {
                "status": "partial",
                "answer": "行情证据尚未取得。",
                "missing": ["今日行情"],
            }, "finish")

    response = run(
        AgentChatRequest(session_id="r", message="查询今日行情"),
        registry(),
        Model(),
    )

    assert response.task_status == "partial"
    assert response.tool_calls == []


def test_grounding_rejects_wrong_metric_then_accepts_supported_repair():
    class Model:
        calls = 0
        evidence_id = ""

        def generate_messages(self, messages, tools, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return call("stock_kline", {
                    "symbol": "600519", "days": 10, "end_date": "2026-05-15"
                }, "kline")
            if self.calls == 2:
                import json
                self.evidence_id = json.loads(
                    [message for message in messages if isinstance(message, ToolMessage)][-1].content
                )["evidence_id"]
                return call("finish", {
                    "status": "complete",
                    "answer": "贵州茅台(600519)在2026-05-15的收益为9.9%。",
                    "evidence_ids": [self.evidence_id],
                }, "wrong")
            return call("finish", {
                "status": "complete",
                "answer": "贵州茅台(600519)在2026-05-15的收益为1.2%。",
                "evidence_ids": [self.evidence_id],
            }, "correct")

    def kline(symbol, days=20, end_date: date | None = None):
        return ToolResult(
            name="stock_kline",
            input={"symbol": symbol, "days": days, "end_date": end_date},
            output={
                "symbol": symbol,
                "name": "贵州茅台",
                "end_date": end_date.isoformat(),
                "return_10d_pct": 1.2,
            },
            summary="行情证据",
        )

    response = run(
        AgentChatRequest(session_id="r", message="查询贵州茅台十日收益"),
        registry(stock_kline=kline),
        Model(),
    )

    assert response.task_status == "complete"
    checks = [trace.output for trace in response.tool_results if trace.name == "react_answer_check"]
    assert "9.9%" in checks[-2]["reason"]
    assert checks[-1]["grounding"]["unsupported_claim_count"] == 0


def test_historical_evidence_alone_cannot_complete_new_research():
    old_evidence = {
        "evidence_id": "ev_old",
        "tool": "stock_kline",
        "payload": {
            "symbol": "600519",
            "name": "贵州茅台",
            "end_date": "2026-05-15",
            "return_10d_pct": 1.2,
        },
        "result_state": "ok",
        "schema_version": "react-evidence-v2",
        "retrieved_at": "2026-05-15T00:00:00Z",
        "source_truncated": False,
        "data_missing": [],
        "historical_reference": False,
        "arguments": {"symbol": "600519", "end_date": "2026-05-15"},
        "sources": ["fixture"],
        "rows": [{"symbol": "600519", "name": "贵州茅台", "return_10d_pct": 1.2}],
    }
    history = [ChatSessionMessage(
        message_id="old",
        session_id="r",
        role="assistant",
        content="上一轮行情",
        metadata={
            "tool_results": [{
                "name": "react_execution",
                "output": {"evidence": {"ev_old": old_evidence}},
            }]
        },
        created_at=datetime.now(timezone.utc),
    )]

    class Model:
        calls = 0

        def generate_messages(self, messages, tools, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return call("finish", {
                    "status": "complete",
                    "answer": "贵州茅台(600519)今天上涨1.2%。",
                    "evidence_ids": ["ev_old"],
                }, "stale")
            return call("finish", {
                "status": "partial",
                "answer": "只有历史参考，尚未取得今天的行情证据。",
                "missing": ["今日行情"],
            }, "repaired")

    response = run(
        AgentChatRequest(session_id="r", message="贵州茅台今天表现如何"),
        registry(),
        Model(),
        history,
    )

    assert response.task_status == "partial"
    checks = [trace.output for trace in response.tool_results if trace.name == "react_answer_check"]
    assert "Historical references alone" in checks[-2]["reason"]
