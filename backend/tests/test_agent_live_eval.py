from copy import deepcopy
from datetime import date
from pathlib import Path

import json
import pytest

from app.agents.chat_live_eval import aggregate_live_results, evaluate_live_trial
from app.agents.chat_live_eval_runner import (
    DATASET_PATH,
    FrozenLiveToolRegistry,
    _default_registry,
    judge_dimensions_for_case,
    judge_live_answer,
    load_live_eval_dataset,
    load_live_tool_world,
    run_live_eval_suite,
    validate_live_tool_world,
)
from app.models import (
    AgentChatRequest,
    AgentChatResponse,
    AgentToolOutcome,
    AgentToolPolicyAudit,
    AgentToolTrace,
)
from app.services.llm_provider import LLMProvider, LLMResult


def test_live_dataset_has_exact_high_value_distribution() -> None:
    dataset = load_live_eval_dataset(DATASET_PATH)
    assert len(dataset.cases) == 36
    assert sum(case.category == "replan" for case in dataset.cases) == 8
    assert sum(len(case.turns) > 1 for case in dataset.cases) == 6
    assert all(case.expected.max_tool_calls <= 8 for case in dataset.cases)


def test_live_dataset_uses_fully_frozen_tool_environment() -> None:
    dataset = load_live_eval_dataset(DATASET_PATH)
    assert dataset.environment_id == "chat-live-world-v2-fully-frozen-v2"
    registry = FrozenLiveToolRegistry([])
    mentioned = {
        tool
        for case in dataset.cases
        for tool in [
            *case.expected.required_tools,
            *case.expected.optional_tools,
            *case.expected.forbidden_tools,
        ]
    }
    assert mentioned <= set(registry.fixture.tools)


def test_live_tool_world_validation_rejects_cross_dated_payload() -> None:
    dataset = load_live_eval_dataset(DATASET_PATH)
    fixture = load_live_tool_world()
    fixture.tools = deepcopy(fixture.tools)
    fixture.tools["dragon_tiger_list"]["payload"]["as_of_date"] = "2026-05-16"

    with pytest.raises(ValueError, match="dragon_tiger_list must declare as_of_date"):
        validate_live_tool_world(dataset, fixture)


def test_frozen_live_world_normalizes_production_answer_contracts() -> None:
    registry = FrozenLiveToolRegistry([])
    request = AgentChatRequest(
        session_id="frozen-contract",
        message="检查冻结事实契约",
        trade_date=date(2026, 5, 15),
    )

    index_trace = registry.execute_frozen_calls(
        [{"name": "market_index_trend", "arguments": {"days": 5}}],
        request=request,
    )["tool_results"][0]
    news_trace = registry.execute_frozen_calls(
        [{"name": "stock_news", "arguments": {"symbol": "300750", "days": 7}}],
        request=request,
    )["tool_results"][0]

    index = index_trace.output["indices"][0]
    assert index_trace.output["requested_end_date"] == "2026-05-15"
    assert {"start_close", "end_close", "positive_days", "max_drawdown_pct"} <= set(index)
    assert news_trace.output["name"] == "宁德时代"
    assert news_trace.output["window_days"] == 7
    assert news_trace.output["cache_status"] == "frozen_fixture"
    assert news_trace.output["items"][0]["published_at"].startswith("2026-05-15")


def test_frozen_registry_executes_fixture_without_production_delegate() -> None:
    case = _case("LIVE-SIMPLE-002")
    registry = _default_registry(case)
    execution = registry.execute_frozen_calls(
        [{"name": "market_index_trend", "arguments": {"days": 5}}],
        request=AgentChatRequest(
            session_id="frozen-world",
            message=case.turns[0].user,
            trade_date=date(2026, 5, 15),
        ),
    )

    assert not hasattr(registry, "_delegate")
    trace = execution["tool_results"][0]
    assert trace.result is not None and trace.result.status == "ok"
    assert trace.output["as_of_date"] == "2026-05-15"
    assert trace.output["indices"][0]["name"] == "上证指数"
    assert "完全冻结" in trace.summary


def test_frozen_registry_applies_argument_scoped_failure_injection() -> None:
    case = _case("LIVE-RECOVERY-004")
    execution = _default_registry(case).execute_frozen_calls(
        [
            {"name": "stock_kline", "arguments": {"symbol": "300750", "days": 20}},
            {"name": "stock_kline", "arguments": {"symbol": "600000", "days": 20}},
        ],
        request=AgentChatRequest(
            session_id="frozen-injection",
            message=case.turns[0].user,
            trade_date=date(2026, 5, 15),
        ),
    )

    assert [trace.result.status for trace in execution["tool_results"]] == ["error", "ok"]
    assert execution["tool_results"][1].output["symbol"] == "600000"


def test_live_suite_runs_real_orchestration_against_only_frozen_facts() -> None:
    report = run_live_eval_suite(
        [_case("LIVE-SIMPLE-002")],
        llm_provider=_FrozenWorldProvider(),
        trials=1,
    )

    assert report["runner_version"] == "agent-react-live-eval-runner-v6"
    assert report["agent_architecture"] == "langgraph-bounded-react"
    assert report["observation_driven_replan_supported"] is True
    assert report["tool_environment"] == {
        "fixture_snapshot_id": "chat-live-world-v2",
        "fully_frozen": True,
        "database_access": False,
        "network_access": False,
    }
    tool_trace = next(
        item for item in report["results"][0]["tool_trace"]
        if item["name"] == "market_index_trend"
    )
    assert tool_trace["output"]["as_of_date"] == "2026-05-15"


def test_argument_dependency_uses_observed_source_values() -> None:
    case = next(
        case for case in load_live_eval_dataset().cases
        if case.case_id == "LIVE-REPLAN-001"
    )
    response = _response(
        [
            AgentToolTrace(
                name="llm_tool_planner",
                input={"capabilities": ["first_board_rating", "stock_trend"]},
                summary="plan",
            ),
            _trace(
                "first_board_ratings",
                {},
                {"top_candidates": [{"symbol": "300750"}, {"symbol": "600000"}]},
            ),
            _trace("stock_kline", {"symbol": "300750"}, {"symbol": "300750"}),
        ],
        answer="300750 最近5日走势已检查。",
    )
    result = evaluate_live_trial(
        case, [response], llm_usage={"call_count": 2}, latency_ms=10
    )
    assert not any("argument dependency failed" in item for item in result["failure_reasons"])

    response.tool_results[-1].input["symbol"] = "000001"
    failed = evaluate_live_trial(
        case, [response], llm_usage={"call_count": 2}, latency_ms=10
    )
    assert any("argument dependency failed" in item for item in failed["failure_reasons"])


def test_unscoped_empty_result_proves_no_source_entity_matches() -> None:
    case = _case("LIVE-REPLAN-002")
    response = _response(
        [
            _planner(["first_board_rating", "dragon_tiger"]),
            _trace("first_board_ratings", {}, {"top_candidates": [{"symbol": "300750"}]}),
            _trace("dragon_tiger_list", {"limit": 100}, {"items": []}, state="empty"),
            _trace("stock_kline", {"symbol": "300750"}, {"symbol": "300750"}),
            _trace("stock_news", {"symbol": "300750"}, {"symbol": "300750"}),
        ],
        answer="龙虎榜为空，已补查K线和新闻。",
    )
    result = evaluate_live_trial(
        case, [response], llm_usage={"call_count": 2}, latency_ms=10
    )
    assert not any("argument dependency failed" in item for item in result["failure_reasons"])


def test_runtime_condition_requires_fallback_only_when_observed() -> None:
    case = next(
        case for case in load_live_eval_dataset().cases
        if case.case_id == "LIVE-REPLAN-004"
    )
    response = _response(
        [
            AgentToolTrace(
                name="llm_tool_planner",
                input={"capabilities": ["stock_news"]},
                summary="plan",
            ),
            _trace("stock_news", {"symbol": "300750"}, {}, state="empty"),
        ],
        answer="没有查到相关新闻。",
    )
    result = evaluate_live_trial(
        case, [response], llm_usage={"call_count": 2}, latency_ms=10
    )
    assert "conditional tools missing after observation: ['stock_activity', 'stock_kline']" in result["failure_reasons"]


def test_conditional_tools_must_follow_trigger_observation() -> None:
    case = _case("LIVE-REPLAN-004")
    before = _response(
        [
            _planner(["stock_news"]),
            _trace("stock_kline", {"symbol": "300750"}, {"symbol": "300750"}),
            _trace("stock_activity", {"symbol": "300750"}, {"symbol": "300750"}),
            _trace("stock_news", {"symbol": "300750"}, {}, state="empty"),
        ],
        answer="新闻为空，已补充其他资料。",
    )
    failed = _evaluate(case, before)
    assert any("conditional tools missing after observation" in item for item in failed["failure_reasons"])

    after = _response(
        [
            _planner(["stock_news"]),
            _trace("stock_news", {"symbol": "300750"}, {}, state="empty"),
            _trace("stock_kline", {"symbol": "300750"}, {"symbol": "300750"}),
            _trace("stock_activity", {"symbol": "300750"}, {"symbol": "300750"}),
        ],
        answer="新闻为空，已补充其他资料。",
    )
    passed = _evaluate(case, after)
    assert not any("conditional tools" in item for item in passed["failure_reasons"])
    assertion = passed["conditional_assertions"][0]
    assert assertion["condition_observation_index"] == 0
    assert assertion["target_tool_call_indices"] == {"stock_kline": [1], "stock_activity": [2]}


def test_content_condition_triggers_only_when_observation_contains_value() -> None:
    case = _case("LIVE-REPLAN-008")
    triggered = _response(
        [
            _planner(["sector_performance"]),
            _trace("sector_performance", {}, {"top_sectors": [{"sector_name": "半导体"}]}),
            _trace("sector_stock_ranking", {"sector": "半导体"}, {"stocks": []}),
        ],
        answer="半导体进入前5。",
    )
    assert _evaluate(case, triggered)["conditional_assertions"][0]["passed"]

    not_triggered = _response(
        [
            _planner(["sector_performance"]),
            _trace("sector_performance", {}, {"top_sectors": [{"sector_name": "医药"}]}),
        ],
        answer="半导体没有进入前5。",
    )
    result = _evaluate(case, not_triggered)
    assert result["conditional_assertions"][0] == {
        "condition_tool": "sector_performance", "matched": False, "passed": True
    }
    assert not any("sector_stock_ranking" in item for item in result["failure_reasons"])


def test_two_source_intersection_dependency_requires_every_source() -> None:
    case = _case("LIVE-REPLAN-006")
    traces = [
        _planner(["popularity", "limit_up_pool", "first_board_rating"]),
        _trace("hot_stock_ranking", {}, {"items": [{"symbol": "A"}, {"symbol": "B"}]}),
        _trace("limit_up_events", {}, {"events": [{"symbol": "B"}, {"symbol": "C"}]}),
        _trace("first_board_ratings", {"symbols": ["B"]}, {"top_candidates": [{"symbol": "B"}]}),
    ]
    passed = _evaluate(case, _response(traces, answer="交集是B。"))
    assert not any("multi-source dependency failed" in item for item in passed["failure_reasons"])

    missing = _evaluate(case, _response([traces[0], traces[1], traces[3]], answer="交集是B。"))
    assert any("missing sources: ['limit_up_events']" in item for item in missing["failure_reasons"])


def test_judge_omits_non_applicable_dimensions() -> None:
    case = _case("LIVE-SIMPLE-002")
    assert judge_dimensions_for_case(case) == [
        "completeness", "clarity", "relevance", "task_resolution"
    ]
    result = judge_live_answer(
        case,
        {"tool_trace": [], "answer": "指数最近5日震荡。"},
        _JudgeProvider(),
    )
    assert result["passed"]
    assert "risk_explanation" not in result["scores"]


def test_judge_accepts_explicit_nested_scores_schema() -> None:
    case = _case("LIVE-SIMPLE-002")
    result = judge_live_answer(
        case,
        {"tool_trace": [], "answer": "指数最近5日震荡。"},
        _JudgeProvider(nested_scores=True),
    )

    assert result["scores"]["completeness"] == 2
    assert result["passed"]


def test_raw_decision_and_effective_recall_are_separate() -> None:
    case = _case("LIVE-SIMPLE-006")
    response = _response(
        [_planner(["limit_up_pool"], raw_tools=[]), _trace("limit_up_events", {}, {"events": []})],
        answer="今日没有二连板。",
        repaired_tools=["limit_up_events"],
    )
    trial = _evaluate(case, response)
    assert trial["raw_capability_recall"] == 0.0
    assert trial["raw_required_tool_recall"] == 0.0
    assert trial["effective_required_tool_recall"] == 1.0
    assert trial["capability_basis"] == "raw_tool_calls"
    metrics = aggregate_live_results([trial])
    assert "replan_trigger_rate" not in metrics


def test_required_fact_coverage_does_not_claim_unsupported_detection() -> None:
    case = _case("LIVE-SIMPLE-002")
    response = _response(
        [
            _planner(["market_index_trend"], raw_tools=["market_index_trend"]),
            _trace("market_index_trend", {}, {"indices": [{"name": "上证指数"}]}),
        ],
        answer="上证指数最近5日震荡。",
    )
    metrics = aggregate_live_results([_evaluate(case, response)])
    assert metrics["required_fact_coverage"] == 1.0
    assert metrics["unsupported_claim_rate"] is None
    assert "grounding_accuracy" not in metrics


def _trace(name: str, arguments: dict, output: dict, state: str = "ok") -> AgentToolTrace:
    return AgentToolTrace(
        name=name,
        input=arguments,
        output=output,
        summary=name,
        status="error" if state == "error" else "success",
        result=AgentToolOutcome(status=state, payload=output),
    )


def _response(
    traces: list[AgentToolTrace], *, answer: str, repaired_tools: list[str] | None = None
) -> AgentChatResponse:
    tools = [trace.name for trace in traces if trace.name != "llm_tool_planner"]
    return AgentChatResponse(
        session_id="live-eval-test",
        intent="test",
        answer=answer,
        tool_calls=["llm_tool_planner", *tools],
        tool_results=traces,
        tool_policy=AgentToolPolicyAudit(
            final_tool_calls=tools,
            policy_repaired_tools=repaired_tools or [],
        ),
        generated_by="test",
    )


def _planner(capabilities: list[str], raw_tools: list[str] | None = None) -> AgentToolTrace:
    calls = [{"name": name, "arguments": {}} for name in (raw_tools or [])]
    return AgentToolTrace(
        name="react_decision",
        output={"tool_calls": calls},
        summary="plan",
    )


def _case(case_id: str):
    return next(case for case in load_live_eval_dataset().cases if case.case_id == case_id)


def _evaluate(case, response: AgentChatResponse) -> dict:
    return evaluate_live_trial(
        case, [response], llm_usage={"call_count": 2}, latency_ms=10
    )


class _JudgeProvider(LLMProvider):
    def __init__(self, *, nested_scores: bool = False) -> None:
        self.nested_scores = nested_scores

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        requested = json.loads(user_prompt)["dimensions"]
        scores = {name: 2 for name in requested}
        return LLMResult(
            content=json.dumps(
                {"scores": scores, "rationale": "ok"}
                if self.nested_scores
                else {**scores, "rationale": "ok"}
            ),
            model="judge-test",
            provider="test",
        )


class _FrozenWorldProvider:
    def generate_messages(self, messages, tools, **kwargs):
        from langchain_core.messages import AIMessage, ToolMessage
        observed = [m for m in messages if isinstance(m, ToolMessage)]
        if not observed:
            return AIMessage(content="", tool_calls=[{"name": "market_index_trend", "args": {"days": 5}, "id": "index"}])
        evidence_id = json.loads(observed[-1].content)["evidence_id"]
        return AIMessage(content="", tool_calls=[{"name": "finish", "args": {
            "status": "complete", "answer": "截至2026-05-15，上证指数、深证成指和创业板指近5日数据已返回。",
            "evidence_ids": [evidence_id], "missing": []}, "id": "finish"}])


def test_react_control_traces_do_not_count_as_business_tools():
    report = run_live_eval_suite([_case("LIVE-SIMPLE-002")], llm_provider=_FrozenWorldProvider(), trials=1)
    result = report["results"][0]
    assert result["llm_call_count"] == 2
    assert result["tool_call_count"] == 1
    assert result["raw_required_tool_recall"] == 1.0
    assert result["task_statuses"] == ["complete"]
    assert [t["name"] for t in result["tool_trace"]] == ["market_index_trend"]
    assert any(t["name"] == "react_execution" for t in result["graph_trace"])


def test_frozen_react_preserves_success_when_another_entity_fails():
    from langchain_core.messages import AIMessage, ToolMessage
    class Model:
        def generate_messages(self, messages, tools, **kwargs):
            observed = [m for m in messages if isinstance(m, ToolMessage)]
            if not observed:
                return AIMessage(content="", tool_calls=[
                    {"name": "stock_kline", "args": {"symbol": symbol, "days": 20, "end_date": "2026-05-15"}, "id": symbol}
                    for symbol in ["300750", "600000"]])
            payloads = [json.loads(m.content) for m in observed]
            assert {p["result_state"] for p in payloads} == {"error", "ok"}
            return AIMessage(content="", tool_calls=[{"name": "finish", "id": "done", "args": {
                "status": "partial", "answer": "300750行情来源失败；600000行情已返回，保留其研究结果。",
                "evidence_ids": [p["evidence_id"] for p in payloads], "missing": ["300750行情"]}}])
    result = run_live_eval_suite([_case("LIVE-RECOVERY-004")], llm_provider=Model(), trials=1)["results"][0]
    assert result["task_statuses"] == ["partial"]
    traces = result["tool_trace"]
    assert [(t["input"]["symbol"], t["result"]["status"]) for t in traces] == [("300750", "error"), ("600000", "ok")]
    assert result["llm_call_count"] == 2


def test_failed_react_run_is_never_a_successful_boundary_answer():
    case = _case("LIVE-SIMPLE-002")
    response = _response([_trace("market_index_trend", {}, {"indices": [{"name": "上证指数"}]})], answer="上证指数")
    response.task_status = "error"
    result = _evaluate(case, response)
    assert not result["passed"]
    assert "runtime did not finish the requested task" in result["failure_reasons"]
