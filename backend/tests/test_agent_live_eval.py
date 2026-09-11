from pathlib import Path

import json

from app.agents.chat_live_eval import aggregate_live_results, evaluate_live_trial
from app.agents.chat_live_eval_runner import (
    DATASET_PATH,
    judge_dimensions_for_case,
    judge_live_answer,
    load_live_eval_dataset,
)
from app.models import AgentChatResponse, AgentToolOutcome, AgentToolPolicyAudit, AgentToolTrace
from app.services.llm_provider import LLMProvider, LLMResult


def test_live_dataset_has_exact_high_value_distribution() -> None:
    dataset = load_live_eval_dataset(DATASET_PATH)
    assert len(dataset.cases) == 36
    assert sum(case.category == "replan" for case in dataset.cases) == 8
    assert sum(len(case.turns) > 1 for case in dataset.cases) == 6
    assert all(case.expected.max_tool_calls <= 8 for case in dataset.cases)


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


def test_raw_planner_and_effective_recall_are_separate() -> None:
    case = _case("LIVE-SIMPLE-006")
    response = _response(
        [_planner(["limit_up_pool"], raw_tools=[]), _trace("limit_up_events", {}, {"events": []})],
        answer="今日没有二连板。",
        repaired_tools=["limit_up_events"],
    )
    trial = _evaluate(case, response)
    assert trial["raw_capability_recall"] == 1.0
    assert trial["raw_required_tool_recall"] == 0.0
    assert trial["effective_required_tool_recall"] == 1.0
    assert trial["backend_repair_needed"]
    metrics = aggregate_live_results([trial])
    assert metrics["backend_repair_rate"] == 1.0


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
            backend_repaired_tools=repaired_tools or [],
        ),
        generated_by="test",
    )


def _planner(capabilities: list[str], raw_tools: list[str] | None = None) -> AgentToolTrace:
    calls = [{"name": name, "arguments": {}} for name in (raw_tools or [])]
    return AgentToolTrace(
        name="llm_tool_planner",
        input={"capabilities": capabilities, "tool_calls": calls},
        summary="plan",
    )


def _case(case_id: str):
    return next(case for case in load_live_eval_dataset().cases if case.case_id == case_id)


def _evaluate(case, response: AgentChatResponse) -> dict:
    return evaluate_live_trial(
        case, [response], llm_usage={"call_count": 2}, latency_ms=10
    )


class _JudgeProvider(LLMProvider):
    def generate(self, system_prompt: str, user_prompt: str) -> LLMResult:
        requested = json.loads(user_prompt)["dimensions"]
        return LLMResult(
            content=json.dumps({**{name: 2 for name in requested}, "rationale": "ok"}),
            model="judge-test",
            provider="test",
        )
