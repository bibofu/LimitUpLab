"""Direct-answer delivery contracts with synthetic tools and scripted semantics.

These tests check the real review/runtime plumbing and deterministic table gate.
They do not measure whether a live model recognizes scope or stays within it in prose.
"""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agents.react_runtime import runtime
from app.agents.react_runtime.compliance import ComplianceReview
from app.agents.react_runtime.contracts import Finish
from app.agents.react_runtime.task_contract import TaskInterpretation, validate_delivery
from app.agents.tools import TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest
from app.services.prompt_security import review_input


def call(name, arguments, key):
    return AIMessage(content="", tool_calls=[{"name": name, "args": arguments, "id": key}])


def registry(**methods):
    return SimpleNamespace(events=[], profile="test", schemas=lambda: TOOL_SCHEMAS,
                           is_enabled=lambda _: True, **methods)


class DirectAnswerModel:
    def __init__(self, *, question, answer, status="complete", missing=None, extra_table=False):
        self.question, self.answer, self.status = question, answer, status
        self.missing, self.extra_table = missing or [], extra_table
        self.answers = 0
        self.review_schema = None
        self.interpretations = []

    def generate_messages(self, messages, tools, **kwargs):
        if tools[0]["function"]["name"] == "submit_input_security_review":
            self.review_schema = tools[0]["function"]["parameters"]
            return call("submit_input_security_review", {
                "decision": "allow", "signals": [], "reason": "scripted direct-answer scope",
                "request_kind": "research", "context_mode": "standalone",
                "current_task": self.question,
                "output_contract": {"mode": "prose_only", "field_requests": [], "table_required": False},
            }, "review")
        for message in messages:
            if isinstance(message.content, str) and message.content.startswith("本轮已解析用户任务"):
                self.interpretations.append(json.loads(message.content.split("：", 1)[1]))
        observations = [json.loads(message.content) for message in messages
                        if isinstance(message, ToolMessage) and message.tool_call_id == "ratings"]
        if not observations:
            return call("first_board_ratings", {"trade_date": "2026-08-12", "symbols": ["600123"]}, "ratings")
        self.answers += 1
        evidence_id = observations[0]["evidence_id"]
        final = {"status": self.status, "answer": self.answer,
                 "evidence_ids": [evidence_id], "missing": self.missing}
        if self.extra_table and self.answers == 1:
            final.update(answer=self.answer + "\n\n{{evidence_table}}", table={
                "evidence_id": evidence_id, "columns": [
                    {"field": "symbol", "label": "代码"}, {"field": "name", "label": "名称"}]})
        elif self.extra_table:
            assert "prose-only" in messages[-1].content
        return call("finish", final, f"finish-{self.answers}")


@pytest.fixture(autouse=True)
def allow_compliance(monkeypatch):
    monkeypatch.setattr(runtime, "review_answer", lambda *args, **kwargs:
        ComplianceReview(decision="allow", violations=[], reason="offline protocol fixture"))


def run_direct(model, *, source_state="ok", rows=None):
    calls = []

    def ratings(**kwargs):
        calls.append(kwargs)
        return ToolResult(name="first_board_ratings", input=kwargs, summary="synthetic directed evidence",
                          result_status=source_state, output={
                              "trade_date": "2026-08-12", "candidates": rows or [],
                              "data_missing": ["评分来源暂不完整"] if source_state == "partial" else []})

    response = runtime.run(AgentChatRequest(session_id="direct-answer", message=model.question),
                           registry(first_board_ratings=ratings), model)
    return response, calls


@pytest.mark.parametrize("question,answer,state,rows", [
    ("回归甲在2026-08-12有评级记录吗？只回答有无。", "回归甲在2026-08-12有评级记录。", "ok",
     [{"symbol": "600123", "name": "回归甲", "rating": "A"}]),
    ("回归甲在2026-08-12有评级记录吗？只回答有无。", "回归甲在2026-08-12没有评级记录。", "empty", []),
    ("回归甲在2026-08-12的评级记录有几条？只回答数量。", "回归甲在2026-08-12的评级记录为0条。", "empty", []),
    ("回归甲在2026-08-12的评级是什么？仅回答评级。", "回归甲在2026-08-12的评级为A。", "ok",
     [{"symbol": "600123", "name": "回归甲", "rating": "A"}]),
])
def test_direct_scope_survives_real_review_and_runtime_without_creating_display_fields(question, answer, state, rows):
    model = DirectAnswerModel(question=question, answer=answer)
    response, calls = run_direct(model, source_state=state, rows=rows)
    assert response.task_status == "complete" and response.stop_reason == "answered"
    assert response.answer == answer and model.answers == 1
    assert len(calls) == 1 and calls[0]["symbols"] == ["600123"]
    assert model.interpretations
    for interpretation in model.interpretations:
        contract = interpretation["output_contract"]
        assert contract["mode"] == "prose_only"
        assert contract["field_requests"] == contract["fields"] == []
        assert not contract["table_required"]


def test_direct_scope_is_part_of_the_input_review_wire_contract():
    model = DirectAnswerModel(question="仅回答是否有评级记录。", answer="有。")
    assessment = review_input(model, message=model.question, timeout_seconds=10)
    node = model.review_schema["properties"]["output_contract"]
    while "$ref" in node:
        node = model.review_schema["$defs"][node["$ref"].split("/")[-1]]
    properties = node["properties"]
    assert set(properties["mode"]["enum"]) == {"freeform", "table_only", "prose_only"}
    assert properties["mode"]["description"]
    assert "fields" not in properties
    assert assessment.output_contract.mode == "prose_only"
    assert assessment.output_contract.field_requests == assessment.output_contract.fields == []


def test_unrequested_table_uses_existing_repair_without_silently_cutting_model_output():
    model = DirectAnswerModel(question="回归甲在2026-08-12有评级记录吗？只回答有无。",
                              answer="回归甲在2026-08-12有评级记录。", extra_table=True)
    response, calls = run_direct(model, rows=[{"symbol": "600123", "name": "回归甲"}])
    checks = [trace.output for trace in response.tool_results if trace.name == "react_answer_check"]
    assert model.answers == 2 and len(calls) == 1
    assert [check["passed"] for check in checks] == [False, True]
    assert "prose-only" in checks[0]["reason"]
    assert response.task_status == "complete" and response.answer == model.answer


def test_direct_scope_preserves_real_missing_evidence_explanation():
    answer = "回归甲在2026-08-12的评分来源不完整，暂时不能确认是否有评级记录。"
    model = DirectAnswerModel(question="回归甲在2026-08-12有评级记录吗？只回答有无。",
                              answer=answer, status="partial", missing=["确认该对象当日是否有评级记录"])
    response, _ = run_direct(model, source_state="partial")
    assert response.task_status == "partial" and response.stop_reason == "answered"
    assert response.answer == answer and model.answers == 1


@pytest.mark.parametrize("status,answer,missing", [
    ("clarify", "请提供要查询的日期。", []),
    ("refuse", "不能提供买卖指令，可以查询有来源的研究事实。", []),
    ("empty", "没有匹配的记录。", []),
    ("partial", "数据源未能返回，暂不能确认。", ["确认记录是否存在"]),
])
def test_direct_scope_allows_necessary_explanations_but_never_extra_tables(status, answer, missing):
    task = TaskInterpretation(output_contract={"mode": "prose_only"})
    final = Finish(status=status, answer=answer, missing=missing)
    validate_delivery(final, task)
    with_table = Finish(status=status, answer=answer + "\n{{evidence_table}}", missing=missing,
                        table={"evidence_id": "synthetic", "columns": [{"field": "symbol", "label": "代码"}]})
    with pytest.raises(ValueError, match="prose-only"):
        validate_delivery(with_table, task)


@pytest.mark.parametrize("mode,answer", [
    ("freeform", "有记录，名单如下。\n{{evidence_table}}"),
    ("table_only", "{{evidence_table}}"),
])
def test_explicit_list_deliveries_are_unchanged(mode, answer):
    task = TaskInterpretation(current_task="判断是否有评级并列出代码。", output_contract={
        "mode": mode, "fields": ["symbol"], "table_required": True})
    final = Finish(status="complete", answer=answer, table={
        "evidence_id": "synthetic", "columns": [{"field": "symbol", "label": "代码"}]})
    validate_delivery(final, task)


@pytest.mark.parametrize("saved_contract,expected", [({}, "freeform"), ({"mode": "prose_only"}, "prose_only")])
def test_checkpoint_roundtrip_keeps_direct_scope_and_legacy_default(saved_contract, expected):
    run = runtime.Run(AgentChatRequest(session_id="direct-answer", message="查记录。"),
                      registry(), object(), [], None)
    run.control = SimpleNamespace(row={"checkpoint_json": json.dumps({
        "runtime": {"task_context": {"current_task": "查记录。", "output_contract": saved_contract}},
        "state": {"messages": [], "resume_node": "agent"},
    })})
    run.restore()
    assert run.task_context.output_contract.mode == expected
    restored = TaskInterpretation.model_validate(run.task_context.model_dump(mode="json"))
    assert restored == run.task_context
