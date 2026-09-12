"""Regression contracts found by real model and HTTP task-runtime acceptance."""

from types import SimpleNamespace
import json

from app.agents.task_runtime.contracts import TaskPlan
from app.agents.task_runtime.runtime import run
from app.agents.task_runtime.writer import Answer, compose
from app.agents.tools import TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest
from app.services.sample_data import SAMPLE_EVENTS
from app.services.llm_provider import LLMResult


def test_task_schema_exposes_only_supported_binding_contract():
    schema = json.dumps(TaskPlan.model_json_schema())
    assert "argument_bindings" not in schema
    assert "output_entity_set" not in schema
    assert "source_step" in schema


def test_invalid_operation_shape_is_rejected_before_execution():
    import pytest
    with pytest.raises(ValueError, match="select requires"):
        TaskPlan.model_validate({"requirements": [{"id": "r", "description": "x", "source_text": "x"}], "steps": [{"step_id": "s", "requirement_ids": ["r"], "step_type": "operation", "operation": "select", "select_path": "items"}]})


def test_combined_completion_draft_uses_two_calls_and_serializes_rows():
    from datetime import date
    plan = {"requirements": [{"id": "r", "description": "名单", "source_text": "名单"}],
            "steps": [{"step_id": "s", "requirement_ids": ["r"], "capability": "limit_up_pool", "tool_name": "limit_up_events"}]}
    outputs = [plan, {"complete": True, "satisfied_ids": ["r"], "reason": "observed", "answer": {"blocks": [{"requirement_id": "r", "content": "中百集团（000759）。", "evidence_steps": ["s"], "claims": [{"step_id": "s", "path": "events.0.symbol", "value": "000759"}]}]}}]
    provider = SimpleNamespace(generate_function_call=lambda *a, **k: LLMResult(content=json.dumps(outputs.pop(0)), model="test", provider="test"))
    event = SAMPLE_EVENTS[0].model_copy(update={"symbol": "000759", "name": "中百集团", "trade_date": date(2026, 9, 8)})
    tools = SimpleNamespace(events=[], profile="test", is_enabled=lambda n: True, schemas=lambda: TOOL_SCHEMAS,
        limit_up_events=lambda: ToolResult(name="limit_up_events", input={}, output=[event], trace_output={"events": [event.model_dump(mode="json")]}, summary="ok"))
    response = run(AgentChatRequest(session_id="test", message="名单"), tools, provider, [])
    ledger = next(t.output for t in response.tool_results if t.name == "task_execution")
    assert ledger["llm_calls"] == 2
    assert ledger["records"]["s"]["payload"]["events"][0]["symbol"] == "000759"
    assert ledger["terminal_state"] == "complete"


def test_bad_claim_repair_never_overwrites_valid_task():
    plan = TaskPlan.model_validate({"requirements": [{"id": rid, "description": rid, "source_text": rid} for rid in ["a", "b"]], "steps": [{"step_id": "s", "requirement_ids": ["a", "b"], "capability": "stock_trend", "tool_name": "stock_kline"}]})
    state = {"plan": plan, "stop": "complete", "records": {"s": {"requirement_ids": ["a", "b"], "tool": "stock_kline", "payload": {"value": 10}, "state": "ok"}}, "answer_draft": {"blocks": [
        {"requirement_id": "a", "content": "保留已验证段落。", "evidence_steps": ["s"]},
        {"requirement_id": "b", "content": "错误20。", "evidence_steps": ["s"], "claims": [{"step_id": "s", "path": "value", "value": 20}]},
    ]}}
    def repair(kind, model, payload):
        assert kind == "answer_repair"
        assert [r["id"] for r in payload["requirements"]] == ["b"]
        return Answer.model_validate({"blocks": [
            {"requirement_id": "a", "content": "不应覆盖。", "evidence_steps": ["s"]},
            {"requirement_id": "b", "content": "正确10。", "evidence_steps": ["s"], "claims": [{"step_id": "s", "path": "value", "value": 10}]},
        ]})
    answer = compose(state, repair)
    assert "保留已验证段落" in answer and "不应覆盖" not in answer
    assert "正确10" in answer and "错误20" not in answer


def test_generic_selection_top_then_filter_and_condition():
    from app.agents.task_runtime.contracts import Predicate, TaskStep
    from app.agents.task_runtime.selection import matches, select
    rows = {"rows": [{"name": "A", "score": 4}, {"name": "B", "score": 9}, {"name": "C", "score": 7}]}
    top = TaskStep(step_id="top", requirement_ids=("r",), step_type="operation", operation="select", depends_on=("source",), select_path="rows", sort_field="score", take=2)
    selected = select(rows, top)
    assert [row["name"] for row in selected] == ["B", "C"]
    assert matches({"items": selected}, Predicate(path="items.*.name", operator="contains", value="B"))
    assert not matches({"items": selected}, Predicate(path="items.*.name", operator="contains", value="A"))
    filtered = top.model_copy(update={"select_path": "items", "filters": (Predicate(path="score", operator="greater_than", value=8),)})
    assert select({"items": selected}, filtered) == [{"name": "B", "score": 9}]


def test_explicit_evidence_namespaces_are_unambiguous():
    from app.agents.task_runtime.contracts import evidence_values
    record = {"payload": {"items": [{"symbol": "1"}, {"symbol": "2"}]}, "selected": [{"symbol": "1"}], "state": "ok"}
    assert evidence_values(record, "payload.items.*.symbol") == ["1", "2"]
    assert evidence_values(record, "items.*.symbol") == ["1", "2"]
    assert evidence_values(record, "selected.*.symbol") == ["1"]
    assert evidence_values(record, "state") == ["ok"]


def test_named_metric_preserves_fixture_value_and_supplies_real_schema_field():
    from app.agents.task_runtime.adapter import evidence_payload
    result = ToolResult(name="first_board_ratings", input={}, summary="score", output={"top_candidates": [{"symbol": "000001", "metric": "score", "value": 88}]})
    row = evidence_payload(result)["top_candidates"][0]
    assert row["score"] == row["value"] == 88


def test_invalid_selection_does_not_discard_raw_success_in_fallback():
    from app.agents.task_runtime.writer import _fallback
    from app.agents.task_runtime.contracts import Requirement
    text = _fallback(Requirement(id="r", description="研究", source_text="研究"), {"records": {"s": {"requirement_ids": ["r"], "state": "error", "payload": {"symbol": "000001", "score": 88}}}})
    assert "000001" in text and "88" in text


def test_frozen_limit_up_respects_highest_only_before_fanout():
    from app.agents.chat_live_eval_runner import FrozenLiveToolRegistry
    tools = FrozenLiveToolRegistry([])
    result = tools.execute_frozen_calls([{"name": "limit_up_events", "arguments": {"trade_date": "2026-05-15", "highest_only": True, "min_board_height": 2, "limit": 100}}], request=AgentChatRequest(session_id="frozen", message=""))
    events = result["tool_results"][0].output["events"]
    assert [(item["symbol"], item["board_height"]) for item in events] == [("001299", 3)]


def test_undeclared_numeric_claim_rejected():
    from app.agents.task_runtime.contracts import Requirement
    from app.agents.task_runtime.writer import AnswerBlock, Claim, _valid
    requirement = Requirement(id="r", description="研究", source_text="研究")
    state = {"records": {"s": {"requirement_ids": ["r"], "payload": {"score": 88}, "state": "ok"}}}
    valid = AnswerBlock(requirement_id="r", content="评分88。", evidence_steps=["s"], claims=[Claim(step_id="s", path="score", value=88)])
    unsupported = valid.model_copy(update={"content": "评分88，排名第3。"})
    assert _valid(valid, requirement, state)
    assert not _valid(unsupported, requirement, state)
