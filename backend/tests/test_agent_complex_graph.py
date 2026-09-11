"""LangGraph Phase 1 routing, binding and frozen-world execution tests."""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.agents.chat import answer_first_board_chat
from app.agents.chat_live_eval_runner import (
    FrozenLiveToolRegistry,
    SAMPLE_EVENTS,
    load_live_eval_dataset,
    run_live_eval_suite,
)
from app.agents.complex_graph import route_complexity, run_complex_graph, run_hot_limit_up_rating_graph
from app.agents.complex_graph.graph import (
    MAX_REPLAN,
    build_flagship_plan,
    resolve_dynamic_arguments,
)
from app.agents.complex_graph.models import ComplexPlanStep, ReplanOutput
from app.agents.complex_graph.replanner import validate_replan
from app.agents.tool_policy import AgentToolPolicyEngine
from app.agents.tool_execution.helpers import _normalize_limit_up_event_arguments
from app.models import AgentChatRequest
from app.services.llm_provider import LLMProvider, LLMResult


class _PhaseOneProvider(LLMProvider):
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        if "first job is to decide which tools are needed" in system_prompt:
            message = json.loads(user_prompt)["message"]
            complex_query = "交集" in message
            capabilities = (
                ["popularity", "limit_up_pool", "first_board_rating"]
                if complex_query
                else ["market_index_trend"]
            )
            return LLMResult(
                content=json.dumps(
                    {
                        "intent_label": capabilities[-1],
                        "safety": "normal",
                        "capabilities": capabilities,
                        "tool_calls": [],
                        "answer_directly": "",
                    }
                ),
                model="phase-one-test-planner",
                provider="test",
            )
        return LLMResult(
            content="工具事实已返回。",
            model="phase-one-test-answer",
            provider="test",
        )


def _live_case(case_id: str):
    return next(
        case for case in load_live_eval_dataset().cases if case.case_id == case_id
    )


def test_complexity_router_preserves_fast_path_for_simple_query() -> None:
    decision = route_complexity("上证指数最近5天走势如何？")
    assert decision.route == "fast"
    assert decision.reason_codes == ("fast_path_default",)


def test_complexity_router_selects_dynamic_intersection_query() -> None:
    decision = route_complexity(
        "取今天热股 Top10 和涨停股的交集，只对交集股票查询首板评分"
    )
    assert decision.route == "complex"
    assert "dependent_tool_arguments" in decision.reason_codes


def test_dynamic_binding_uses_observed_entity_set_and_keeps_empty_set() -> None:
    rating_step = build_flagship_plan({})[-1]
    resolved = resolve_dynamic_arguments(
        rating_step,
        {
            "hot_limit_up_intersection": [
                {"symbol": "300750", "name": "宁德时代", "source_steps": ["S1", "S2"]},
                {"symbol": "600000", "name": "浦发银行", "source_steps": ["S1", "S2"]},
            ]
        },
    )
    empty = resolve_dynamic_arguments(
        rating_step, {"hot_limit_up_intersection": []}
    )
    assert resolved == {"symbols": ["300750", "600000"]}
    assert empty == {"symbols": []}


def test_dynamic_binding_rejects_missing_source() -> None:
    with pytest.raises(ValueError, match="missing entity set"):
        resolve_dynamic_arguments(build_flagship_plan({})[-1], {})


def test_flagship_source_contract_is_not_polluted_by_top_or_rating_terms() -> None:
    step = build_flagship_plan({"event_status": "closed"})[1]
    normalized = _normalize_limit_up_event_arguments(
        AgentChatRequest(session_id="source-contract", message="查询当日完整涨停池"),
        step.arguments,
    )
    assert normalized["board_height"] is None
    assert normalized["limit"] == 100


def test_graph_executes_sources_then_binds_intersection_to_ratings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy_calls: list[tuple[str, ...]] = []
    original_reconcile = AgentToolPolicyEngine.reconcile

    def _reconcile_spy(self, *args, **kwargs):
        policy_calls.append(tuple(kwargs.get("capabilities") or ()))
        return original_reconcile(self, *args, **kwargs)

    monkeypatch.setattr(AgentToolPolicyEngine, "reconcile", _reconcile_spy)
    request = AgentChatRequest(
        session_id="phase-one-graph",
        message="取今天热股 Top10 和涨停股的交集，只对交集股票查询首板评分",
        trade_date=date(2026, 5, 15),
    )
    result = run_hot_limit_up_rating_graph(
        request=request,
        tools=FrozenLiveToolRegistry([]),
        limit_up_arguments={"trade_date": "2026-05-15", "event_status": "closed"},
        capabilities=("popularity", "limit_up_pool", "first_board_rating"),
        context_symbol=None,
        answer_builder=lambda execution: {
            "answer": "已基于冻结事实完成。",
            "source": "template_general_answer",
        },
    )

    assert result.completion_status == "complete"
    assert result.execution["tool_call_names"] == [
        "hot_stock_ranking",
        "limit_up_events",
        "first_board_ratings",
    ]
    assert result.execution["tool_results"][1].input["board_height"] is None
    assert result.execution["tool_results"][1].input["limit"] == 100
    rating_trace = result.execution["tool_results"][-1]
    intersection = result.execution["facts"]["hot_stock_limit_up_intersection"]
    assert intersection["event_label"] == "涨停票"
    expected_symbols = [item["symbol"] for item in intersection["items"]]
    assert rating_trace.input["symbols"] == expected_symbols
    assert {
        item["symbol"] for item in rating_trace.output["top_candidates"]
    } <= set(expected_symbols)
    rating_step = next(
        item for item in result.graph_traces
        if item.name == "complex_graph_step" and item.input["graph_step_id"] == "S4"
    )
    assert rating_step.input["dependency_sources"] == ["S3"]
    assert policy_calls == []


def test_graph_plan_validation_blocks_disabled_tool_before_execution() -> None:
    class _Registry(FrozenLiveToolRegistry):
        def is_enabled(self, tool_name: str) -> bool:
            return tool_name != "first_board_ratings" and super().is_enabled(tool_name)

    result = run_hot_limit_up_rating_graph(
        request=AgentChatRequest(session_id="blocked", message="热股和涨停股交集评分"),
        tools=_Registry([]),
        limit_up_arguments={},
        capabilities=("popularity", "limit_up_pool", "first_board_rating"),
        context_symbol=None,
        answer_builder=lambda execution: {"answer": "明确失败。", "source": "template_general_answer"},
    )
    assert result.completion_status == "failed"
    assert result.failed_steps == ["validate_plan"]
    assert result.execution["tool_call_names"] == []


def _run_scenario(case_id: str, scenario: str):
    case = _live_case(case_id)
    return run_complex_graph(
        scenario=scenario,
        request=AgentChatRequest(
            session_id=f"phase-two-{case_id}",
            message=case.turns[-1].user,
            trade_date=date(2026, 5, 15),
        ),
        tools=FrozenLiveToolRegistry(case.failure_injections),
        limit_up_arguments={"trade_date": "2026-05-15", "event_status": "closed"},
        context_symbol=None,
        answer_builder=lambda execution: {"answer": "已基于工具证据回答。", "source": "test"},
    )


def test_empty_observation_replans_to_fallback_tools() -> None:
    result = _run_scenario("LIVE-REPLAN-004", "empty_news_fallback_v2")
    assert result.completion_status == "complete"
    assert result.replan_count == 1
    assert result.execution["tool_call_names"] == ["stock_news", "stock_kline", "stock_activity"]


def test_partial_candidate_failure_keeps_successful_candidate() -> None:
    result = _run_scenario("LIVE-RECOVERY-004", "partial_stock_comparison_v2")
    assert result.completion_status == "complete"
    assert result.replan_count == 1
    assert result.execution["tool_call_names"] == ["stock_kline", "stock_kline", "stock_kline"]
    assert [item.result.status for item in result.execution["tool_results"]] == ["error", "ok", "error"]
    retry = result.execution["tool_results"][-1]
    assert retry.input["symbol"] == "300750"


def test_first_plan_complete_does_not_trigger_replan() -> None:
    result = _run_scenario("LIVE-REPLAN-006", "hot_limit_up_rating_intersection_v1")
    assert result.replan_count == 0
    assert not any(item.name == "complex_graph_replan" for item in result.graph_traces)


def test_max_replan_and_tool_budget_stop_with_partial_disclosure(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.agents.complex_graph.graph as graph_module

    monkeypatch.setattr(graph_module, "MAX_REPLAN", 0)
    bounded = _run_scenario("LIVE-REPLAN-004", "empty_news_fallback_v2")
    assert bounded.replan_count == 0
    assert bounded.completion_status == "partial"
    assert bounded.final_answer.startswith("当前信息不足或部分步骤未完成。")

    monkeypatch.setattr(graph_module, "MAX_REPLAN", MAX_REPLAN)
    monkeypatch.setattr(graph_module, "MAX_TOOL_CALLS", 1)
    tool_bounded = _run_scenario("LIVE-REPLAN-004", "empty_news_fallback_v2")
    assert tool_bounded.execution["tool_call_names"] == ["stock_news"]
    assert tool_bounded.completion_status == "partial"


def test_duplicate_replan_is_rejected() -> None:
    output = ReplanOutput(
        reason="duplicate",
        new_steps=[ComplexPlanStep(step_id="S1", capability="stock_trend", tool_name="stock_kline")],
    )
    assert "duplicate step" in validate_replan(output, prior_steps=[{"step_id": "S1"}], remaining_tool_calls=5)


def test_illegal_replan_tool_is_blocked_before_execution() -> None:
    errors = AgentToolPolicyEngine(FrozenLiveToolRegistry([])).validate_calls(
        [{"name": "finance_news", "arguments": {}}], capability="stock_trend"
    )
    assert errors == ["finance_news: not authorized by capability stock_trend"]
    invalid_args = AgentToolPolicyEngine(FrozenLiveToolRegistry([])).validate_calls(
        [{"name": "stock_kline", "arguments": {"symbol": ["bad"], "days": 999}}],
        capability="stock_trend",
    )
    assert "stock_kline.symbol: item 0: does not match required pattern" in invalid_args
    assert "stock_kline.days: must be <= 60" in invalid_args


def test_replan_trace_records_observation_completion_and_output() -> None:
    result = _run_scenario("LIVE-REPLAN-004", "empty_news_fallback_v2")
    replan_trace = next(item for item in result.graph_traces if item.name == "complex_graph_replan")
    assert replan_trace.input["tool_observations"][0]["result_state"] == "empty"
    assert replan_trace.input["missing_requirements"] == ["news_fallback_evidence"]
    assert replan_trace.output["new_steps"]
    step_trace = next(item for item in result.graph_traces if item.name == "complex_graph_step")
    for field in ("graph_run_id", "graph_step_id", "step_index", "step_type", "tool_args", "resolved_dynamic_args", "tool_call_count", "llm_call_count", "latency_ms"):
        assert field in step_trace.input


def test_stress_intersection_replans_risk_and_empty_fallback() -> None:
    result = _run_scenario("LIVE-STRESS-002", "intersection_risk_v2")
    assert result.completion_status == "complete"
    assert result.replan_count == 2
    assert result.execution["tool_call_names"][:3] == [
        "hot_stock_ranking", "limit_up_events", "first_board_ratings"
    ]
    assert "dragon_tiger_list" in result.execution["tool_call_names"]
    assert result.execution["tool_call_names"][-1] == "stock_news"
    assert len(result.execution["tool_call_names"]) <= 8
    kline = next(item for item in result.execution["tool_results"] if item.name == "stock_kline")
    assert set(kline.input["symbol"]) == {
        item["symbol"]
        for item in result.execution["facts"]["hot_stock_limit_up_intersection"]["items"]
    }


def test_live_target_uses_complex_trace_and_observed_rating_symbols() -> None:
    report = run_live_eval_suite(
        [_live_case("LIVE-REPLAN-006")],
        llm_provider=_PhaseOneProvider(),
        trials=1,
    )
    trial = report["results"][0]
    traces = trial["tool_trace"]
    rating = next(item for item in traces if item["name"] == "first_board_ratings")
    hot_symbols = [
        item["symbol"]
        for item in next(
            item for item in traces if item["name"] == "hot_stock_ranking"
        )["output"]["items"]
    ]
    limit_up_symbols = {
        item["symbol"]
        for item in next(
            item for item in traces if item["name"] == "limit_up_events"
        )["output"]["events"]
    }
    assert trial["passed"], trial["failure_reasons"]
    assert rating["input"]["symbols"] == [
        symbol for symbol in hot_symbols if symbol in limit_up_symbols
    ]
    assert "llm_tool_answer" not in trial["tool_calls"]


def test_simple_live_case_stays_on_fast_path() -> None:
    response = answer_first_board_chat(
        AgentChatRequest(session_id="phase-one-fast", message="上证指数最近5天走势如何？"),
        SAMPLE_EVENTS,
        llm_provider=_PhaseOneProvider(),
        tool_registry=FrozenLiveToolRegistry([]),
    )
    routing = next(item for item in response.tool_results if item.name == "routing_decision")
    assert routing.input["route"] == "fast"
    assert not any(item.name == "complex_graph_plan" for item in response.tool_results)


def test_complex_control_traces_do_not_appear_as_tools_or_evidence_cards() -> None:
    response = answer_first_board_chat(
        AgentChatRequest(
            session_id="phase-one-control-traces",
            message="取今天热股 Top10 和涨停股的交集，只对交集股票查询首板评分",
        ),
        SAMPLE_EVENTS,
        llm_provider=_PhaseOneProvider(),
        tool_registry=FrozenLiveToolRegistry([]),
    )
    assert response.tool_policy.final_tool_calls == [
        "hot_stock_ranking",
        "limit_up_events",
        "first_board_ratings",
    ]
    assert all(
        card.title not in {"complex_graph_plan", "complex_graph_step"}
        for card in response.evidence_cards
    )
