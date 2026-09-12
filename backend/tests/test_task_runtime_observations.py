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


def test_missing_predicate_field_is_not_a_false_observation():
    import pytest
    from app.agents.task_runtime.contracts import Predicate, TaskStep
    from app.agents.task_runtime.selection import evaluate, select
    predicate = Predicate(path="missing.rank", operator="less_than", value=6)
    assert evaluate({"items": [{"rank": 1}]}, predicate) == (False, False)
    step = TaskStep(
        step_id="filter", requirement_ids=("r",), step_type="operation",
        operation="select", depends_on=("source",), select_path="items",
        filters=(Predicate(path="market_rank", operator="less_than", value=6),),
    )
    with pytest.raises(ValueError, match="filter field missing"):
        select({"items": [{"sector_name": "半导体"}]}, step)


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


def test_numeric_scope_from_original_requirement_does_not_need_market_claim():
    from app.agents.task_runtime.contracts import Requirement
    from app.agents.task_runtime.writer import AnswerBlock, Claim, _valid
    requirement = Requirement(
        id="r", description="2026-05-15评分Top3", source_text="查2026-05-15评分Top3",
        constraints={"top_n": 3},
    )
    state = {"records": {"s": {"requirement_ids": ["r"], "payload": {"score": 88}, "state": "ok"}}}
    block = AnswerBlock(
        requirement_id="r", content="2026-05-15评分Top3中该项评分88。",
        evidence_steps=["s"], claims=[Claim(step_id="s", path="score", value=88)],
    )
    assert _valid(block, requirement, state)


def test_planner_source_text_normalization_does_not_discard_plan():
    from app.agents.task_runtime.runtime import run
    outputs = [{
        "requirements": [{"id": "r", "description": "说明", "source_text": "模型改写"}],
        "steps": [], "behavior": "answer", "message": "这是一般说明。",
    }]
    provider = SimpleNamespace(generate_function_call=lambda *a, **k: LLMResult(
        content=json.dumps(outputs.pop(0)), model="test", provider="test"
    ))
    tools = SimpleNamespace(events=[], profile="test", is_enabled=lambda n: True, schemas=lambda: TOOL_SCHEMAS)
    response = run(AgentChatRequest(session_id="test", message="请做一般说明"), tools, provider, [])
    assert response.answer == "这是一般说明。"
    assert response.warnings == ["planner source_text was normalized to the original user query"]


def test_unmatched_conditional_branch_is_valid_observation_not_replan_gap():
    from app.agents.task_runtime.runtime import run
    plan = {
        "requirements": [
            {"id": "r1", "description": "查询", "source_text": "研究"},
            {"id": "r2", "description": "否则分支", "source_text": "研究"},
        ],
        "steps": [
            {"step_id": "s1", "requirement_ids": ["r1"], "capability": "popularity", "tool_name": "hot_stock_ranking"},
            {"step_id": "s2", "requirement_ids": ["r2"], "capability": "stock_trend", "tool_name": "stock_kline", "arguments": {"symbol": "000001"}, "when_step": "s1", "when_states": ["empty"]},
        ],
    }
    completion = {"complete": True, "satisfied_ids": ["r1", "r2"], "missing": {}, "reason": "else branch not applicable", "answer": {"blocks": [
        {"requirement_id": "r1", "content": "已取得证据。", "evidence_steps": ["s1"]},
        {"requirement_id": "r2", "content": "条件未满足，该分支不适用。", "evidence_steps": ["s2"]},
    ]}}
    outputs = [plan, completion]
    provider = SimpleNamespace(generate_function_call=lambda *a, **k: LLMResult(content=json.dumps(outputs.pop(0)), model="test", provider="test"))
    tools = SimpleNamespace(events=[], profile="test", is_enabled=lambda n: True, schemas=lambda: TOOL_SCHEMAS,
        hot_stock_ranking=lambda: ToolResult(name="hot_stock_ranking", input={}, output={"items": [{"symbol": "000001"}]}, trace_output={"items": [{"symbol": "000001"}]}, summary="ok"))
    response = run(AgentChatRequest(session_id="test", message="研究"), tools, provider, [])
    ledger = next(t.output for t in response.tool_results if t.name == "task_execution")
    assert ledger["terminal_state"] == "complete"
    assert ledger["replan_count"] == 0
    assert ledger["records"]["s2"]["condition"]["matched"] is False


def test_binding_contract_error_overrides_false_unrecoverable_verdict():
    from app.agents.task_runtime.runtime import run
    outputs = [
        {"requirements": [{"id": "r", "description": "研究", "source_text": "研究"}], "steps": [
            {"step_id": "s1", "requirement_ids": ["r"], "capability": "popularity", "tool_name": "hot_stock_ranking"},
            {"step_id": "s2", "requirement_ids": ["r"], "capability": "stock_trend", "tool_name": "stock_kline", "bindings": [{"source_step": "s1", "path": "missing.symbol", "target_argument": "symbol"}]},
        ]},
        {"complete": False, "satisfied_ids": [], "missing": {"r": "binding failed"}, "reason": "incorrectly called unavailable", "can_recover": False},
        {"new_steps": [], "reason": "no valid repair"},
        {"blocks": [{"requirement_id": "r", "content": "下游证据缺失。", "evidence_steps": ["s2"]}]},
    ]
    provider = SimpleNamespace(generate_function_call=lambda *a, **k: LLMResult(content=json.dumps(outputs.pop(0)), model="test", provider="test"))
    tools = SimpleNamespace(events=[], profile="test", is_enabled=lambda n: True, schemas=lambda: TOOL_SCHEMAS,
        hot_stock_ranking=lambda: ToolResult(name="hot_stock_ranking", input={}, output={"items": [{"symbol": "000001"}]}, trace_output={"items": [{"symbol": "000001"}]}, summary="ok"))
    response = run(AgentChatRequest(session_id="test", message="研究"), tools, provider, [])
    ledger = next(t.output for t in response.tool_results if t.name == "task_execution")
    assert ledger["replan_count"] == 1
    assert any(t.name == "task_replan" for t in response.tool_results)


def test_invalid_native_structure_retries_once_as_explicit_json():
    from app.agents.task_runtime.runtime import run
    from app.services.llm_provider import NativeFunctionCallingError
    plan = {
        "requirements": [{"id": "r", "description": "说明", "source_text": "说明"}],
        "steps": [], "behavior": "answer", "message": "一般说明。",
    }
    class Provider:
        planner_max_tokens = 100
        max_attempts = 2
        def generate_function_call(self, *args, **kwargs):
            raise NativeFunctionCallingError("invalid envelope")
        def generate(self, *args, **kwargs):
            return LLMResult(content=json.dumps(plan), model="test", provider="test")
    tools = SimpleNamespace(events=[], profile="test", is_enabled=lambda n: True, schemas=lambda: TOOL_SCHEMAS)
    response = run(AgentChatRequest(session_id="test", message="说明"), tools, Provider(), [])
    ledger = next(t.output for t in response.tool_results if t.name == "task_execution")
    assert response.answer == "一般说明。"
    assert ledger["llm_calls"] == 2
    assert any(t.name == "task_plan_provider_error" for t in response.tool_results)


def test_completion_proposes_observation_patch_without_second_replan_call():
    from app.agents.task_runtime.runtime import run
    plan = {"requirements": [{"id": "r", "description": "研究", "source_text": "研究"}], "steps": [
        {"step_id": "s1", "requirement_ids": ["r"], "capability": "stock_news", "tool_name": "stock_news", "arguments": {"symbol": "000001"}},
    ]}
    completion = {
        "complete": False, "satisfied_ids": [], "missing": {"r": "news empty; need trend"},
        "reason": "observed empty", "can_recover": True,
        "proposed_steps": [{"step_id": "s2", "requirement_ids": ["r"], "capability": "stock_trend", "tool_name": "stock_kline", "arguments": {"symbol": "000001"}}],
    }
    final = {"complete": True, "satisfied_ids": ["r"], "missing": {}, "reason": "trend available", "answer": {"blocks": [
        {"requirement_id": "r", "content": "新闻为空，走势证据可用。", "evidence_steps": ["s1", "s2"]},
    ]}}
    outputs = [plan, completion, final]
    provider = SimpleNamespace(generate_function_call=lambda *a, **k: LLMResult(content=json.dumps(outputs.pop(0)), model="test", provider="test"))
    tools = SimpleNamespace(events=[], profile="test", is_enabled=lambda n: True, schemas=lambda: TOOL_SCHEMAS,
        stock_news=lambda symbol: ToolResult(name="stock_news", input={"symbol": symbol}, output={"items": []}, trace_output={"items": []}, summary="empty", result_status="empty"),
        stock_kline=lambda symbol: ToolResult(name="stock_kline", input={"symbol": symbol}, output={"symbol": symbol}, trace_output={"symbol": symbol}, summary="ok"))
    response = run(AgentChatRequest(session_id="test", message="研究"), tools, provider, [])
    ledger = next(t.output for t in response.tool_results if t.name == "task_execution")
    assert not outputs
    assert ledger["llm_calls"] == 3
    assert ledger["replan_count"] == 1
    assert next(t for t in response.tool_results if t.name == "task_replan").input["decision_type"] == "completion_patch"


def test_completion_duplicate_patch_stops_without_reexecuting_tool():
    from app.agents.task_runtime.runtime import run
    plan = {"requirements": [{"id": "r", "description": "研究", "source_text": "研究"}], "steps": [
        {"step_id": "s1", "requirement_ids": ["r"], "capability": "stock_news", "tool_name": "stock_news", "arguments": {"symbol": "000001"}},
    ]}
    completion = {
        "complete": False, "satisfied_ids": [], "missing": {"r": "fixture has no item"},
        "reason": "no evidence", "can_recover": True,
        "proposed_steps": [{"step_id": "s2", "requirement_ids": ["r"], "capability": "stock_news", "tool_name": "stock_news", "arguments": {"symbol": "000001"}}],
        "answer": {"blocks": [{"requirement_id": "r", "content": "新闻结果为空。", "evidence_steps": ["s1"]}]},
    }
    outputs = [plan, completion]
    provider = SimpleNamespace(generate_function_call=lambda *a, **k: LLMResult(content=json.dumps(outputs.pop(0)), model="test", provider="test"))
    calls = []
    def stock_news(symbol):
        calls.append(symbol)
        return ToolResult(name="stock_news", input={"symbol": symbol}, output={"items": []}, trace_output={"items": []}, summary="empty", result_status="empty")
    tools = SimpleNamespace(events=[], profile="test", is_enabled=lambda n: True, schemas=lambda: TOOL_SCHEMAS, stock_news=stock_news)
    response = run(AgentChatRequest(session_id="test", message="研究"), tools, provider, [])
    ledger = next(t.output for t in response.tool_results if t.name == "task_execution")
    assert calls == ["000001"]
    assert ledger["llm_calls"] == 2
    assert ledger["replan_count"] == 1
    assert ledger["terminal_state"] == "partial"


def test_snapshot_only_tool_cannot_mix_current_fact_into_historical_request():
    from datetime import date
    from app.agents.query_contract import query_reference_date_override
    from app.agents.task_runtime.runtime import run
    plan = {"requirements": [{"id": "r", "description": "历史热榜", "source_text": "查询2026-05-15历史热榜", "constraints": {"trade_date": "2026-05-15"}}], "steps": [
        {"step_id": "s1", "requirement_ids": ["r"], "capability": "popularity", "tool_name": "hot_stock_ranking", "arguments": {"limit": 2}},
    ]}
    completion = {"complete": False, "satisfied_ids": [], "missing": {"r": "工具仅支持当前快照"}, "reason": "historical unavailable", "can_recover": False, "answer": {"blocks": [
        {"requirement_id": "r", "content": "无法取得2026-05-15的历史热榜。", "evidence_steps": ["s1"]},
    ]}}
    outputs = [plan, completion]
    provider = SimpleNamespace(generate_function_call=lambda *a, **k: LLMResult(content=json.dumps(outputs.pop(0)), model="test", provider="test"))
    calls = []
    tools = SimpleNamespace(events=[], profile="test", is_enabled=lambda n: True, schemas=lambda: TOOL_SCHEMAS,
        hot_stock_ranking=lambda limit=10: calls.append(limit))
    with query_reference_date_override(date(2026, 9, 12)):
        response = run(AgentChatRequest(session_id="test", message="查询2026-05-15历史热榜"), tools, provider, [])
    ledger = next(t.output for t in response.tool_results if t.name == "task_execution")
    assert calls == []
    assert ledger["tool_calls"] == 0
    assert "snapshot-only" in ledger["records"]["s1"]["reason"]
    assert "无法取得2026-05-15" in response.answer
