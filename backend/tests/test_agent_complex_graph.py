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
from app.agents.complex_graph import route_complexity, run_hot_limit_up_rating_graph
from app.agents.complex_graph.graph import (
    build_flagship_plan,
    resolve_dynamic_arguments,
)
from app.agents.tool_policy import AgentToolPolicyEngine
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
    rating_trace = result.execution["tool_results"][-1]
    intersection = result.execution["facts"]["hot_stock_limit_up_intersection"]
    expected_symbols = [item["symbol"] for item in intersection["items"]]
    assert rating_trace.input["symbols"] == expected_symbols
    assert {
        item["symbol"] for item in rating_trace.output["top_candidates"]
    } <= set(expected_symbols)
    assert result.graph_traces[-1].input["dependency_sources"] == ["S3"]
    assert policy_calls == [("popularity", "limit_up_pool", "first_board_rating")]


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
