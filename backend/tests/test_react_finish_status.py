"""Offline finish-status regressions; scripted decisions are not model-quality tests."""

import json
from datetime import date
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.react_runtime import runtime as runtime_module
from app.agents.react_runtime.compliance import ComplianceReview
from app.agents.react_runtime.runtime import run
from app.agents.tools import TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest
from app.services.prompt_security import PromptInjectionAssessment


@pytest.fixture(autouse=True)
def offline_reviews(monkeypatch):
    monkeypatch.setattr(
        runtime_module, "review_input",
        lambda *args, **kwargs: PromptInjectionAssessment(
            decision="allow", signals=[], reason="offline fixture",
            request_kind="research", context_mode="standalone",
        ),
    )
    monkeypatch.setattr(
        runtime_module, "review_answer",
        lambda *args, **kwargs: ComplianceReview(
            decision="allow", violations=[], reason="offline fixture",
        ),
    )


def tool_call(name, args, key):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": key}])


def run_finishes(finishes, *, source_missing=False, asks_news=False):
    """Run a real graph with one isolated synthetic source and scripted finishes."""
    queries = []

    def stock_kline(symbol, days=20, end_date: date | None = None):
        queries.append((symbol, days, end_date))
        return ToolResult(
            name="stock_kline", input={"symbol": symbol}, summary="synthetic status fixture",
            output={
                "symbol": symbol, "name": "合成样本", "data_as_of": "2026-09-22",
                "return_10d_pct": 1.2, "source": "synthetic-finish-status-fixture",
                "data_missing": ["未提供换手率"] if source_missing else [],
            },
        )

    class Model:
        def __init__(self):
            self.calls = 0
            self.feedback = []

        def generate_messages(self, messages, tools, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return tool_call("stock_kline", {
                    "symbol": "600101", "days": 20, "end_date": "2026-09-22",
                }, "kline")
            if self.calls > 2:
                self.feedback.append(messages[-1])
            observed = next(json.loads(message.content) for message in messages
                            if isinstance(message, ToolMessage) and message.tool_call_id == "kline")
            # Repeated invalid decisions must stop at the gate before a third repair.
            index = min(self.calls - 2, len(finishes) - 1)
            status, missing = finishes[index]
            answer = "合成样本截至2026-09-22的10日收益为1.2%。"
            if asks_news:
                answer += "新闻尚未取得。"
            if source_missing:
                answer += "来源未提供换手率，不影响本次收益查询。"
            return tool_call("finish", {
                "status": status, "answer": answer,
                "evidence_ids": [observed["evidence_id"]], "missing": missing,
            }, f"finish-{self.calls}")

    registry = SimpleNamespace(
        events=[], profile="test", schemas=lambda: TOOL_SCHEMAS,
        is_enabled=lambda _: True, stock_kline=stock_kline,
    )
    model = Model()
    request = "查询合成样本截至2026-09-22的10日收益"
    if asks_news:
        request += "及新闻"
    response = run(AgentChatRequest(session_id="finish-status-fixture", message=request),
                   registry, model)
    checks = [trace.output for trace in response.tool_results if trace.name == "react_answer_check"]
    return response, model, queries, checks


@pytest.mark.parametrize("invalid,corrected,asks_news", [
    pytest.param(("complete", ["新闻"]), ("partial", ["新闻"]), True, id="complete-with-missing"),
    pytest.param(("complete", [" "]), ("complete", []), False, id="complete-with-blank"),
    pytest.param(("partial", []), ("complete", []), False, id="partial-with-empty-list"),
    pytest.param(("partial", [""]), ("partial", ["新闻"]), True, id="partial-with-empty-item"),
    pytest.param(("partial", [" \t\n"]), ("partial", ["新闻"]), True, id="partial-with-whitespace"),
    pytest.param(("partial", ["新闻", " "]), ("partial", ["新闻"]), True, id="partial-with-mixed-items"),
])
def test_inconsistent_finish_uses_one_gate_repair(invalid, corrected, asks_news):
    response, model, queries, checks = run_finishes([invalid, corrected], asks_news=asks_news)

    assert [check["passed"] for check in checks] == [False, True]
    assert model.calls == 3 and len(model.feedback) == 1
    assert isinstance(model.feedback[0], HumanMessage)
    assert "回答校验反馈：" in model.feedback[0].content
    assert "唯一一次修复机会" in model.feedback[0].content
    assert checks[-1]["missing"] == corrected[1]
    assert response.task_status == corrected[0] and response.stop_reason == "answered"
    assert response.tool_calls == ["stock_kline"]
    assert queries == [("600101", 20, date(2026, 9, 22))]
    # A schema/policy rejection alone would bypass the shared answer-repair budget.
    assert not any(trace.name == "react_policy" and trace.output.get("decision") == "reject"
                   for trace in response.tool_results)


def test_second_inconsistent_finish_stops_without_another_repair():
    response, model, queries, checks = run_finishes([
        ("complete", ["新闻"]), ("partial", [" \t"]),
    ], asks_news=True)

    assert [check["passed"] for check in checks] == [False, False]
    assert model.calls == 3 and len(model.feedback) == 1
    assert response.stop_reason == "validation_failed"
    assert response.task_status == "partial"
    assert len(queries) == 1


@pytest.mark.parametrize("status,missing,source_missing,asks_news", [
    pytest.param("complete", [], False, False, id="complete"),
    pytest.param("complete", [], True, False, id="complete-with-unrelated-source-gap"),
    pytest.param("partial", ["新闻"], False, True, id="partial-with-user-deliverable-gap"),
])
def test_consistent_finish_does_not_consume_repair(status, missing, source_missing, asks_news):
    response, model, queries, checks = run_finishes(
        [(status, missing)], source_missing=source_missing, asks_news=asks_news,
    )

    assert [check["passed"] for check in checks] == [True]
    assert checks[0]["missing"] == missing
    assert response.task_status == status and response.stop_reason == "answered"
    assert model.calls == 2 and model.feedback == [] and len(queries) == 1
    if source_missing:
        execution = next(trace.output for trace in response.tool_results if trace.name == "react_execution")
        record = next(iter(execution["evidence"].values()))
        assert record["result_state"] == "partial"
        assert record["data_missing"] == ["未提供换手率"]
