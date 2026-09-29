"""Offline regressions for truthful, sanitized stop summaries; no live model calls."""

from copy import deepcopy
from datetime import date
import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage
import pytest

from app.agents.react_runtime import runtime as runtime_module
from app.agents.react_runtime.compliance import ComplianceReview
from app.agents.react_runtime.runtime import Run, run
from app.agents.tools import TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest, AgentToolTrace
from app.services.prompt_security import PromptInjectionAssessment


SECRET = "private-credential@internal.example.invalid"
ARGS = {"symbol": "600101", "end_date": "2026-09-22"}


@pytest.fixture(autouse=True)
def offline_reviews(monkeypatch):
    monkeypatch.setattr(runtime_module, "review_input", lambda *a, **k:
        PromptInjectionAssessment(decision="allow", signals=[], reason="offline fixture",
                                  request_kind="research", context_mode="standalone"))
    monkeypatch.setattr(runtime_module, "review_answer", lambda *a, **k:
        ComplianceReview(decision="allow", violations=[], reason="offline fixture"))


def registry(**methods):
    return SimpleNamespace(events=[], profile="test", schemas=lambda: TOOL_SCHEMAS,
                           is_enabled=lambda _: True, **methods)


def request():
    return AgentChatRequest(session_id="stop-failure-fixture", message="查询指定日期的个股数据")


def new_run(**methods):
    return Run(request(), registry(**methods), object(), [], None)


def failed_trace(*, name="stock_kline", status="error", execution="failed", error_type="RuntimeError"):
    return AgentToolTrace(name=name, status=status, input=ARGS, summary="untrusted " + SECRET,
                          output={"execution_status": execution, "result_state": "error",
                                  "error_type": error_type, "error": "untrusted " + SECRET})


def assert_query_failure_disclosed(answer):
    # Small semantic obligations, not a full wording snapshot or a model grader.
    assert "查询" in answer and "失败" in answer
    assert "不能" in answer or "不应" in answer
    assert "空" in answer or "零" in answer or "0" in answer
    assert SECRET not in answer and "stock_kline" not in answer


@pytest.mark.parametrize("failure", ["exception", "stale-date"])
def test_graph_stop_preserves_business_failure_after_both_finish_attempts_fail(failure):
    executions = []

    def stock_kline(symbol, days=20, end_date: date | None = None):
        executions.append((symbol, end_date))
        if failure == "exception":
            raise RuntimeError("upstream failed with " + SECRET)
        return ToolResult(name="stock_kline", input=ARGS, summary="stale fixture " + SECRET,
                          output={"symbol": symbol, "data_as_of": "2026-09-21",
                                  "return_10d_pct": 1.2, "source": "offline-stop-fixture"})

    class Model:
        calls = 0

        def generate_messages(self, messages, tools, **kwargs):
            self.calls += 1
            if self.calls == 1:
                name, args = "stock_kline", ARGS
            else:
                name, args = "finish", {"status": "partial", "answer": "查询失败，尚无可用结果。",
                                        "evidence_ids": [], "missing": ["本次查询结果"]}
            return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": str(self.calls)}])

    model = Model()
    response = run(request(), registry(stock_kline=stock_kline), model)
    assert model.calls == 3 and len(executions) == 1
    assert response.stop_reason == "validation_failed" and response.task_status == "error"
    failures = [t for t in response.tool_results if t.name == "stock_kline"]
    assert len(failures) == 1 and failures[0].output["execution_status"] == "failed"
    assert SECRET not in json.dumps(failures[0].model_dump())
    execution = next(t.output for t in response.tool_results if t.name == "react_execution")
    assert execution["evidence"] == {}
    assert_query_failure_disclosed(response.answer)


@pytest.mark.parametrize("trace", [
    failed_trace(name="react_policy"),
    failed_trace(name="react_provider_error"),
    failed_trace(name="finish"),
    failed_trace(name="compute_result"),
    failed_trace(name="not_a_registered_business_tool"),
    failed_trace(execution="rejected"),
    failed_trace(execution="cancelled"),
    failed_trace(status="success"),
    failed_trace(error_type="UncertainExecution"),
], ids=["policy", "provider", "finish", "compute", "unknown-tool", "rejected", "cancelled",
        "payload-cannot-forge-runtime-failure", "uncertain-outcome"])
def test_non_business_failure_or_unknown_outcome_does_not_claim_query_failure(trace):
    runtime = new_run()
    runtime.traces.append(trace)
    runtime.stop("validation_failed")
    assert runtime.status == "error"
    assert not ("查询" in runtime.answer and "失败" in runtime.answer)
    assert SECRET not in runtime.answer


@pytest.mark.parametrize("initial_reason", ["cancelled", "validation_failed"])
def test_cancellation_wins_even_when_it_arrives_after_caller_selected_stop_reason(initial_reason):
    runtime = new_run()
    runtime.traces.append(failed_trace())
    runtime.control = SimpleNamespace(cancelled=lambda: True)
    runtime.stop(initial_reason)
    assert runtime.reason == "cancelled" and runtime.status == "cancelled"
    assert "取消" in runtime.answer
    assert not ("查询" in runtime.answer and "失败" in runtime.answer)
    assert SECRET not in runtime.answer


def restore_checkpoint(runtime, **methods):
    class Checkpoint:
        row = {}

        def save(self, payload):
            self.row = {"checkpoint_json": json.dumps(payload, ensure_ascii=False)}

        def cancelled(self):
            return False

    checkpoint = Checkpoint()
    runtime.control = checkpoint
    runtime.save({"messages": [], "done": False}, "agent")
    restored = new_run(**methods)
    restored.control = checkpoint
    assert restored.restore()["resume_node"] == "agent"
    restored.control = None
    return restored


def test_restored_failure_and_historical_evidence_do_not_hide_current_query_failure():
    runtime = new_run()
    runtime.traces.append(failed_trace())
    runtime.evidence.restore_history("old", {
        "tool": "stock_kline", "result_state": "ok", "rows": [{"name": "旧轮对象"}],
    })
    restored = restore_checkpoint(runtime)
    assert len(restored.traces) == 1 and restored.evidence.current_records() == []
    restored.stop("validation_failed")
    assert restored.status == "error" and "旧轮对象" not in restored.answer
    assert_query_failure_disclosed(restored.answer)


@pytest.mark.parametrize("result_state", ["ok", "empty", "partial"])
def test_restored_failure_then_valid_result_preserves_existing_fallback_and_evidence(result_state):
    rows = [] if result_state == "empty" else [{"symbol": "600101", "name": "新返回合成对象"}]
    payload = {"items": rows, "data_as_of": "2026-09-22", "source": "offline-stop-fixture",
               "data_missing": ["部分来源未返回"] if result_state == "partial" else []}
    executed = []

    def stock_kline(symbol, days=20, end_date: date | None = None):
        executed.append((symbol, end_date))
        return ToolResult(name="stock_kline", input=ARGS, output=deepcopy(payload),
                          summary="offline result", result_status=result_state)

    runtime = new_run()
    runtime.traces.append(failed_trace())
    restored = restore_checkpoint(runtime, stock_kline=stock_kline)
    state = {"pending": [{"name": "stock_kline", "args": ARGS, "id": "retry"}]}
    planned = restored.policy(state)
    restored.tools_node(planned)
    assert len(executed) == 1
    records_before = deepcopy(restored.evidence.records)
    current = restored.evidence.current_records()
    assert len(current) == 1 and current[0]["result_state"] == result_state
    assert current[0]["rows"] == rows and current[0]["data_missing"] == payload["data_missing"]
    # Compare to the same current evidence without a historical execution failure.
    # A recovered failure must not create a new claim that the result is still missing.
    neutral = new_run()
    neutral.evidence.records = deepcopy(records_before)
    neutral.stop("validation_failed")
    restored.stop("validation_failed")
    assert (restored.status, restored.answer) == (neutral.status, neutral.answer)
    assert restored.evidence.records == records_before
    assert not ("查询" in restored.answer and "失败" in restored.answer)
    assert SECRET not in restored.answer
