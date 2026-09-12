"""Task-scoped execution invariants independent of stock names and query wording."""

from datetime import date
from types import SimpleNamespace

import pytest

from app.agents.task_runtime.adapter import invoke
from app.agents.task_runtime.contracts import TaskPlan, values_at
from app.agents.tools import TOOL_SCHEMAS


def test_plan_preserves_two_queries_of_same_tool():
    plan = TaskPlan.model_validate({
        "requirements": [{"id": "r", "description": "compare dates", "source_text": "dates"}],
        "steps": [
            {"step_id": "a", "requirement_ids": ["r"], "capability": "dragon_tiger", "tool_name": "dragon_tiger_list", "arguments": {"trade_date": "2026-09-08"}},
            {"step_id": "b", "requirement_ids": ["r"], "capability": "dragon_tiger", "tool_name": "dragon_tiger_list", "arguments": {"trade_date": "2026-09-11"}},
        ],
    })
    assert plan.steps[0].arguments != plan.steps[1].arguments


@pytest.mark.parametrize("source", ["missing", "b"])
def test_unresolved_and_cyclic_bindings_rejected(source):
    with pytest.raises(ValueError, match="earlier step"):
        TaskPlan.model_validate({
            "requirements": [{"id": "r", "description": "compare", "source_text": "compare"}],
            "steps": [{"step_id": "b", "requirement_ids": ["r"], "bindings": [{"source_step": source, "path": "items.*.symbol", "target_argument": "symbol"}]}],
        })


def test_bound_values_preserve_entity_order_and_empty():
    assert values_at({"items": [{"symbol": "2"}, {"symbol": "1"}]}, "items.*.symbol") == ["2", "1"]
    assert values_at({"items": []}, "items.*.symbol") == []
    assert values_at({"items": [{"symbol": "2"}]}, "$.items[0].symbol") == ["2"]
    with pytest.raises(ValueError):
        values_at({}, "__import__('os')")


def test_adapter_preserves_historical_date_and_count():
    seen = {}
    def dragon_tiger_list(*, trade_date: date | None = None, limit=30):
        seen.update(trade_date=trade_date, limit=limit)
        return "result"
    tools = SimpleNamespace(
        profile="test", schemas=lambda: TOOL_SCHEMAS, is_enabled=lambda name: True,
        dragon_tiger_list=dragon_tiger_list,
    )
    assert invoke(tools, "dragon_tiger", "dragon_tiger_list", {"trade_date": "2026-09-08", "limit": 2}) == "result"
    assert seen == {"trade_date": date(2026, 9, 8), "limit": 2}
    with pytest.raises(ValueError):
        invoke(tools, "dragon_tiger", "dragon_tiger_list", {"limit": 101})


def test_model_replans_from_observed_error_and_preserves_success():
    import json
    from app.agents.task_runtime.runtime import run
    from app.agents.tools import ToolResult
    from app.models import AgentChatRequest
    from app.services.llm_provider import LLMResult

    outputs = [
        {"requirements": [{"id": "r", "description": "证据", "source_text": "证据"}], "steps": [{"step_id": "s1", "requirement_ids": ["r"], "capability": "stock_news", "tool_name": "stock_news", "arguments": {"symbol": "605011"}}]},
        {"complete": False, "satisfied_ids": [], "missing": {"r": "news unavailable; need trend"}, "reason": "observed error"},
        {"new_steps": [{"step_id": "s2", "requirement_ids": ["r"], "capability": "stock_trend", "tool_name": "stock_kline", "arguments": {"symbol": "605011", "days": 12}}], "reason": "observation-driven alternative evidence"},
        {"complete": True, "satisfied_ids": ["r"], "missing": {}, "reason": "trend with news error disclosure available"},
        {"blocks": [{"requirement_id": "r", "content": "新闻查询失败；走势证据可用。", "evidence_steps": ["s1", "s2"]}]},
    ]
    class Provider:
        def generate_function_call(self, system, user, **kwargs):
            if kwargs["function_name"] == "task_replan":
                assert "source unavailable" in user
            return LLMResult(content=json.dumps(outputs.pop(0)), model="test", provider="test")
    def stock_news(symbol, days=7, limit=10):
        raise RuntimeError("source unavailable")
    def stock_kline(symbol, days=20, end_date=None):
        assert days == 12
        return ToolResult(name="stock_kline", input={"symbol": symbol, "days": days}, output={"symbol": symbol}, summary="ok", trace_output={"symbol": symbol})
    tools = SimpleNamespace(events=[], profile="test", is_enabled=lambda name: True, schemas=lambda: TOOL_SCHEMAS, stock_news=stock_news, stock_kline=stock_kline)
    result = run(AgentChatRequest(session_id="test", message="证据"), tools, Provider(), [])
    assert not outputs
    ledger = next(t.output for t in result.tool_results if t.name == "task_execution")
    assert ledger["replan_count"] == 1
    assert ledger["tool_calls"] == 2
    assert ledger["records"]["s1"]["state"] == "error"
    assert ledger["records"]["s2"]["state"] == "ok"
    assert "新闻查询失败" in result.answer


def test_frozen_dispatch_and_required_schema_do_not_touch_live_services():
    from app.agents.chat_live_eval_runner import FrozenLiveToolRegistry
    from app.agents.task_runtime.adapter import schemas_for_runtime
    tools = FrozenLiveToolRegistry([])
    result = invoke(tools, "stock_trend", "stock_kline", {"symbol": "000001", "days": 5})
    assert result.trace().result is not None
    schema = next(s for s in schemas_for_runtime(tools) if s["name"] == "rating_backtest")
    assert schema["args_schema"]["required"] == ["end_date", "start_date"]


def test_budget_keeps_completed_fanout_and_marks_partial(monkeypatch):
    import json
    import app.agents.task_runtime.runtime as runtime
    from app.agents.tools import ToolResult
    from app.models import AgentChatRequest
    from app.services.llm_provider import LLMResult
    monkeypatch.setattr(runtime, "MAX_TOOL_CALLS", 2)
    outputs = [
        {"requirements": [{"id": "r", "description": "研究", "source_text": "研究"}], "steps": [
            {"step_id": "a", "requirement_ids": ["r"], "tool_name": "hot_stock_ranking", "capability": "popularity"},
            {"step_id": "b", "requirement_ids": ["r"], "tool_name": "stock_kline", "capability": "stock_trend", "bindings": [{"source_step": "a", "path": "items.*.symbol", "target_argument": "symbol", "fan_out": True}]},
        ]},
        {"complete": False, "missing": {"r": "fanout incomplete"}, "reason": "budget"},
        {"blocks": []},
    ]
    provider = SimpleNamespace(generate_function_call=lambda *a, **k: LLMResult(content=json.dumps(outputs.pop(0)), model="test", provider="test"))
    def result(name, payload):
        return ToolResult(name=name, input={}, output=payload, trace_output=payload, summary="ok")
    tools = SimpleNamespace(events=[], profile="test", is_enabled=lambda n: True, schemas=lambda: TOOL_SCHEMAS,
        hot_stock_ranking=lambda: result("hot_stock_ranking", {"items": [{"symbol": "000001"}, {"symbol": "000002"}]}),
        stock_kline=lambda symbol: result("stock_kline", {"symbol": symbol, "close": 12.3}))
    response = runtime.run(AgentChatRequest(session_id="test", message="研究"), tools, provider, [])
    ledger = next(t.output for t in response.tool_results if t.name == "task_execution")
    assert ledger["tool_calls"] == 2
    assert ledger["records"]["b"]["state"] == "partial"
    assert ledger["records"]["b"]["payload"]["close"] == 12.3
    assert "12.3" in response.answer and "000001" in response.answer
    assert ledger["terminal_state"] == "partial"


def test_missing_task_cannot_pass_completion_and_anchor_is_frozen(monkeypatch):
    import json
    from app.agents.query_contract import query_reference_date_override
    from app.agents.task_runtime.runtime import run
    from app.agents.tools import ToolResult
    from app.models import AgentChatRequest
    from app.services.llm_provider import LLMResult
    outputs = [
        {"requirements": [{"id": rid, "description": rid, "source_text": "研究"} for rid in ["r1", "r2"]], "steps": [{"step_id": "s", "requirement_ids": ["r1"], "tool_name": "hot_stock_ranking", "capability": "popularity"}]},
        {"complete": True, "satisfied_ids": ["r1", "r2"], "reason": "incorrect model verdict"},
        {"new_steps": [], "reason": "unsupported"},
        {"blocks": []},
    ]
    def decide(system, user, **kwargs):
        assert json.loads(user)["anchor_date"] == "2026-05-15"
        if kwargs["function_name"] == "task_replan":
            assert "r2" in json.loads(user)["missing_requirements"]
        return LLMResult(content=json.dumps(outputs.pop(0)), model="test", provider="test")
    tools = SimpleNamespace(events=[], profile="test", is_enabled=lambda n: True, schemas=lambda: TOOL_SCHEMAS,
        hot_stock_ranking=lambda: ToolResult(name="hot_stock_ranking", input={}, output={"items": []}, trace_output={"items": []}, summary="empty", result_status="empty"))
    with query_reference_date_override(date(2026, 5, 15)):
        response = run(AgentChatRequest(session_id="test", message="研究"), tools, SimpleNamespace(generate_function_call=decide), [])
    ledger = next(t.output for t in response.tool_results if t.name == "task_execution")
    assert ledger["terminal_state"] == "partial"
    assert "r2" in ledger["completion"]["missing"]
    assert ledger["records"]["s"]["state"] == "empty"
