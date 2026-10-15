"""Synthetic row-bound regressions, not live-model intent-quality measurements."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from app.agents.react_runtime import runtime
from app.agents.react_runtime.compliance import ComplianceReview
from app.agents.react_runtime.context import prepare_task_context
from app.agents.react_runtime.contracts import Compute, Finish
from app.agents.react_runtime.evidence import EvidenceStore
from app.agents.react_runtime.rendering import render_answer
from app.agents.react_runtime.task_contract import RowSelection, TaskInterpretation, validate_delivery
from app.agents.tools import TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest, ChatSessionMessage
from app.services.prompt_security import review_input


DATE = "2026-08-12"


def rows(count):
    return [{"symbol": f"600{i:03d}", "name": f"回归样本{i}", "trade_date": DATE,
             "first_limit_time": f"09:{30 + i:02d}:00", "amount": float(100 - i)}
            for i in range(count)]


def evidence(count, **metadata):
    store = EvidenceStore()
    key = store.add(tool="synthetic_rows", state="ok" if count else "empty", arguments={},
                    payload={"items": rows(count), **metadata})
    return store, key


def table(key, *, status="complete", mode="table_only"):
    answer = "{{evidence_table}}" if mode == "table_only" else "已有结果如下。\n{{evidence_table}}"
    return Finish(status=status, answer=answer, missing=["源集合不完整"] if status == "partial" else [],
                  evidence_ids=[key], table={"evidence_id": key, "columns": [
                      {"field": "symbol", "label": "代码"}, {"field": "name", "label": "名称"}]})


def task(limit, *, offset=0, mode="table_only"):
    return TaskInterpretation(output_contract={"mode": mode, "fields": ["symbol", "name"],
        "table_required": True, "row_selection": {"requested": f"窗口起点{offset}，最多{limit}行",
                                                   "limit": limit, "offset": offset}})


@pytest.mark.parametrize("mode,status", [
    ("table_only", "complete"), ("freeform", "complete"),
    ("table_only", "partial"), ("freeform", "partial"),
])
def test_actual_full_rows_over_limit_are_rejected_without_trimming(mode, status):
    store, key = evidence(11, returned_count=2)
    assert len(store.view(key)["rows"]) == 8
    with pytest.raises(ValueError, match="row count 11 exceeds the requested maximum 10"):
        validate_delivery(table(key, status=status, mode=mode), task(10, mode=mode), store)
    assert store.get(key)["rows"] == rows(11)


@pytest.mark.parametrize("count", [0, 1, 3, 5])
def test_full_collection_smaller_than_requested_limit_is_not_forced_to_invent_rows(count):
    store, key = evidence(count)
    final = table(key, status="complete" if count else "empty")
    validate_delivery(final, task(5), store)
    assert len(render_answer(final, store).splitlines()) == count + 2


def test_row_constraint_with_table_requires_store_instead_of_silently_bypassing_check():
    with pytest.raises(ValueError, match="requires the current evidence store"):
        validate_delivery(table("unread"), task(2))


def test_source_truncation_remains_partial_even_when_result_is_under_row_limit():
    store, key = evidence(2, matched_count=8, source_truncated=True)
    partial = table(key, status="partial", mode="freeform")
    validate_delivery(partial, task(5, mode="freeform"), store)
    assert "600000" in render_answer(partial, store)
    complete = table(key)
    validate_delivery(complete, task(5), store)
    with pytest.raises(ValueError, match="Truncated"):
        render_answer(complete, store)


def test_existing_complete_rank_scope_still_works_with_requested_window():
    store = EvidenceStore()
    args = {"sort_by": "amount", "sort_order": "desc"}
    root = store.add(tool="limit_up_events", state="ok", arguments=args, payload={
        "events": rows(8), "trade_date": DATE, "recent_trade_days": 1,
        "matched_count": 12, "returned_count": 8, **args})
    selected = store.compute(Compute(evidence_id=root, sort_by="amount", offset=3, limit=3))
    assert store.complete_rank_scope(selected)["offset"] == 3
    validate_delivery(table(selected), task(3, offset=3), store)
    answer = render_answer(table(selected), store)
    assert [row["symbol"] for row in store.get(selected)["rows"]] == ["600003", "600004", "600005"]
    assert len(answer.splitlines()) == 5


def test_window_intent_is_retained_without_claiming_row_count_proves_membership():
    store, key = evidence(3)
    interpretation = task(3, offset=3)
    validate_delivery(table(key), interpretation, store)
    # Cardinality alone does not establish semantic rank membership.
    assert interpretation.output_contract.row_selection.offset == 3


def test_no_row_bound_preserves_existing_table_delivery():
    store, key = evidence(11)
    validate_delivery(table(key), TaskInterpretation(output_contract={"mode": "table_only"}), store)


def test_row_bound_does_not_override_prose_only_or_necessary_clarification():
    store, key = evidence(1)
    interpretation = task(2, mode="prose_only")
    with pytest.raises(ValueError, match="prose-only"):
        validate_delivery(table(key), interpretation, store)
    validate_delivery(Finish(status="clarify", answer="请明确查询日期。"), interpretation)


@pytest.mark.parametrize("selection", [
    {"requested": "前零名", "limit": 0}, {"requested": "前两名", "limit": True},
    {"requested": "前两名", "limit": 2, "offset": -1},
    {"requested": "前两名", "limit": 2, "offset": False},
])
def test_invalid_cardinality_values_fail_schema_validation(selection):
    with pytest.raises(ValidationError):
        RowSelection.model_validate(selection)


def call(name, args, identifier):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": identifier}])


class Reviewer:
    def __init__(self, selection=None, *, mode="table_only", current_task="当前名单请求"):
        self.selection, self.mode, self.current_task = selection, mode, current_task
        self.input_payload = self.schema = None

    def generate_messages(self, messages, tools, **kwargs):
        self.input_payload = json.loads(messages[-1].content)
        self.schema = tools[0]["function"]["parameters"]
        return call("submit_input_security_review", {
            "decision": "allow", "signals": [], "reason": "scripted semantic contract",
            "request_kind": "research", "context_mode": "follow_up", "current_task": self.current_task,
            "date_scope": {"mode": "unspecified"},
            "output_contract": {"mode": self.mode, "row_selection": self.selection,
                                "table_required": self.mode != "prose_only"},
        }, "review")


@pytest.mark.parametrize("reply,selection,mode", [
    ("数量改成3只。", {"requested": "3只", "limit": 3, "offset": 0}, "table_only"),
    ("改成第4至6名。", {"requested": "第4至6名", "limit": 3, "offset": 3}, "table_only"),
    ("日期改成2026-08-13。", {"requested": "前2只", "limit": 2, "offset": 0}, "table_only"),
    ("改成全部名单。", None, "table_only"),
    ("只回答数量。", None, "prose_only"),
])
def test_current_semantic_selection_replaces_prior_constraint_without_losing_context(reply, selection, mode):
    previous = task(2).model_dump(mode="json")
    history = [ChatSessionMessage(message_id="previous", session_id="rank-followup", role="assistant",
        content="此前名单", created_at=datetime(2026, 8, 12, tzinfo=timezone.utc), metadata={
            "tool_results": [{"name": "react_task_context", "output": previous}]})]
    request = AgentChatRequest(session_id="rank-followup", message=reply)
    reviewer = Reviewer(selection, mode=mode)
    assessment = review_input(reviewer, message=reply, timeout_seconds=10,
                              task_context=prepare_task_context(request, history))
    prior = reviewer.input_payload["task_context"]["previous_task"]["output_contract"]["row_selection"]
    assert prior["limit"] == 2
    actual = assessment.output_contract.row_selection
    assert (actual.model_dump() if actual else None) == selection
    assert assessment.output_contract.mode == mode
    node = reviewer.schema["properties"]["output_contract"]
    while "$ref" in node:
        node = reviewer.schema["$defs"][node["$ref"].split("/")[-1]]
    assert "row_selection" in node["properties"]


@pytest.mark.parametrize("saved", [
    {}, {"row_selection": {"requested": "第4至6名", "limit": 3, "offset": 3}},
])
def test_checkpoint_task_roundtrip_accepts_legacy_and_preserves_row_window(saved):
    original = TaskInterpretation.model_validate({"output_contract": saved})
    restored = TaskInterpretation.model_validate_json(original.model_dump_json())
    assert restored == original
    assert (restored.output_contract.row_selection is None) == (not saved)


def test_runtime_rejects_extra_row_then_repairs_by_selecting_current_evidence(monkeypatch):
    monkeypatch.setattr(runtime, "review_answer", lambda *args, **kwargs:
        ComplianceReview(decision="allow", violations=[], reason="offline regression"))
    query_calls = []

    def events(**kwargs):
        query_calls.append(kwargs)
        return ToolResult(name="limit_up_events", input=kwargs, summary="three synthetic rows",
            output={"events": rows(3), "trade_date": DATE, "matched_count": 3,
                    "returned_count": 3, "sort_by": "first_limit_time", "sort_order": "asc"})

    class Model(Reviewer):
        finishes = 0

        def generate_messages(self, messages, tools, **kwargs):
            if tools[0]["function"]["name"] == "submit_input_security_review":
                return super().generate_messages(messages, tools, **kwargs)
            observations = {message.tool_call_id: json.loads(message.content) for message in messages
                            if isinstance(message, ToolMessage)}
            if "query" not in observations:
                return call("limit_up_events", {"trade_date": DATE, "board_height": 1,
                    "market": "main_board", "closed_only": True, "sort_by": "first_limit_time",
                    "sort_order": "asc", "limit": 100}, "query")
            if self.finishes and "selected" not in observations:
                assert "row count 3 exceeds the requested maximum 2" in messages[-1].content
                return call("compute_result", {"evidence_id": observations["query"]["evidence_id"],
                    "sort_by": "first_limit_time", "descending": False, "limit": 2}, "selected")
            selected = observations.get("selected", observations["query"])["evidence_id"]
            self.finishes += 1
            return call("finish", table(selected).model_dump(mode="json"), f"finish-{self.finishes}")

    model = Model({"requested": "首次封板最早2只", "limit": 2, "offset": 0},
                  current_task="查询主板首板收盘封住，首次封板最早2只，只列代码名称。")
    registry = SimpleNamespace(events=[], schemas=lambda: TOOL_SCHEMAS, is_enabled=lambda _: True,
                               limit_up_events=events)
    response = runtime.run(AgentChatRequest(session_id="row-repair", message=model.current_task), registry, model)
    assert response.task_status == "complete" and response.stop_reason == "answered"
    assert len(query_calls) == 1 and query_calls[0]["limit"] == 100
    assert model.finishes == 2
    checks = [trace.output for trace in response.tool_results if trace.name == "react_answer_check"]
    assert [check["passed"] for check in checks] == [False, True]
    assert response.answer.splitlines()[2:] == ["| 600000 | 回归样本0 |", "| 600001 | 回归样本1 |"]
