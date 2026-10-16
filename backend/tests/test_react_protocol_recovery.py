"""Protocol repair keeps evidence; exhausted repair has a separately audited fallback."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.react_runtime import runtime
from app.agents.react_runtime.compliance import ComplianceReview
from app.agents.react_runtime.task_contract import TaskInterpretation
from app.agents.tools import TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest
from app.services.llm_provider import NativeFunctionCallingError
from app.services.prompt_security import PromptInjectionAssessment


SECRET = "private-credential@internal.example.invalid"
CONTRACT = {"mode": "freeform", "table_required": True, "fields": ["symbol", "name"]}
ROWS = [{"symbol": "600101", "name": "合成甲"}, {"symbol": "000202", "name": "合成乙"}]


@pytest.fixture(autouse=True)
def offline_reviews(monkeypatch):
    monkeypatch.setattr(runtime, "review_input", lambda *a, **k: PromptInjectionAssessment(
        decision="allow", signals=[], reason="fixture", request_kind="research",
        context_mode="standalone", output_contract=CONTRACT))
    monkeypatch.setattr(runtime, "review_answer", lambda *a, **k:
        ComplianceReview(decision="allow", violations=[], reason="fixture"))


def registry(**methods):
    return SimpleNamespace(events=[], profile="test", schemas=lambda: TOOL_SCHEMAS,
                           is_enabled=lambda _: True, **methods)


def request():
    return AgentChatRequest(session_id="protocol-recovery", message="列出代码名称，来源截断就说明缺口。")


def payload(**updates):
    return {"events": deepcopy(ROWS), "source": "synthetic-recovery", "trade_date": "2026-09-22",
            "source_truncated": True, "matched_count": 8, "returned_count": 2, **updates}


def native(name, args, key):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": key}])


def bad_protocol():
    return NativeFunctionCallingError(SECRET, diagnostics={
        "failure_category": "tool_arguments", "finish_reason": "tool_calls",
        "arguments": [{"shape": "string", "size": 500, "json_failure": "syntax", "value": SECRET}],
        "raw": SECRET})


@pytest.mark.parametrize("recover", [True, False])
def test_protocol_repair_reuses_evidence_and_exhaustion_preserves_audited_partial(recover):
    queried = []
    events = []

    def query(**args):
        queried.append(args)
        return ToolResult(name="limit_up_events", input=args, output=payload(),
                          result_status="partial", summary="synthetic")

    class Model:
        calls = 0

        def generate_messages(self, messages, tools, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return native("limit_up_events", {"trade_date": "2026-09-22"}, "query")
            if self.calls == 2:
                if "on_chunk" in kwargs:
                    from langchain_core.messages import AIMessageChunk
                    kwargs["on_chunk"](AIMessageChunk(content="", tool_call_chunks=[{
                        "name": "finish", "id": "bad", "index": 0,
                        "args": '{"answer":"待撤回草稿",'}]))
                raise bad_protocol()
            assert isinstance(messages[-1], HumanMessage)
            assert "missing必须是字符串数组" in messages[-1].content
            assert SECRET not in str(messages)
            observations = [json.loads(m.content) for m in messages if isinstance(m, ToolMessage)]
            assert len(observations) == 1 and observations[0]["row_count"] == 2
            if not recover:
                raise bad_protocol()
            key = observations[0]["evidence_id"]
            return native("finish", {"status": "partial", "answer": "来源截断，仅为返回部分。\n{{evidence_table}}",
                "evidence_ids": [key], "missing": ["完整范围"], "table": {"evidence_id": key,
                "columns": [{"field": "symbol", "label": "代码"}, {"field": "name", "label": "名称"}]}}, "finish")

    model = Model()
    response = runtime.run(request(), registry(limit_up_events=query), model,
                           answer_event=lambda event, data: events.append((event, data)))
    assert len(queried) == 1 and model.calls == 3
    assert response.task_status == "partial"
    assert response.stop_reason == ("answered" if recover else "provider_error")
    assert "600101" in response.answer and "000202" in response.answer and "截断" in response.answer
    assert "待撤回草稿" not in response.answer and events == []
    assert SECRET not in json.dumps(response.model_dump(mode="json"), ensure_ascii=False)
    errors = [t.output for t in response.tool_results if t.name == "react_provider_error"]
    assert len(errors) == (1 if recover else 2)
    assert errors[0]["diagnostics"]["failure_category"] == "tool_arguments"
    fallback = [t for t in response.tool_results if t.name == "react_fallback_delivery"]
    finishes = [call for t in response.tool_results if t.name == "react_decision"
                for call in t.output["tool_calls"] if call["name"] == "finish"]
    assert bool(finishes) is recover
    if recover:
        assert not fallback
    else:
        assert len(fallback) == 1 and fallback[0].result is None
        assert fallback[0].output["origin"] == "runtime" and fallback[0].output["passed"]
        final = fallback[0].output["finish"]
        execution = next(t.output for t in response.tool_results if t.name == "react_execution")
        record = execution["evidence"][final["table"]["evidence_id"]]
        assert record["rows"] == ROWS and record["source_truncated"] is True
        assert final["evidence_ids"] == [record["evidence_id"]] and final["missing"]
        assert "全部记录" in response.answer and execution["compliance_checks"] == 1


def prepared_run(**changes):
    target = runtime.Run(request(), registry(), object(), [], None)
    target.task_context = TaskInterpretation(output_contract=CONTRACT)
    key = target.evidence.add(tool="limit_up_events", payload=payload(**changes), state="partial", arguments={})
    return target, key


@pytest.mark.parametrize("boundary", ["ambiguous", "historical", "stale", "prose",
    "unresolved", "over_limit", "unproven_window", "non_scalar", "cancelled", "deadline"])
def test_uncertain_or_ineligible_recovery_never_publishes_a_table(monkeypatch, boundary):
    target, key = prepared_run()
    reason = "provider_error"
    if boundary == "ambiguous":
        target.evidence.add(tool="stock_news", payload={"items": deepcopy(ROWS)}, state="ok", arguments={})
    elif boundary == "historical":
        target.evidence.restore_history(key, target.evidence.records[key])
    elif boundary == "stale":
        target.evidence.records[key]["payload"]["data_fresh"] = False
    elif boundary == "non_scalar":
        target.evidence.records[key]["rows"][0]["name"] = {"untrusted": SECRET}
    elif boundary == "prose":
        target.task_context.output_contract.mode = "prose_only"
    elif boundary == "unresolved":
        target.task_context.pending_slots = ["日期"]
    elif boundary == "over_limit":
        target.task_context = TaskInterpretation(output_contract={**CONTRACT,
            "row_selection": {"requested": "前1只", "limit": 1}})
    elif boundary == "unproven_window":
        target.task_context = TaskInterpretation(output_contract={**CONTRACT,
            "row_selection": {"requested": "第2至3名", "limit": 2, "offset": 1}})
    elif boundary == "cancelled":
        target.control = SimpleNamespace(cancelled=lambda: True)
    elif boundary == "deadline":
        target.deadline = 0
    def unexpected_review(*a, **k):
        pytest.fail("Ineligible recovery must not call the model")
    monkeypatch.setattr(runtime, "review_answer", unexpected_review)
    before = deepcopy(target.evidence.records)
    target.stop(reason)
    assert "| 代码 |" not in target.answer and SECRET not in target.answer
    assert not any(t.output.get("passed") for t in target.traces if t.name == "react_fallback_delivery")
    assert target.evidence.records == before


@pytest.mark.parametrize("failure", ["reject", "exception", "cancel_during_review", "expire_during_review"])
def test_recovery_review_fails_closed_and_generic_stop_does_not_echo_rejected_cells(monkeypatch, failure):
    target, key = prepared_run()
    target.evidence.records[key]["rows"][0]["name"] = SECRET
    def review(*a, **k):
        if failure == "exception":
            raise RuntimeError(SECRET)
        if failure == "cancel_during_review":
            target.control = SimpleNamespace(cancelled=lambda: True)
        if failure == "expire_during_review":
            target.deadline = 0
        return ComplianceReview(decision="reject", violations=["trade_instruction"], reason="fixture") if failure == "reject" else ComplianceReview(decision="allow", violations=[], reason="fixture")
    monkeypatch.setattr(runtime, "review_answer", review)
    target.stop("provider_error")
    assert SECRET not in target.answer and "| 代码 |" not in target.answer
    assert target.compliance_checks == 1
    assert target.reason == ("cancelled" if failure == "cancel_during_review" else
                             "budget_exhausted" if failure == "expire_during_review" else "provider_error")
    if target.reason != "cancelled":
        assert "来源截断" in target.answer
