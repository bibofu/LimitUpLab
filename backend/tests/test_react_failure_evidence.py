"""Failed queries are auditable evidence, not empty market results."""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agents.react_runtime import runtime
from app.agents.react_runtime.compliance import ComplianceReview
from app.agents.react_runtime.contracts import Compute, Finish
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.lifecycle import CURRENT_CONTROL, Journal, RunControl
from app.agents.react_runtime.rendering import render_answer
from app.agents.react_runtime.tools import ToolGateway
from app.agents.tools import TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest
from app.repositories import SQLiteChatSessionRepository
from app.services.prompt_security import PromptInjectionAssessment
from evals.golden.contracts import Expectation
from evals.golden.grading import grade_turn


SECRET = "private-credential@internal.example.invalid"
ARGS = {"symbol": "600101"}


@pytest.fixture(autouse=True)
def offline_reviews(monkeypatch):
    monkeypatch.setattr(runtime, "review_input", lambda *a, **k:
        PromptInjectionAssessment(decision="allow", signals=[], reason="fixture",
                                  request_kind="research", context_mode="standalone"))
    monkeypatch.setattr(runtime, "review_answer", lambda *a, **k:
        ComplianceReview(decision="allow", violations=[], reason="fixture"))


def registry(**methods):
    return SimpleNamespace(events=[], profile="test", schemas=lambda: TOOL_SCHEMAS,
                           is_enabled=lambda _: True, **methods)


def request():
    return AgentChatRequest(session_id="s", message_id="m", message="查询样本甲的新闻")


def native(name, args, key):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": key}])


def observations(messages):
    return [json.loads(message.content) for message in messages if isinstance(message, ToolMessage)]


def partial(evidence_id):
    return {"status": "partial", "answer": "新闻查询失败，无法核实新闻，不能认定没有新闻。",
            "evidence_ids": [evidence_id], "missing": ["样本甲的新闻"]}


def execution(response):
    return next(trace.output for trace in response.tool_results if trace.name == "react_execution")


def failing_news(**kwargs):
    raise RuntimeError("upstream details: " + SECRET)


class FailureModel:
    def generate_messages(self, messages, tools, **kwargs):
        results = observations(messages)
        if not results:
            return native("stock_news", ARGS, "query")
        return native("finish", partial(results[0]["evidence_id"]), "finish")


def test_exception_evidence_allows_truthful_partial_without_repair_or_secret_leak():
    response = runtime.run(request(), registry(stock_news=failing_news), FailureModel())
    assert response.task_status == "partial" and response.stop_reason == "answered"
    run = execution(response)
    assert run["model_calls"] == 2 and run["tool_calls"] == 1
    assert len(run["evidence"]) == 1
    record = next(iter(run["evidence"].values()))
    assert record["result_state"] == "error" and record["rows"] == []
    assert record["evidence_scope"] == "current_run" and record["arguments"] == ARGS
    assert record["data_missing"] == ["tool_execution_failed"]
    assert SECRET not in json.dumps(response.model_dump(), ensure_ascii=False)
    assert len([trace for trace in response.tool_results if trace.name == "react_answer_check"]) == 1
    trace = next(trace for trace in response.tool_results if trace.name == "stock_news")
    assert trace.output == record["payload"]
    checks = grade_turn(Expectation(statuses=["partial"], require_evidence=False), response)
    original_payload = [check for check in checks if check.name == "original_tool_payload"]
    assert len(original_payload) == 1 and original_payload[0].passed is True
    store = EvidenceStore()
    store.records = run["evidence"]
    with pytest.raises(ValueError):
        store.compute(Compute(evidence_id=record["evidence_id"], operation="aggregate"))
    with pytest.raises(ValueError):
        render_answer(Finish(**{**partial(record["evidence_id"]), "answer": "{{evidence_table}}",
            "table": {"evidence_id": record["evidence_id"],
                      "columns": [{"field": "symbol", "label": "代码"}]}}), store)


@pytest.mark.parametrize("status", ["complete", "empty"])
def test_execution_failure_cannot_prove_complete_or_empty(status):
    class Model(FailureModel):
        attempts = 0

        def generate_messages(self, messages, tools, **kwargs):
            results = observations(messages)
            if not results:
                return native("stock_news", ARGS, "query")
            self.attempts += 1
            args = partial(results[0]["evidence_id"])
            if self.attempts == 1:
                args.update(status=status, missing=[], answer="没有新闻记录。")
            return native("finish", args, "finish-" + str(self.attempts))

    response = runtime.run(request(), registry(stock_news=failing_news), Model())
    assert response.task_status == "partial" and response.stop_reason == "answered"
    checks = [trace.output for trace in response.tool_results if trace.name == "react_answer_check"]
    assert [check["passed"] for check in checks] == [False, True]


@pytest.mark.parametrize("initial_state", ["exception", "error", "partial"])
def test_exact_failed_queries_are_reused_but_partial_queries_can_recover(initial_state):
    calls = []

    def news(**kwargs):
        calls.append(kwargs)
        if initial_state == "exception":
            return failing_news(**kwargs)
        state = initial_state if len(calls) == 1 else "ok"
        return ToolResult(name="stock_news", input=kwargs, summary="fixture", result_status=state,
                          output={"items": [], "source": "offline-fixture"})

    class Model:
        def generate_messages(self, messages, tools, **kwargs):
            results = observations(messages)
            if len(results) < 2:
                return native("stock_news", ARGS, "query-" + str(len(results)))
            if initial_state != "partial":
                assert results[1]["reused"] is True
                assert results[0]["evidence_id"] == results[1]["evidence_id"]
            else:
                assert results[0]["result_state"] == "partial"
                assert results[1]["result_state"] == "ok"
                assert results[0]["evidence_id"] != results[1]["evidence_id"]
            return native("finish", partial(results[-1]["evidence_id"]), "finish")

    response = runtime.run(request(), registry(stock_news=news), Model())
    expected_calls = 2 if initial_state == "partial" else 1
    assert len(calls) == expected_calls and execution(response)["tool_calls"] == expected_calls
    assert response.stop_reason == "answered"


@pytest.mark.parametrize("failure_count", [1, 2])
def test_failed_queries_do_not_prevent_an_independent_successful_query(failure_count):
    queried = []

    def news(**kwargs):
        queried.append(kwargs["symbol"])
        return failing_news(**kwargs)

    def kline(**kwargs):
        queried.append(kwargs["symbol"])
        return ToolResult(name="stock_kline", input=kwargs, summary="fixture", result_status="ok",
                          output={"symbol": "600103", "trend": "震荡", "source": "offline-fixture",
                                  "bars": [{"trade_date": "2026-09-22", "close": 10.0}]})

    class Model:
        def generate_messages(self, messages, tools, **kwargs):
            results = observations(messages)
            if len(results) < failure_count:
                return native("stock_news", {"symbol": "60010" + str(len(results) + 1)},
                              "news-" + str(len(results)))
            if len(results) == failure_count:
                assert all(result["result_state"] == "error" for result in results)
                assert "stock_kline" in {tool["function"]["name"] for tool in tools}
                return native("stock_kline", {"symbol": "600103"}, "kline")
            return native("finish", {**partial(results[-1]["evidence_id"]),
                "answer": "样本丙行情显示震荡；其他对象的新闻查询失败，新闻部分仍未完成。"}, "finish")

    independent_request = request().model_copy(update={
        "message": "分别查样本甲600101和样本乙600102的新闻，再查样本丙600103的行情，失败项明确说明。"})
    response = runtime.run(independent_request, registry(stock_news=news, stock_kline=kline), Model())
    assert response.task_status == "partial" and response.stop_reason == "answered"
    assert len(queried) == failure_count + 1 and queried[-1] == "600103"
    assert execution(response)["tool_calls"] == failure_count + 1
    assert {record["result_state"] for record in execution(response)["evidence"].values()} == {"ok", "error"}


@pytest.mark.parametrize("checkpoint_boundary", ["observe", "agent"])
def test_failure_journal_replay_and_checkpoint_restore_do_not_repeat_query(tmp_path, checkpoint_boundary):
    path = tmp_path / "failure.sqlite"
    SQLiteChatSessionRepository(path).ensure_session("s", owner_id="owner")
    journal = Journal(path)
    row, _ = journal.create(request(), "owner")
    calls = []

    def news(**kwargs):
        calls.append(kwargs)
        return failing_news(**kwargs)

    class Crash(BaseException):
        pass

    class CrashingControl(RunControl):
        def save(self, payload):
            if payload["runtime"]["evidence"]["records"] and payload["state"]["resume_node"] == checkpoint_boundary:
                raise Crash()
            super().save(payload)

    token = CURRENT_CONTROL.set(CrashingControl(journal, row))
    try:
        with pytest.raises(Crash):
            runtime.run(request(), registry(stock_news=news), FailureModel())
    finally:
        CURRENT_CONTROL.reset(token)
    restored = journal.get(row["run_id"], "owner")

    class ResumeModel(FailureModel):
        def generate_messages(self, messages, tools, **kwargs):
            results = observations(messages)
            if len(results) == 1:
                return native("stock_news", ARGS, "retry-after-resume")
            assert results[-1]["reused"] is True
            assert results[0]["evidence_id"] == results[-1]["evidence_id"]
            return native("finish", partial(results[-1]["evidence_id"]), "finish")

    token = CURRENT_CONTROL.set(RunControl(journal, restored))
    try:
        response = runtime.run(request(), registry(stock_news=news), ResumeModel())
    finally:
        CURRENT_CONTROL.reset(token)
    assert len(calls) == 1 and response.stop_reason == "answered"
    assert len(execution(response)["evidence"]) == 1


@pytest.mark.parametrize("failure", ["uncertain", "persistence", "cancelled"])
def test_unconfirmed_or_cancelled_execution_does_not_create_confirmed_failure_evidence(failure):
    calls = []

    def news(**kwargs):
        calls.append(kwargs)
        return ToolResult(name="stock_news", input=kwargs, output={"items": []}, summary="fixture")

    class Control:
        def cancelled(self):
            return failure == "cancelled"

        def begin_call(self, call):
            if failure == "uncertain":
                return {"execution_status": "failed", "result_state": "error",
                        "error_type": "UncertainExecution", "error": "Previous outcome unknown"}
            return None

        def finish_call(self, call_id, result):
            if failure == "persistence":
                raise OSError("journal write failed: " + SECRET)

    target = runtime.Run(request(), registry(stock_news=news), object(), [], None)
    target.control = Control()
    planned = target.policy({"pending": [{"name": "stock_news", "args": ARGS, "id": "query"}]})
    target.tools_node(planned)
    assert target.evidence.current_records() == [] and target.cache == {}
    assert len(calls) == (1 if failure == "persistence" else 0)
    assert SECRET not in json.dumps([trace.model_dump() for trace in target.traces])


@pytest.mark.parametrize("reported,expected", [("error", "error"), ("partial", "partial"), ("ok", "empty")])
def test_empty_event_payload_preserves_error_and_partial_states(reported, expected):
    def events(**kwargs):
        return ToolResult(name="limit_up_events", input=kwargs, output=[], summary="fixture",
                          result_status=reported)

    gateway = ToolGateway(registry(limit_up_events=events), EvidenceStore())
    _, payload, state = gateway.execute("limit_up_events", {})
    assert payload["events"] == [] and state == expected
