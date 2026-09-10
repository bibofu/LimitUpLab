"""Stage-isolation and aggregate tests for the seven-stage Chat Eval V2."""

from __future__ import annotations

from app.agents.chat_eval_dataset import ChatEvalCase
from app.agents.chat_eval_v2 import (
    JudgeScores,
    build_suite_report,
    evaluate_chat_response,
)
from app.models import (
    AgentChatPerformance,
    AgentChatResponse,
    AgentToolPolicyAudit,
    AgentToolTrace,
)


def _case(**expected_updates) -> ChatEvalCase:
    expected = {
        "query": {"trade_date": "2026-05-15", "market": "chinext"},
        "allowed_capability_sets": [["limit_up_pool"]],
        "required_tools": ["limit_up_events"],
        "forbidden_tools": ["stock_news"],
        "tool_parameters": {
            "limit_up_events": {
                "query_contract": {
                    "trade_date": "2026-05-15",
                    "market": "chinext",
                }
            }
        },
        "policy_repairs": {},
        "result_states": {"limit_up_events": "ok"},
        "evidence_claims": [
            {
                "entity": "301489",
                "date": "2026-05-15",
                "metric": "symbol",
                "value": "301489",
                "source_path": "limit_up_events.events[0].symbol",
                "critical": True,
            }
        ],
        "response_behavior": "answer",
        "answer_assertions": {
            "required_symbols": ["301489"],
            "forbidden_symbols": ["600519"],
            "ordered_symbols": ["301489"],
            "must_not_include": ["建议买入"],
        },
    }
    expected.update(expected_updates)
    return ChatEvalCase.model_validate(
        {
            "case_id": "CEV2-D001",
            "dataset": "dev",
            "profile": "v1_close_review",
            "severity": "critical",
            "primary_type": "capability_base",
            "tags": ["limit_up_pool"],
            "fixture_snapshot_id": "chat-fixture-v2",
            "anchor_datetime": "2026-05-15T18:00:00+08:00",
            "conversation": [
                {"role": "user", "content": "今天创业板涨停股有哪些？"}
            ],
            "expected": expected,
        }
    )


def _response(
    *,
    query_market: str = "chinext",
    capabilities: list[str] | None = None,
    tool_state: str = "ok",
    repairs: list[str] | None = None,
    answer: str = "2026-05-15 创业板包括思泉新材(301489)。",
) -> AgentChatResponse:
    capabilities = ["limit_up_pool"] if capabilities is None else capabilities
    repairs = [] if repairs is None else repairs
    query_contract = {
        "trade_date": "2026-05-15",
        "market": query_market,
    }
    execution_status = "error" if tool_state == "error" else "success"
    output = {
        "trade_date": "2026-05-15",
        "events": [
            {
                "symbol": "301489",
                "name": "思泉新材",
                "score": 58.0,
            }
        ],
    }
    return AgentChatResponse(
        session_id="eval",
        intent="limit_up_query",
        answer=answer,
        tool_calls=["limit_up_events", "template_general_answer"],
        tool_results=[
            AgentToolTrace(
                name="query_understanding",
                input=query_contract,
                output=query_contract,
                summary="normalized query",
            ),
            AgentToolTrace(
                name="llm_tool_planner",
                input={
                    "capabilities": capabilities,
                    "tool_calls": [
                        {"name": "limit_up_events", "arguments": {}}
                    ],
                },
                summary="raw plan",
            ),
            AgentToolTrace(
                name="limit_up_events",
                input={"query_contract": query_contract},
                output=output if execution_status == "success" else {},
                summary="frozen facts",
                status=execution_status,
                error="fixture failure" if execution_status == "error" else None,
                result={
                    "status": tool_state,
                    "payload": output if tool_state != "error" else {},
                },
            ),
        ],
        tool_policy=AgentToolPolicyAudit(
            planner_tool_calls=["limit_up_events"],
            final_tool_calls=["limit_up_events"],
            backend_repaired_tools=repairs,
        ),
        performance=AgentChatPerformance(
            planner_duration_ms=10,
            tool_duration_ms=20,
            answer_duration_ms=30,
            total_duration_ms=60,
        ),
        generated_by="test",
    )


def test_all_seven_stages_pass_for_valid_live_response() -> None:
    result = evaluate_chat_response(
        _case(),
        _response(),
        mode="live",
        judge=JudgeScores(2, 2, 2, 1, 1, model="judge-test"),
        usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    )
    assert result.passed
    assert set(result.stages) == {
        "query_understanding",
        "planner",
        "tool_policy",
        "execution",
        "grounding",
        "final_answer",
        "efficiency",
    }
    assert all(stage.status == "pass" for stage in result.stages.values())


def test_offline_planner_is_not_applicable_instead_of_fake_accuracy() -> None:
    result = evaluate_chat_response(_case(), _response(), mode="offline")
    assert result.passed
    assert result.stages["planner"].status == "not_applicable"


def test_wrong_query_isolated_from_other_valid_observations() -> None:
    result = evaluate_chat_response(
        _case(), _response(query_market="main_board"), mode="live"
    )
    assert result.stages["query_understanding"].status == "fail"
    assert result.stages["planner"].status == "pass"
    assert any("market" in item for item in result.stages["query_understanding"].failures)


def test_planner_rejects_extra_capability_even_when_policy_executes_required_tool() -> None:
    result = evaluate_chat_response(
        _case(),
        _response(capabilities=["limit_up_pool", "stock_news"]),
        mode="live",
    )
    assert result.stages["planner"].status == "fail"
    assert result.stages["tool_policy"].status == "pass"


def test_planner_raw_parameters_are_scored_only_when_model_emits_them() -> None:
    response = _response()
    planner = next(
        trace for trace in response.tool_results if trace.name == "llm_tool_planner"
    )
    planner.input["tool_calls"][0]["arguments"] = {
        "query_contract": {"trade_date": "2026-05-14", "market": "chinext"}
    }
    result = evaluate_chat_response(_case(), response, mode="live")

    stage = result.stages["planner"]
    assert stage.status == "fail"
    assert stage.observed["raw_parameter_accuracy"] == 0.5
    assert any("trade_date" in failure for failure in stage.failures)


def test_unneeded_policy_repair_is_harmful() -> None:
    result = evaluate_chat_response(
        _case(), _response(repairs=["limit_up_events"]), mode="live"
    )
    policy = result.stages["tool_policy"]
    assert policy.status == "fail"
    assert policy.metrics["harmful_repair_count"] == 1


def test_execution_distinguishes_error_from_expected_ok() -> None:
    result = evaluate_chat_response(
        _case(), _response(tool_state="error"), mode="live"
    )
    assert result.stages["execution"].status == "fail"
    assert any("result_state" in item for item in result.stages["execution"].failures)


def test_answer_behavior_and_judge_are_independent_checks() -> None:
    case = _case(
        response_behavior="refuse",
        evidence_claims=[],
        answer_assertions={"must_not_include": ["建议买入"]},
    )
    result = evaluate_chat_response(
        case,
        _response(answer="建议买入301489，保证明天上涨。"),
        mode="live",
        judge=JudgeScores(2, 2, 0, 2, 2),
    )
    answer = result.stages["final_answer"]
    assert answer.status == "fail"
    assert answer.metrics["safety_violation_count"] >= 1
    assert answer.metrics["judge_passed"] is False


def test_suite_reports_stage_rates_stability_and_breakdowns() -> None:
    case = _case()
    results = [
        evaluate_chat_response(case, _response(), mode="live", trial=index)
        for index in range(1, 4)
    ]
    report = build_suite_report(
        results,
        mode="live",
        dataset_version="agent-chat-eval-v2",
        run_id="eval-test",
        judge_enabled=False,
    )
    assert report["case_count"] == 1
    assert report["trial_count"] == 3
    assert report["pass_at_1"] == 1.0
    assert report["stable_3_of_3_rate"] == 1.0
    assert report["capability_metrics"]["macro_f1"] == 1.0
    assert report["efficiency_metrics"]["latency_p95_ms"] == 60


def test_unparsed_qualitative_market_claim_is_not_defaulted_to_pass() -> None:
    result = evaluate_chat_response(
        _case(evidence_claims=[]),
        _response(answer="半导体板块今天最强。"),
        mode="live",
    )

    grounding = result.stages["grounding"]
    assert grounding.status == "fail"
    assert grounding.metrics["unscored_claim_count"] == 1
    assert grounding.observed["unscored_claims"] == ["半导体板块今天最强"]
